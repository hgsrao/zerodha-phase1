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
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import StartupNotCertifiedError
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

# max_positions_per_symbol is registry range 1..3, and passes its own
# PositionManagerBox unit test (called directly with symbol_positions_count
# >= max_per_symbol correctly zeroes sizing). But both real orchestrators
# forbid a second concurrent position in the same symbol before this
# parameter is ever consulted: Revision2Orchestrator doesn't pass
# symbol_positions_count at all (defaults to 0), and
# Revision2PortfolioOrchestrator's `if symbol in self.open_trades: continue`
# guard runs earlier in the same loop, so `symbol_positions_count=1 if
# symbol in self.open_trades else 0` is always evaluated with that
# condition already False. Verified with a causal sweep over the full
# 1..3 range on both orchestrators: byte-identical trade counts and net_pnl
# at every value (tests/test_revision2_causal_sensitivity.py). Enabling
# real multi-position-per-symbol trading (pyramiding) would need open_trades
# to hold a list per symbol plus reworked exit/exposure accounting — a
# product decision on strategy behavior, not a search-space bookkeeping
# fix, so it's excluded here rather than silently built in.
DEAD_PARAMS_UNTIL_MULTI_LOT_SUPPORT = {"max_positions_per_symbol"}


def trading_search_space(registry: CanonicalParameterRegistry) -> SearchSpace:
    space = SearchSpace.from_registry(registry)
    excluded = CALIBRATION_CONTROL_PARAMS | DEAD_PARAMS_UNTIL_MULTI_LOT_SUPPORT
    keep = [n for n in space.names if n not in excluded]
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


def _trade_pnl(trade: Dict[str, Any]) -> float:
    """Net-of-this-trade's-own-cost P&L when the report has it
    (Revision2PortfolioOrchestrator trades carry `net_pnl`), falling back
    to gross `pnl` otherwise (e.g. a single-symbol Revision2Orchestrator
    trade, which doesn't allocate per-trade costs). Every profit-factor /
    drawdown / expectancy calculation below goes through this so a
    candidate can't clear the gates on gross trade P&L while being
    unprofitable net of costs."""
    return trade.get("net_pnl", trade["pnl"])


def _reconstruct_drawdown_from_trades(report: Dict[str, Any]) -> float:
    """Fallback max-drawdown reconstruction from completed-trade P&L only —
    used when a report has no real mtm_equity_curve (e.g. a single-symbol
    Revision2Orchestrator report). This deliberately UNDER-counts risk: it
    can't see unrealized intratrade drawdown, simultaneous open-position
    mark-to-market losses, or equity movement between completed trades.
    Prefer report["mtm_max_drawdown_fraction"] (from
    Revision2PortfolioOrchestrator) whenever it's present."""
    trades = report.get("trades", [])
    starting = report.get("ending_equity", 0.0) - report.get("net_pnl", 0.0)
    cum = starting
    peak = starting
    max_dd = 0.0
    for t in trades:
        cum += _trade_pnl(t)
        peak = max(peak, cum)
        if peak > 0:
            max_dd = max(max_dd, (peak - cum) / peak)
    return max_dd


def compute_sharpe_ratio(mtm_equity_curve: List[Any], periods_per_year: int = 252) -> float:
    """A real, conventional Sharpe ratio: daily-resampled returns from the
    actual marked-to-market equity curve (last value per calendar date),
    annualized by sqrt(252) trading days. Returns 0.0 if there's too little
    data to say anything (never -inf/NaN, so it can be safely compared)."""
    if not mtm_equity_curve:
        return 0.0
    daily: Dict[str, float] = {}
    for ts, equity in mtm_equity_curve:
        if not ts:
            continue
        daily[str(ts)[:10]] = equity  # curve is chronological -> last write per date wins
    values = list(daily.values())
    if len(values) < 3:
        return 0.0
    values_arr = np.array(values, dtype=float)
    returns = np.diff(values_arr) / np.maximum(values_arr[:-1], 1e-9)
    std = returns.std(ddof=1)
    if std == 0 or not math.isfinite(std):
        return 0.0
    return float(np.mean(returns) / std * math.sqrt(periods_per_year))


