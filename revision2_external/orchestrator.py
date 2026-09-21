"""External-library calibration engine: the same shared-portfolio pipeline
shape as revision2/portfolio_orchestrator.py, with 8 of the 10 boxes
replaced by real external libraries:

  1. StartupCapabilityLock -> Pydantic            (revision2_external.startup_validation)
  2. DataIngestion         -> unchanged (in-house allow/deny filter; ArcticDB
                               is the loader upstream of this, not this box)
  3. L2DataCertifier       -> Pandera              (revision2_external.data_certification_pandera)
  4. PredictiveAnalytics   -> TA-Lib               (revision2_external.indicators_talib)
  5. IntelligentDiscrim.   -> Gaussian HMM          (revision2_external.regime_id_box;
                               hmmlearn itself is blocked by numba/Python 3.14,
                               see regime_hmm.py's own docstring)
  6. ModelPredictiveControl -> simple-pid           (revision2_external.pid_controller)
  7. SafetyGates (18-gate)  -> unchanged, in-house   (gates_framework.py)
  8. PositionManager       -> PyPortfolioOpt        (revision2_external.position_sizing_pyportfolioopt)
  9. P01D                  -> unchanged, in-house    (revision2.boxes.P01DBox; stdlib hmac
                               already covers signing where it's used)
  10. UnifiedExecution      -> kiteconnect+tenacity  (revision2_external.broker_adapter_kite;
                               NOT used during calibration -- offline/historical only)

This is deliberately a SEPARATE, additive engine -- it does not modify or
replace revision2/portfolio_orchestrator.py. Both exist so a real candidate
can eventually be scored on both and compared, the same cross-checking
principle as the Backtrader parity work.
"""

from __future__ import annotations
from revision2_external.dynamic_parameter_controller import DynamicParameterController, MarketEnvironmentState

import itertools
import math
from dataclasses import replace, asdict
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from gates_framework import EntryDecisionEngine, EntrySignal, SafetyGateConfig, SystemState
from revision2.boxes import P01DBox, SafetyGatesTargetBox
from revision2.contracts import EffectiveConfig, MarketSnapshot, SafetyContract, StartupCertificate, StartupNotCertifiedError
from revision2.portfolio_orchestrator import SECTOR_MAP, _ClockEvent
from revision2_external.composite_study_signal import CompositeStudySignal
from revision2_external.closed_loop_control import ClosedLoopSupervisor, HMMRiskHysteresis, regime_risk_derate
from revision2_external.continuous_exit_controller import ContinuousExitController, ExitControllerState
from revision2_external.data_certification_pandera import certify_bars, certify_session_completeness
from revision2_external.dynamic_target_setpoint import FrozenTargetSetpointProvider
from revision2_external.entry_expectancy_evidence import CausalEntryExpectancyLedger
from revision2_external.entry_candidate_observations import EntryCandidateObservationLedger
from revision2_external.final_execution_controller import FinalExecutionController
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.indicators_talib import TALibPredictiveAnalyticsBox
from revision2_external.pid_controller import SimplePIDModelPredictiveControlBox
from revision2_external.position_sizing_pyportfolioopt import PyPortfolioOptPositionManagerBox, compute_portfolio_weights
from revision2_external.regime_id_box import HMMIntelligentDiscriminationBox
from revision2_external.startup_validation import validate_runtime_parameters, validate_safety_contract
from runtime.operating_mode import ExecutionGate
from revision2_external.paper_execution import CostedPaperBrokerAdapter, ReplayIntentLedger
from revision2.transaction_costs import leg_cost, paper_fill_price
from revision5.supervisory_bridge import Revision5SupervisoryBridge

SNAPSHOT_LOOKBACK_BARS = 300
# F17: fixed PyPortfolioOpt maintenance policy, deliberately NOT calibratable.
# Refit every 500 unique portfolio clock ticks using the trailing 2,000
# one-minute bars.  The former rebalance_frequency_minutes registry entry
# was removed because it never controlled this real refit path.
PORTFOLIO_WEIGHT_REFIT_EVERY_BARS = 500
PORTFOLIO_WEIGHT_LOOKBACK_MINUTE_BARS = 2_000  # ~130 completed 15-minute samples

# F11: fixed machine-bay protection policy, deliberately NOT calibratable.
# A net losing exit blocks new entries in that symbol until bar index
# exit_bar + BAY_LOSS_COOLDOWN_BARS.  Session rollover clears the latch.
BAY_LOSS_COOLDOWN_BARS = 15


class ExternalEngineStartupNotCertifiedError(StartupNotCertifiedError):
    pass


