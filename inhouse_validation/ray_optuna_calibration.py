"""Bounded Ray Tune + Optuna calibration for the in-house sealed replay.

Workers invoke the real shared-portfolio ``Revision2PortfolioOrchestrator``
through ``SealedReplayRunner``.  This is intentionally separate from the
legacy root-level Ray scripts, which search safety settings and do not provide
sealed train/validation separation.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from ray import tune
from ray.tune import RunConfig
from ray.tune.search.optuna import OptunaSearch

from canonical_parameter_registry import CanonicalParameterRegistry, ParameterSpec
from inhouse_validation.sealed_replay_runner import SealedReplayRunner


INELIGIBLE_SCORE = -1.0e18
DEFAULT_INHOUSE_ECONOMIC_PARAMETERS = (
    "entry_confidence_threshold",
    "profit_target_atr_mult",
    "stop_loss_atr_mult",
    "minimum_absolute_profit_rupees",
    "min_risk_reward_ratio",
    "max_hold_bars",
)
# The profit-floor bounds are deliberately narrower than the registry's broad
# 0–200 declaration: observed plans are below Rs.50, while <Rs.10 gives too
# little room above direct costs.  This is an economic search bound, not a
# safety-policy change.
SEARCH_BOUNDS = {"entry_confidence_threshold": (0.30, 0.55),
                 "minimum_absolute_profit_rupees": (10.0, 50.0)}


def _specs(names: Iterable[str]) -> Dict[str, ParameterSpec]:
    registry = CanonicalParameterRegistry()
    result, errors = {}, []
    for name in tuple(names):
        try:
            spec = registry.get(name)
        except KeyError:
            errors.append(f"unknown parameter {name}")
            continue
        if not spec.calibratable or spec.param_type not in {"int", "float"}:
            errors.append(f"non-calibratable or non-numeric parameter {name}")
            continue
        result[name] = spec
    if not result:
        errors.append("at least one economic parameter is required")
    if errors:
        raise ValueError("invalid in-house calibration surface: " + "; ".join(errors))
    return result


def inhouse_search_space(names: Iterable[str]) -> Dict[str, Any]:
    space: Dict[str, Any] = {}
    for name, spec in _specs(names).items():
        lower, upper = SEARCH_BOUNDS.get(name, (spec.minimum, spec.maximum))
        if spec.param_type == "int":
            space[name] = tune.randint(int(lower), int(upper) + 1)
        else:
            space[name] = tune.uniform(float(lower), float(upper))
    return space


def _normalise(config: Mapping[str, Any], names: Iterable[str]) -> Dict[str, Any]:
    return {name: (int(config[name]) if spec.param_type == "int" else float(config[name]))
            for name, spec in _specs(names).items()}


def _eligible(report: Mapping[str, Any], completed_trades: int) -> bool:
    return (
        report.get("status") == "PASSED"
        and bool(report.get("reconciliation_exact"))
        and bool(report.get("audit_chain_valid"))
        and int(report.get("gate16_breaches", 0)) == 0
        and completed_trades > 0
    )


def evaluate_inhouse_trial(
    config: Mapping[str, Any], *, manifest_path: str, data_dir: Optional[str],
    train_start: str, train_end: str, parameter_names: Tuple[str, ...],
    runner_factory: Callable[..., SealedReplayRunner] = SealedReplayRunner,
) -> None:
    """One manifest-verified, one-month, shared-portfolio Ray worker."""
    overrides = _normalise(config, parameter_names)
    runner = runner_factory(
        manifest_path=manifest_path, nse_data_dir_override=data_dir,
        calibration_overrides=overrides, month_start=train_start, month_end=train_end,
    )
    report = asdict(runner.run())
    completed = len(runner.completed_trades)
    eligible = _eligible(report, completed)
    net_pnl = float(report["realized_pnl"])
    tune.report({
        "score": net_pnl if eligible else INELIGIBLE_SCORE,
        "eligible": eligible,
        "net_pnl": net_pnl,
        "completed_trades": completed,
        "status": report["status"],
        "reconciliation_exact": bool(report["reconciliation_exact"]),
        "audit_chain_valid": bool(report["audit_chain_valid"]),
        "gate16_breaches": int(report["gate16_breaches"]),
    })


@dataclass(frozen=True)
class InHouseCalibrationResult:
    training_trials: List[Dict[str, Any]]
    validation_trials: List[Dict[str, Any]]
    selected_params: Optional[Dict[str, Any]]
    selected_validation_report: Optional[Dict[str, Any]]
    artifact_path: str


class InHouseRayOptunaCalibrator:
    """Two-worker, train-only search followed by non-feedback validation."""

    def __init__(self, *, manifest_path: str, train_start: str, train_end: str,
                 validation_start: str, validation_end: str,
                 data_dir: Optional[str] = None,
                 parameter_names: Iterable[str] = DEFAULT_INHOUSE_ECONOMIC_PARAMETERS,
                 runner_factory: Callable[..., SealedReplayRunner] = SealedReplayRunner,
                 seed: int = 20260909) -> None:
        self.manifest_path = str(Path(manifest_path).resolve())
        self.data_dir = None if data_dir is None else str(Path(data_dir).resolve())
        self.train_start, self.train_end = train_start, train_end
        self.validation_start, self.validation_end = validation_start, validation_end
        self.parameter_names = tuple(parameter_names)
        _specs(self.parameter_names)
        self.runner_factory, self.seed = runner_factory, seed

    def run(self, *, samples: int = 8, max_concurrent_trials: int = 2,
            validation_finalists: int = 2,
            storage_path: str | Path = "diagnostic_output/inhouse_ray_optuna") -> InHouseCalibrationResult:
        if samples < 1 or validation_finalists < 1:
            raise ValueError("samples and validation_finalists must be positive")
        if not 1 <= max_concurrent_trials <= 2:
            raise ValueError("max_concurrent_trials must be between 1 and 2")
        root = Path(storage_path).resolve()
        root.mkdir(parents=True, exist_ok=True)
        trainable = tune.with_resources(tune.with_parameters(
            evaluate_inhouse_trial, manifest_path=self.manifest_path, data_dir=self.data_dir,
            train_start=self.train_start, train_end=self.train_end,
            parameter_names=self.parameter_names, runner_factory=self.runner_factory,
        ), {"cpu": 1})
        results = tune.Tuner(
            trainable, param_space=inhouse_search_space(self.parameter_names),
            tune_config=tune.TuneConfig(
                metric="score", mode="max", num_samples=samples,
                max_concurrent_trials=max_concurrent_trials,
                search_alg=OptunaSearch(metric="score", mode="max", seed=self.seed),
            ),
            run_config=RunConfig(name="inhouse_sealed_ray_optuna_train", storage_path=str(root), verbose=1),
        ).fit()
        training = [{"params": _normalise(result.config, self.parameter_names),
                     "metrics": dict(result.metrics)} for result in results]
        finalists = sorted((trial for trial in training if trial["metrics"].get("eligible") is True),
                           key=lambda trial: float(trial["metrics"]["score"]), reverse=True)[:validation_finalists]
        validations = []
        for finalist in finalists:
            runner = self.runner_factory(
                manifest_path=self.manifest_path, nse_data_dir_override=self.data_dir,
                calibration_overrides=finalist["params"],
                month_start=self.validation_start, month_end=self.validation_end,
            )
            report = asdict(runner.run())
            completed = len(runner.completed_trades)
            eligible = _eligible(report, completed)
            validations.append({"params": finalist["params"], "report": report,
                                "eligible": eligible,
                                "score": float(report["realized_pnl"]) if eligible else INELIGIBLE_SCORE})
        promotable = [trial for trial in validations if trial["eligible"] and trial["score"] > 0.0]
        winner = max(promotable, key=lambda trial: trial["score"]) if promotable else None
        return InHouseCalibrationResult(training, validations,
                                        None if winner is None else winner["params"],
                                        None if winner is None else winner["report"], str(root))