class AcceptanceGates:
    """Hard gates a candidate clears before it is even ranked — closes the
    old ProductionOptimizer defect where final_cash (which contains
    starting capital) dominated the score regardless of strategy quality.
    A candidate that fails any of these gets score = -inf and can never
    win, no matter how the softer score formula below would have rated it.

    The constructor defaults are smoke-test values — deliberately loose so
    a mechanically-correct but not-yet-tuned candidate can still clear them
    and prove the pipeline works (Stage F.1-F.3). Use production_defaults()
    for selecting or freezing an actual candidate off the full calibration.
    """

    def __init__(
        self,
        min_trades: int = 10,
        min_profit_factor: float = 1.05,
        max_drawdown_fraction: float = 0.20,
        min_symbols_traded: int = 1,
        max_single_symbol_pnl_share: float = 0.80,
        require_positive_net_pnl: bool = False,
        require_positive_net_expectancy: bool = False,
        min_sharpe: Optional[float] = None,
        max_safety_violations: int = 0,
        min_regime_coverage: Optional[float] = None,
        max_validation_degradation_fraction: Optional[float] = None,
    ):
        self.min_trades = min_trades
        self.min_profit_factor = min_profit_factor
        self.max_drawdown_fraction = max_drawdown_fraction
        self.min_symbols_traded = min_symbols_traded
        self.max_single_symbol_pnl_share = max_single_symbol_pnl_share
        self.require_positive_net_pnl = require_positive_net_pnl
        self.require_positive_net_expectancy = require_positive_net_expectancy
        self.min_sharpe = min_sharpe
        self.max_safety_violations = max_safety_violations
        # Both None by default: checked only when the caller actually
        # supplies the corresponding data (regime_coverage in the report,
        # or a validation_report to evaluate()) — the underlying regime-
        # classification and train/validation-sealing infrastructure isn't
        # built yet (Stage E), so these gates exist and work today but have
        # nothing to check against until that infrastructure supplies it.
        self.min_regime_coverage = min_regime_coverage
        self.max_validation_degradation_fraction = max_validation_degradation_fraction

    @staticmethod
    def smoke_test_defaults() -> "AcceptanceGates":
        """The loose values above, named explicitly so a caller can ask for
        them on purpose rather than relying on the constructor defaults
        silently being the permissive ones."""
        return AcceptanceGates()

    @staticmethod
    def production_defaults() -> "AcceptanceGates":
        """The recommended acceptance bar for selecting or freezing a
        candidate off the full three-year, 48-symbol calibration —
        materially stricter than smoke_test_defaults(). Do not run the long
        calibration, or accept a winner, against the smoke-test values."""
        return AcceptanceGates(
            min_trades=300,
            min_profit_factor=1.20,
            max_drawdown_fraction=0.15,
            min_symbols_traded=27,
            max_single_symbol_pnl_share=0.20,
            require_positive_net_pnl=True,
            require_positive_net_expectancy=True,
            min_sharpe=0.75,
            max_safety_violations=0,
        )

    def evaluate(self, report: Dict[str, Any], validation_report: Optional[Dict[str, Any]] = None) -> AcceptanceGateResult:
        reasons: List[str] = []
        trades = report.get("trades", [])

        if len(trades) < self.min_trades:
            reasons.append(f"only {len(trades)} trades, need >= {self.min_trades}")

        symbols_traded = len({t["symbol"] for t in trades if "symbol" in t}) or (1 if trades else 0)
        if symbols_traded < self.min_symbols_traded:
            reasons.append(f"only {symbols_traded} symbol(s) traded, need >= {self.min_symbols_traded}")

        gains = sum(_trade_pnl(t) for t in trades if _trade_pnl(t) > 0)
        losses = -sum(_trade_pnl(t) for t in trades if _trade_pnl(t) < 0)
        profit_factor = gains / losses if losses > 0 else (math.inf if gains > 0 else 0.0)
        if profit_factor < self.min_profit_factor:
            reasons.append(f"profit factor {profit_factor:.2f} below {self.min_profit_factor}")

        # Prefer the real marked-to-market drawdown (includes unrealized
        # intratrade loss and simultaneous open-position exposure); fall
        # back to the trade-completion reconstruction only if the report
        # doesn't have one (e.g. a non-portfolio orchestrator report).
        max_dd = report.get("mtm_max_drawdown_fraction")
        if max_dd is None:
            max_dd = _reconstruct_drawdown_from_trades(report)
        if max_dd > self.max_drawdown_fraction:
            reasons.append(f"max drawdown {max_dd:.2%} exceeds {self.max_drawdown_fraction:.0%}")

        if symbols_traded > 1 and trades:
            per_symbol_gain: Dict[str, float] = {}
            for t in trades:
                sym = t.get("symbol", "?")
                per_symbol_gain[sym] = per_symbol_gain.get(sym, 0.0) + max(0.0, _trade_pnl(t))
            total_gain = sum(per_symbol_gain.values()) or 1.0
            share = max(per_symbol_gain.values()) / total_gain
            if share > self.max_single_symbol_pnl_share:
                reasons.append(f"{share:.0%} of gains concentrated in one symbol, exceeds {self.max_single_symbol_pnl_share:.0%}")

        net_pnl = report.get("net_pnl", 0.0)
        if self.require_positive_net_pnl and net_pnl <= 0:
            reasons.append(f"net P&L {net_pnl:.2f} is not positive")

        net_expectancy = (net_pnl / len(trades)) if trades else 0.0
        if self.require_positive_net_expectancy and net_expectancy <= 0:
            reasons.append(f"net expectancy {net_expectancy:.2f}/trade is not positive")

        if self.min_sharpe is not None:
            mtm_curve = report.get("mtm_equity_curve")
            if not mtm_curve:
                reasons.append("Sharpe ratio required but no mtm_equity_curve in report")
            else:
                sharpe = compute_sharpe_ratio(mtm_curve)
                if sharpe < self.min_sharpe:
                    reasons.append(f"Sharpe {sharpe:.2f} below {self.min_sharpe}")

        safety_violations = report.get("safety_violations", 0)
        if safety_violations > self.max_safety_violations:
            reasons.append(f"{safety_violations} safety violation(s) exceeds max {self.max_safety_violations}")

        if self.min_regime_coverage is not None:
            coverage = report.get("regime_coverage")
            if coverage is None:
                reasons.append("regime coverage required but not supplied (regime classification not yet wired — Stage E)")
            elif coverage < self.min_regime_coverage:
                reasons.append(f"regime coverage {coverage:.0%} below {self.min_regime_coverage:.0%}")

        if self.max_validation_degradation_fraction is not None:
            if validation_report is None:
                reasons.append("train->validation degradation check required but no validation_report supplied (train/validation sealing not yet wired — Stage E)")
            else:
                train_score = score_candidate(report)
                val_score = score_candidate(validation_report)
                if math.isfinite(train_score) and train_score > 0:
                    degradation = (train_score - val_score) / abs(train_score)
                    if degradation > self.max_validation_degradation_fraction:
                        reasons.append(f"train->validation degradation {degradation:.0%} exceeds {self.max_validation_degradation_fraction:.0%}")

        return AcceptanceGateResult(passed=(len(reasons) == 0), reasons=reasons)

    def guidance_score(self, report: Dict[str, Any], raw_score: float) -> float:
        """A finite score the SEARCH algorithms optimize toward, distinct
        from the hard pass/fail that decides who can actually win.

        Without this, a rejected candidate scores -inf, TPE's history only
        ever grows from finite scores, and when every phase-1 candidate is
        rejected (which is exactly what happened on the real 48-symbol,
        10-candidate run), TPE never gets a single observation to learn
        from and phase 2/3 never even start — CalibrationSupervisor.run()
        used to bail out right there. This gives every candidate, rejected
        or not, a finite score that improves as it gets closer to clearing
        each gate, so the optimizer has a gradient toward feasibility
        instead of being blind across the entire infeasible region."""
        trades = report.get("trades", [])
        penalty = 0.0

        trade_count = len(trades)
        if trade_count < self.min_trades:
            penalty += (self.min_trades - trade_count) / max(self.min_trades, 1)

        gains = sum(_trade_pnl(t) for t in trades if _trade_pnl(t) > 0)
        losses = -sum(_trade_pnl(t) for t in trades if _trade_pnl(t) < 0)
        profit_factor = gains / losses if losses > 0 else (10.0 if gains > 0 else 0.0)
        if profit_factor < self.min_profit_factor:
            penalty += (self.min_profit_factor - profit_factor)

        max_dd = report.get("mtm_max_drawdown_fraction")
        if max_dd is None:
            max_dd = _reconstruct_drawdown_from_trades(report)
        if max_dd > self.max_drawdown_fraction:
            penalty += (max_dd - self.max_drawdown_fraction) * 5.0

        symbols_traded = len({t["symbol"] for t in trades if "symbol" in t}) or (1 if trades else 0)
        if symbols_traded < self.min_symbols_traded:
            penalty += (self.min_symbols_traded - symbols_traded) / max(self.min_symbols_traded, 1)

        if self.require_positive_net_pnl and report.get("net_pnl", 0.0) <= 0:
            penalty += 1.0

        base = raw_score if math.isfinite(raw_score) else -10.0
        return float(base - penalty * 5.0)


