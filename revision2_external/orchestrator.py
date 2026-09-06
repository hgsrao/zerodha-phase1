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

import itertools
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from gates_framework import EntryDecisionEngine, EntrySignal, SafetyGateConfig, SystemState
from revision2.boxes import DataIngestionBox, P01DBox, SafetyGatesTargetBox
from revision2.contracts import EffectiveConfig, MarketSnapshot, SafetyContract, StartupCertificate, StartupNotCertifiedError
from revision2.portfolio_orchestrator import SECTOR_MAP, _ClockEvent
from revision2_external.continuous_exit_controller import ContinuousExitController, ExitControllerState
from revision2_external.data_certification_pandera import certify_bars
from revision2_external.indicators_talib import TALibPredictiveAnalyticsBox
from revision2_external.pid_controller import SimplePIDModelPredictiveControlBox
from revision2_external.position_sizing_pyportfolioopt import PyPortfolioOptPositionManagerBox, compute_portfolio_weights
from revision2_external.regime_id_box import HMMIntelligentDiscriminationBox
from revision2_external.startup_validation import validate_runtime_parameters, validate_safety_contract
from runtime.operating_mode import ExecutionGate, PaperBrokerAdapter

SNAPSHOT_LOOKBACK_BARS = 300
PORTFOLIO_WEIGHT_REFIT_EVERY_BARS = 500  # PyPortfolioOpt refit cadence, per unique clock tick


class ExternalEngineStartupNotCertifiedError(StartupNotCertifiedError):
    pass


