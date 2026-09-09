"""Shared, chronological, multi-symbol portfolio engine.

This is the actual prerequisite for calibration meaning anything: running
48 independent single-symbol backtests and summing their P&L would
duplicate starting capital 48 times over and bypass every portfolio-level
risk cap (concurrent positions, gross exposure, sector exposure). This
module instead merges every symbol's real bars into ONE chronological
event stream and drives them all through ONE shared broker, ONE equity
curve, and ONE set of portfolio-level caps.

Per-symbol PA/ID/MPC state (their internal history/PID dictionaries) is
already keyed by symbol, so this reuses one instance of each box across
every symbol rather than duplicating them.
"""

from __future__ import annotations

import itertools
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from gates_framework import EntryDecisionEngine, EntrySignal, SafetyGateConfig, SystemState
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
from revision2.contracts import (
    EffectiveConfig,
    MarketSnapshot,
    SafetyContract,
    StartupCertificate,
    StartupNotCertifiedError,
)
from runtime.operating_mode import ExecutionGate, OperatingMode, PaperBrokerAdapter, RuntimeConfig, StartupGate

# See revision2/orchestrator.py's SNAPSHOT_LOOKBACK_BARS comment: PA only
# ever uses a bounded trailing window, so bounding what's handed to it here
# avoids O(n^2) total cost over a long, many-symbol run.
SNAPSHOT_LOOKBACK_BARS = 300

# Approximate NSE sector classification for this 48-symbol universe, hand-
# assigned from general market knowledge — NOT sourced from a live sector-
# classification feed. It exists so max_sector_exposure_fraction has a real
# grouping to enforce across symbols instead of behaving like a second
# per-symbol cap; treat the grouping itself as a documented approximation.
SECTOR_MAP: Dict[str, str] = {
    "AXISBANK": "Financials", "BAJFINANCE": "Financials", "BAJAJFINSV": "Financials",
    "HDFCBANK": "Financials", "HDFCLIFE": "Financials", "ICICIBANK": "Financials",
    "KOTAKBANK": "Financials", "SBILIFE": "Financials", "SBIN": "Financials",
    "SHRIRAMFIN": "Financials", "JIOFIN": "Financials",
    "HCLTECH": "IT", "INFY": "IT", "TCS": "IT", "TECHM": "IT", "WIPRO": "IT",
    "BAJAJ-AUTO": "Auto", "EICHERMOT": "Auto", "M&M": "Auto", "MARUTI": "Auto",
    "APOLLOHOSP": "Healthcare", "CIPLA": "Healthcare", "DRREDDY": "Healthcare",
    "SUNPHARMA": "Healthcare", "MAXHEALTH": "Healthcare",
    "ADANIENT": "Metals&Mining", "HINDALCO": "Metals&Mining", "JSWSTEEL": "Metals&Mining",
    "TATASTEEL": "Metals&Mining", "COALINDIA": "Metals&Mining",
    "NTPC": "Energy&Utilities", "ONGC": "Energy&Utilities", "POWERGRID": "Energy&Utilities",
    "RELIANCE": "Energy&Utilities",
    "ASIANPAINT": "Consumer", "HINDUNILVR": "Consumer", "ITC": "Consumer",
    "TATACONSUM": "Consumer", "TITAN": "Consumer", "TRENT": "Consumer",
    "BHARTIARTL": "Telecom",
    "GRASIM": "Infra&Cement", "LT": "Infra&Cement", "ULTRACEMCO": "Infra&Cement", "ADANIPORTS": "Infra&Cement",
    "INDIGO": "Aviation",
    "ETERNAL": "ConsumerInternet",
    "BEL": "CapitalGoods&Defense",
}


@dataclass
class _ClockEvent:
    timestamp: pd.Timestamp
    symbol: str
    bar_idx: int