def score_candidate(report: Dict[str, Any]) -> float:
    """Net, cost-adjusted incremental performance — never raw ending
    balance. Combines a real, annualized Sharpe ratio from the marked-to-
    market equity curve (falling back to an explicitly-labeled trade-level
    Sharpe-LIKE ratio when no mtm_equity_curve is available), a bounded
    profit-factor bonus, and a max-drawdown penalty."""
    trades = report.get("trades", [])
    if len(trades) < 2:
        return float("-inf")

    pnls = np.array([_trade_pnl(t) for t in trades], dtype=float)
    mtm_curve = report.get("mtm_equity_curve")
    if mtm_curve and len(mtm_curve) >= 3:
        sharpe = compute_sharpe_ratio(mtm_curve)
        drawdown = report.get("mtm_max_drawdown_fraction", 0.0)
    else:
        std = pnls.std(ddof=1) or 1.0
        sharpe = report.get("net_pnl", 0.0) / max(std, 1e-6)  # trade-level Sharpe-LIKE, not annualized
        drawdown = _reconstruct_drawdown_from_trades(report)

    gains = pnls[pnls > 0].sum()
    losses = -pnls[pnls < 0].sum()
    profit_factor = gains / losses if losses > 0 else (2.0 if gains > 0 else 0.0)

    return float(sharpe + 0.25 * min(profit_factor, 5.0) - 2.0 * drawdown)