class Revision2ExternalEngineOrchestrator:
    """Shared-portfolio orchestrator using the external-library box set."""

    def __init__(
        self,
        symbols: List[str],
        registry: Optional[CanonicalParameterRegistry] = None,
        calibration_overrides: Optional[Dict[str, Any]] = None,
        starting_equity: float = 1_000_000.0,
        sector_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self.symbols = list(symbols)
        self.registry = registry or CanonicalParameterRegistry()
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
        self.sector_map = dict(sector_map) if sector_map is not None else dict(SECTOR_MAP)

        self.data_ingestion = DataIngestionBox()
        self.pa = TALibPredictiveAnalyticsBox()
        self.id_box = HMMIntelligentDiscriminationBox()
        self.mpc = SimplePIDModelPredictiveControlBox()
        self.safety_gates_target = SafetyGatesTargetBox()
        self.position_manager = PyPortfolioOptPositionManagerBox()
        self.p01d = P01DBox()
        self.broker = PaperBrokerAdapter(account_id="PAPER-EXTERNAL-ENGINE")
        self.entry_decision_engine = EntryDecisionEngine(config=self._build_safety_gate_config())

        self.starting_equity = starting_equity
        self.consumed_parameters: set = set()
        self.completed_trades: List[Dict[str, Any]] = []
        self.open_trades: Dict[str, Dict[str, Any]] = {}
        self._equity_curve: List[float] = [starting_equity]
        self._last_close: Dict[str, float] = {}
        self._mtm_equity_curve: List[Tuple[str, float]] = [("", starting_equity)]
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
        )
        self.consumed_parameters.update({
            "pid_kp_exit", "pid_ki_exit", "pid_kd_exit", "pid_integral_max_clamp",
            "pid_integral_window_bars", "trailing_stop_atr_mult",
        })
        self._exit_controller_states: Dict[str, ExitControllerState] = {}

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
        return self.starting_equity + self.broker.realized_pnl

    def _current_drawdown(self) -> float:
        peak = max(self._equity_curve) if self._equity_curve else self.starting_equity
        current = self._equity_curve[-1] if self._equity_curve else self.starting_equity
        return (peak - current) / peak if peak > 0 else 0.0

    def _mark_to_market_equity(self) -> float:
        unrealized = 0.0
        for symbol, trade in self.open_trades.items():
            mark = self._last_close.get(symbol, trade["entry_price"])
            unrealized += (
                (mark - trade["entry_price"]) * trade["quantity"] if trade["side"] == "BUY"
                else (trade["entry_price"] - mark) * trade["quantity"]
            )
        return self.starting_equity + self.broker.realized_pnl + unrealized

    def _gross_exposure_notional(self) -> float:
        return sum(t["quantity"] * t["entry_price"] for t in self.open_trades.values())

    @staticmethod
    def _leg_cost(price: float, quantity: int, side: str) -> float:
        turnover = price * quantity
        cost = min(20.0, 0.0003 * turnover) + 0.0000345 * turnover
        if side == "SELL":
            cost += 0.00025 * turnover
        return cost

    def _execute_exit(self, symbol: str, timestamp, trade: Dict[str, Any], exit_price: float, reason: str) -> None:
        close_side = "SELL" if trade["side"] == "BUY" else "BUY"
        result = self.broker.place_order(
            symbol=symbol, side=close_side, quantity=trade["quantity"], order_type="MARKET",
            market_price=exit_price, config=self.safety_contract.as_dict(), parameter_registry=self.registry,
        )
        if result["passed"]:
            pnl = (
                (result["filled_price"] - trade["entry_price"]) * trade["quantity"]
                if trade["side"] == "BUY" else (trade["entry_price"] - result["filled_price"]) * trade["quantity"]
            )
            trade_costs = self._leg_cost(trade["entry_price"], trade["quantity"], trade["side"]) + self._leg_cost(
                result["filled_price"], trade["quantity"], close_side
            )
            self.completed_trades.append({
                "symbol": symbol, "side": trade["side"], "entry_price": trade["entry_price"],
                "exit_price": result["filled_price"], "quantity": trade["quantity"],
                "entry_timestamp": trade["entry_timestamp"], "exit_timestamp": str(timestamp),
                "reason": reason, "pnl": pnl, "costs": trade_costs, "net_pnl": pnl - trade_costs,
            })
            self._equity_curve.append(self._equity())
            del self.open_trades[symbol]
            self._exit_controller_states.pop(symbol, None)

    def _maybe_exit(self, symbol: str, timestamp, bar, signal, held_bars: int, session_last_bar: bool) -> None:
        trade = self.open_trades.get(symbol)
        if trade is None:
            return
        halt_dd = float(self.config.require("drawdown_halt_threshold"))
        if self._current_drawdown() >= halt_dd:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "forced_close_drawdown_halt")
            return

        # The real feedback path: re-run the exit controller on THIS bar's
        # freshly-evaluated PA signal (exit_confidence -- the field PA
        # computes specifically for exit timing, distinct from entry
        # confidence) before checking anything else. current_stop is a
        # ratcheted stop that only ever tightens; the frozen
        # trade["stop_price"] is no longer what's actually checked below.
        state = self._exit_controller_states.get(symbol)
        current_stop = trade["stop_price"]
        if state is not None:
            # Droop input: CURRENT ATR, re-measured this bar -- not the
            # frozen entry-time ATR -- matches how atr is computed
            # everywhere else in this file (signal.volatility * close).
            current_atr = float(signal.volatility) * float(bar["close"])
            state = self.exit_controller.update(symbol, state, float(signal.exit_confidence), float(bar["close"]), current_atr)
            self._exit_controller_states[symbol] = state
            current_stop = state.current_stop_price

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
        # sustained saturation, only actionable once the minimum hold is met.
        if state is not None and held_bars >= trade["minimum_hold_bars"] and self.exit_controller.should_exit_on_saturation(state):
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "saturation_exit")
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

    @staticmethod
    def build_clock(symbol_bars: Dict[str, pd.DataFrame], warmup: int) -> List[_ClockEvent]:
        events: List[_ClockEvent] = []
        for symbol, bars in symbol_bars.items():
            ts = pd.to_datetime(bars["timestamp"])
            for bar_idx in range(warmup, len(bars) - 1):
                events.append(_ClockEvent(ts.iloc[bar_idx], symbol, bar_idx))
        events.sort(key=lambda e: (e.timestamp, e.symbol))
        return events

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
            frame, audit = certify_bars(bars)
            certified[symbol] = frame
            certification_audit[symbol] = audit
        symbol_bars = certified

        for symbol, bars in symbol_bars.items():
            self.pa.calibrate(symbol, bars.iloc[:warmup])
            self.id_box.calibrate(symbol, bars.iloc[:warmup])

        funnel = {
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

            for event in tick_events:
                self._last_close[event.symbol] = float(symbol_bars[event.symbol].iloc[event.bar_idx]["close"])
            self._mtm_equity_curve.append((str(timestamp), self._mark_to_market_equity()))

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
                    window = bars[bars["timestamp"] <= idx].tail(500)
                    if len(window) >= 30:
                        price_history[symbol] = window.set_index("timestamp")["close"]
                if len(price_history) >= 2:
                    self._portfolio_weights = compute_portfolio_weights(price_history)

            for event in tick_events:
                funnel["bars_processed"] += 1
                symbol, bar_idx = event.symbol, event.bar_idx
                bars = symbol_bars[symbol]
                next_ts = pd.Timestamp(bars.iloc[bar_idx + 1]["timestamp"])
                end_time = str(self.config.require("trading_hours_end"))
                session_last_bar = next_ts.date() != event_ts.date() or event_ts.strftime("%H:%M") >= end_time

                admitted, _, trace = self.data_ingestion.admit(symbol, self.config)
                self._record(trace)
                if not admitted:
                    continue
                in_window = self._in_trading_window(str(timestamp))

                snapshot = MarketSnapshot(
                    symbol=symbol, timestamp=str(timestamp),
                    bars=bars.iloc[max(0, bar_idx - SNAPSHOT_LOOKBACK_BARS + 1):bar_idx + 1],
                )
                signal, trace = self.pa.evaluate(snapshot, self.config)
                self._record(trace)
                funnel["pa_signals"] += 1

                held = bar_idx - entry_bar_index.get(symbol, bar_idx)
                self._maybe_exit(symbol, timestamp, bars.iloc[bar_idx], signal, held, session_last_bar)

                if symbol in self.open_trades or not in_window:
                    continue
                if next_ts.date() != event_ts.date() or next_ts.strftime("%H:%M") >= str(self.safety_contract.values["no_entry_cutoff_time"]):
                    continue

                decision, trace = self.id_box.evaluate(signal, self.config, latest_close=float(bars.iloc[bar_idx]["close"]))
                self._record(trace)
                if not decision.approved:
                    funnel["id_rejections"] += 1
                    continue
                funnel["id_approvals"] += 1

                atr = signal.volatility * bars.iloc[bar_idx]["close"]
                next_open = float(bars.iloc[bar_idx + 1]["open"])
                plan, pid_info, trace = self.mpc.build_plan(signal, decision, next_open, atr, self.config)
                self._record(trace)
                if plan is None:
                    continue
                funnel["mpc_plans"] += 1

                approved, _, size_mult, trace = self.safety_gates_target.evaluate_pre_sizing(self._equity_curve, self.config)
                self._record(trace)
                if not approved:
                    funnel["safety_rejections"] += 1
                    continue
                size_mult *= pid_info["entry_timing_multiplier"]

                if len(self.open_trades) >= max_concurrent:
                    funnel["portfolio_cap_rejections"] += 1
                    continue
                equity_now = self._equity()
                sector = self.sector_map.get(symbol, "Unclassified")

                quantity, trace = self.position_manager.size(
                    plan, equity_now, size_mult, self.config, symbol=symbol,
                    portfolio_weights=self._portfolio_weights,
                    max_exposure_per_symbol_fraction=float(self.safety_contract.values["max_exposure_per_symbol_fraction"]),
                    open_positions_count=len(self.open_trades),
                    symbol_positions_count=1 if symbol in self.open_trades else 0,
                )
                self._record(trace)
                if quantity <= 0:
                    continue

                real_notional = plan.entry_price * quantity
                if self._gross_exposure_notional() + real_notional > equity_now * max_gross_fraction:
                    funnel["portfolio_cap_rejections"] += 1
                    continue
                sector_notional = sum(
                    t["quantity"] * t["entry_price"] for s, t in self.open_trades.items()
                    if self.sector_map.get(s, "Unclassified") == sector
                )
                if sector_notional + real_notional > equity_now * sector_cap_fraction:
                    funnel["portfolio_cap_rejections"] += 1
                    continue

                post_ok, _, trace = self.safety_gates_target.evaluate_post_sizing(self._equity_curve, plan, quantity, self.config)
                self._record(trace)
                if not post_ok:
                    funnel["safety_rejections"] += 1
                    continue
                funnel["safety_approvals"] += 1

                order, trace = self.p01d.create_order(symbol, plan, quantity, self.config)
                self._record(trace)
                if order is None:
                    continue

                state = SystemState(
                    portfolio_value=equity_now, current_dd_percent=self._current_drawdown(),
                    current_lambda=self._gross_exposure_notional() / max(equity_now, 1.0),
                    daily_realized_loss=max(0.0, self._day_start_equity - equity_now),
                    # Was a hardcoded 0.0. Real, verified before fixing: no
                    # gate in gates_framework.py actually reads
                    # daily_unrealized_loss (grepped every Gate0X class body
                    # -- it's defined on SystemState and appears in one
                    # diagnostic dict, never in a pass/fail check), so this
                    # fix changes zero real gate decisions today. Fixed
                    # anyway because it's real, cheap, already-available
                    # data (the same _mark_to_market_equity() the MTM curve
                    # already uses) -- a future gate that DOES read it
                    # should see the truth, not a permanent zero.
                    daily_unrealized_loss=max(0.0, self._day_start_equity - self._mark_to_market_equity()),
                    open_positions_count=len(self.open_trades),
                    open_positions=[type("P", (), {"position_notional": t["quantity"] * t["entry_price"]})() for t in self.open_trades.values()],
                    # market_data_age_seconds / broker_connected /
                    # circuit_breaker_triggered stay fixed "healthy" here on
                    # purpose, not by oversight: this is an offline replay
                    # against historical bars -- there is no live broker
                    # session to disconnect, no live feed to go stale, and
                    # no circuit-breaker signal computed anywhere in this
                    # codebase. Gate04BrokerHalt/Gate07StaleData/
                    # Gate18CircuitBreaker DO read these three for real
                    # (verified), so faking a plausible-looking number here
                    # would be worse than an honest, documented backtest
                    # default -- a real live-trading mode (not built yet)
                    # would need to feed these from actual broker/feed
                    # telemetry, not from this replay orchestrator.
                    market_data_age_seconds=0, broker_connected=True, broker_offline_seconds=0,
                    kill_switch_active=not bool(self.safety_contract.values["kill_switch_enabled"]),
                    circuit_breaker_triggered=False,
                )
                entry_signal = EntrySignal(
                    symbol=symbol, entry_price=plan.entry_price, stop_loss_price=plan.stop_price,
                    profit_target_price=plan.target_price, confidence=decision.confidence,
                    suggested_quantity=quantity, position_notional=real_notional,
                    risk_reward_ratio=decision.risk_reward_ratio,
                )
                try:
                    current_time = datetime.fromisoformat(str(next_ts))
                except Exception:
                    current_time = datetime.now()
                # Box 7 (SafetyGates/18-gate) is unchanged, in-house, from
                # this branch's own base commit -- evaluate_pre_submit()/
                # evaluate_post_fill() are a different branch's enhancement
                # (codex/ten-box-remediation), not present here. Using
                # .evaluate() as-is, matching "kept in-house, untouched".
                gate_result = self.entry_decision_engine.evaluate(
                    state, signal=entry_signal, current_time=current_time, proposed_quantity=quantity,
                    target_price=plan.entry_price, fill_price=plan.entry_price, expected_qty=quantity,
                    actual_qty=quantity, symbol=symbol, seen_recent=False, proposed_notional=real_notional,
                )
                funnel["gates_evaluated"] += 1
                if not gate_result["passed"]:
                    funnel["gates_rejected"] += 1
                    continue
                funnel["gates_passed"] += 1
                quantity = max(0, int(gate_result["adjusted_quantity"]))
                if quantity <= 0:
                    continue

                gate2 = ExecutionGate().validate_pre_submit(
                    self.safety_contract.as_dict(),
                    {"symbol": symbol, "side": order.side, "quantity": quantity, "order_type": order.order_type},
                    parameter_registry=self.registry,
                )
                if not gate2["passed"]:
                    funnel["safety_rejections"] += 1
                    continue

                fill = self.broker.place_order(
                    symbol=symbol, side=order.side, quantity=quantity, order_type=order.order_type,
                    market_price=next_open, config=self.safety_contract.as_dict(), parameter_registry=self.registry,
                )
                funnel["orders_submitted"] += 1
                if fill["passed"]:
                    funnel["fills"] += 1
                    self.open_trades[symbol] = {
                        "side": plan.side, "entry_price": fill["filled_price"], "stop_price": plan.stop_price,
                        "target_price": plan.target_price, "quantity": quantity,
                        "minimum_hold_bars": plan.minimum_hold_bars, "maximum_hold_bars": plan.maximum_hold_bars,
                        "exit_confidence_threshold": decision.timing_quality, "entry_timestamp": str(next_ts),
                    }
                    entry_bar_index[symbol] = bar_idx + 1
                    self._exit_controller_states[symbol] = self.exit_controller.open_position(
                        plan.side, fill["filled_price"], plan.stop_price, plan.target_price, plan.maximum_hold_bars,
                    )

        for symbol in list(self.open_trades.keys()):
            bars = symbol_bars[symbol]
            final_close = float(bars.iloc[len(bars) - 1]["close"])
            self._execute_exit(symbol, bars.iloc[len(bars) - 1].get("timestamp", ""), self.open_trades[symbol], final_close, "end_of_run_reconciliation")

        gross_pnl = self.broker.realized_pnl
        assert abs(gross_pnl - sum(t["pnl"] for t in self.completed_trades)) < 1e-6

        target_names = set(self.registry.params)
        safety_names = set(self.registry.safety_params)
        coverage_target = sorted(target_names & self.consumed_parameters)

        mtm_values = [e for _, e in self._mtm_equity_curve]
        mtm_peak = mtm_values[0]
        mtm_max_drawdown_fraction = 0.0
        for v in mtm_values:
            mtm_peak = max(mtm_peak, v)
            if mtm_peak > 0:
                mtm_max_drawdown_fraction = max(mtm_max_drawdown_fraction, (mtm_peak - v) / mtm_peak)

        return {
            **funnel, "symbols": self.symbols, "completed_trades": len(self.completed_trades),
            "gross_pnl": gross_pnl, "net_pnl": sum(t["net_pnl"] for t in self.completed_trades),
            "ending_equity": self.starting_equity + sum(t["net_pnl"] for t in self.completed_trades),
            "config_hash": self.config.config_hash, "safety_contract_hash": self.safety_contract.contract_hash,
            "certification_audit": certification_audit,
            "final_portfolio_weights": self._portfolio_weights,
            "parameter_coverage": {
                "target_total": len(target_names), "target_consumed": len(coverage_target),
                "target_missing": sorted(target_names - self.consumed_parameters),
                "safety_total": len(safety_names),
            },
            "trades": self.completed_trades,
            "mtm_equity_curve": self._mtm_equity_curve,
            "mtm_max_drawdown_fraction": mtm_max_drawdown_fraction,
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
