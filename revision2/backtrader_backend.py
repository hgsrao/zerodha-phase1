"""Runs the SAME Revision 2 decision boxes (PA/ID/MPC/SafetyGates/
PositionManager/P01D/EntryDecisionEngine) inside a real Backtrader
Strategy, letting Backtrader own order execution, fills, and cash/position
accounting -- an independent cross-check of revision2/orchestrator.py's
own hand-rolled PaperBrokerAdapter, per the project's stated design
principle: reuse mature infrastructure for commodity execution/accounting,
keep custom code for the unique decision logic.

Decision logic (which box is called with what) is deliberately duplicated
from revision2/orchestrator.py's run() loop rather than imported as a
shared function, because the entire point of this module is to prove two
independently-written call sequences against the same boxes agree -- a
shared driver function would defeat that.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import backtrader as bt
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from gates_framework import EntryDecisionEngine, EntrySignal, SafetyGateConfig, SystemState
from revision2.backend_contract import BackendEvent, canonical_parameter_snapshot, normalize_event_timestamp
from revision2.boxes import (
    DataIngestionBox,
    IntelligentDiscriminationBox,
    L2DataCertifierBox,
    ModelPredictiveControlBox,
    P01DBox,
    PositionManagerBox,
    PredictiveAnalyticsBox,
    SafetyGatesTargetBox,
    UnifiedExecutionBox,
)
from revision2.contracts import EffectiveConfig, MarketSnapshot, SafetyContract

# Matches revision2/orchestrator.py's SNAPSHOT_LOOKBACK_BARS.
SNAPSHOT_LOOKBACK_BARS = 300

# Matches runtime/operating_mode.py's PaperBrokerAdapter.__init__ default
# slippage_fraction (0.0005) -- both orchestrators construct their broker
# with no override, so this is the real value fills use in production.
PAPER_BROKER_SLIPPAGE_FRACTION = 0.0005


def _leg_cost(price: float, quantity: int, side: str) -> float:
    """Identical formula to orchestrator.py's _transaction_costs() and
    portfolio_orchestrator.py's _leg_cost() -- duplicated deliberately
    (matching revision2/in_house_backend.py's own copy), same rationale as
    this module's docstring: independent verification, not a shared
    driver function that could hide a real divergence in the shared code."""
    turnover = price * quantity
    cost = min(20.0, 0.0003 * turnover) + 0.0000345 * turnover
    if side == "SELL":
        cost += 0.00025 * turnover
    return cost


class ParityCommission(bt.CommInfoBase):
    """Identical brokerage/exchange/tax formula to orchestrator.py's
    _transaction_costs() / portfolio_orchestrator.py's _leg_cost(), so a
    parity divergence reflects a real logic difference, not a mismatched
    cost model between the two engines."""

    params = (("stocklike", True), ("commtype", bt.CommInfoBase.COMM_PERC), ("percabs", True))

    def _getcommission(self, size, price, pseudoexec):
        turnover = abs(size) * price
        brokerage = min(20.0, 0.0003 * turnover)
        exchange = 0.0000345 * turnover
        tax = 0.00025 * turnover if size < 0 else 0.0  # SELL leg only
        return brokerage + exchange + tax


class Revision2ParityStrategy(bt.Strategy):
    params = dict(
        symbol="", registry=None, calibration_overrides=None, warmup=60, config_hash="",
        events=None, total_bars=0, source_bars=None,
    )

    def __init__(self) -> None:
        self.registry: CanonicalParameterRegistry = self.p.registry
        overrides = self.p.calibration_overrides or {}
        values = {name: spec.default for name, spec in self.registry.params.items()}
        values.update(overrides)
        self.config = EffectiveConfig.build(values, registry_hash=self.registry.FROZEN_IDENTITY_SHA256)
        self.safety_contract = SafetyContract.from_registry(self.registry)

        self.data_ingestion = DataIngestionBox()
        self.l2_certifier = L2DataCertifierBox()
        self.pa = PredictiveAnalyticsBox()
        self.id_box = IntelligentDiscriminationBox()
        self.mpc = ModelPredictiveControlBox()
        self.safety_gates_target = SafetyGatesTargetBox()
        self.position_manager = PositionManagerBox()
        self.p01d = P01DBox()
        self.unified_execution = UnifiedExecutionBox()
        self.entry_decision_engine = EntryDecisionEngine(config=self._build_safety_gate_config())

        self._equity_curve: List[float] = [self.broker.getvalue()]
        self._sequence = 0
        self._pa_calibrated = False
        self._open_trade: Optional[Dict[str, Any]] = None
        self._pending_order = None
        self._pending_plan = None
        self._pending_decision = None
        self._pending_exit_reason = "exit"
        self._entry_order_submit_ts: Optional[str] = None
        self._events: List[BackendEvent] = self.p.events if self.p.events is not None else []
        self._bar_idx = -1

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

    def _emit(self, event_type: str, event_timestamp: str, **kw) -> None:
        self._sequence += 1
        self._events.append(BackendEvent(
            backend="backtrader", event_type=event_type, symbol=self.p.symbol,
            decision_timestamp=kw.pop("decision_timestamp", event_timestamp),
            event_timestamp=event_timestamp, sequence=self._sequence,
            config_hash=self.p.config_hash, **kw,
        ))

    def _current_drawdown(self) -> float:
        peak = max(self._equity_curve) if self._equity_curve else self.broker.getvalue()
        current = self._equity_curve[-1] if self._equity_curve else self.broker.getvalue()
        return (peak - current) / peak if peak > 0 else 0.0

    def _frame(self, size: int) -> pd.DataFrame:
        n = max(1, min(size, len(self.data)))
        rows = []
        for i in range(-(n - 1), 1):
            rows.append({
                "timestamp": self.data.datetime.datetime(i),
                "open": self.data.open[i], "high": self.data.high[i],
                "low": self.data.low[i], "close": self.data.close[i], "volume": self.data.volume[i],
            })
        return pd.DataFrame(rows)

    def next(self) -> None:
        self._bar_idx += 1
        ts = normalize_event_timestamp(self.data.datetime.datetime(0))

        if not self._pa_calibrated:
            if len(self.data) < self.p.warmup:
                return
            self.pa.calibrate(self.p.symbol, self._frame(size=self.p.warmup))
            self._pa_calibrated = True
            # Matches revision2/orchestrator.py exactly: it calibrates on
            # bars[0:warmup] then starts its evaluation loop AT bar_idx
            # warmup (0-indexed) -- the first bar AFTER the calibration
            # window, never the calibration window's own last bar, and
            # emits no trace_sink record at all for the calibration bars.
            # Without this early return, the bar that completes the warmup
            # window also gets evaluated as a trading bar, one bar too
            # early relative to the in-house engine (a real divergence this
            # comparison caught on its very first run).
            return

        # revision2/orchestrator.py's loop is range(warmup, n - 1): it
        # never evaluates the very last bar, because entry fills need
        # bars.iloc[bar_idx + 1]["open"] and there is no bar after the
        # last one. No trace_sink record -- not even equity -- exists for
        # that excluded bar, so skip it here too rather than let Backtrader
        # evaluate one bar further than the in-house engine ever does.
        if self.p.total_bars and self._bar_idx >= self.p.total_bars - 1:
            return

        self._equity_curve.append(self.broker.getvalue())
        # in_house_backend.py emits exactly one "equity" event per evaluated
        # bar, unconditionally, as the LAST event of that bar regardless of
        # which stage rejected it. try/finally reproduces that ordering
        # here without duplicating the emit call at every early return.
        try:
            self._evaluate_bar(ts)
        finally:
            self._emit("equity", ts, price=float(self.broker.getvalue()), reason="post-bar")

    def _evaluate_bar(self, ts: str) -> None:
        admitted, _, _ = self.data_ingestion.admit(self.p.symbol, self.config)
        if not admitted:
            return
        certified, _, _ = self.l2_certifier.certify(self._frame(size=6), self.config)
        if not certified:
            return
        in_window, _, _ = self.unified_execution.check_window(ts, self.config)

        bars = self._frame(size=SNAPSHOT_LOOKBACK_BARS)
        snapshot = MarketSnapshot(symbol=self.p.symbol, timestamp=ts, bars=bars)
        signal, _ = self.pa.evaluate(snapshot, self.config)
        self._emit("signal", ts, price=float(self.data.close[0]),
                    reason=f"direction={signal.direction} confidence={signal.confidence:.4f}")

        self._maybe_exit(ts, signal)

        if self.position or self._pending_order is not None or not in_window:
            return

        decision, _ = self.id_box.evaluate(signal, self.config)
        self._emit("gate_decision", ts, reason=f"ID: {decision.reason}", passed=decision.approved)
        if not decision.approved:
            return

        atr = signal.volatility * float(self.data.close[0])
        # revision2/orchestrator.py deliberately feeds MPC the NEXT bar's
        # actual open (bars.iloc[bar_idx + 1]["open"]) as the reference
        # entry price for slippage/effective_entry and the stop/target
        # distances -- a legitimate "this market order fills at the next
        # print" simulation, not a control-flow decision made on future
        # data. Backtrader's own next() has no native look-ahead (bar t+1
        # genuinely is not yet delivered when next() runs for bar t), so
        # matching self.data.close[0] here would compare the two engines
        # under different information, not different logic -- exactly what
        # produced this call's very first divergence. Reading the same
        # future open from source_bars (the identical DataFrame both
        # engines were given) isolates real logic/execution differences
        # instead. Whether next-bar-open lookahead itself belongs in a
        # live-faithful engine is a separate, open design question (see
        # the "Canonical execution contract" work) -- deliberately not
        # resolved here.
        next_open = float(self.p.source_bars.iloc[self._bar_idx + 1]["open"])
        plan, pid_info, _ = self.mpc.build_plan(signal, decision, next_open, atr, self.config)
        if plan is None:
            return

        approved, _, size_mult, _ = self.safety_gates_target.evaluate_pre_sizing(self._equity_curve, self.config)
        if not approved:
            self._emit("gate_decision", ts, reason="SafetyGates pre-sizing: rejected", passed=False)
            return
        size_mult *= pid_info["entry_timing_multiplier"]

        quantity, _ = self.position_manager.size(plan, self.broker.getvalue(), size_mult, self.config)
        if quantity <= 0:
            return

        post_ok, _, _ = self.safety_gates_target.evaluate_post_sizing(self._equity_curve, plan, quantity, self.config)
        if not post_ok:
            self._emit("gate_decision", ts, reason="SafetyGates post-sizing: rejected", passed=False)
            return

        order, _ = self.p01d.create_order(self.p.symbol, plan, quantity, self.config)
        if order is None:
            return

        state = SystemState(
            portfolio_value=self.broker.getvalue(), current_dd_percent=self._current_drawdown(),
            current_lambda=0.0, daily_realized_loss=max(0.0, max(self._equity_curve) - self.broker.getvalue()),
            daily_unrealized_loss=0.0, open_positions_count=0, open_positions=[],
            market_data_age_seconds=0, broker_connected=True, broker_offline_seconds=0,
            kill_switch_active=not bool(self.safety_contract.values["kill_switch_enabled"]),
            circuit_breaker_triggered=False,
        )
        entry_signal = EntrySignal(
            symbol=self.p.symbol, entry_price=plan.entry_price, stop_loss_price=plan.stop_price,
            profit_target_price=plan.target_price, confidence=decision.confidence,
            suggested_quantity=quantity, position_notional=quantity * plan.entry_price,
            risk_reward_ratio=decision.risk_reward_ratio,
        )
        gate_result = self.entry_decision_engine.evaluate(
            state, signal=entry_signal, current_time=self.data.datetime.datetime(0), proposed_quantity=quantity,
            target_price=plan.entry_price, fill_price=plan.entry_price, expected_qty=quantity,
            actual_qty=quantity, symbol=self.p.symbol, seen_recent=False, proposed_notional=quantity * plan.entry_price,
        )
        self._emit("gate_decision", ts, reason=f"18gates: {gate_result['gate']}: {gate_result['reason']}", passed=gate_result["passed"])
        if not gate_result["passed"]:
            return
        quantity = max(0, int(gate_result["adjusted_quantity"]))
        if quantity <= 0:
            return

        self._emit("order", ts, side=plan.side, quantity=quantity, price=plan.entry_price, reason="submitted")
        self._pending_plan = plan
        self._pending_decision = decision
        self._entry_order_submit_ts = ts
        if plan.side == "BUY":
            self._pending_order = self.buy(size=quantity, exectype=bt.Order.Market)
        else:
            self._pending_order = self.sell(size=quantity, exectype=bt.Order.Market)

    def _maybe_exit(self, ts: str, signal) -> None:
        trade = self._open_trade
        if trade is None or self._pending_order is not None:
            return
        held = self._bar_idx - trade["entry_bar_idx"]

        halt_dd = float(self.config.require("drawdown_halt_threshold"))
        if self._current_drawdown() >= halt_dd:
            self._submit_close("forced_close_drawdown_halt")
            return

        high, low = float(self.data.high[0]), float(self.data.low[0])
        if trade["side"] == "BUY":
            if low <= trade["stop_price"]:
                self._submit_close("stop"); return
            if high >= trade["target_price"]:
                self._submit_close("target"); return
        else:
            if high >= trade["stop_price"]:
                self._submit_close("stop"); return
            if low <= trade["target_price"]:
                self._submit_close("target"); return

        if held >= trade["minimum_hold_bars"] and (
            signal.exit_confidence < trade["exit_confidence_threshold"] or signal.quality_band == "red"
        ):
            self._submit_close("signal_exit")
            return

        if held >= trade["maximum_hold_bars"]:
            self._submit_close("max_hold")

    def _submit_close(self, reason: str) -> None:
        self._pending_exit_reason = reason
        self._pending_order = self.close()

    def notify_order(self, order) -> None:
        if order.status in (order.Submitted, order.Accepted):
            return
        if order.status == order.Completed:
            ts = normalize_event_timestamp(self.data.datetime.datetime(0))
            side = "BUY" if order.isbuy() else "SELL"
            raw_price = float(order.executed.price)
            # Identical formula to PaperBrokerAdapter.place_order(): BUY
            # pays raw_price higher, SELL receives raw_price lower, rounded
            # to 4 decimals -- see the module-level comment on
            # PAPER_BROKER_SLIPPAGE_FRACTION for why this is applied here
            # rather than through Backtrader's own slippage model.
            slip = raw_price * PAPER_BROKER_SLIPPAGE_FRACTION
            price = round(raw_price + slip if side == "BUY" else raw_price - slip, 4)
            qty = int(abs(order.executed.size))
            # order.executed.comm was computed by ParityCommission on
            # raw_price (Backtrader's own pre-slippage execution price).
            # orchestrator.py's _transaction_costs() and
            # portfolio_orchestrator.py's _leg_cost() compute cost on the
            # ACTUAL (post-slippage) fill price -- recomputed here with the
            # identical formula rather than trusting order.executed.comm,
            # so a real cost-model divergence isn't masked by comparing
            # commission-on-the-wrong-price against commission-on-the-
            # wrong-price and calling it agreement.
            comm = _leg_cost(price, qty, side)

            if self._open_trade is None:
                # Entry fill. in_house_backend.py emits exactly "fill" then
                # "cost" (reason="entry leg cost") for this, decision_
                # timestamp on both set to when the order was SUBMITTED
                # (the bar before this one), not when it filled -- matching
                # that here rather than defaulting decision_timestamp to
                # this fill bar's own ts.
                self._emit("fill", ts, decision_timestamp=self._entry_order_submit_ts,
                            side=side, quantity=qty, price=price, reason="filled")
                self._emit("cost", ts, decision_timestamp=self._entry_order_submit_ts,
                            side=side, quantity=qty, price=comm, reason="entry leg cost")
                self._open_trade = {
                    "side": self._pending_plan.side, "stop_price": self._pending_plan.stop_price,
                    # Backtrader calls notify_order() for a fill BEFORE
                    # next() runs for that same bar, so self._bar_idx still
                    # holds the PREVIOUS bar's value (next()'s += 1 hasn't
                    # run yet) -- entry_bar_idx=self._bar_idx recorded the
                    # decision bar, one bar too early relative to
                    # orchestrator.py's entry_bar_idx=bar_idx+1 (the actual
                    # fill bar). That silently shifted every later held =
                    # bar_idx - entry_bar_idx computation by one bar,
                    # letting minimum_hold_bars clear a bar early and
                    # produce a genuinely different exit (signal_exit
                    # instead of target) on real data -- the first fixture
                    # this harness ran that held a position past entry
                    # caught it immediately.
                    "target_price": self._pending_plan.target_price, "entry_bar_idx": self._bar_idx + 1,
                    "minimum_hold_bars": self._pending_plan.minimum_hold_bars,
                    "maximum_hold_bars": self._pending_plan.maximum_hold_bars,
                    "exit_confidence_threshold": self._pending_decision.timing_quality,
                }
            else:
                # Exit fill. in_house_backend.py emits only "exit" then
                # "cost" (reason="exit leg cost") for a position close --
                # no separate "fill" event -- so match that shape exactly
                # rather than also emitting "fill" here.
                self._emit("exit", ts, side=side, quantity=qty, price=price, reason=self._pending_exit_reason)
                self._emit("cost", ts, side=side, quantity=qty, price=comm, reason="exit leg cost")
                self._open_trade = None
        elif order.status in (order.Canceled, order.Margin, order.Rejected):
            self._emit("gate_decision", str(self.data.datetime.datetime(0)),
                        reason=f"broker rejected order: status={order.getstatusname()}", passed=False)
        self._pending_order = None



def run_backtrader_events(
    symbol: str,
    bars: pd.DataFrame,
    registry: CanonicalParameterRegistry,
    calibration_overrides: Optional[Dict[str, Any]] = None,
    warmup: int = 60,
    starting_cash: float = 100_000.0,
) -> List[BackendEvent]:
    snapshot = canonical_parameter_snapshot(registry, calibration_overrides)
    config_hash = snapshot["effective_config_sha256"]

    frame = bars.copy()
    frame.columns = [c.lower() for c in frame.columns]
    source_bars = frame.reset_index(drop=True)
    # Backtrader's PandasData silently converts a tz-aware index to naive
    # UTC internally (self.data.datetime.datetime(0) then reports the UTC
    # clock face, e.g. 05:45 for what the source data calls 11:15+05:30).
    # tz_localize(None) strips the tzinfo but keeps the SAME clock-face
    # reading, so trading_hours_start/end comparisons in check_window() see
    # the same wall-clock time the in-house engine sees. Using tz_convert
    # instead would silently reproduce the UTC-shift bug this comment
    # exists to prevent.
    parsed = pd.to_datetime(frame["timestamp"])
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_localize(None)
    frame["datetime"] = parsed
    frame = frame.set_index("datetime")[["open", "high", "low", "close", "volume"]]

    events: List[BackendEvent] = []

    cerebro = bt.Cerebro(stdstats=False)
    data = bt.feeds.PandasData(dataname=frame)
    cerebro.adddata(data, name=symbol)
    cerebro.broker.setcash(starting_cash)
    cerebro.broker.addcommissioninfo(ParityCommission())
    # PaperBrokerAdapter.place_order() applies real percentage slippage
    # (round(market_price +/- market_price * slippage_fraction, 4)) to
    # every fill. Backtrader's own set_slippage_perc() does not reproduce
    # that formula exactly (a real fill compared 3006.6 vs the correct
    # 3006.3961 -- close enough to look like a genuine execution bug until
    # you check the arithmetic), so slippage is left at Backtrader's
    # default zero and applied explicitly in notify_order() instead, using
    # the identical formula PaperBrokerAdapter uses. Backtrader still owns
    # fill TIMING (next bar's open) and commission; only the slippage
    # adjustment on the reported price is applied manually.
    cerebro.addstrategy(
        Revision2ParityStrategy, symbol=symbol, registry=registry,
        calibration_overrides=calibration_overrides, warmup=warmup,
        config_hash=config_hash, events=events, total_bars=len(frame),
        source_bars=source_bars,
    )
    cerebro.run()
    return events