@dataclass
class CandidateRecord:
    phase: str
    params: Dict[str, Any]
    score: float  # -inf unless the candidate cleared every hard gate; this is what "winning" means
    accepted: bool
    reject_reasons: List[str]
    metrics: Dict[str, Any]
    elapsed_seconds: float
    guidance_score: float = float("-inf")  # always finite; what the search algorithms actually optimize


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
        self._consecutive_internal_errors = 0
        # Built once, lazily, on first use — symbol_bars never changes
        # between candidates in a calibration run, only the parameters do,
        # so the (potentially multi-million-event, real-scale) chronological
        # clock only needs building and sorting a single time per run().
        self._clock = None

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
        destination = Path(self.run_config.checkpoint_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # A watchdog can read this file while calibration is writing it.
        # Write and fsync a sibling temporary file, then atomically replace
        # the checkpoint so readers never observe truncated JSON.
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, default=str)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

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

            if self._clock is None:
                self._clock = Revision2PortfolioOrchestrator.build_clock(self.symbol_bars, self.warmup)

            t0 = time.monotonic()
            try:
                orch = Revision2PortfolioOrchestrator(
                    self.symbols, self.registry, calibration_overrides=params,
                    starting_equity=self.starting_equity, sector_map=self.sector_map,
                )
                report = orch.run(self.symbol_bars, warmup=self.warmup, precomputed_clock=self._clock)
            except (ValueError, KeyError, IndexError, StartupNotCertifiedError, FileNotFoundError) as exc:
                # Expected candidate-level failures: a bad calibration
                # payload, missing/malformed bar data, a startup
                # certificate that didn't pass. One bad candidate must
                # never take down the whole calibration run.
                self._consecutive_internal_errors = 0
                elapsed = time.monotonic() - t0
                record = CandidateRecord(phase=phase, params=params, score=float("-inf"), accepted=False,
                                          reject_reasons=[f"candidate raised: {exc}"], metrics={}, elapsed_seconds=elapsed,
                                          guidance_score=-50.0)
                self._candidates.append(record)
                self._checkpoint()
                return -50.0, {"error": str(exc)}
            except Exception:
                # NOT an expected candidate failure — AttributeError,
                # TypeError, an internal AssertionError, etc. all mean the
                # engine itself is broken, not that this candidate is bad.
                # Letting hundreds of candidates silently "fail" against a
                # broken engine for hours is worse than stopping now: after
                # a few in a row, this run must not continue pretending to
                # calibrate.
                self._consecutive_internal_errors += 1
                elapsed = time.monotonic() - t0
                record = CandidateRecord(phase=phase, params=params, score=float("-inf"), accepted=False,
                                          reject_reasons=["internal error (see traceback) — not counted as a bad candidate"],
                                          metrics={}, elapsed_seconds=elapsed, guidance_score=-50.0)
                self._candidates.append(record)
                self._checkpoint()
                if self._consecutive_internal_errors >= 3:
                    raise
                return -50.0, {"internal_error": True}

            self._consecutive_internal_errors = 0
            elapsed = time.monotonic() - t0
            gate_result = self.gates.evaluate(report)
            raw_score = score_candidate(report)
            final_score = raw_score if gate_result.passed else float("-inf")
            guidance = self.gates.guidance_score(report, raw_score)
            trade_count = report["completed_trades"]

            metrics = {
                "net_pnl": report["net_pnl"], "gross_pnl": report["gross_pnl"],
                "completed_trades": trade_count, "raw_score": raw_score,
                "net_expectancy": (report["net_pnl"] / trade_count) if trade_count else 0.0,
                "sharpe": compute_sharpe_ratio(report.get("mtm_equity_curve") or []),
                "max_drawdown_fraction": report.get("mtm_max_drawdown_fraction", _reconstruct_drawdown_from_trades(report)),
                "safety_violations": report.get("safety_violations", 0),
                "symbols_traded": len({t["symbol"] for t in report["trades"] if "symbol" in t}),
            }
            if self.run_config.deep_dive:
                metrics["trades"] = report["trades"]
                metrics["funnel"] = {k: v for k, v in report.items() if k.endswith(("_rejections", "_approvals", "_evaluated", "_passed"))}

            self._candidates.append(CandidateRecord(
                phase=phase, params=params, score=final_score, accepted=gate_result.passed,
                reject_reasons=gate_result.reasons, metrics=metrics, elapsed_seconds=elapsed,
                guidance_score=guidance,
            ))
            self._checkpoint()
            # The search algorithms (RandomSearch/TPE/CMA-ES) optimize
            # `guidance`, which is always finite — final_score (used for
            # winner eligibility in _finalize()) stays -inf for anything
            # that didn't clear the hard gates, exactly as before.
            return guidance, metrics

        return run

    def run(self) -> CalibrationResult:
        self._start_time = time.monotonic()
        cfg = self.run_config

        random_search = RandomSearch(self.space, seed=cfg.seed)
        phase1_random = random_search.run(self._objective("phase1_random"), cfg.phase1_trials // 2)

        tpe = TPESampler(self.space, seed=cfg.seed + 1)
        phase1_tpe = tpe.run(self._objective("phase1_tpe"), cfg.phase1_trials - cfg.phase1_trials // 2, seed_trials=phase1_random)

        phase1_all = [c for c in self._candidates if c.phase.startswith("phase1")]
        # Seed phase 2 from the best candidate BY GUIDANCE SCORE, which is
        # always finite — never require an already-*accepted* candidate to
        # exist yet. Requiring that here was the bug that made the real
        # 48-symbol, 10-candidate run silently skip CMA-ES and fine-tuning
        # entirely: every phase-1 candidate was rejected, so there was
        # nothing with a finite `score` to seed from, and the run returned
        # immediately as if calibration were "done."
        if not phase1_all or self._budget_exhausted():
            return self._finalize()

        phase1_best_params = max(phase1_all, key=lambda c: c.guidance_score).params

        cmaes = CMAES(self.space, seed=cfg.seed + 2)
        cmaes.run(self._objective("phase2_cmaes"), n_generations=cfg.phase2_generations, seed_mean=phase1_best_params)

        if self._budget_exhausted():
            return self._finalize()

        if not self._candidates:
            return self._finalize()
        best_so_far = max(self._candidates, key=lambda c: c.guidance_score).params

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
