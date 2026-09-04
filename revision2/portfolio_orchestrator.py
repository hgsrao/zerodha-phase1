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
from revision2.data_certification import certify_bars
from revision2.transaction_costs import equity_intraday_leg
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

        self.starting_equity = starting_equity
        self.consumed_parameters: set = set()
        self.completed_trades: List[Dict[str, Any]] = []
        self.open_trades: Dict[str, Dict[str, Any]] = {}
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
        self._active_trading_date = None
        self._day_start_equity = starting_equity

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
            max_market_data_age_seconds=int(v["max_market_data_age_seconds"]),
            drawdown_derate_threshold=float(v["drawdown_derate_threshold"]),
            drawdown_derate_multiplier=float(v["drawdown_derate_multiplier"]),
            order_dedup_window_seconds=int(v["order_dedup_window_seconds"]),
            order_timeout_seconds=int(v["order_timeout_seconds_execution"]),
            max_reconciliation_qty_diff=int(v["max_reconciliation_qty_diff"]),
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
        return equity_intraday_leg(price, quantity, side).total

    def _execute_exit(self, symbol: str, timestamp, trade: Dict[str, Any], exit_price: float, reason: str) -> None:
        close_side = "SELL" if trade["side"] == "BUY" else "BUY"
        result = self.broker.place_order(
            symbol=symbol, side=close_side, quantity=trade["quantity"], order_type="MARKET",
            market_price=exit_price, config=self.safety_contract.as_dict(), parameter_registry=self.registry,
            event_time=str(timestamp),
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

    def _maybe_exit(self, symbol: str, timestamp, bar, signal, held_bars: int, session_last_bar: bool = False) -> None:
        trade = self.open_trades.get(symbol)
        if trade is None:
            return

        halt_dd = float(self.config.require("drawdown_halt_threshold"))
        if self._current_drawdown() >= halt_dd:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "forced_close_drawdown_halt")
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
            self._execute_exit(symbol, timestamp, trade, exit_price, reason)
            return

        if session_last_bar:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "mis_session_close")
            return

        if held_bars >= trade["minimum_hold_bars"] and (
            signal.exit_confidence < trade["exit_confidence_threshold"] or signal.quality_band == "red"
        ):
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "signal_exit")
            return

        if held_bars >= trade["maximum_hold_bars"]:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "max_hold")

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
        certification: Dict[str, Dict[str, int]] = {}
        for symbol, bars in symbol_bars.items():
            normalized[symbol], certification[symbol] = certify_bars(bars)
        symbol_bars = normalized

        for symbol, bars in symbol_bars.items():
            self.pa.calibrate(symbol, bars.iloc[:warmup])

        funnel = {
            "bars_processed": 0, "pa_signals": 0, "id_approvals": 0, "id_rejections": 0,
            "mpc_plans": 0, "safety_approvals": 0, "safety_rejections": 0,
            "gates_evaluated": 0, "gates_passed": 0, "gates_rejected": 0,
            "orders_submitted": 0, "exit_orders_submitted": 0, "fills": 0,
            "portfolio_cap_rejections": 0,
        }
        max_concurrent = int(self.safety_contract.values["max_concurrent_positions"])
        max_gross_fraction = float(self.safety_contract.values["max_gross_exposure_fraction"])
        sector_cap_fraction = float(self.registry.get("max_sector_exposure_fraction").default)

        clock = precomputed_clock if precomputed_clock is not None else self.build_clock(symbol_bars, warmup)
        entry_bar_index: Dict[str, int] = {}  # symbol -> bar_idx of current open trade's entry

        for timestamp, tick_events in itertools.groupby(clock, key=lambda e: e.timestamp):
            tick_events = list(tick_events)
            event_ts = pd.Timestamp(timestamp)
            if self._active_trading_date != event_ts.date():
                self._active_trading_date = event_ts.date()
                self._day_start_equity = self._equity()
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
                next_ts = pd.Timestamp(bars.iloc[bar_idx + 1]["timestamp"])
                end_time = str(self.config.require("trading_hours_end"))
                session_last_bar = next_ts.date() != event_ts.date() or event_ts.strftime("%H:%M") >= end_time

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
                self._maybe_exit(symbol, timestamp, bars.iloc[bar_idx], signal, held, session_last_bar=session_last_bar)

                if symbol in self.open_trades or not in_window:
                    continue
                cutoff = str(self.safety_contract.values["no_entry_cutoff_time"])
                if next_ts.date() != event_ts.date() or next_ts.strftime("%H:%M") >= cutoff:
                    continue

                decision, trace = self.id_box.evaluate(signal, self.config)
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

                # Portfolio-level caps — the actual point of this module: these
                # cannot be evaluated per-symbol, they need the shared state.
                if len(self.open_trades) >= max_concurrent:
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
                    open_positions_count=len(self.open_trades),
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
                    portfolio_value=equity_now,
                    current_dd_percent=self._current_drawdown(),
                    current_lambda=self._gross_exposure_notional() / max(equity_now, 1.0),
                    daily_realized_loss=max(0.0, self._day_start_equity - equity_now),
                    daily_unrealized_loss=0.0,
                    open_positions_count=len(self.open_trades),
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
                    current_time = datetime.fromisoformat(str(next_ts))
                except Exception:
                    current_time = datetime.now()
                gate_result = self.entry_decision_engine.evaluate_pre_submit(
                    state, signal=entry_signal, current_time=current_time, proposed_quantity=quantity,
                    target_price=plan.entry_price, fill_price=plan.entry_price, expected_qty=quantity,
                    actual_qty=quantity, symbol=symbol, proposed_notional=real_notional,
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
                    event_time=str(bars.iloc[bar_idx + 1].get("timestamp", bar_idx + 1)),
                )
                funnel["orders_submitted"] += 1
                if fill["passed"]:
                    funnel["fills"] += 1
                    post_fill = self.entry_decision_engine.evaluate_post_fill(
                        quantity, int(fill["filled_quantity"]), next_open, float(fill["filled_price"]),
                    )
                    if not post_fill["passed"]:
                        close_side = "SELL" if order.side == "BUY" else "BUY"
                        corrective = self.broker.place_order(
                            symbol=symbol, side=close_side, quantity=int(fill["filled_quantity"]),
                            order_type="MARKET", market_price=next_open,
                            config=self.safety_contract.as_dict(), parameter_registry=self.registry,
                            event_time=str(bars.iloc[bar_idx + 1].get("timestamp", bar_idx + 1)),
                        )
                        funnel["exit_orders_submitted"] += 1
                        if corrective["passed"]:
                            funnel["fills"] += 1
                            pnl = (
                                (corrective["filled_price"] - fill["filled_price"]) * int(fill["filled_quantity"])
                                if order.side == "BUY" else
                                (fill["filled_price"] - corrective["filled_price"]) * int(fill["filled_quantity"])
                            )
                            trade_costs = (
                                self._leg_cost(fill["filled_price"], int(fill["filled_quantity"]), order.side)
                                + self._leg_cost(corrective["filled_price"], int(fill["filled_quantity"]), close_side)
                            )
                            fill_timestamp = str(bars.iloc[bar_idx + 1].get("timestamp", bar_idx + 1))
                            self.completed_trades.append({
                                "symbol": symbol, "side": order.side,
                                "entry_price": fill["filled_price"], "exit_price": corrective["filled_price"],
                                "quantity": int(fill["filled_quantity"]),
                                "entry_timestamp": fill_timestamp, "exit_timestamp": fill_timestamp,
                                "reason": "post_fill_safety_flatten", "pnl": pnl,
                                "costs": trade_costs, "net_pnl": pnl - trade_costs,
                            })
                        funnel["gates_rejected"] += 1
                        continue
                    self.open_trades[symbol] = {
                        "side": plan.side, "entry_price": fill["filled_price"], "stop_price": plan.stop_price,
                        "target_price": plan.target_price, "quantity": quantity,
                        "minimum_hold_bars": plan.minimum_hold_bars, "maximum_hold_bars": plan.maximum_hold_bars,
                        "exit_confidence_threshold": decision.timing_quality, "entry_timestamp": str(next_ts),
                    }
                    entry_bar_index[symbol] = bar_idx + 1

        # End-of-run reconciliation for every symbol still open.
        for symbol in list(self.open_trades.keys()):
            bars = symbol_bars[symbol]
            final_close = float(bars.iloc[len(bars) - 1]["close"])
            self._execute_exit(symbol, bars.iloc[len(bars) - 1].get("timestamp", ""), self.open_trades[symbol], final_close, "end_of_run_reconciliation")

        gross_pnl = self.broker.realized_pnl
        assert abs(gross_pnl - sum(t["pnl"] for t in self.completed_trades)) < 1e-6

        costs = self._transaction_costs()
        net_pnl = gross_pnl - costs["total_cost"]
        ledger_net_pnl = sum(t["net_pnl"] for t in self.completed_trades)
        assert abs(net_pnl - ledger_net_pnl) < 1e-3, "net_pnl / trade-ledger mismatch"

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

        return {
            **funnel,
            "symbols": self.symbols,
            "completed_trades": len(self.completed_trades),
            "gross_pnl": gross_pnl,
            **costs,
            "net_pnl": net_pnl,
            "ledger_net_pnl": ledger_net_pnl,
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
            "data_certification": certification,
        }

    def _transaction_costs(self) -> Dict[str, float]:
        slippage_cost = brokerage = exchange_charges = stt = sebi = stamp = gst = 0.0
        for f in self.broker.fills:
            turnover = f["price"] * f["quantity"]
            market_price = f.get("market_price")
            if market_price is not None:
                slippage_cost += abs(f["price"] - market_price) * f["quantity"]
            leg = equity_intraday_leg(f["price"], f["quantity"], f["side"])
            brokerage += leg.brokerage
            exchange_charges += leg.exchange_transaction_charge
            stt += leg.stt
            sebi += leg.sebi_charge
            stamp += leg.stamp_duty
            gst += leg.gst
        taxes = stt + sebi + stamp + gst
        total_cost = brokerage + exchange_charges + taxes
        return {
            "slippage_cost": round(slippage_cost, 4), "brokerage": round(brokerage, 4),
            "exchange_charges": round(exchange_charges, 4), "taxes": round(taxes, 4),
            "stt": round(stt, 4), "sebi_charges": round(sebi, 4),
            "stamp_duty": round(stamp, 4), "gst": round(gst, 4),
            "total_cost": round(total_cost, 4),
        }
