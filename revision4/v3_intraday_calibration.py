"""Calibration adapter for the sealed V3 intraday replay.

The search algorithms are the existing Revision 2 implementations.  This
module changes only their evaluator: every candidate is replayed through the
V3 shared portfolio, daily EOD flattening, immutable safety policies and the
complete completed-trade ledger.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.optimizer import CMAES, RandomSearch, SearchSpace, TPESampler, Trial, local_fine_tune
from revision4.validate_48symbol_sealed import run_48symbol_validation


DEFAULT_ECONOMIC_PARAMETERS = (
    "entry_confidence_threshold",
    "profit_target_atr_mult",
    "stop_loss_atr_mult",
    "minimum_profit_margin_over_cost",
    "max_hold_bars",
)


@dataclass(frozen=True)
class V3CalibrationResult:
    training_trials: List[Trial]
    validation_trials: List[Trial]
    selected_params: Optional[Dict[str, Any]]
    selected_validation_report: Optional[Dict[str, Any]]


class V3IntradayCalibrator:
    """Search training only; validate finalists without feeding back results."""

    def __init__(
        self,
        train_start: str,
        train_end: str,
        validation_start: str,
        validation_end: str,
        parameter_names: Iterable[str] = DEFAULT_ECONOMIC_PARAMETERS,
        evaluator: Callable[..., Dict[str, Any]] = run_48symbol_validation,
        seed: int = 0,
    ) -> None:
        self.train_start, self.train_end = train_start, train_end
        self.validation_start, self.validation_end = validation_start, validation_end
        self.evaluator = evaluator
        self.seed = seed
        registry = CanonicalParameterRegistry()
        names = list(parameter_names)
        if not names:
            raise ValueError("at least one economic parameter is required")
        invalid = []
        for name in names:
            try:
                spec = registry.get(name)
            except KeyError:
                invalid.append(f"unknown parameter {name}")
                continue
            if not spec.calibratable or spec.param_type not in ("int", "float"):
                invalid.append(f"non-calibratable or non-numeric parameter {name}")
        if invalid:
            raise ValueError("invalid calibration surface: " + "; ".join(invalid))
        self.space = SearchSpace(
            names=names,
            minimum={name: float(registry.get(name).minimum) for name in names},
            maximum={name: float(registry.get(name).maximum) for name in names},
            is_int={name: registry.get(name).param_type == "int" for name in names},
        )

    @staticmethod
    def _score(report: Dict[str, Any]) -> float:
        """Fail closed: unsafe, non-intraday and empty candidates cannot rank."""
        intraday = report.get("intraday_audit", {})
        reconciliation = report.get("reconciliation", {})
        if report.get("status") != "PASSED":
            return float("-inf")
        if not reconciliation.get("exact") or not intraday.get("all_trades_same_session"):
            return float("-inf")
        if intraday.get("completed_trade_count", 0) == 0:
            return float("-inf")
        return float(report["financials"]["realized_pnl"])

    def _evaluate_training(self, params: Dict[str, Any]):
        report = self.evaluator(
            month_start=self.train_start, month_end=self.train_end,
            calibration_overrides=params,
        )
        return self._score(report), {
            "net_pnl": report["financials"]["realized_pnl"],
            "trades": report["intraday_audit"]["completed_trade_count"],
            "status": report["status"],
            "reconciliation_exact": report["reconciliation"]["exact"],
            "all_trades_same_session": report["intraday_audit"]["all_trades_same_session"],
        }

    def run(self, phase1_trials: int = 6, phase2_generations: int = 1,
            phase3_iterations: int = 1, validation_finalists: int = 2) -> V3CalibrationResult:
        if phase1_trials < 2:
            raise ValueError("phase1_trials must be at least 2")
        objective = self._evaluate_training
        random_count = phase1_trials // 2
        random_trials = RandomSearch(self.space, seed=self.seed).run(objective, random_count, "phase1_random")
        tpe_trials = TPESampler(self.space, seed=self.seed + 1).run(
            objective, phase1_trials - random_count, random_trials, "phase1_tpe"
        )
        training_trials = random_trials + tpe_trials
        finite = [trial for trial in training_trials if math.isfinite(trial.score)]
        if finite and phase2_generations:
            seed_mean = max(finite, key=lambda trial: trial.score).params
            phase2 = CMAES(self.space, seed=self.seed + 2).run(
                objective, phase2_generations, seed_mean, "phase2_cmaes"
            )
            training_trials.extend(phase2)
            finite = [trial for trial in training_trials if math.isfinite(trial.score)]
        if finite and phase3_iterations:
            seed_params = max(finite, key=lambda trial: trial.score).params
            training_trials.extend(local_fine_tune(
                objective, self.space, seed_params, phase3_iterations, self.seed + 3
            ))

        finalists = sorted(
            (trial for trial in training_trials if math.isfinite(trial.score)),
            key=lambda trial: trial.score, reverse=True,
        )[:validation_finalists]
        validation_trials = []
        reports = []
        for finalist in finalists:
            report = self.evaluator(
                month_start=self.validation_start, month_end=self.validation_end,
                calibration_overrides=finalist.params,
            )
            validation_trials.append(Trial(
                params=finalist.params, score=self._score(report),
                metrics={"net_pnl": report["financials"]["realized_pnl"],
                         "trades": report["intraday_audit"]["completed_trade_count"],
                         "status": report["status"]},
                phase="validation_selection",
            ))
            reports.append(report)
        if not validation_trials:
            return V3CalibrationResult(training_trials, [], None, None)
        best_index = max(range(len(validation_trials)), key=lambda index: validation_trials[index].score)
        winner = validation_trials[best_index]
        return V3CalibrationResult(
            training_trials, validation_trials,
            winner.params if math.isfinite(winner.score) else None,
            reports[best_index] if math.isfinite(winner.score) else None,
        )
