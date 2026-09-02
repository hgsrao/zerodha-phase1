"""The single authoritative calibration entry point for Revision 2.

Supersedes three fake or misrouted calibration paths, none of which this
module calls and none of which should be used for calibration going
forward:
  - production_optimizer.py — real random search, but scores candidates
    through ProductionTradingEngine, a separate, simplified, long-only
    momentum strategy that shares no code with Revision2Orchestrator.
    Calibrating through it would not calibrate the system this repo just
    spent a full pass repairing.
  - oos_calibration_engine.py — scores candidates from the parameter
    VALUES themselves (score_candidate) and reports "performance" computed
    from raw buy-and-hold close-to-close returns — never a simulated trade.
  - META_LEARNING_LOOP_ORCHESTRATOR_20260829.py — its "Bayesian" phase 2 is
    current-best-plus-Gaussian-noise: no surrogate model, no TPE density,
    no acquisition function. Its phase 3 is the same local-perturbation
    idea CalibrationSupervisor's phase 3 formalizes for real, but only
    tunes 6 legacy parameters and is not connected to Revision 2 at all.

Every candidate here is evaluated through Revision2PortfolioOrchestrator —
the real 10-box pipeline, the real 18-gate EntryDecisionEngine, the real
PaperBrokerAdapter, across a real shared, chronological, multi-symbol
portfolio. There is no scoring shortcut anywhere in this module.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.optimizer import CMAES, RandomSearch, SearchSpace, TPESampler, Trial, local_fine_tune
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator

# Optimizer-run controls, not trading behavior — excluded from the trading
# search space even though learning_rate_exploration_factor is numerically
# calibratable in the canonical registry. Its only current runtime effect
# is a diagnostic exploration_bias value in UnifiedExecutionBox, never a
# trading decision, so calibrating it as if it changed trade outcomes would
# be spending search budget on a parameter the ledger can't see.
# phase1_exploration_intensity / phase2_optimization_intensity are already
# fixed (non-calibratable) in the registry, so they don't need excluding —
# they're listed for documentation completeness only.
CALIBRATION_CONTROL_PARAMS = {
    "learning_rate_exploration_factor", "phase1_exploration_intensity", "phase2_optimization_intensity",
}


def trading_search_space(registry: CanonicalParameterRegistry) -> SearchSpace:
    space = SearchSpace.from_registry(registry)
    keep = [n for n in space.names if n not in CALIBRATION_CONTROL_PARAMS]
    return SearchSpace(
        names=keep,
        minimum={n: space.minimum[n] for n in keep},
        maximum={n: space.maximum[n] for n in keep},
        is_int={n: space.is_int[n] for n in keep},
    )


@dataclass
class CalibrationRunConfig:
    """The supervisor's own operational controls — deliberately separate
    from the 45-parameter trading search space (see CALIBRATION_CONTROL_
    PARAMS above for why phase1/phase2 intensity and the learning rate
    don't drive this instead: they're registry entries, but this is the
    dataclass that should actually own that role going forward)."""

    phase1_trials: int = 10
    phase2_generations: int = 4
    phase3_iterations: int = 15
    wall_clock_budget_seconds: Optional[float] = None
    checkpoint_path: Optional[str] = None
    deep_dive: bool = False
    seed: int = 0

    @staticmethod
    def from_registry_defaults(registry: CanonicalParameterRegistry, **overrides) -> "CalibrationRunConfig":
        base = {"phase1_trials": max(6, int(registry.get("phase1_exploration_intensity").default) // 5)}
        base.update(overrides)
        return CalibrationRunConfig(**base)


@dataclass
class AcceptanceGateResult:
    passed: bool
    reasons: List[str]


class AcceptanceGates:
    """Hard gates a candidate clears before it is even ranked — closes the
    old ProductionOptimizer defect where final_cash (which contains
    starting capital) dominated the score regardless of strategy quality.
    A candidate that fails any of these gets score = -inf and can never
    win, no matter how the softer score formula below would have rated it.
    """

    def __init__(
        self,
        min_trades: int = 10,
        min_profit_factor: float = 1.05,
        max_drawdown_fraction: float = 0.20,
        min_symbols_traded: int = 1,
        max_single_symbol_pnl_share: float = 0.80,
    ):
        self.min_trades = min_trades
        self.min_profit_factor = min_profit_factor
        self.max_drawdown_fraction = max_drawdown_fraction
        self.min_symbols_traded = min_symbols_traded
        self.max_single_symbol_pnl_share = max_single_symbol_pnl_share

    def evaluate(self, report: Dict[str, Any]) -> AcceptanceGateResult:
        reasons: List[str] = []
        trades = report.get("trades", [])

        if len(trades) < self.min_trades:
            reasons.append(f"only {len(trades)} trades, need >= {self.min_trades}")

        symbols_traded = len({t["symbol"] for t in trades if "symbol" in t}) or (1 if trades else 0)
        if symbols_traded < self.min_symbols_traded:
            reasons.append(f"only {symbols_traded} symbol(s) traded, need >= {self.min_symbols_traded}")

        gains = sum(t["pnl"] for t in trades if t["pnl"] > 0)
        losses = -sum(t["pnl"] for t in trades if t["pnl"] < 0)
        profit_factor = gains / losses if losses > 0 else (math.inf if gains > 0 else 0.0)
        if profit_factor < self.min_profit_factor:
            reasons.append(f"profit factor {profit_factor:.2f} below {self.min_profit_factor}")

        starting = report.get("ending_equity", 0.0) - report.get("net_pnl", 0.0)
        cum = starting
        peak = starting
        max_dd = 0.0
        for t in trades:
            cum += t["pnl"]
            peak = max(peak, cum)
            if peak > 0:
                max_dd = max(max_dd, (peak - cum) / peak)
        if max_dd > self.max_drawdown_fraction:
            reasons.append(f"max drawdown {max_dd:.2%} exceeds {self.max_drawdown_fraction:.0%}")

        if symbols_traded > 1 and trades:
            per_symbol_gain: Dict[str, float] = {}
            for t in trades:
                sym = t.get("symbol", "?")
                per_symbol_gain[sym] = per_symbol_gain.get(sym, 0.0) + max(0.0, t["pnl"])
            total_gain = sum(per_symbol_gain.values()) or 1.0
            share = max(per_symbol_gain.values()) / total_gain
            if share > self.max_single_symbol_pnl_share:
                reasons.append(f"{share:.0%} of gains concentrated in one symbol, exceeds {self.max_single_symbol_pnl_share:.0%}")

        return AcceptanceGateResult(passed=(len(reasons) == 0), reasons=reasons)


def score_candidate(report: Dict[str, Any]) -> float:
    """Net, cost-adjusted incremental performance — never raw ending
    balance. A trade-level Sharpe-like ratio (net P&L over the standard
    deviation of individual trade P&L) plus a bounded profit-factor bonus."""
    trades = report.get("trades", [])
    if len(trades) < 2:
        return float("-inf")
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    std = pnls.std(ddof=1) or 1.0
    sharpe_like = report["net_pnl"] / max(std, 1e-6)
    gains = pnls[pnls > 0].sum()
    losses = -pnls[pnls < 0].sum()
    profit_factor = gains / losses if losses > 0 else (2.0 if gains > 0 else 0.0)
    return float(sharpe_like + 0.25 * min(profit_factor, 5.0))


@dataclass
class CandidateRecord:
    phase: str
    params: Dict[str, Any]
    score: float
    accepted: bool
    reject_reasons: List[str]
    metrics: Dict[str, Any]
    elapsed_seconds: float


@dataclass
class CalibrationResult:
    best_params: Optional[Dict[str, Any]]
    best_score: float
    best_report: Optional[Dict[str, Any]]
    candidates: List[CandidateRecord] = field(default_factory=list)
    stopped_reason: str = "completed"


class CalibrationSupervisor:
    """The one authoritative Revision 2 calibration path.

    `orchestrator_kwargs` is forwarded to Revision2PortfolioOrchestrator on
    every candidate construction (symbols, starting_equity, sector_map);
    `symbol_bars` is the (already train/validation-sliced, by the caller)
    bar data every candidate is backtested against.
    """

    def __init__(
        self,
        registry: CanonicalParameterRegistry,
        symbols: List[str],
        symbol_bars: Dict[str, pd.DataFrame],
        run_config: Optional[CalibrationRunConfig] = None,
        gates: Optional[AcceptanceGates] = None,
        warmup: int = 40,
        starting_equity: float = 1_000_000.0,
        sector_map: Optional[Dict[str, str]] = None,
    ):
        self.registry = registry
        self.symbols = symbols
        self.symbol_bars = symbol_bars
        self.run_config = run_config or CalibrationRunConfig.from_registry_defaults(registry)
        self.gates = gates or AcceptanceGates()
        self.warmup = warmup
        self.starting_equity = starting_equity
        self.sector_map = sector_map
        self.space = trading_search_space(registry)

        self._start_time: Optional[float] = None
        self._candidates: List[CandidateRecord] = []
        self._stopped_reason = "completed"

    def _budget_exhausted(self) -> bool:
        if self.run_config.wall_clock_budget_seconds is None or self._start_time is None:
            return False
        return (time.monotonic() - self._start_time) >= self.run_config.wall_clock_budget_seconds

    def _checkpoint(self) -> None:
        if not self.run_config.checkpoint_path:
            return
        payload = {
            "run_config": asdict(self.run_config),
            "candidates": [
                {**asdict(c), "params": c.params} for c in self._candidates
            ],
        }
        Path(self.run_config.checkpoint_path).write_text(json.dumps(payload, indent=2, default=str))

    @staticmethod
    def load_checkpoint(path: str) -> List[CandidateRecord]:
        data = json.loads(Path(path).read_text())
        return [CandidateRecord(**c) for c in data["candidates"]]

    def _objective(self, phase: str) -> Callable[[Dict[str, Any]], Any]:
        def run(params: Dict[str, Any]):
            if self._budget_exhausted():
                self._stopped_reason = "wall_clock_budget_exhausted"
                metrics = {"skipped": "wall_clock_budget_exhausted"}
                self._candidates.append(CandidateRecord(
                    phase=phase, params=params, score=float("-inf"), accepted=False,
                    reject_reasons=["wall clock budget exhausted before this candidate ran"],
                    metrics=metrics, elapsed_seconds=0.0,
                ))
                return float("-inf"), metrics

            t0 = time.monotonic()
            try:
                orch = Revision2PortfolioOrchestrator(
                    self.symbols, self.registry, calibration_overrides=params,
                    starting_equity=self.starting_equity, sector_map=self.sector_map,
                )
                report = orch.run(self.symbol_bars, warmup=self.warmup)
            except Exception as exc:  # a candidate must never crash the whole run
                elapsed = time.monotonic() - t0
                record = CandidateRecord(phase=phase, params=params, score=float("-inf"), accepted=False,
                                          reject_reasons=[f"candidate raised: {exc}"], metrics={}, elapsed_seconds=elapsed)
                self._candidates.append(record)
                self._checkpoint()
                return float("-inf"), {"error": str(exc)}

            elapsed = time.monotonic() - t0
            gate_result = self.gates.evaluate(report)
            raw_score = score_candidate(report)
            final_score = raw_score if gate_result.passed else float("-inf")

            metrics = {
                "net_pnl": report["net_pnl"], "gross_pnl": report["gross_pnl"],
                "completed_trades": report["completed_trades"], "raw_score": raw_score,
            }
            if self.run_config.deep_dive:
                metrics["trades"] = report["trades"]
                metrics["funnel"] = {k: v for k, v in report.items() if k.endswith(("_rejections", "_approvals", "_evaluated", "_passed"))}

            self._candidates.append(CandidateRecord(
                phase=phase, params=params, score=final_score, accepted=gate_result.passed,
                reject_reasons=gate_result.reasons, metrics=metrics, elapsed_seconds=elapsed,
            ))
            self._checkpoint()
            return final_score, metrics

        return run

    def run(self) -> CalibrationResult:
        self._start_time = time.monotonic()
        cfg = self.run_config

        random_search = RandomSearch(self.space, seed=cfg.seed)
        phase1_random = random_search.run(self._objective("phase1_random"), cfg.phase1_trials // 2)

        tpe = TPESampler(self.space, seed=cfg.seed + 1)
        phase1_tpe = tpe.run(self._objective("phase1_tpe"), cfg.phase1_trials - cfg.phase1_trials // 2, seed_trials=phase1_random)

        phase1_all = [c for c in self._candidates if c.phase.startswith("phase1")]
        accepted_or_any = [c for c in phase1_all if math.isfinite(c.score)]
        if not accepted_or_any or self._budget_exhausted():
            return self._finalize()

        phase1_best_params = max(accepted_or_any, key=lambda c: c.score).params

        cmaes = CMAES(self.space, seed=cfg.seed + 2)
        cmaes.run(self._objective("phase2_cmaes"), n_generations=cfg.phase2_generations, seed_mean=phase1_best_params)

        if self._budget_exhausted():
            return self._finalize()

        finite_so_far = [c for c in self._candidates if math.isfinite(c.score)]
        if not finite_so_far:
            return self._finalize()
        best_so_far = max(finite_so_far, key=lambda c: c.score).params

        local_fine_tune(self._objective("phase3_finetune"), self.space, best_so_far, iterations=cfg.phase3_iterations, seed=cfg.seed + 3)

        return self._finalize()

    def _finalize(self) -> CalibrationResult:
        finite = [c for c in self._candidates if math.isfinite(c.score)]
        if not finite:
            return CalibrationResult(best_params=None, best_score=float("-inf"), best_report=None,
                                      candidates=self._candidates, stopped_reason=self._stopped_reason)
        winner = max(finite, key=lambda c: c.score)
        return CalibrationResult(
            best_params=winner.params, best_score=winner.score, best_report=winner.metrics,
            candidates=self._candidates, stopped_reason=self._stopped_reason,
        )