class Revision2PortfolioOrchestrator:
    """Runs the Revision 2 pipeline across many symbols with one shared
    broker/equity/portfolio-cap state, in true chronological order."""

    def __init__(
        self,
        symbols: List[str],
        registry: Optional[CanonicalParameterRegistry] = None,
        calibration_overrides: Optional[Dict[str, Any]] = None,
        starting_equity: float = 1_000_000.0,
        sector_map: Optional[Dict[str, str]] = None,
    ):
        self.symbols = list(symbols)
        self.registry = registry or CanonicalParameterRegistry()
        overrides = calibration_overrides or {}
        errors = self.registry.validate_calibration_payload(overrides)
        if errors:
            raise ValueError(f"invalid calibration overrides: {errors}")

        values = {name: spec.default for name, spec in self.registry.params.items()}
        values.update(overrides)
        self.config = EffectiveConfig.build(values, registry_hash=self.registry.FROZEN_IDENTITY_SHA256)
        self.safety_contract = SafetyContract.from_registry(self.registry)
        self.sector_map = dict(sector_map) if sector_map is not None else dict(SECTOR_MAP)

        self.data_ingestion = DataIngestionBox()
        self.l2_certifier = L2DataCertifierBox()
        self.pa = PredictiveAnalyticsBox()
        self.id_box = IntelligentDiscriminationBox()
        self.mpc = ModelPredictiveControlBox()
        self.safety_gates_target = SafetyGatesTargetBox()
        self.position_manager = PositionManagerBox()
        self.p01d = P01DBox()
        self.unified_execution = UnifiedExecutionBox()
        self.broker = PaperBrokerAdapter(account_id="PAPER-R2-PORTFOLIO")
        self.entry_decision_engine = EntryDecisionEngine(config=self._build_safety_gate_config())

        # Safety control lifecycle dependencies (wired during run())
        self.cross_session_policy = None  # Set by caller before run()
        self.gate16_remediator = None     # Set by caller before run()
        self.event_ledger: List[Dict[str, Any]] = []  # Authoritative event record
        self._event_hash = "GENESIS"
        self.quarantine_mode = False
        self.trading_halted = False
        self._scheduled_flattens: Dict[str, Dict[str, Any]] = {}  # Positions to flatten next bar

        self.starting_equity = starting_equity
        self.consumed_parameters: set = set()
        self.completed_trades: List[Dict[str, Any]] = []
        self.open_trades: Dict[str, Dict[str, Any]] = {}
        # A signal at bar t can only create an order for the next event.
        # Keeping it separate from open_trades prevents future fills from
        # mutating portfolio state before their timestamp is reached.
        self.pending_entries: Dict[str, Dict[str, Any]] = {}
        self._equity_curve: List[float] = [starting_equity]  # realized-only, at trade completion; used by SafetyGatesTargetBox

        # Real chronological, marked-to-market portfolio equity — includes
        # unrealized P&L on every open position, sampled once per unique
        # timestamp across all symbols. This is deliberately additive and
        # parallel to _equity_curve above: it never feeds the orchestrator's
        # own trading decisions (changing that would shift trade counts
        # across every already-tested scenario), it exists purely so a
        # caller (the calibration acceptance gates, in particular) can
        # compute a real intratrade/mark-to-market drawdown and Sharpe
        # instead of one reconstructed only from completed-trade P&L.
        self._last_close: Dict[str, float] = {}
        self._mtm_equity_curve: List[Tuple[str, float]] = [("", starting_equity)]

        self.startup_certificate = self._certify_startup()
        if not self.startup_certificate.passed:
            raise StartupNotCertifiedError(
                f"startup certification failed: {'; '.join(self.startup_certificate.reasons)}"
            )

    # ---- setup helpers ----------------------------------------------------
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

    def _certify_startup(self) -> StartupCertificate:
        runtime_config = RuntimeConfig(
            operating_mode=OperatingMode.PAPER,
            live_trading_enabled=False,
            broker_account_id=self.broker.account_id,
            signing_key="",
            durable_db=True,
            runtime_parameters=self.config.as_dict(),
            parameter_registry=self.registry,
        )
        gate_report = StartupGate().certify_startup(runtime_config, self.broker, signing_key="", durable_db=True)
        return StartupCertificate.issue(gate_report, self.config.config_hash, self.safety_contract.contract_hash)

    def _record(self, trace) -> None:
        for use in trace:
            self.consumed_parameters.add(use.parameter)

    def _emit_event(self, event_type: str, timestamp: Any, **payload: Any) -> None:
        """Append a tamper-evident runtime event used by sealed reports."""
        material = {"event_type": event_type, "timestamp": str(timestamp),
                    "payload": payload, "prior_hash": self._event_hash}
        record_hash = hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()
        self.event_ledger.append({**material, "record_hash": record_hash})
        self._event_hash = record_hash

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

    def _sector_exposure_notional(self, sector: str) -> float:
        return sum(
            t["quantity"] * t["entry_price"] for sym, t in self.open_trades.items()
            if self.sector_map.get(sym, "Unclassified") == sector
        )

    # ---- exit handling ------------------------------------------------
    @staticmethod
    def _leg_cost(price: float, quantity: int, side: str) -> float:
        """Same brokerage/exchange/tax formula as _transaction_costs(),
        applied to one order leg — used to allocate real, per-trade net P&L
        instead of leaving costs as a portfolio-level-only aggregate that
        acceptance gates (profit factor, drawdown, expectancy) never see."""
        turnover = price * quantity
        cost = min(20.0, 0.0003 * turnover) + 0.0000345 * turnover
        if side == "SELL":
            cost += 0.00025 * turnover
        return cost

    def _execute_exit(self, symbol: str, timestamp, trade: Dict[str, Any], exit_price: float, reason: str, exit_bar_idx: Optional[int] = None) -> None:
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
            entry_cost = self._leg_cost(trade["entry_price"], trade["quantity"], trade["side"])
            exit_cost = self._leg_cost(result["filled_price"], trade["quantity"], close_side)
            trade_costs = entry_cost + exit_cost
            self.completed_trades.append({
                "symbol": symbol, "side": trade["side"], "entry_price": trade["entry_price"],
                "exit_price": result["filled_price"], "quantity": trade["quantity"],
                "entry_timestamp": trade["entry_timestamp"], "exit_timestamp": str(timestamp),
                "entry_bar_idx": trade.get("entry_bar_idx"), "exit_bar_idx": exit_bar_idx,
                "holding_bars": (
                    max(0, exit_bar_idx - trade["entry_bar_idx"])
                    if exit_bar_idx is not None and trade.get("entry_bar_idx") is not None else None
                ),
                "reason": reason, "pnl": pnl,
                # Net of this trade's own allocated brokerage/exchange/tax
                # (not slippage — that's already inside `pnl` via the fill
                # price itself). Acceptance gates and score_candidate use
                # this when present so profit factor / drawdown / net
                # expectancy reflect what the trade actually kept, not its
                # gross price move.
                "costs": trade_costs,
                "net_pnl": pnl - trade_costs,
            })
            self._equity_curve.append(self._equity())
            del self.open_trades[symbol]

    def _maybe_exit(self, symbol: str, timestamp, bar, signal, held_bars: int, bar_idx: int) -> None:
        trade = self.open_trades.get(symbol)
        if trade is None:
            return

        halt_dd = float(self.config.require("drawdown_halt_threshold"))
        if self._current_drawdown() >= halt_dd:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "forced_close_drawdown_halt", bar_idx)
            return

        exit_price, reason = None, None
        if trade["side"] == "BUY":
            if bar["open"] <= trade["stop_price"]:
                exit_price, reason = float(bar["open"]), "stop_gap"
            elif bar["open"] >= trade["target_price"]:
                exit_price, reason = float(bar["open"]), "target_gap"
            elif bar["low"] <= trade["stop_price"]:
                exit_price, reason = trade["stop_price"], "stop"
            elif bar["high"] >= trade["target_price"]:
                exit_price, reason = trade["target_price"], "target"
        else:
            if bar["open"] >= trade["stop_price"]:
                exit_price, reason = float(bar["open"]), "stop_gap"
            elif bar["open"] <= trade["target_price"]:
                exit_price, reason = float(bar["open"]), "target_gap"
            elif bar["high"] >= trade["stop_price"]:
                exit_price, reason = trade["stop_price"], "stop"
            elif bar["low"] <= trade["target_price"]:
                exit_price, reason = trade["target_price"], "target"

        if exit_price is not None:
            self._execute_exit(symbol, timestamp, trade, exit_price, reason, bar_idx)
            return

        if held_bars >= trade["minimum_hold_bars"] and (
            signal.exit_confidence < trade["exit_confidence_threshold"] or signal.quality_band == "red"
        ):
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "signal_exit", bar_idx)
            return

        if held_bars >= trade["maximum_hold_bars"]:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "max_hold", bar_idx)

    # ---- chronological clock -------------------------------------------
    @staticmethod
    def build_clock(symbol_bars: Dict[str, pd.DataFrame], warmup: int) -> List[_ClockEvent]:
        """Static and side-effect-free so a caller running many candidates
        against the SAME symbol_bars (a calibration run) can build this
        once and pass it to every candidate's run() via precomputed_clock=
        — at real (hundreds-of-thousands-of-bars, 48-symbol) scale this is
        a multi-million-object sort, and rebuilding it from scratch for
        every candidate is pure waste when the bars never change between
        them, only the parameters do."""
        events: List[_ClockEvent] = []
        for symbol, bars in symbol_bars.items():
            ts = pd.to_datetime(bars["timestamp"])
            for bar_idx in range(warmup, len(bars) - 1):
                events.append(_ClockEvent(ts.iloc[bar_idx], symbol, bar_idx))
        events.sort(key=lambda e: (e.timestamp, e.symbol))
        return events

    # ---- main loop --------------------------------------------------------
    def run(
        self, symbol_bars: Dict[str, pd.DataFrame], warmup: int = 60,
        precomputed_clock: Optional[List[_ClockEvent]] = None,
    ) -> Dict[str, Any]:
        normalized: Dict[str, pd.DataFrame] = {}
        for symbol, bars in symbol_bars.items():
            cols = {c.lower(): c for c in bars.columns}
            normalized[symbol] = bars.rename(columns={v: k for k, v in cols.items()})
        symbol_bars = normalized

        for symbol, bars in symbol_bars.items():
            self.pa.calibrate(symbol, bars.iloc[:warmup])

        funnel = {
            "bars_processed": 0, "pa_signals": 0, "id_approvals": 0, "id_rejections": 0,
            "mpc_plans": 0, "safety_approvals": 0, "safety_rejections": 0,
            # A total alone cannot distinguish a legitimate safety rejection
            # from a wiring/configuration defect.  Keep the authoritative
            # rejection reason at the point it is produced; this is reporting
            # only and does not change authorization behaviour.
            "safety_rejection_reasons": {},
            "gates_evaluated": 0, "gates_passed": 0, "gates_rejected": 0,
            "orders_submitted": 0, "exit_orders_submitted": 0, "fills": 0,
            "portfolio_cap_rejections": 0, "orders_queued": 0,
            "pending_orders_cancelled": 0, "cross_session_rejections": 0,
        }

        def record_safety_rejection(stage: str, reason: str) -> None:
            funnel["safety_rejections"] += 1
            key = f"{stage}: {reason or 'rejected'}"
            reasons = funnel["safety_rejection_reasons"]
            reasons[key] = reasons.get(key, 0) + 1
        max_concurrent = int(self.safety_contract.values["max_concurrent_positions"])
        max_gross_fraction = float(self.safety_contract.values["max_gross_exposure_fraction"])
        sector_cap_fraction = float(self.config.require("max_sector_exposure_fraction"))

        clock = precomputed_clock if precomputed_clock is not None else self.build_clock(symbol_bars, warmup)
        entry_bar_index: Dict[str, int] = {}  # symbol -> bar_idx of current open trade's entry

        for timestamp, tick_events in itertools.groupby(clock, key=lambda e: e.timestamp):
            tick_events = list(tick_events)
            # Fill only orders whose scheduled bar has now arrived.  This is
            # intentionally before any signal handling for this timestamp.
            for event in tick_events:
                # Gate16 remediation is deliberately delayed until this
                # symbol's next eligible bar.  No current-bar close or future
                # data is used to manufacture the exit price.
                scheduled = self._scheduled_flattens.get(event.symbol)
                if scheduled is not None and pd.Timestamp(timestamp) > pd.Timestamp(scheduled["after_timestamp"]):
                    trade = self.open_trades.get(event.symbol)
                    if trade is not None:
                        bar_open = float(symbol_bars[event.symbol].iloc[event.bar_idx]["open"])
                        factor = 0.999 if trade["side"] == "BUY" else 1.001
                        exit_price = bar_open * factor
                        self._emit_event("POSITION_FLATTENED", timestamp, symbol=event.symbol,
                                         next_bar_open=bar_open, adverse_factor=factor,
                                         exit_price=exit_price)
                        self._execute_exit(event.symbol, timestamp, trade, exit_price,
                                           "gate16_quarantine_flatten", event.bar_idx)
                    del self._scheduled_flattens[event.symbol]

                pending = self.pending_entries.get(event.symbol)
                if pending is None or pending["fill_bar_idx"] != event.bar_idx:
                    continue
                bars = symbol_bars[event.symbol]
                if self.cross_session_policy is not None:
                    allowed, reason = self.cross_session_policy.check_fill_time(
                        pd.Timestamp(pending["signal_timestamp"]).date().isoformat(), str(timestamp),
                    )
                    if not allowed:
                        del self.pending_entries[event.symbol]
                        funnel["pending_orders_cancelled"] += 1
                        self._emit_event("ORDER_CANCELLED", timestamp, symbol=event.symbol,
                                         order_id=getattr(pending["order"], "order_id", None), reason=reason)
                        continue
                fill = self.broker.place_order(
                    symbol=event.symbol, side=pending["order"].side,
                    quantity=pending["quantity"], order_type=pending["order"].order_type,
                    market_price=float(bars.iloc[event.bar_idx]["open"]),
                    config=self.safety_contract.as_dict(), parameter_registry=self.registry,
                )
                funnel["orders_submitted"] += 1
                del self.pending_entries[event.symbol]
                if fill["passed"]:
                    funnel["fills"] += 1
                    plan = pending["plan"]
                    self.open_trades[event.symbol] = {
                        "side": plan.side, "entry_price": fill["filled_price"],
                        "stop_price": plan.stop_price, "target_price": plan.target_price,
                        "quantity": pending["quantity"],
                        "minimum_hold_bars": plan.minimum_hold_bars,
                        "maximum_hold_bars": plan.maximum_hold_bars,
                        "exit_confidence_threshold": pending["decision"].timing_quality,
                        "signal_timestamp": pending["signal_timestamp"],
                        "entry_timestamp": str(timestamp), "entry_bar_idx": event.bar_idx,
                    }
                    entry_bar_index[event.symbol] = event.bar_idx
                    self._emit_event("FILL", timestamp, symbol=event.symbol,
                                     order_id=fill.get("order_id"), fill_price=fill["filled_price"],
                                     quantity=pending["quantity"])
                    # Gate16 is necessarily post-fill.  The fill remains in
                    # the book; a breach freezes later entries and is handled
                    # by the remediation lifecycle rather than being undone.
                    if self.gate16_remediator is not None:
                        breached = self.gate16_remediator.detect_breach(
                            timestamp=str(timestamp), symbol=event.symbol,
                            order_id=str(fill.get("order_id", f"fill_{event.bar_idx}_{event.symbol}")),
                            fill_id=str(fill.get("order_id", f"fill_{event.bar_idx}_{event.symbol}")),
                            intended_entry_price=float(plan.entry_price),
                            actual_fill_price=float(fill["filled_price"]),
                        )
                        if breached:
                            self.quarantine_mode = True
                            self.trading_halted = bool(self.gate16_remediator.trading_halted)
                            self._emit_event("GATE16_BREACH", timestamp, symbol=event.symbol,
                                             order_id=fill.get("order_id"), intended_entry_price=float(plan.entry_price),
                                             actual_fill_price=float(fill["filled_price"]))
                            self._emit_event("QUARANTINE_STARTED", timestamp)
                            for cancel_symbol, queued in list(self.pending_entries.items()):
                                del self.pending_entries[cancel_symbol]
                                funnel["pending_orders_cancelled"] += 1
                                self._emit_event("ORDER_CANCELLED", timestamp, symbol=cancel_symbol,
                                                 order_id=getattr(queued["order"], "order_id", None),
                                                 reason="GATE16_QUARANTINE")
                            for flatten_symbol in self.open_trades:
                                self._scheduled_flattens.setdefault(flatten_symbol, {
                                    "after_timestamp": str(timestamp),
                                })

                            # Step 4 is already handled above (lines 453-458 cancel all pending)
                            # No additional cancellation needed

                            # Step 5: Schedule all open positions for adverse flatten at next eligible bar
                            for flatten_symbol, trade in list(self.open_trades.items()):
                                self._scheduled_flattens[flatten_symbol] = {
                                    "entry_price": trade["entry_price"],
                                    "entry_bar_idx": trade["entry_bar_idx"],
                                    "side": trade["side"],
                                    "quantity": trade["quantity"],
                                }
                                # Use hash-linked _emit_event for consistency
                                self._emit_event(
                                    "FLATTEN_SCHEDULED", timestamp,
                                    symbol=flatten_symbol,
                                    entry_price=float(trade["entry_price"]),
                                    quantity=trade["quantity"],
                                    side=trade["side"],
                                )
            # Refresh every symbol trading at this exact timestamp before
            # sampling mark-to-market equity for the tick. Sampling inside
            # the per-symbol loop below (keyed off whichever symbol
            # happened to sort first into this tick) used `_last_close`
            # values that were still last-bar-stale for every other symbol
            # sharing the same timestamp — real 1-minute NSE data has all
            # 48 symbols on the same clock, so that made a whole bar's
            # price action show up one tick late in the curve, and
            # silently dropped it altogether for a shock landing on the
            # run's very last bar (no later tick left to "catch up" on
            # it). See tests/test_revision2_portfolio.py.
            for event in tick_events:
                self._last_close[event.symbol] = float(symbol_bars[event.symbol].iloc[event.bar_idx]["close"])
            self._mtm_equity_curve.append((str(timestamp), self._mark_to_market_equity()))

            for event in tick_events:
                funnel["bars_processed"] += 1
                symbol, bar_idx = event.symbol, event.bar_idx
                bars = symbol_bars[symbol]

                # Step 6: Execute any scheduled adverse flattens for this symbol
                if self.quarantine_mode and symbol in self._scheduled_flattens:
                    flatten_plan = self._scheduled_flattens[symbol]
                    trade = self.open_trades.get(symbol)
                    if trade is None:
                        del self._scheduled_flattens[symbol]
                        continue
                    bar_open = float(bars.iloc[bar_idx]["open"])
                    # Apply adverse factor: -10bps for long, +10bps for short
                    adverse_factor = 0.999 if trade["side"] == "BUY" else 1.001
                    exit_price = bar_open * adverse_factor

                    # Record flatten event (hash-linked)
                    self._emit_event(
                        "POSITION_FLATTENED", timestamp,
                        symbol=symbol,
                        exit_price=exit_price,
                        adverse_factor=adverse_factor,
                        quantity=trade["quantity"],
                    )

                    # Execute adverse flatten (same path as normal exits)
                    self._execute_exit(
                        symbol=symbol,
                        timestamp=timestamp,
                        trade=trade,
                        exit_price=exit_price,
                        reason="quarantine_flatten",
                        exit_bar_idx=bar_idx,
                    )

                    # Remove from scheduled flattens
                    del self._scheduled_flattens[symbol]
                    funnel["quarantine_flattens"] = funnel.get("quarantine_flattens", 0) + 1

                admitted, _, trace = self.data_ingestion.admit(symbol, self.config)
                self._record(trace)
                if not admitted:
                    continue
                certified, _, trace = self.l2_certifier.certify(bars.iloc[max(0, bar_idx - 5):bar_idx + 1], self.config)
                self._record(trace)
                if not certified:
                    continue
                in_window, _, trace = self.unified_execution.check_window(str(timestamp), self.config)
                self._record(trace)

                snapshot = MarketSnapshot(
                    symbol=symbol, timestamp=str(timestamp),
                    bars=bars.iloc[max(0, bar_idx - SNAPSHOT_LOOKBACK_BARS + 1):bar_idx + 1],
                )
                signal, trace = self.pa.evaluate(snapshot, self.config)
                self._record(trace)
                funnel["pa_signals"] += 1

                held = bar_idx - entry_bar_index.get(symbol, bar_idx)
                self._maybe_exit(symbol, timestamp, bars.iloc[bar_idx], signal, held, bar_idx)

                if (self.quarantine_mode or self.trading_halted or symbol in self.open_trades
                        or symbol in self.pending_entries or not in_window):
                    continue

                decision, trace = self.id_box.evaluate(signal, self.config)
                self._record(trace)
                if not decision.approved:
                    funnel["id_rejections"] += 1
                    continue
                funnel["id_approvals"] += 1

                atr = signal.volatility * bars.iloc[bar_idx]["close"]
                # The next open is unknown at t.  Construct the order using
                # the completed bar's close as its risk reference; actual
                # execution happens only at the next event's open above.
                plan, pid_info, trace = self.mpc.build_plan(signal, decision, float(bars.iloc[bar_idx]["close"]), atr, self.config)
                self._record(trace)
                if plan is None:
                    continue
                funnel["mpc_plans"] += 1

                approved, safety_reason, size_mult, trace = self.safety_gates_target.evaluate_pre_sizing(self._equity_curve, self.config)
                self._record(trace)
                if not approved:
                    record_safety_rejection("SafetyGatesTargetBox.pre_sizing", safety_reason)
                    continue
                size_mult *= pid_info["entry_timing_multiplier"]

                # Portfolio-level caps — the actual point of this module: these
                # cannot be evaluated per-symbol, they need the shared state.
                if len(self.open_trades) + len(self.pending_entries) >= max_concurrent:
                    funnel["portfolio_cap_rejections"] += 1
                    continue
                proposed_notional = plan.entry_price * 1  # provisional; re-checked with real quantity below
                equity_now = self._equity()
                sector = self.sector_map.get(symbol, "Unclassified")
                if self._sector_exposure_notional(sector) + proposed_notional > equity_now * sector_cap_fraction:
                    funnel["portfolio_cap_rejections"] += 1
                    continue
                if self._gross_exposure_notional() + proposed_notional > equity_now * max_gross_fraction:
                    funnel["portfolio_cap_rejections"] += 1
                    continue

                quantity, trace = self.position_manager.size(
                    plan, equity_now, size_mult, self.config,
                    open_positions_count=len(self.open_trades) + len(self.pending_entries),
                    symbol_positions_count=1 if symbol in self.open_trades else 0,
                )
                self._record(trace)
                if quantity <= 0:
                    continue

                # Re-check gross/sector exposure with the real notional now that
                # quantity is known (the provisional check above used qty=1).
                real_notional = plan.entry_price * quantity
                if self._sector_exposure_notional(sector) + real_notional > equity_now * sector_cap_fraction:
                    funnel["portfolio_cap_rejections"] += 1
                    continue
                if self._gross_exposure_notional() + real_notional > equity_now * max_gross_fraction:
                    funnel["portfolio_cap_rejections"] += 1
                    continue

                post_ok, safety_reason, trace = self.safety_gates_target.evaluate_post_sizing(self._equity_curve, plan, quantity, self.config)
                self._record(trace)
                if not post_ok:
                    record_safety_rejection("SafetyGatesTargetBox.post_sizing", safety_reason)
                    continue
                funnel["safety_approvals"] += 1

                order, trace = self.p01d.create_order(symbol, plan, quantity, self.config)
                self._record(trace)
                if order is None:
                    continue

                state = SystemState(
                    portfolio_value=equity_now,
                    current_dd_percent=self._current_drawdown(),
                    current_lambda=self._gross_exposure_notional() / max(equity_now, 1.0),
                    daily_realized_loss=max(0.0, max(self._equity_curve) - equity_now),
                    daily_unrealized_loss=0.0,
                    open_positions_count=len(self.open_trades) + len(self.pending_entries),
                    open_positions=[type("P", (), {"position_notional": t["quantity"] * t["entry_price"]})() for t in self.open_trades.values()],
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
                    current_time = datetime.fromisoformat(str(timestamp))
                except Exception:
                    current_time = datetime.now()
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
                    record_safety_rejection("ExecutionGate.pre_submit", str(gate2.get("reason", "rejected")))
                    continue

                # An intraday intent must have a same-session next eligible
                # bar for this exact symbol.  The policy owns the date check;
                # the orchestrator owns the real DataFrame/bar-index inputs.
                if self.cross_session_policy is not None:
                    allowed, reason = self.cross_session_policy.check_pre_submission(
                        symbol=symbol, decision_timestamp=str(timestamp),
                        current_bar_index=bar_idx, all_bars=symbol_bars,
                    )
                    if not allowed:
                        funnel["cross_session_rejections"] += 1
                        self._emit_event(
                            "ORDER_REJECTED", timestamp,
                            symbol=symbol, order_id=getattr(order, "order_id", None),
                            reason=reason,
                        )
                        continue

                self.pending_entries[symbol] = {
                    "order": order, "plan": plan, "quantity": quantity,
                    "decision": decision, "signal_timestamp": str(timestamp),
                    "fill_bar_idx": bar_idx + 1,
                }
                funnel["orders_queued"] += 1

                # Step 7: Emit ORDER_SUBMITTED event
                self._emit_event(
                    "ORDER_SUBMITTED", timestamp,
                    symbol=symbol,
                    order_id=getattr(order, "order_id", None),
                    entry_price=float(plan.entry_price),
                    quantity=quantity,
                    side=order.side,
                )

        # The clock intentionally has no event after the final complete bar;
        # a queued order without its scheduled event must be cancelled rather
        # than fabricated as a future fill.
        for symbol, pending in list(self.pending_entries.items()):
            self._emit_event("ORDER_CANCELLED", "END_OF_RUN", symbol=symbol,
                             order_id=getattr(pending["order"], "order_id", None),
                             reason="NO_ELIGIBLE_FILL_BAR")
        funnel["pending_orders_cancelled"] = len(self.pending_entries)
        self.pending_entries.clear()

        # End-of-run reconciliation for every symbol still open.
        for symbol in list(self.open_trades.keys()):
            bars = symbol_bars[symbol]
            final_close = float(bars.iloc[len(bars) - 1]["close"])
            self._execute_exit(symbol, bars.iloc[len(bars) - 1].get("timestamp", ""), self.open_trades[symbol], final_close, "end_of_run_reconciliation", len(bars) - 1)

        gross_pnl = self.broker.realized_pnl
        assert abs(gross_pnl - sum(t["pnl"] for t in self.completed_trades)) < 1e-6

        costs = self._transaction_costs()
        net_pnl = gross_pnl - costs["total_cost"]

        target_names = set(self.registry.params)
        safety_names = set(self.registry.safety_params)
        coverage_target = sorted(target_names & self.consumed_parameters)
        safety_consumed = sorted(safety_names) if funnel["orders_submitted"] or funnel["exit_orders_submitted"] or funnel["fills"] else []

        mtm_values = [e for _, e in self._mtm_equity_curve]
        mtm_peak = mtm_values[0]
        mtm_max_drawdown_fraction = 0.0
        for v in mtm_values:
            mtm_peak = max(mtm_peak, v)
            if mtm_peak > 0:
                mtm_max_drawdown_fraction = max(mtm_max_drawdown_fraction, (mtm_peak - v) / mtm_peak)
        safety_violations = sum(1 for t in self.completed_trades if t["reason"] == "forced_close_drawdown_halt")

        # Step 8: Enforce exact reconciliation
        # After EOD flatten, unrealized P&L must be zero
        unrealized_pnl = self._mark_to_market_equity() - self.starting_equity - self.broker.realized_pnl
        reconciliation_exact = (
            not self.open_trades and
            not self.pending_entries and
            abs(unrealized_pnl) < 0.01  # Must be near zero after EOD flatten
        )

        self._emit_event("RECONCILIATION_COMPLETED", "END_OF_RUN",
                         exact=reconciliation_exact, open_positions=len(self.open_trades),
                         pending_orders=len(self.pending_entries), realized_pnl=self.broker.realized_pnl,
                         unrealized_pnl=unrealized_pnl, quarantine_mode=self.quarantine_mode)

        if not reconciliation_exact:
            self.trading_halted = True
            raise RuntimeError("final portfolio reconciliation failed")

        return {
            **funnel,
            "symbols": self.symbols,
            "completed_trades": len(self.completed_trades),
            "gross_pnl": gross_pnl,
            **costs,
            "net_pnl": net_pnl,
            "ending_equity": self.starting_equity + net_pnl,
            "config_hash": self.config.config_hash,
            "safety_contract_hash": self.safety_contract.contract_hash,
            "parameter_coverage": {
                "target_total": len(target_names), "target_consumed": len(coverage_target),
                "target_missing": sorted(target_names - self.consumed_parameters),
                "safety_total": len(safety_names), "safety_consumed": len(safety_consumed),
            },
            "trades": self.completed_trades,
            # Real chronological, marked-to-market portfolio equity — see
            # the constructor comment on self._mtm_equity_curve. Downstream
            # consumers (CalibrationSupervisor's acceptance gates) should use
            # mtm_max_drawdown_fraction, not a curve reconstructed only from
            # completed-trade P&L.
            "mtm_equity_curve": self._mtm_equity_curve,
            "mtm_max_drawdown_fraction": mtm_max_drawdown_fraction,
            "safety_violations": safety_violations,
            "status": "REMEDIATION_REQUIRED" if self.quarantine_mode else "PASSED",
            "event_ledger": self.event_ledger,
        }

    def _transaction_costs(self) -> Dict[str, float]:
        slippage_cost = brokerage = exchange_charges = taxes = 0.0
        for f in self.broker.fills:
            turnover = f["price"] * f["quantity"]
            market_price = f.get("market_price")
            if market_price is not None:
                slippage_cost += abs(f["price"] - market_price) * f["quantity"]
            brokerage += min(20.0, 0.0003 * turnover)
            exchange_charges += 0.0000345 * turnover
            if f["side"] == "SELL":
                taxes += 0.00025 * turnover
        total_cost = brokerage + exchange_charges + taxes
        return {
            "slippage_cost": round(slippage_cost, 4), "brokerage": round(brokerage, 4),
            "exchange_charges": round(exchange_charges, 4), "taxes": round(taxes, 4),
            "total_cost": round(total_cost, 4),
        }