class Revision2ExternalEngineOrchestrator:
    """Shared-portfolio orchestrator using the external-library box set."""

    # These retain compatibility in the shared registry but have no external
    # replay actuator. They must not spend calibration budget.
    INACTIVE_CALIBRATION_PARAMETERS = frozenset({
        "exit_confidence_threshold", "pid_derivative_smoothing",
        "limit_order_offset_percent", "max_retry_attempts", "retry_delay_seconds",
        "max_positions_per_symbol", "max_symbol_concentration", "data_validation_mode",
    })

    def __init__(
        self,
        symbols: List[str],
        registry: Optional[CanonicalParameterRegistry] = None,
        calibration_overrides: Optional[Dict[str, Any]] = None,
        starting_equity: float = 1_000_000.0,
        sector_map: Optional[Dict[str, str]] = None,
        grid_context_provider: Optional[SealedGridContextProvider] = None,
        closed_loop_mode: str = "shadow",
        telemetry_mode: str = "full",
        pid_mode: str = "enabled",
        dynamic_target_setpoint_provider: Optional[FrozenTargetSetpointProvider] = None,
        dynamic_target_setpoint_mode: str = "shadow",
        risk_profile: str = "development",
        session_schedule: Optional[Dict[str, Tuple[str, str]]] = None,
        supervisory_bridge: Optional[Revision5SupervisoryBridge] = None,
    ) -> None:
        if closed_loop_mode not in {"shadow", "active_paper"}:
            raise ValueError("closed_loop_mode must be 'shadow' or 'active_paper'")
        if telemetry_mode not in {"full", "compact"}:
            raise ValueError("telemetry_mode must be 'full' or 'compact'")
        if pid_mode not in {"enabled", "disabled"}:
            raise ValueError("pid_mode must be 'enabled' or 'disabled'")
        if dynamic_target_setpoint_mode not in {"shadow", "paper_apply"}:
            raise ValueError("dynamic_target_setpoint_mode must be 'shadow' or 'paper_apply'")
        self.symbols = list(symbols)
        # Machine Bay & Symbol Closed-Loop Lockout Registers
        self.symbol_cooldown_until_bar = {}
        self.symbol_consecutive_losses = {}
        self.symbol_tripped = {}
        self.closed_loop_mode = closed_loop_mode
        self.telemetry_mode = telemetry_mode
        self.pid_mode = pid_mode
        self.dynamic_target_setpoint_provider = dynamic_target_setpoint_provider
        self.dynamic_target_setpoint_mode = dynamic_target_setpoint_mode
        if risk_profile not in {"development", "trial"}:
            raise ValueError("risk_profile must be development or trial")
        self.risk_profile = risk_profile
        self.session_schedule = dict(session_schedule or {})
        self.registry = registry or CanonicalParameterRegistry()
        if risk_profile == "trial":
            self.registry = self.registry.trial_profile()
        overrides = calibration_overrides or {}
        errors = self.registry.validate_calibration_payload(overrides)
        if errors:
            raise ValueError(f"invalid calibration overrides: {errors}")

        values = {name: spec.default for name, spec in self.registry.params.items()}
        values.update(overrides)

        # Box 1: Pydantic-based validation, in place of StartupGate's
        # hand-written type/range checks.
        param_errors = validate_runtime_parameters(self.registry, values)
        safety_values = {name: spec.default for name, spec in self.registry.safety_params.items()}
        safety_errors = validate_safety_contract(self.registry, safety_values)
        if param_errors or safety_errors:
            raise ExternalEngineStartupNotCertifiedError(
                "Pydantic startup certification failed: " + "; ".join(param_errors + safety_errors)
            )
        self.startup_certificate_reasons: List[str] = []

        self.config = EffectiveConfig.build(values, registry_hash=self.registry.FROZEN_IDENTITY_SHA256)
        self.safety_contract = SafetyContract.from_registry(self.registry)
        # BB01/BB02 remain upstream.  The bridge provides their single
        # tracked hand-off to R5; it does not call a plant or alter strategy.
        self.supervisory_bridge = (
            supervisory_bridge
            if supervisory_bridge is not None
            else Revision5SupervisoryBridge(self.registry)
        )
        if self.config.require("order_type") != "MARKET":
            raise ValueError("External replay supports MARKET orders only")
        self.sector_map = dict(sector_map) if sector_map is not None else dict(SECTOR_MAP)
        # The provider is shadow-only in this release.  It records a causal,
        # timestamp-aligned Nifty/VIX assessment but is not an entry gate and
        # cannot alter quantity, stops, targets, or safety policy.
        self.grid_context_provider = grid_context_provider
        self.grid_shadow_observations: List[Dict[str, Any]] = []
        # Audit ledger for controller comparators and, in ``active_paper``
        # mode, their bounded paper-only actuations.  It is never a safety
        # override and is not connected to a live broker.
        self.controller_telemetry: List[Dict[str, Any]] = []
        self._controller_event_counts: Counter[str] = Counter()
        self._position_sizing_events: List[Dict[str, Any]] = []
        # Entry research telemetry. It is a strictly causal ledger: a
        # pre-entry observation is paired only when that filled trade later
        # completes. No current execution decision reads this ledger.
        self.entry_expectancy_ledger = CausalEntryExpectancyLedger()
        self.entry_candidate_observations = EntryCandidateObservationLedger()
        self._controller_sequence = 0
        self._trade_sequence = 0
        self.bb03_bb04_supervisory_by_symbol: Dict[str, Any] = {}
        self.bb09_bb10_supervisory_by_symbol: Dict[str, Any] = {}

        self.pa = TALibPredictiveAnalyticsBox()
        # Box 4b, the Chart-Studies Confirmation Layer -- gains/clamp/
        # grading-horizon/hit-rate-window are real, disclosed, fixed
        # defaults, not yet exposed as registry-calibratable parameters
        # (same "disclosed first cut, not swept" discipline this
        # project's own prior chart-studies work used for its thresholds).
        self.chart_studies = CompositeStudySignal()
        self.final_execution_controller = FinalExecutionController()
        self.id_box = HMMIntelligentDiscriminationBox()
        self.mpc = SimplePIDModelPredictiveControlBox(pid_enabled=pid_mode == "enabled")
        self.safety_gates_target = SafetyGatesTargetBox()
        self.position_manager = PyPortfolioOptPositionManagerBox()
        self.p01d = P01DBox()
        self.broker = CostedPaperBrokerAdapter(
            account_id="PAPER-EXTERNAL-ENGINE",
            slippage_fraction=float(self.config.require("slippage_cost_multiplier")) * 0.0005,
        )
        self.entry_decision_engine = EntryDecisionEngine(config=self._build_safety_gate_config())
        self._intent_ledger = ReplayIntentLedger(self.safety_contract.values["order_dedup_window_seconds"])
        self._execution_halted = False
        self._exit_orders_submitted = 0
        self._post_fill_checks = []

        self.starting_equity = starting_equity
        self.consumed_parameters: set = set()
        self.completed_trades: List[Dict[str, Any]] = []
        self.open_trades: Dict[str, Dict[str, Any]] = {}
        self._equity_curve: List[float] = [starting_equity]
        self._last_close: Dict[str, float] = {}
        self._mtm_equity_curve: List[Tuple[str, float]] = [("", starting_equity)]
        self._mtm_peak = starting_equity
        self._mtm_max_drawdown_fraction = 0.0
        self._active_trading_date = None
        self._day_start_equity = starting_equity
        self._portfolio_weights: Dict[str, float] = {s: 1.0 / len(symbols) for s in symbols}

        # Box 6's real feedback path -- see BOX6_CONTROL_LOOP_DIAGRAM_20260906.html
        # for why this exists: without it, a position's exit is governed
        # only by a frozen entry-time stop/target plus a bare elapsed-time
        # check, with maximum_hold_bars never enforced and no live PA
        # signal feeding back in after entry. Reuses the exit PID's own
        # registry gains (pid_kp_exit/pid_ki_exit/pid_kd_exit/
        # pid_integral_max_clamp/pid_integral_window_bars). The droop
        # magnitude is its OWN independently-calibratable parameter,
        # trailing_stop_atr_mult -- NOT stop_loss_atr_mult (the one-shot
        # entry stop's multiplier). Real data forced this split: a real
        # INFY 6-month run with the droop borrowing stop_loss_atr_mult
        # (0.75) produced a stop narrower than ordinary 1-minute noise
        # (verified: median single-bar range is ~0.95x median ATR, so a
        # sub-1x-ATR continuous stop barely survives one bar, let alone a
        # multi-bar hold) -- 100% of trades exited via stop/stop_gap, zero
        # via max_hold or saturation_exit, ever. See
        # continuous_exit_controller.py's module docstring for the full
        # reasoning (including why ATR, not a guessed percentage, is the
        # real physical constant this droop should be grounded in).
        self.exit_controller = ContinuousExitController(
            kp=float(self.config.require("pid_kp_exit")), ki=float(self.config.require("pid_ki_exit")),
            kd=float(self.config.require("pid_kd_exit")), clamp=float(self.config.require("pid_integral_max_clamp")),
            atr_droop_mult=float(self.config.require("trailing_stop_atr_mult")),
            baseline_window=int(self.config.require("pid_integral_window_bars")),
            saturation_exit_bars=int(self.config.require("saturation_exit_bars")),
        )
        self.consumed_parameters.update({
            "pid_kp_exit", "pid_ki_exit", "pid_kd_exit", "pid_integral_max_clamp",
            "pid_integral_window_bars", "trailing_stop_atr_mult", "saturation_exit_bars",
        })
        self._exit_controller_states: Dict[str, ExitControllerState] = {}
        # Three-loop supervisory layer. ``shadow`` records comparators only;
        # ``active_paper`` permits bounded, one-way paper actuations only:
        # entry/portfolio loops can reduce size, and the path loop can only
        # tighten a stop. Neither mode can weaken a safety constraint.
        self.closed_loop = ClosedLoopSupervisor()
        self._hmm_risk_hysteresis: Dict[str, HMMRiskHysteresis] = {}

        self.startup_certificate = self._issue_startup_certificate()

    def _build_safety_gate_config(self) -> SafetyGateConfig:
        v = self.safety_contract.values
        return SafetyGateConfig(
            kill_switch_enabled=bool(v["kill_switch_enabled"]),
            drawdown_halt_threshold=float(v["safety_drawdown_halt_threshold"]),
            daily_loss_halt_threshold=float(v["max_daily_loss_rupees"]),
            lambda_derate_threshold=float(v["lambda_derate_threshold"]),
            lambda_derate_multiplier=float(v["lambda_derate_multiplier"]),
            min_signal_confidence=float(v["min_signal_confidence"]),
            min_risk_reward_ratio=float(v["safety_min_risk_reward_ratio"]),
            slippage_tolerance_percent=float(v["max_slippage_fraction"]),
            max_position_quantity=int(v["max_position_quantity"]),
            max_concurrent_positions=int(v["max_concurrent_positions"]),
            max_gross_exposure_fraction=float(v["max_gross_exposure_fraction"]),
            max_exposure_per_symbol_fraction=float(v["max_exposure_per_symbol_fraction"]),
            no_entry_cutoff_time=str(v["no_entry_cutoff_time"]),
            max_market_data_age_seconds=int(v["max_market_data_age_seconds"]),
            drawdown_derate_threshold=float(v["drawdown_derate_threshold"]),
            drawdown_derate_multiplier=float(v["drawdown_derate_multiplier"]),
            order_timeout_seconds=min(int(v["order_timeout_seconds_execution"]),
                                      int(self.config.require("order_timeout_seconds"))),
            max_reconciliation_qty_diff=int(v["max_reconciliation_qty_diff"]),
        )

    def _issue_startup_certificate(self) -> StartupCertificate:
        # Pydantic already certified the parameter surface above (Box 1);
        # this issues the same StartupCertificate record type so downstream
        # code (reports, hashes) has the identical shape as the in-house
        # engine, without re-running StartupGate's own (now superseded)
        # validation.
        return StartupCertificate.issue(
            {"passed": True, "operating_mode": "paper", "broker_environment": self.broker.environment, "reasons": []},
            self.config.config_hash, self.safety_contract.contract_hash,
        )

    def _record(self, trace) -> None:
        for use in trace:
            self.consumed_parameters.add(use.parameter)

    def _equity(self) -> float:
        return self.starting_equity + self.broker.realized_pnl - self.broker.booked_costs

    def _current_drawdown(self) -> float:
        peak = max(self._mtm_peak, max(self._equity_curve, default=self.starting_equity))
        current = self._mark_to_market_equity()
        return (peak - current) / peak if peak > 0 else 0.0

    def _mark_to_market_equity(self) -> float:
        unrealized = 0.0
        for symbol, trade in self.open_trades.items():
            mark = self._last_close.get(symbol, trade["entry_price"])
            unrealized += (
                (mark - trade["entry_price"]) * trade["quantity"] if trade["side"] == "BUY"
                else (trade["entry_price"] - mark) * trade["quantity"]
            )
        return self._equity() + unrealized

    def _gross_exposure_notional(self) -> float:
        return sum(t["quantity"] * self._last_close.get(s, t["entry_price"])
                   for s, t in self.open_trades.items())

    def _record_mtm(self, timestamp: object) -> None:
        """Update drawdown online; retain the full curve only for full telemetry."""
        equity = self._mark_to_market_equity()
        self._mtm_peak = max(self._mtm_peak, equity)
        if self._mtm_peak > 0.0:
            self._mtm_max_drawdown_fraction = max(
                self._mtm_max_drawdown_fraction,
                (self._mtm_peak - equity) / self._mtm_peak,
            )
        if self.telemetry_mode == "full":
            self._mtm_equity_curve.append((str(timestamp), equity))

    _leg_cost = staticmethod(leg_cost)
    _paper_fill_price = staticmethod(paper_fill_price)

    def _system_state(self) -> SystemState:
        equity = self._equity()
        unrealized = self._mark_to_market_equity() - equity
        return SystemState(
            portfolio_value=equity, current_dd_percent=self._current_drawdown(),
            current_lambda=self._gross_exposure_notional() / max(equity, 1.0),
            daily_realized_loss=max(0.0, self._day_start_equity - equity),
            daily_unrealized_loss=max(0.0, -unrealized),
            open_positions_count=len(self.open_trades),
            open_positions=[type("P", (), {"position_notional":
                t["quantity"] * self._last_close.get(s, t["entry_price"])})()
                for s, t in self.open_trades.items()],
            market_data_age_seconds=0, broker_connected=True, broker_offline_seconds=0,
            kill_switch_active=not bool(self.safety_contract.values["kill_switch_enabled"]),
            circuit_breaker_triggered=False,
        )

    def _execute_exit(self, symbol: str, timestamp, trade: Dict[str, Any], exit_price: float, reason: str) -> None:
        close_side = "SELL" if trade["side"] == "BUY" else "BUY"
        self._exit_orders_submitted += 1
        result = self.broker.place_order(
            symbol=symbol, side=close_side, quantity=trade["quantity"], order_type="MARKET",
            market_price=exit_price, config=self.safety_contract.as_dict(), parameter_registry=self.registry,
        )
        if result["passed"]:
            state = self._exit_controller_states.get(symbol)
            pnl = (
                (result["filled_price"] - trade["entry_price"]) * trade["quantity"]
                if trade["side"] == "BUY" else (trade["entry_price"] - result["filled_price"]) * trade["quantity"]
            )
            trade_costs = self._leg_cost(trade["entry_price"], trade["quantity"], trade["side"]) + self._leg_cost(
                result["filled_price"], trade["quantity"], close_side
            )
            completed = {
                "symbol": symbol, "side": trade["side"], "entry_price": trade["entry_price"],
                "exit_price": result["filled_price"], "quantity": trade["quantity"],
                "entry_timestamp": trade["entry_timestamp"], "exit_timestamp": str(timestamp),
                "reason": reason, "pnl": pnl, "costs": trade_costs, "net_pnl": pnl - trade_costs,
                "trade_id": trade.get("trade_id"), "candidate_id": trade.get("candidate_id"),
                "bars_held": int(state.bars_held) if state is not None else None,
                "entry_atr": trade.get("entry_atr"),
                "planned_entry_price": trade.get("planned_entry_price"),
                "planned_stop_price": trade.get("planned_stop_price"),
                "planned_target_price": trade.get("planned_target_price"),
            }
            if state is not None:
                risk = abs(float(trade["entry_price"]) - float(state.initial_stop_price))
                if risk > 0:
                    favorable = state.mfe_price - trade["entry_price"] if trade["side"] == "BUY" else trade["entry_price"] - state.mfe_price
                    adverse = state.mae_price - trade["entry_price"] if trade["side"] == "BUY" else trade["entry_price"] - state.mae_price
                    completed.update({"mfe_price": state.mfe_price, "mae_price": state.mae_price,
                                      "mfe_r": favorable / risk, "mae_r": adverse / risk,
                                      "terminal_bar_excursion": "intrabar_order_unknown"})
                    terminal = trade.get("_terminal_bar")
                    inclusive_mfe, inclusive_mae = state.mfe_price, state.mae_price
                    if terminal is not None:
                        if trade["side"] == "BUY":
                            inclusive_mfe = max(inclusive_mfe, float(terminal["high"]))
                            inclusive_mae = min(inclusive_mae, float(terminal["low"]))
                        else:
                            inclusive_mfe = min(inclusive_mfe, float(terminal["low"]))
                            inclusive_mae = max(inclusive_mae, float(terminal["high"]))
                    sign = 1 if trade["side"] == "BUY" else -1
                    completed.update({
                        "mfe_pre_exit_bar_r": favorable / risk,
                        "mae_pre_exit_bar_r": adverse / risk,
                        "mfe_terminal_inclusive_r": sign * (inclusive_mfe - trade["entry_price"]) / risk,
                        "mae_terminal_inclusive_r": sign * (inclusive_mae - trade["entry_price"]) / risk,
                        "terminal_inclusive_is_ohlc_bound": True,
                    })
            shadow = None
            if state is not None and state.shadow_exit_price is not None:
                shadow_close_side = "SELL" if trade["side"] == "BUY" else "BUY"
                # Compare like with like: the shadow stop identifies the
                # counterfactual *market* trigger from causal OHLC, then it
                # receives the same deterministic paper-broker adverse fill
                # adjustment as the live exit.  Comparing its raw stop with
                # the live broker fill would fabricate a P&L difference even
                # when both trigger on the same bar.
                shadow_market_exit_price = float(state.shadow_exit_price)
                shadow_filled_price = self._paper_fill_price(
                    shadow_market_exit_price, shadow_close_side, self.broker.slippage_fraction
                )
                shadow_pnl = (
                    (shadow_filled_price - trade["entry_price"]) * trade["quantity"]
                    if trade["side"] == "BUY" else (trade["entry_price"] - shadow_filled_price) * trade["quantity"]
                )
                shadow_costs = self._leg_cost(trade["entry_price"], trade["quantity"], trade["side"]) + self._leg_cost(
                    shadow_filled_price, trade["quantity"], shadow_close_side
                )
                shadow = {
                    "shadow_exit_timestamp": state.shadow_exit_timestamp,
                    "shadow_market_exit_price": shadow_market_exit_price,
                    "shadow_exit_price": shadow_filled_price,
                    "shadow_exit_reason": state.shadow_exit_reason,
                    "shadow_exit_bars_held": state.shadow_exit_bars_held,
                    "shadow_pnl": shadow_pnl,
                    "shadow_costs": shadow_costs,
                    "shadow_net_pnl": shadow_pnl - shadow_costs,
                }
                completed["shadow_r_trajectory"] = shadow
            self.completed_trades.append(completed)
            entry_evidence = self.entry_expectancy_ledger.record_outcome({
                "candidate_id": completed["candidate_id"], "trade_id": completed["trade_id"],
                "exit_timestamp": completed["exit_timestamp"], "exit_reason": reason,
                "bars_held": completed["bars_held"], "pnl": completed["pnl"],
                "costs": completed["costs"], "net_pnl": completed["net_pnl"],
                "mfe_r": completed.get("mfe_r"), "mae_r": completed.get("mae_r"),
                "terminal_bar_excursion": completed.get("terminal_bar_excursion"),
            })
            if entry_evidence is not None:
                self._record_controller_event("ENTRY_EXPECTANCY_OUTCOME", timestamp, symbol, entry_evidence)
            closed_loop_profile = self.closed_loop.record_outcome(
                completed, regime=trade.get("closed_loop", {}).get("regime", "unknown"),
            )

            # --- CLOSED-LOOP INTER-BOX FEEDBACK (Box 10 -> Box 6/8) ---
            _pnl = float(completed.get("net_pnl", 0.0))
            if _pnl < 0:
                _c_losses = self.symbol_consecutive_losses.get(symbol, 0) + 1
                self.symbol_consecutive_losses[symbol] = _c_losses
                self.symbol_cooldown_until_bar[symbol] = (
                    getattr(self, "_current_bar_idx", 0) + BAY_LOSS_COOLDOWN_BARS
                )
                if _c_losses >= 2:
                    self.symbol_tripped[symbol] = True
            elif _pnl > 0:
                # Any profitable exit breaks consecutive loss streak
                self.symbol_consecutive_losses[symbol] = 0
            self._record_controller_event("CONTROLLER_OUTCOME", timestamp, symbol, {
                "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                "exit_reason": reason, "net_pnl": completed["net_pnl"], "pnl": pnl, "costs": trade_costs,
                "entry_atr": completed["entry_atr"], "planned_entry_price": completed["planned_entry_price"],
                "planned_stop_price": completed["planned_stop_price"], "planned_target_price": completed["planned_target_price"],
                "mfe_r": completed.get("mfe_r"), "mae_r": completed.get("mae_r"),
                "terminal_bar_excursion": completed.get("terminal_bar_excursion"),
                "shadow_r_trajectory": shadow,
            })
            self._record_controller_event("OUTCOME_LEDGER_UPDATE", timestamp, symbol, {
                "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                "net_pnl": completed["net_pnl"], "entry_quality_profile": closed_loop_profile,
            })
            self._equity_curve.append(self._equity())
            del self.open_trades[symbol]
            self._exit_controller_states.pop(symbol, None)
            self._record_mtm(timestamp)

    def _record_controller_event(self, event_type: str, timestamp: object, symbol: str, payload: Dict[str, Any]) -> None:
        """Record controller state and any bounded paper-only actuation."""
        event = {"event_type": event_type, "timestamp": str(timestamp), "symbol": symbol, **payload}
        self._controller_event_counts[event_type] += 1
        if event_type == "POSITION_SIZING":
            # Compact mode retains only the small, decision-level sizing
            # stream. Per-bar controller observations remain aggregate-only.
            self._position_sizing_events.append(event)
        if self.telemetry_mode == "full":
            self.controller_telemetry.append(event)

    def _maybe_exit(
        self, symbol: str, timestamp, bar, signal, held_bars: int, session_last_bar: bool,
        chart_studies_confidence: float, chart_studies_audit: Optional[Dict[str, Any]] = None,
    ) -> None:
        trade = self.open_trades.get(symbol)
        if trade is None:
            return
        # Completed fill bar is elapsed bar 0; the clock owns this counter.
        state = self._exit_controller_states.get(symbol)
        if state is not None:
            state.bars_held = int(held_bars)
        trade["_terminal_bar"] = dict(bar)
        studies_direction = (chart_studies_audit or {}).get("direction")
        if studies_direction is not None and studies_direction != (1 if trade["side"] == "BUY" else -1):
            chart_studies_confidence = 0.0
        if pd.Timestamp(timestamp).strftime("%H:%M") >= self.entry_decision_engine.config.force_close_time:
            self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "force_close_time")
            return
        halt_dd = min(float(self.config.require("drawdown_halt_threshold")),
                      float(self.safety_contract.values["safety_drawdown_halt_threshold"]))
        if self._current_drawdown() >= halt_dd:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "forced_close_drawdown_halt")
            return

        # The real feedback path: re-run the exit controller on THIS bar's
        # freshly-evaluated PA signal (exit_confidence -- the field PA
        # computes specifically for exit timing, distinct from entry
        # confidence) AND the chart-studies composite confidence (Box 4b,
        # computed above, never blended into `signal`) as two fully
        # separate PID tracks, before checking anything else. current_stop
        # is a ratcheted stop that only ever tightens.  Critically, an update
        # calculated from THIS bar's close is armed for the NEXT bar.  The
        # OHLC order inside the current bar is unknown, so testing a freshly
        # calculated stop against this bar's high/low would be look-ahead.
        state = self._exit_controller_states.get(symbol)
        # In shadow mode the controller is strictly observational.  In
        # active-paper mode a stop ratchet computed from a completed prior
        # bar becomes the protective stop for this bar.
        current_stop = (
            float(state.current_stop_price)
            if state is not None and self.closed_loop_mode == "active_paper"
            else float(trade["stop_price"])
        )
        pending_exit = trade.get("controller_exit_pending")
        if pending_exit is not None:
            # The exit was armed using only the preceding completed bar.
            # Preserve a hard-stop or target gap's precedence at this open;
            # otherwise the controller has direct paper-only exit authority.
            if trade["side"] == "BUY" and float(bar["open"]) <= current_stop:
                self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "stop_gap")
            elif trade["side"] == "SELL" and float(bar["open"]) >= current_stop:
                self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "stop_gap")
            elif trade["side"] == "BUY" and float(bar["open"]) >= float(trade["target_price"]):
                self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "target_gap")
            elif trade["side"] == "SELL" and float(bar["open"]) <= float(trade["target_price"]):
                self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "target_gap")
            else:
                self._record_controller_event("CONTROLLER_PATH_EXIT", timestamp, symbol, {
                    "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                    **pending_exit,
                })
                self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "controller_path_exit")
            return
        if state is not None:
            shadow_exit = self.exit_controller.check_shadow_stop(state, bar, timestamp)
            if shadow_exit is not None:
                self._record_controller_event("SHADOW_R_TRAJECTORY_EXIT", timestamp, symbol, {
                    "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"), **shadow_exit,
                })
            # Droop input: CURRENT ATR, re-measured this bar -- not the
            # frozen entry-time ATR -- matches how atr is computed
            # everywhere else in this file (signal.volatility * close).
            current_atr = float(signal.volatility) * float(bar["close"])
            state = self.exit_controller.update(
                symbol, state, float(signal.exit_confidence), chart_studies_confidence,
                float(bar["close"]), current_atr, held_bars=held_bars,
            )
            self._exit_controller_states[symbol] = state
            closed_loop_snapshot = trade.get("closed_loop")
            if closed_loop_snapshot is not None:
                path_observation = self.closed_loop.observe_trade_path(
                    closed_loop_snapshot, float(bar["close"]), state.bars_held,
                )
                self._record_controller_event("TRADE_PATH_COMPARATOR", timestamp, symbol, {
                    "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                    "bars_held": state.bars_held, **path_observation.to_dict(),
                })
                if self.closed_loop_mode == "active_paper" and path_observation.behind_path:
                    # Path control is one-way and never crosses the current
                    # price (guaranteed by the supervisor). It can only make
                    # the already-ratcheted stop stricter.
                    risk = float(closed_loop_snapshot["reference_path"]["initial_risk"])
                    entry = float(trade["entry_price"])
                    proposed_stop = (
                        entry + path_observation.suggested_protection_r * risk
                        if trade["side"] == "BUY" else entry - path_observation.suggested_protection_r * risk
                    )
                    before_path_actuation = state.current_stop_price
                    if trade["side"] == "BUY":
                        state.current_stop_price = max(state.current_stop_price, proposed_stop)
                    else:
                        state.current_stop_price = min(state.current_stop_price, proposed_stop)
                    self._record_controller_event("TRADE_PATH_STOP_ACTUATION", timestamp, symbol, {
                        "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                        "stop_before": before_path_actuation, "proposed_stop": proposed_stop,
                        "stop_after": state.current_stop_price,
                    })
                    if held_bars >= trade["minimum_hold_bars"]:
                        # Do not retroactively execute on this bar: its
                        # OHLC sequence is unknown.  Arm a market exit for
                        # the next bar's open, shortening (never extending)
                        # the holding period if the path remains breached.
                        trade["controller_exit_pending"] = {
                            "armed_timestamp": str(timestamp),
                            "actual_r": path_observation.actual_r,
                            "expected_r": path_observation.expected_r,
                            "error_r": path_observation.error_r,
                            "reason": "behind_reference_path",
                        }
                        self._record_controller_event("CONTROLLER_PATH_EXIT_ARMED", timestamp, symbol, {
                            "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                            **trade["controller_exit_pending"],
                        })
            entry_atr = trade.get("entry_atr")
            atr_drift_fraction = None
            if entry_atr is not None and float(entry_atr) > 0.0:
                atr_drift_fraction = (current_atr - float(entry_atr)) / float(entry_atr)
            study_weights = dict((chart_studies_audit or {}).get("weights", {}))
            study_hit_rates = dict((chart_studies_audit or {}).get("hit_rates", {}))
            # These bounds are owned by CompositeStudySignal.  This is a
            # passive audit label only; it cannot influence the controller.
            study_weights_clamped = any(
                weight <= 0.05 + 1e-12 or weight >= 0.60 - 1e-12
                for weight in study_weights.values()
            )
            self._record_controller_event("EXIT_PROTECTION_UPDATE", timestamp, symbol, {
                "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                "baseline_window_bars": self.exit_controller.baseline_window,
                "entry_atr": entry_atr,
                "atr_drift_fraction": atr_drift_fraction,
                "study_weights": study_weights,
                "study_hit_rates": study_hit_rates,
                "study_votes": dict((chart_studies_audit or {}).get("votes", {})),
                # Raw causal study measurements and the four independent
                # study-weight PID outputs.  Audit only: neither field is an
                # additional execution input at this layer.
                "study_indicator_inputs": dict((chart_studies_audit or {}).get("indicator_inputs", {})),
                "study_weight_pid_audit": dict((chart_studies_audit or {}).get("weight_pid_audit", {})),
                "study_weights_clamped": study_weights_clamped,
                **state.last_telemetry,
            })
            final_exit = self.final_execution_controller.exit_decision(
                path=path_observation.to_dict() if closed_loop_snapshot is not None else None,
                exit_pid=state.last_telemetry, held_bars=held_bars,
                minimum_hold_bars=int(trade["minimum_hold_bars"]),
                maximum_hold_bars=int(trade["maximum_hold_bars"]), side=trade["side"],
            )
            self._record_controller_event("FINAL_EXECUTION_EXIT_DECISION", timestamp, symbol, {
                "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"), **final_exit,
            })

        exit_price, reason = None, None
        if trade["side"] == "BUY":
            if bar["open"] <= current_stop:
                exit_price, reason = float(bar["open"]), "stop_gap"
            elif bar["open"] >= trade["target_price"]:
                exit_price, reason = float(bar["open"]), "target_gap"
            elif bar["low"] <= current_stop:
                exit_price, reason = current_stop, "stop"
            elif bar["high"] >= trade["target_price"]:
                exit_price, reason = trade["target_price"], "target"
        else:
            if bar["open"] >= current_stop:
                exit_price, reason = float(bar["open"]), "stop_gap"
            elif bar["open"] <= trade["target_price"]:
                exit_price, reason = float(bar["open"]), "target_gap"
            elif bar["high"] >= current_stop:
                exit_price, reason = current_stop, "stop"
            elif bar["low"] <= trade["target_price"]:
                exit_price, reason = trade["target_price"], "target"

        if exit_price is not None:
            self._execute_exit(symbol, timestamp, trade, exit_price, reason)
            return
        if session_last_bar:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "mis_session_close")
            return
        # maximum_hold_bars is now a REAL, independent hard ceiling -- it
        # used to be stored on every trade and never once read anywhere.
        if held_bars >= trade["maximum_hold_bars"]:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "max_hold")
            return
        # The old code exited unconditionally the instant minimum_hold_bars
        # was satisfied, labeled "max_hold_or_signal" despite checking
        # neither -- see BOX6_CONTROL_LOOP_DIAGRAM_20260906.html. Replaced
        # with the controller's own real, live-signal-driven condition:
        # sustained saturation on EITHER independent track (PA or chart
        # studies) is enough on its own to trigger the exit -- OR logic,
        # not a merge -- only actionable once the minimum hold is met.
        # saturation_exit_reason() names which track actually fired
        # ("saturation_exit_pa" / "saturation_exit_studies"), so the trade
        # record's own reason field now tells the two apart instead of a
        # single opaque "saturation_exit" label.
        if state is not None and held_bars >= trade["minimum_hold_bars"]:
            saturation_reason = self.exit_controller.saturation_exit_reason(state)
            if saturation_reason is not None:
                self._execute_exit(symbol, timestamp, trade, float(bar["close"]), saturation_reason)
                return
        # Box 5's real feedback path: while a position is open, ID's
        # regime check was never called at all -- `if symbol in
        # self.open_trades: continue` skips straight past it before entry
        # evaluation, and there was no separate call anywhere in exit
        # logic either. A real regime shift to "stressed" DURING a held
        # position had no path back into the exit decision, even though
        # the exact same shift would have vetoed a fresh entry moments
        # earlier. _current_regime() is real and safe to call here -- it's
        # the same method the entry path already uses, refits periodically
        # (not every bar), and this call also fixes a second, smaller real
        # gap: the regime model's bar history previously went blind for
        # every bar a position was held (only fed while flat), which
        # biased its own trailing window. Gated by minimum_hold_bars, same
        # as saturation_exit, so a position isn't force-exited on a
        # regime read taken moments after entry.
        regime = self.id_box._current_regime(symbol, float(bar["close"]))
        if regime == "stressed" and held_bars >= trade["minimum_hold_bars"]:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "regime_stressed_exit")
            return
        if state is not None:
            self.exit_controller.observe_completed_bar_excursion(state, bar)
        if state is not None and state.shadow_exit_price is None:
            shadow_update = self.exit_controller.update_shadow_r_trajectory(state, float(bar["close"]))
            self._record_controller_event("SHADOW_R_TRAJECTORY_UPDATE", timestamp, symbol, {
                "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"), **shadow_update,
            })

    @staticmethod
    def prepare_market_data(symbol_bars):
        """Canonical frames must precede any clock construction."""
        return {symbol: certify_bars(frame)[0] for symbol, frame in symbol_bars.items()}

    @staticmethod
    def build_clock(symbol_bars: Dict[str, pd.DataFrame], warmup: int) -> List[_ClockEvent]:
        events: List[_ClockEvent] = []
        for symbol, bars in symbol_bars.items():
            ts = pd.to_datetime(bars["timestamp"])
            for bar_idx in range(warmup, len(bars) - 1):
                events.append(_ClockEvent(ts.iloc[bar_idx], symbol, bar_idx))
        events.sort(key=lambda e: (e.timestamp, e.symbol))
        return events

    @staticmethod
    def rank_simultaneous_entry_candidates(candidates):
        """Deterministic arbitration for candidates created at one timestamp.

        This does not create alpha or tune a weighted score. It uses existing
        authoritative entry evidence lexicographically:

        1. higher ID confidence,
        2. higher actual planned bracket reward:risk,
        3. symbol only as the deterministic final tie-break.

        Input order must never affect the returned ordering.
        """
        def key(candidate):
            entry = float(candidate["plan"].entry_price)
            stop = float(candidate["plan"].stop_price)
            target = float(candidate["plan"].target_price)

            risk = abs(entry - stop)
            reward = abs(target - entry)
            rr = reward / risk if risk > 0.0 else 0.0

            return (
                -float(candidate["decision"].confidence),
                -float(rr),
                str(candidate["symbol"]),
            )

        return sorted(candidates, key=key)

    def run(
        self, symbol_bars: Dict[str, pd.DataFrame], warmup: int = 60,
        precomputed_clock: Optional[List[_ClockEvent]] = None,
    ) -> Dict[str, Any]:
        # Box 3: Pandera certification, once per symbol, before the loop --
        # bars don't change during a backtest, so re-validating them every
        # iteration (the per-bar design the in-house box uses) is pure
        # waste at this scale; certifying once up front is the more
        # faithful "certify before trading on it" reading of the box's job.
        certified: Dict[str, pd.DataFrame] = {}
        certification_audit: Dict[str, Dict[str, int]] = {}
        for symbol, bars in symbol_bars.items():
            frame, audit = certify_bars(
                bars,
                validation_mode=str(
                    self.config.require("data_validation_mode")
                ),
            )
            certified[symbol] = frame
            certification_audit[symbol] = audit
        symbol_bars = certified
        if precomputed_clock is not None:
            expected_clock = self.build_clock(symbol_bars, warmup)
            if precomputed_clock != expected_clock:
                raise ValueError("precomputed clock does not match certified frames")

        for symbol, bars in symbol_bars.items():
            self.pa.calibrate(symbol, bars.iloc[:warmup], self.config)
            self.id_box.calibrate(symbol, bars.iloc[:0])
            # Replay warmup through sensor histories and eligible planner PIDs,
            # with no order construction, portfolio allocation, or submission.
            for idx in range(min(warmup, len(bars))):
                snapshot = MarketSnapshot(symbol, str(bars.iloc[idx]["timestamp"]), bars.iloc[:idx + 1])
                warm_signal, _ = self.pa.evaluate(snapshot, self.config)
                self.chart_studies.evaluate(symbol, snapshot.bars)
                if warm_signal.direction == 0:
                    self.id_box._current_regime(symbol, float(bars.iloc[idx]["close"]))
                    continue
                warm_decision, _ = self.id_box.evaluate(
                    warm_signal, self.config, latest_close=float(bars.iloc[idx]["close"]))
                if warm_decision.approved:
                    close = float(bars.iloc[idx]["close"])
                    self.mpc.build_plan(warm_signal, warm_decision, close,
                                        max(float(warm_signal.volatility) * close, 1e-6), self.config)

        funnel = {
            "pa_evaluations": 0, "directional_signals": 0, "confidence_qualified": 0,
            "pa_green": 0, "pa_amber": 0, "pa_red": 0, "pa_neutral": 0,
            "bay_trip_rejections": 0, "bay_cooldown_rejections": 0,
            "bars_processed": 0, "pa_signals": 0, "id_approvals": 0, "id_rejections": 0,
            "mpc_plans": 0, "safety_approvals": 0, "safety_rejections": 0,
            "gates_evaluated": 0, "gates_passed": 0, "gates_rejected": 0,
            "orders_submitted": 0, "exit_orders_submitted": 0, "fills": 0,
            "portfolio_cap_rejections": 0,
        }
        max_concurrent = int(self.safety_contract.values["max_concurrent_positions"])
        max_gross_fraction = float(self.safety_contract.values["max_gross_exposure_fraction"])
        # Was self.registry.get(...).default -- silently ignored calibration
        # overrides (always read the registry's frozen default, never the
        # candidate's actual value) and, since it bypassed self.config,
        # never registered as consumed either, which is why this parameter
        # was incorrectly in test_orchestrator_end_to_end.py's
        # expected_missing set under a "replaced by PyPortfolioOpt weights"
        # rationale that isn't true -- it's read and it enforces a real
        # sector cap, just never the calibrated one. Found during external
        # review, verified directly against this line before fixing.
        sector_cap_fraction = float(self.config.require("max_sector_exposure_fraction"))
        self.consumed_parameters.add("max_sector_exposure_fraction")

        # Calibration driving many candidates against the SAME symbol_bars
        # can build this once and pass it via precomputed_clock= -- mirrors
        # revision2/portfolio_orchestrator.py's identical optimization, so
        # the two engines' calibration supervisors can share one code path.
        clock = precomputed_clock if precomputed_clock is not None else self.build_clock(symbol_bars, warmup)
        entry_bar_index: Dict[str, int] = {}
        ticks_since_reweight = 0

        for timestamp, tick_events in itertools.groupby(clock, key=lambda e: e.timestamp):
            tick_events = list(tick_events)
            event_ts = pd.Timestamp(timestamp)
            if self._active_trading_date != event_ts.date():
                self._active_trading_date = event_ts.date()
                self._day_start_equity = self._equity()
                # Session-scoped machine bay lockout reset
                self.symbol_cooldown_until_bar.clear()
                self.symbol_consecutive_losses.clear()
                self.symbol_tripped.clear()

            for event in tick_events:
                self._last_close[event.symbol] = float(symbol_bars[event.symbol].iloc[event.bar_idx]["close"])
            self._record_mtm(timestamp)

            # Box 8: refit PyPortfolioOpt weights periodically from real
            # trailing prices across the universe -- not every tick (that
            # would dominate runtime for no real benefit at 1-minute
            # granularity).
            ticks_since_reweight += 1
            if ticks_since_reweight >= PORTFOLIO_WEIGHT_REFIT_EVERY_BARS:
                ticks_since_reweight = 0
                price_history = {}
                for symbol in self.symbols:
                    bars = symbol_bars[symbol]
                    idx = min(event_ts, bars["timestamp"].max())
                    window = bars[bars["timestamp"] <= idx].tail(PORTFOLIO_WEIGHT_LOOKBACK_MINUTE_BARS)
                    if len(window) >= PORTFOLIO_WEIGHT_LOOKBACK_MINUTE_BARS:
                        price_history[symbol] = window.set_index("timestamp")["close"]
                if len(price_history) >= 2:
                    self._portfolio_weights = compute_portfolio_weights(price_history)

            pending_entry_candidates = []

            for event in tick_events:
                funnel["bars_processed"] += 1
                symbol, bar_idx = event.symbol, event.bar_idx
                self._current_bar_idx = bar_idx
                bars = symbol_bars[symbol]
                next_ts = pd.Timestamp(bars.iloc[bar_idx + 1]["timestamp"])
                end_time = str(self.config.require("trading_hours_end"))
                session_last_bar = next_ts.date() != event_ts.date() or event_ts.strftime("%H:%M") >= end_time

                upstream = self.supervisory_bridge.evaluate_upstream_admission(
                    symbol=symbol,
                    runtime_parameters=self.config.as_dict(),
                    safety_parameters=self.safety_contract.as_dict(),
                )
                self.consumed_parameters.update(
                    upstream.consumed_parameters
                )
                if not upstream.admitted:
                    continue
                in_window = self._in_trading_window(str(timestamp))

                snapshot = MarketSnapshot(
                    symbol=symbol, timestamp=str(timestamp),
                    bars=bars.iloc[max(0, bar_idx - SNAPSHOT_LOOKBACK_BARS + 1):bar_idx + 1],
                )
                signal, trace = self.pa.evaluate(snapshot, self.config)
                self._record(trace)
                self.bb03_bb04_supervisory_by_symbol[symbol] = (
                    self.supervisory_bridge.snapshot_bb03_bb04(
                        certification_audit=(
                            certification_audit[symbol]
                        ),
                        analytics_signal=signal,
                    )
                )
                funnel["pa_signals"] += 1  # deprecated compatibility alias for pa_evaluations
                funnel["pa_evaluations"] += 1
                funnel["directional_signals"] += int(signal.direction != 0)
                funnel["confidence_qualified"] += int(signal.confidence >= float(self.config.require("entry_confidence_threshold")))
                funnel["pa_" + signal.quality_band] += 1

                # Box 4b, the Chart-Studies Confirmation Layer -- a SECOND,
                # fully independent confidence reading (Ichimoku/Bollinger/
                # Stochastic/session VWAP, PID-weighted -- see
                # composite_study_signal.py), deliberately kept SEPARATE
                # from PA's own confidence rather than blended into it.
                # An earlier version of this wiring blended the two
                # ("agree -> average, disagree -> floor to the lower",
                # chart_studies_confirmation.py) directly into `signal`
                # before ID/MPC ever saw it -- a real INFY 6-month backtest
                # showed this let a weak PA signal get boosted past ID's
                # threshold purely by an unrelated composite agreeing with
                # it (id_approvals 455->2,949, win rate 9.3%->5.9%, net
                # P&L -37,907.57->-81,964.77), which is structurally
                # unsound: two independent opinions averaged together can
                # manufacture confidence neither one earned alone. Per
                # explicit user direction, this signal instead goes
                # straight into the exit controller (Box 6's feedback
                # path, below) as its own separate PID track -- never
                # merged with PA's confidence, never touching entry
                # (ID/MPC) at all. See continuous_exit_controller.py's
                # module docstring for the two-track design.
                composite_result = self.chart_studies.evaluate(symbol, snapshot.bars)
                chart_studies_confidence = float(composite_result["confidence"])

                held = bar_idx - entry_bar_index.get(symbol, bar_idx)
                self._maybe_exit(
                    symbol, timestamp, bars.iloc[bar_idx], signal, held, session_last_bar,
                    chart_studies_confidence, composite_result,
                )

                if symbol in self.open_trades or not in_window or self._execution_halted:
                    continue
                if next_ts.date() != event_ts.date() or next_ts.strftime("%H:%M") >= str(self.safety_contract.values["no_entry_cutoff_time"]):
                    continue

                decision, trace = self.id_box.evaluate(signal, self.config, latest_close=float(bars.iloc[bar_idx]["close"]))
                self._record(trace)
                # HMM posterior -> portfolio-risk input is deliberately
                # shadow-only.  The posterior is causal (filtering, not a
                # future-smoothed state estimate) and the adapter can only
                # recommend a derate.  It cannot change this candidate's
                # order, size, safety state, or existing HMM ID veto until
                # its own sealed out-of-sample study earns that authority.
                regime_observation = self.id_box.latest_regime_observation(symbol)
                hysteresis = self._hmm_risk_hysteresis.setdefault(symbol, HMMRiskHysteresis())
                self._record_controller_event("HMM_REGIME_RISK_SHADOW", timestamp, symbol, {
                    "candidate_id": f"candidate-preview-{self._controller_sequence + 1}",
                    "id_approved": decision.approved, "id_reason": decision.reason,
                    "hmm_observation": regime_observation,
                    **regime_risk_derate(regime_observation),
                    "hysteresis": hysteresis.update(regime_observation),
                })
                if not decision.approved:
                    funnel["id_rejections"] += 1
                    continue
                funnel["id_approvals"] += 1

                if self.grid_context_provider is not None:
                    observation = self.grid_context_provider.observe(
                        symbol,
                        bars.iloc[:bar_idx + 1],
                        timestamp,
                        signal.direction,
                    )
                    self.grid_shadow_observations.append(observation.to_dict())

                _close_price = float(bars.iloc[bar_idx]["close"])
                env_state = MarketEnvironmentState.from_bars(symbol, bars, bar_idx, regime=decision.regime if hasattr(decision, "regime") else "trend")
                t1_params = DynamicParameterController.get_tier1_vol_parameters(env_state)
                _min_atr_floor = t1_params["min_atr_floor"]
                atr = max(float(signal.volatility * _close_price), _min_atr_floor)
                next_open = float(bars.iloc[bar_idx + 1]["open"])
                plan, pid_info, trace = self.mpc.build_plan(signal, decision, next_open, atr, self.config)
                self._record(trace)
                if plan is None:
                    continue
                # Inter-box Closed Loop Lockout Checks
                if self.symbol_tripped.get(symbol, False):
                    self._controller_sequence += 1
                    candidate_id = f"candidate-{self._controller_sequence}"
                    self.entry_candidate_observations.observe({
                        "candidate_id": candidate_id, "symbol": symbol, "side": plan.side,
                        "timestamp": str(timestamp), "planned_fill_timestamp": str(next_ts),
                        "pa_confidence": float(signal.confidence), "id_confidence": float(decision.confidence),
                    })
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "bay_ansi_flameout_tripped")
                    funnel["bay_trip_rejections"] += 1
                    continue

                if bar_idx < self.symbol_cooldown_until_bar.get(symbol, -1):
                    self._controller_sequence += 1
                    candidate_id = f"candidate-{self._controller_sequence}"
                    self.entry_candidate_observations.observe({
                        "candidate_id": candidate_id, "symbol": symbol, "side": plan.side,
                        "timestamp": str(timestamp), "planned_fill_timestamp": str(next_ts),
                        "pa_confidence": float(signal.confidence), "id_confidence": float(decision.confidence),
                    })
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "bay_cooldown_active")
                    funnel["bay_cooldown_rejections"] += 1
                    continue

                funnel["mpc_plans"] += 1
                self._controller_sequence += 1
                candidate_id = f"candidate-{self._controller_sequence}"
                self.entry_candidate_observations.observe({
                    "candidate_id": candidate_id, "symbol": symbol, "side": plan.side,
                    "timestamp": str(timestamp), "planned_fill_timestamp": str(next_ts),
                    "pa_confidence": float(signal.confidence), "id_confidence": float(decision.confidence),
                    "studies_confidence": float(chart_studies_confidence),
                    "planned_entry_price": float(plan.entry_price), "planned_stop_price": float(plan.stop_price),
                    "planned_target_price": float(plan.target_price), "planned_maximum_hold_bars": int(plan.maximum_hold_bars),
                })
                entry_quality = self.closed_loop.outcomes.profile(symbol, plan.side)
                self._record_controller_event("ENTRY_QUALITY_COMPARATOR", timestamp, symbol, {
                    "candidate_id": candidate_id, "id_confidence": float(decision.confidence),
                    **entry_quality,
                })
                self._record_controller_event("ENTRY_CONFIDENCE_THROTTLE", timestamp, symbol, {
                    "candidate_id": candidate_id,
                    # Passive input/geometry telemetry. These values were
                    # fixed by PA, ID and MPC above; recording them cannot
                    # alter admission, sizing, stops, targets or execution.
                    "pa_confidence": float(signal.confidence),
                    "id_confidence": float(decision.confidence),
                    "id_timing_quality": float(decision.timing_quality),
                    "id_risk_reward_ratio": float(decision.risk_reward_ratio),
                    "entry_atr": float(atr),
                    "planned_entry_price": float(plan.entry_price),
                    "planned_stop_price": float(plan.stop_price),
                    "planned_target_price": float(plan.target_price),
                    "planned_stop_distance": abs(float(plan.entry_price) - float(plan.stop_price)),
                    "planned_target_distance": abs(float(plan.target_price) - float(plan.entry_price)),
                    "planned_minimum_hold_bars": int(plan.minimum_hold_bars),
                    "planned_maximum_hold_bars": int(plan.maximum_hold_bars),
                    **pid_info,
                })
                if self.dynamic_target_setpoint_provider is not None:
                    proposal = self.dynamic_target_setpoint_provider.propose(
                        entry_price=float(plan.entry_price), stop_price=float(plan.stop_price),
                        target_price=float(plan.target_price), maximum_hold_bars=int(plan.maximum_hold_bars),
                    )
                    self._record_controller_event("DYNAMIC_TARGET_SETPOINT_SHADOW", timestamp, symbol, {
                        "candidate_id": candidate_id, **proposal,
                    })
                    if self.dynamic_target_setpoint_mode == "paper_apply" and proposal["available"]:
                        risk = abs(float(plan.entry_price) - float(plan.stop_price))
                        signed_target = risk * float(proposal["proposed_target_r"])
                        target_price = (
                            float(plan.entry_price) + signed_target if plan.side == "BUY"
                            else float(plan.entry_price) - signed_target
                        )
                        plan = replace(
                            plan, target_price=target_price,
                            maximum_hold_bars=int(proposal["proposed_maximum_hold_bars"]),
                        )
                        self._record_controller_event("DYNAMIC_TARGET_SETPOINT_PAPER_APPLIED", timestamp, symbol, {
                            "candidate_id": candidate_id, "target_price": target_price,
                            "maximum_hold_bars": plan.maximum_hold_bars, **proposal,
                        })

                # S4: candidate construction is complete.  No portfolio
                # capacity, sizing, gate submission, or broker mutation is
                # allowed until every symbol at this timestamp has reached
                # this same boundary.
                pending_entry_candidates.append({
                    "symbol": symbol,
                    "bar_idx": bar_idx,
                    "bars": bars,
                    "next_ts": next_ts,
                    "signal": signal,
                    "decision": decision,
                    "plan": plan,
                    "pid_info": pid_info,
                    "candidate_id": candidate_id,
                    "entry_quality": entry_quality,
                    "composite_result": composite_result,
                    "chart_studies_confidence": chart_studies_confidence,
                    "atr": atr,
                })
                continue

            # S4_PHASE_B_SIMULTANEOUS_ARBITRATION
            # Every eligible candidate for this exact timestamp now exists.
            # Rank the immutable candidate batch first; only then allow any
            # candidate to consume portfolio capacity or create a fill.
            for pending in self.rank_simultaneous_entry_candidates(
                pending_entry_candidates
            ):
                symbol = pending["symbol"]
                bar_idx = pending["bar_idx"]
                bars = pending["bars"]
                next_ts = pending["next_ts"]
                signal = pending["signal"]
                decision = pending["decision"]
                plan = pending["plan"]
                pid_info = pending["pid_info"]
                candidate_id = pending["candidate_id"]
                entry_quality = pending["entry_quality"]
                composite_result = pending["composite_result"]
                chart_studies_confidence = pending["chart_studies_confidence"]
                atr = pending["atr"]

                # Some lifecycle/audit code uses this authoritative bar index.
                self._current_bar_idx = bar_idx

                approved, _, size_mult, trace = self.safety_gates_target.evaluate_pre_sizing(
                    self._equity_curve, self.config, current_lambda=self._system_state().current_lambda,
                )
                self._record(trace)
                if not approved:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "pre_sizing_safety")
                    funnel["safety_rejections"] += 1
                    continue
                symbol_dynamics = self.closed_loop.dynamics_profiler.estimate(bars.iloc[:bar_idx + 1])
                final_entry = self.final_execution_controller.entry_decision(
                    side=plan.side, pa_confidence=float(signal.confidence), id_approved=bool(decision.approved),
                    studies_direction=int(composite_result["direction"]), studies_confidence=chart_studies_confidence,
                    entry_price=float(plan.entry_price), stop_price=float(plan.stop_price), target_price=float(plan.target_price),
                    maximum_hold_bars=int(plan.maximum_hold_bars), entry_quality=entry_quality,
                    dynamics=symbol_dynamics,
                )
                self._record_controller_event("FINAL_EXECUTION_ENTRY_DECISION", timestamp, symbol, {
                    "candidate_id": candidate_id, **final_entry,
                })
                # The unified decision is an actual paper-only entry veto in
                # active mode. Shadow mode records the identical decision
                # but leaves the existing baseline execution untouched.
                if self.closed_loop_mode == "active_paper" and final_entry["action"] != "ADMIT":
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "final_execution_entry_veto")
                    self._record_controller_event("FINAL_EXECUTION_ENTRY_VETO", timestamp, symbol, {
                        "candidate_id": candidate_id, **final_entry,
                    })
                    continue
                size_mult *= pid_info["entry_timing_multiplier"]
                if (
                    self.closed_loop_mode == "active_paper"
                    and entry_quality["symbol_regime_samples"] >= 20
                    and float(decision.confidence) < float(self.config.require("entry_confidence_threshold"))
                    + float(entry_quality["suggested_confidence_offset"])
                ):
                    self.entry_candidate_observations.dispose(candidate_id, "DEFERRED", "entry_quality_hold")
                    self._record_controller_event("ENTRY_QUALITY_HOLD", timestamp, symbol, {
                        "candidate_id": candidate_id,
                        "effective_confidence_floor": float(self.config.require("entry_confidence_threshold"))
                        + float(entry_quality["suggested_confidence_offset"]),
                        "id_confidence": float(decision.confidence),
                    })
                    continue

                if len(self.open_trades) >= max_concurrent:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "max_concurrent_positions")
                    funnel["portfolio_cap_rejections"] += 1
                    continue
                equity_now = self._equity()
                portfolio_observation = self.closed_loop.observe_portfolio_risk(
                    self._gross_exposure_notional(), equity_now, max_gross_fraction,
                )
                self._record_controller_event("PORTFOLIO_RISK_COMPARATOR", timestamp, symbol, {
                    "candidate_id": candidate_id, **portfolio_observation,
                })
                if self.closed_loop_mode == "active_paper":
                    size_mult *= float(entry_quality["suggested_entry_derate"])
                    size_mult *= float(portfolio_observation["suggested_new_risk_derate"])
                    self._record_controller_event("DYNAMIC_SIZE_ACTUATION", timestamp, symbol, {
                        "candidate_id": candidate_id,
                        "entry_quality_derate": entry_quality["suggested_entry_derate"],
                        "portfolio_risk_derate": portfolio_observation["suggested_new_risk_derate"],
                        "resulting_size_multiplier": size_mult,
                    })
                sector = self.sector_map.get(symbol, "Unclassified")

                quantity, trace = self.position_manager.size(
                    plan, equity_now, size_mult, self.config, symbol=symbol,
                    portfolio_weights=self._portfolio_weights,
                    max_exposure_per_symbol_fraction=float(self.safety_contract.values["max_exposure_per_symbol_fraction"]),
                    open_positions_count=len(self.open_trades),
                    symbol_positions_count=1 if symbol in self.open_trades else 0,
                )
                self._record(trace)
                self._record_controller_event("POSITION_SIZING", timestamp, symbol, {
                    "candidate_id": candidate_id,
                    **self.position_manager.last_sizing_telemetry,
                })
                if quantity <= 0:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "position_sizing_zero")
                    continue

                real_notional = plan.entry_price * quantity
                if self._gross_exposure_notional() + real_notional > equity_now * max_gross_fraction:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "gross_exposure_cap")
                    funnel["portfolio_cap_rejections"] += 1
                    continue
                sector_notional = sum(
                    t["quantity"] * self._last_close.get(s, t["entry_price"]) for s, t in self.open_trades.items()
                    if self.sector_map.get(s, "Unclassified") == sector
                )
                if sector_notional + real_notional > equity_now * sector_cap_fraction:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "sector_exposure_cap")
                    funnel["portfolio_cap_rejections"] += 1
                    continue

                post_ok, _, trace = self.safety_gates_target.evaluate_post_sizing(
                    self._equity_curve, plan, quantity, self.config,
                    daily_loss=self._system_state().daily_realized_loss + self._system_state().daily_unrealized_loss,
                )
                self._record(trace)
                if not post_ok:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "post_sizing_safety")
                    funnel["safety_rejections"] += 1
                    continue
                funnel["safety_approvals"] += 1

                order, trace = self.p01d.create_order(symbol, plan, quantity, self.config)
                self._record(trace)
                if order is None:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "order_construction")
                    continue

                state = self._system_state()
                entry_signal = EntrySignal(
                    symbol=symbol, entry_price=plan.entry_price, stop_loss_price=plan.stop_price,
                    profit_target_price=plan.target_price, confidence=decision.confidence,
                    suggested_quantity=quantity, position_notional=real_notional,
                    risk_reward_ratio=abs(plan.target_price - plan.entry_price) / max(abs(plan.entry_price - plan.stop_price), 1e-12),
                )
                try:
                    current_time = datetime.fromisoformat(str(next_ts))
                except Exception:
                    current_time = datetime.now()
                gate_result = self.entry_decision_engine.evaluate_pre_submit(
                    state, signal=entry_signal, current_time=current_time, proposed_quantity=quantity,
                    target_price=plan.entry_price, fill_price=plan.entry_price, expected_qty=quantity,
                    actual_qty=quantity, symbol=symbol,
                    seen_recent=self._intent_ledger.seen_recent(symbol, order.side, current_time),
                    proposed_notional=real_notional,
                )
                # GRID GATE INTEGRATION: DISABLED PENDING REAL DATA INJECTION
                # Status: Fail-closed logic is implemented, but orchestrator never initializes:
                # - self.grid_sync (no synchronizer attached)
                # - self.nifty_prices (no market index data)
                # - self.vix_prices (no volatility index data)
                # Result: Would reject ALL entries (zero trades), which is fail-safe but not useful.
                # Enable only when real, timestamp-aligned NIFTY/VIX data is injected.

                if False:  # DISABLED
                    if not hasattr(self, 'grid_sync') or self.grid_sync is None:
                        funnel["grid_rejected"] = funnel.get("grid_rejected", 0) + 1
                        continue

                    try:
                        grid_ok, grid_state = self.grid_sync.check_grid_synchronization(
                            self.nifty_prices[max(0, bar_idx - 500):bar_idx + 1] if bar_idx < len(self.nifty_prices) else self.nifty_prices,
                            float(self.vix_prices[bar_idx]) if bar_idx < len(self.vix_prices) else 20.0,
                            trade_direction=signal.direction if hasattr(signal, 'direction') else 1
                        )
                        if not grid_ok:
                            funnel["grid_rejected"] = funnel.get("grid_rejected", 0) + 1
                            continue
                    except Exception as e:
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.critical(f"Grid sync error (FAIL-CLOSED): {e}")
                        funnel["grid_rejected"] = funnel.get("grid_rejected", 0) + 1
                        continue
                funnel["gates_evaluated"] += 1
                if not gate_result["passed"]:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "entry_decision_gate",
                        details={**gate_result, "decisions": [asdict(d) for d in gate_result["decisions"]]})
                    funnel["gates_rejected"] += 1
                    continue
                funnel["gates_passed"] += 1
                quantity = max(0, int(gate_result["adjusted_quantity"]))
                if quantity <= 0:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "entry_decision_quantity_zero")
                    continue

                gate2 = ExecutionGate().validate_pre_submit(
                    self.safety_contract.as_dict(),
                    {"symbol": symbol, "side": order.side, "quantity": quantity, "order_type": order.order_type},
                    parameter_registry=self.registry,
                )
                if not gate2["passed"]:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "execution_gate")
                    funnel["safety_rejections"] += 1
                    continue

                # Derating/clamping can only round DOWN to a valid symbol lot.
                lot_size = max(1, int(self.config.require("lot_size_by_symbol").get(symbol, 1)))
                quantity = (quantity // lot_size) * lot_size
                if quantity <= 0:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "lot_rounding_zero")
                    continue
                self._intent_ledger.record(symbol, order.side, current_time)
                fill = self.broker.place_order(
                    symbol=symbol, side=order.side, quantity=quantity, order_type=order.order_type,
                    market_price=float(pid_info["execution_market_price"]),
                    config=self.safety_contract.as_dict(), parameter_registry=self.registry,
                )
                funnel["orders_submitted"] += 1
                # BB09/BB10 supervisory hand-off: a read-only record of what
                # P01D constructed and what UnifiedExecution did with it.
                # Never gates or revises the outcome above -- see
                # Revision5SupervisoryBridge.snapshot_bb09_bb10.
                self.bb09_bb10_supervisory_by_symbol[symbol] = (
                    self.supervisory_bridge.snapshot_bb09_bb10(
                        proposed_order=replace(order, quantity=quantity),
                        fill_result=fill,
                    )
                )
                if fill["passed"]:
                    actual_quantity = int(fill["filled_quantity"])
                    post_fill = self.entry_decision_engine.evaluate_post_fill(
                        target_price=float(pid_info["execution_market_price"]), fill_price=float(fill["filled_price"]),
                        expected_qty=quantity, actual_qty=actual_quantity,
                        elapsed_seconds=float(fill["ack_elapsed_seconds"]),
                        expected_position=actual_quantity * (1 if order.side == "BUY" else -1),
                        actual_position=self.broker.get_position(symbol)["quantity"],
                    )
                    self._post_fill_checks.append({
                        "candidate_id": candidate_id, **post_fill,
                        "decisions": [asdict(d) for d in post_fill["decisions"]],
                    })
                    if not post_fill["passed"]:
                        # A fill already happened: retain it in the ledger and halt NEW entries.
                        # Existing protective exits remain enabled.
                        self._execution_halted = True
                    if actual_quantity <= 0:
                        raise RuntimeError("Paper fill has no reconcilable positive quantity")
                    quantity = actual_quantity
                    self.entry_candidate_observations.dispose(candidate_id, "FILLED", "paper_fill")
                    funnel["fills"] += 1
                    self._trade_sequence += 1
                    risk = abs(float(plan.entry_price) - float(plan.stop_price))
                    target_r = abs(float(plan.target_price) - float(plan.entry_price)) / risk if risk > 0.0 else 0.0
                    entry_evidence = self.entry_expectancy_ledger.observe_fill({
                        "candidate_id": candidate_id,
                        "symbol": symbol,
                        "side": plan.side,
                        "timestamp": str(timestamp),
                        "fill_timestamp": str(next_ts),
                        "pa_confidence": float(signal.confidence),
                        "id_confidence": float(decision.confidence),
                        "studies_confidence": chart_studies_confidence,
                        "studies_direction": int(composite_result["direction"]),
                        "atr_fraction": float(atr) / max(abs(float(plan.entry_price)), 1e-12),
                        "target_r": target_r,
                        "maximum_hold_bars": int(plan.maximum_hold_bars),
                        "session_minute": int(next_ts.hour * 60 + next_ts.minute),
                    })
                    self._record_controller_event("ENTRY_EXPECTANCY_CANDIDATE", timestamp, symbol, entry_evidence)
                    # Causal plant/dynamics estimate: this slice ends at
                    # the decision bar. It cannot see the fill bar or any
                    # subsequent held-position price action.
                    closed_loop_snapshot = self.closed_loop.entry_snapshot(
                        symbol=symbol, side=plan.side, entry_price=float(fill["filled_price"]),
                        stop_price=float(plan.stop_price), target_price=float(plan.target_price),
                        max_hold_bars=int(plan.maximum_hold_bars), regime="unknown", dynamics=symbol_dynamics,
                    )
                    self._record_controller_event("CLOSED_LOOP_ENTRY_SNAPSHOT", next_ts, symbol, {
                        "candidate_id": candidate_id, "trade_id": f"trade-{self._trade_sequence}",
                        **closed_loop_snapshot,
                    })
                    self.open_trades[symbol] = {
                        "side": plan.side, "entry_price": fill["filled_price"], "stop_price": plan.stop_price,
                        "target_price": plan.target_price, "quantity": quantity,
                        "minimum_hold_bars": plan.minimum_hold_bars, "maximum_hold_bars": plan.maximum_hold_bars,
                        "exit_confidence_threshold": decision.timing_quality, "entry_timestamp": str(next_ts),
                        "entry_atr": float(atr), "planned_entry_price": float(plan.entry_price),
                        "planned_stop_price": float(plan.stop_price), "planned_target_price": float(plan.target_price),
                        "candidate_id": candidate_id, "trade_id": f"trade-{self._trade_sequence}",
                        "controller_exit_pending": None,
                        "closed_loop": closed_loop_snapshot,
                    }
                    entry_bar_index[symbol] = bar_idx + 1
                    self._exit_controller_states[symbol] = self.exit_controller.open_position(
                        plan.side, fill["filled_price"], plan.stop_price, plan.target_price, plan.maximum_hold_bars,
                    )
                else:
                    self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "paper_fill_rejected")

        for symbol in list(self.open_trades.keys()):
            bars = symbol_bars[symbol]
            final_close = float(bars.iloc[len(bars) - 1]["close"])
            self._execute_exit(symbol, bars.iloc[len(bars) - 1].get("timestamp", ""), self.open_trades[symbol], final_close, "end_of_run_reconciliation")

        self.entry_candidate_observations.finalize_pending()
        gross_pnl = self.broker.realized_pnl
        assert abs(gross_pnl - sum(t["pnl"] for t in self.completed_trades)) < 1e-6

        target_names = set(self.registry.params)
        safety_names = set(self.registry.safety_params)
        coverage_target = sorted(target_names & self.consumed_parameters)

        funnel["exit_orders_submitted"] = self._exit_orders_submitted
        return {
            "risk_profile": self.risk_profile,
            "operating_mode": "paper",
            "post_fill_checks": self._post_fill_checks,
            "execution_halted": self._execution_halted,
            "safety_violations": sum(not row["passed"] for row in self._post_fill_checks),
            **funnel, "symbols": self.symbols, "completed_trades": len(self.completed_trades),
            "gross_pnl": gross_pnl, "net_pnl": sum(t["net_pnl"] for t in self.completed_trades),
            "ending_equity": self.starting_equity + sum(t["net_pnl"] for t in self.completed_trades),
            "config_hash": self.config.config_hash, "safety_contract_hash": self.safety_contract.contract_hash,
            "closed_loop_mode": self.closed_loop_mode,
            "pid_mode": self.pid_mode,
            "certification_audit": certification_audit,
            "session_completeness": {
                s: certify_session_completeness(frame, self.session_schedule)
                for s, frame in symbol_bars.items()
            },
            "final_portfolio_weights": self._portfolio_weights,
            "parameter_coverage": {
                "target_total": len(target_names), "target_consumed": len(coverage_target),
                "target_missing": sorted(target_names - self.consumed_parameters),
                "safety_total": len(safety_names),
            },
            "trades": self.completed_trades,
            "mtm_equity_curve": self._mtm_equity_curve if self.telemetry_mode == "full" else [],
            "mtm_max_drawdown_fraction": self._mtm_max_drawdown_fraction,
            "controller_telemetry": self.controller_telemetry,
            "controller_telemetry_summary": {
                "events": sum(self._controller_event_counts.values()),
                "entry_throttle_updates": self._controller_event_counts["ENTRY_CONFIDENCE_THROTTLE"],
                "exit_protection_updates": self._controller_event_counts["EXIT_PROTECTION_UPDATE"],
                "shadow_r_trajectory_updates": self._controller_event_counts["SHADOW_R_TRAJECTORY_UPDATE"],
                "shadow_r_trajectory_exits": self._controller_event_counts["SHADOW_R_TRAJECTORY_EXIT"],
                "outcomes": self._controller_event_counts["CONTROLLER_OUTCOME"],
                "closed_loop_entry_snapshots": self._controller_event_counts["CLOSED_LOOP_ENTRY_SNAPSHOT"],
                "trade_path_comparisons": self._controller_event_counts["TRADE_PATH_COMPARATOR"],
                "portfolio_risk_comparisons": self._controller_event_counts["PORTFOLIO_RISK_COMPARATOR"],
                "outcome_ledger_updates": self._controller_event_counts["OUTCOME_LEDGER_UPDATE"],
                "entry_quality_comparisons": self._controller_event_counts["ENTRY_QUALITY_COMPARATOR"],
                "dynamic_size_actuations": self._controller_event_counts["DYNAMIC_SIZE_ACTUATION"],
                "trade_path_stop_actuations": self._controller_event_counts["TRADE_PATH_STOP_ACTUATION"],
                "hmm_regime_risk_shadow_observations": self._controller_event_counts["HMM_REGIME_RISK_SHADOW"],
            },
            "position_sizing_events": self._position_sizing_events,
            "entry_expectancy_evidence": {
                **self.entry_expectancy_ledger.summary(),
                # Resolved entry/outcome rows are trade-level evidence, not
                # per-bar telemetry.  Retain them in compact mode so a
                # research replay can be small without losing the causal
                # pairs needed for a later, frozen entry-quality study.
                "resolved": self.entry_expectancy_ledger.resolved,
            },
            "entry_candidate_observations": self.entry_candidate_observations.report(),
            "grid_shadow": {
                "enabled": self.grid_context_provider is not None,
                "observations": self.grid_shadow_observations,
                "available": sum(1 for row in self.grid_shadow_observations if row["available"]),
                "synchronized": sum(1 for row in self.grid_shadow_observations if row["synchronized"] is True),
            },
        }

    def _in_trading_window(self, timestamp: str) -> bool:
        start = str(self.config.require("trading_hours_start"))
        end = str(self.config.require("trading_hours_end"))
        # These two really are consumed and really do gate control flow
        # below -- tracking them here (not just calling config.require()
        # directly) matches every box's own req()-wrapper pattern, so the
        # parameter_coverage report doesn't undercount a parameter that
        # genuinely does something. Missing this made trading_hours_start/
        # end show up as "unconsumed" despite being load-bearing.
        self.consumed_parameters.add("trading_hours_start")
        self.consumed_parameters.add("trading_hours_end")
        try:
            raw = str(timestamp)
            time_part = (raw.split("T")[-1] if "T" in raw else raw.split(" ")[-1])[:5]
            return start <= time_part <= end
        except Exception:
            return True
