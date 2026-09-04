"""Ties DataIngestion -> L2DataCertifier -> PA -> ID -> MPC -> SafetyGates ->
PositionManager -> P01D -> [18-gate EntryDecisionEngine] -> UnifiedExecution
-> PaperBrokerAdapter into one real, bar-by-bar pipeline for a single symbol.

Every stage returns a typed, explicit result. Nothing here reports "READY"
without having actually run a calculation.

Exit priority (never suppressed by minimum_hold_bars except the signal exit):
  forced close (drawdown halt) > protective stop > target > signal exit
  (gated by minimum_hold_bars) > maximum hold.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

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
    PASignal,
    SafetyContract,
    StartupCertificate,
    StartupNotCertifiedError,
)
from revision2.data_certification import certify_bars
from revision2.transaction_costs import equity_intraday_leg
from runtime.operating_mode import ExecutionGate, OperatingMode, PaperBrokerAdapter, RuntimeConfig, StartupGate

# PA only ever looks at a bounded trailing window (its largest lookback
# parameter — atr/momentum/vwap period — maxes out at 30 bars in the
# registry). Handing it the *entire* history-so-far every bar, as
# `bars.iloc[:bar_idx + 1]` did, makes both the slice and PA's own
# `.to_numpy()` conversion O(bar_idx) work repeated on every single bar —
# O(n^2) total over a run, which is invisible on a short backtest and
# becomes the dominant cost at real (hundreds-of-thousands-of-bars) scale.
# 300 bars is generous headroom above any registry lookback max.
SNAPSHOT_LOOKBACK_BARS = 300


class Revision2Orchestrator:
    def __init__(
        self,
        symbol: str,
        registry: Optional[CanonicalParameterRegistry] = None,
        calibration_overrides: Optional[Dict[str, Any]] = None,
        starting_equity: float = 100000.0,
    ):
        self.symbol = symbol
        self.registry = registry or CanonicalParameterRegistry()
        overrides = calibration_overrides or {}
        errors = self.registry.validate_calibration_payload(overrides)
        if errors:
            raise ValueError(f"invalid calibration overrides: {errors}")

        values = {name: spec.default for name, spec in self.registry.params.items()}
        values.update(overrides)
        self.config = EffectiveConfig.build(values, registry_hash=self.registry.FROZEN_IDENTITY_SHA256)
        self.safety_contract = SafetyContract.from_registry(self.registry)
        self.entry_decision_engine = EntryDecisionEngine(config=self._build_safety_gate_config())

        self.data_ingestion = DataIngestionBox()
        self.l2_certifier = L2DataCertifierBox()
        self.pa = PredictiveAnalyticsBox()
        self.id_box = IntelligentDiscriminationBox()
        self.mpc = ModelPredictiveControlBox()
        self.safety_gates_target = SafetyGatesTargetBox()
        self.position_manager = PositionManagerBox()
        self.p01d = P01DBox()
        self.unified_execution = UnifiedExecutionBox()
        self.broker = PaperBrokerAdapter(account_id="PAPER-R2")

        self.starting_equity = starting_equity
        self.consumed_parameters: set = set()
        self.completed_trades: List[Dict[str, Any]] = []
        self._open_trade: Optional[Dict[str, Any]] = None
        self._equity_curve: List[float] = [starting_equity]
        self._active_trading_date = None
        self._day_start_equity = starting_equity

        # No certificate, no run: StartupGate is invoked here, before any
        # data is touched, and a failing certificate raises immediately —
        # there is no code path in run() that can be reached without one.
        self.startup_certificate = self._certify_startup()
        if not self.startup_certificate.passed:
            raise StartupNotCertifiedError(
                f"startup certification failed: {'; '.join(self.startup_certificate.reasons)}"
            )

    def _certify_startup(self) -> StartupCertificate:
        runtime_config = RuntimeConfig(
            operating_mode=OperatingMode.PAPER,
            live_trading_enabled=False,
            broker_account_id=self.broker.account_id,
            signing_key="",  # only required for LIVE mode
            durable_db=True,
            runtime_parameters=self.config.as_dict(),
            parameter_registry=self.registry,
        )
        gate_report = StartupGate().certify_startup(
            runtime_config, self.broker, signing_key="", durable_db=True,
        )
        return StartupCertificate.issue(gate_report, self.config.config_hash, self.safety_contract.contract_hash)

    # ---- helpers --------------------------------------------------------
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
            # max_broker_offline_seconds and force_close_time have no
            # canonical registry equivalent yet — kept at gates_framework's
            # own defaults (documented gap, not silently invented here).
        )

    def _equity(self) -> float:
        return self.starting_equity + self.broker.realized_pnl

    def _current_drawdown(self) -> float:
        peak = max(self._equity_curve) if self._equity_curve else self.starting_equity
        current = self._equity_curve[-1] if self._equity_curve else self.starting_equity
        return (peak - current) / peak if peak > 0 else 0.0

    def _record(self, trace) -> None:
        for use in trace:
            self.consumed_parameters.add(use.parameter)

    def _parse_timestamp(self, timestamp: str) -> datetime:
        try:
            return datetime.fromisoformat(str(timestamp))
        except Exception:
            return datetime.now()

    def _build_system_state(self) -> SystemState:
        peak = max(self._equity_curve) if self._equity_curve else self.starting_equity
        current = self._equity_curve[-1] if self._equity_curve else self.starting_equity
        return SystemState(
            portfolio_value=current,
            current_dd_percent=self._current_drawdown(),
            # Single-symbol simplification: a true portfolio lambda needs
            # cross-symbol correlation data that doesn't exist until the
            # 48-symbol scale-up. Documented gap, not a silent guess.
            current_lambda=0.0,
            daily_realized_loss=max(0.0, self._day_start_equity - current),
            daily_unrealized_loss=0.0,
            open_positions_count=0,  # entry path only runs while flat
            open_positions=[],
            market_data_age_seconds=0,
            broker_connected=True,
            broker_offline_seconds=0,
            kill_switch_active=not bool(self.safety_contract.values["kill_switch_enabled"]),
            circuit_breaker_triggered=False,
        )

    def _execute_exit(self, bar_idx: int, trade: Dict[str, Any], exit_price: float, reason: str, funnel: Dict[str, int], event_time: Optional[str] = None) -> None:
        close_side = "SELL" if trade["side"] == "BUY" else "BUY"
        result = self.broker.place_order(
            symbol=self.symbol,
            side=close_side,
            quantity=trade["quantity"],
            order_type="MARKET",
            market_price=exit_price,
            config=self.safety_contract.as_dict(),
            parameter_registry=self.registry,
            event_time=event_time,
        )
        funnel["exit_orders_submitted"] += 1
        if result["passed"]:
            funnel["fills"] += 1
            pnl = (
                (result["filled_price"] - trade["entry_price"]) * trade["quantity"]
                if trade["side"] == "BUY"
                else (trade["entry_price"] - result["filled_price"]) * trade["quantity"]
            )
            entry_cost = equity_intraday_leg(trade["entry_price"], trade["quantity"], trade["side"]).total
            exit_cost = equity_intraday_leg(result["filled_price"], trade["quantity"], close_side).total
            trade_costs = entry_cost + exit_cost
            self.completed_trades.append({
                "side": trade["side"],
                "entry_price": trade["entry_price"],
                "exit_price": result["filled_price"],
                "quantity": trade["quantity"],
                "entry_bar_idx": trade["entry_bar_idx"],
                "exit_bar_idx": bar_idx,
                "entry_timestamp": trade["entry_timestamp"],
                "exit_timestamp": event_time or str(bar_idx),
                "reason": reason,
                "pnl": pnl,
                "costs": trade_costs,
                "net_pnl": pnl - trade_costs,
            })
            self._equity_curve.append(self._equity())
            self._open_trade = None

    def _maybe_exit(self, bar_idx: int, bars: pd.DataFrame, signal: PASignal, funnel: Dict[str, int], session_last_bar: bool = False) -> None:
        trade = self._open_trade
        if trade is None:
            return
        bar = bars.iloc[bar_idx]
        held = bar_idx - trade["entry_bar_idx"]

        # Priority 1: forced close — never suppressed by minimum_hold_bars.
        halt_dd = float(self.config.require("drawdown_halt_threshold"))
        if self._current_drawdown() >= halt_dd:
            self._execute_exit(bar_idx, trade, float(bar["close"]), "forced_close_drawdown_halt", funnel, str(bar.get("timestamp", bar_idx)))
            return

        exit_price = None
        reason = None
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
            # Priorities 2-3: protective stop and target — never suppressed
            # by minimum_hold_bars either. A stop or a hit target is not a
            # discretionary decision.
            self._execute_exit(bar_idx, trade, exit_price, reason, funnel, str(bar.get("timestamp", bar_idx)))
            return

        if session_last_bar:
            self._execute_exit(bar_idx, trade, float(bar["close"]), "mis_session_close", funnel, str(bar.get("timestamp", bar_idx)))
            return

        # Priority 4: discretionary signal exit — the only exit gated by
        # minimum_hold_bars. Fires on either: the exit_confidence_threshold
        # ID set at entry time being breached by PA's freshly recomputed
        # exit_confidence, or the current bar's PA read collapsing into the
        # red quality band (red_threshold's own, independent effect — it
        # can't ever flip an *entry* approval at default settings, since
        # red's range sits entirely under entry_confidence_threshold's
        # default, so it needs this separate path to be causal at all).
        if held >= trade["minimum_hold_bars"] and (
            signal.exit_confidence < trade["exit_confidence_threshold"] or signal.quality_band == "red"
        ):
            self._execute_exit(bar_idx, trade, float(bar["close"]), "signal_exit", funnel, str(bar.get("timestamp", bar_idx)))
            return

        # Priority 5: maximum hold — forced regardless of minimum_hold_bars
        # (structurally always >= it anyway, by the registry's own ranges).
        if held >= trade["maximum_hold_bars"]:
            self._execute_exit(bar_idx, trade, float(bar["close"]), "max_hold", funnel, str(bar.get("timestamp", bar_idx)))

    def _transaction_costs(self) -> Dict[str, float]:
        """Slippage is already embedded in gross_pnl via the fill price
        itself; it's reported here for visibility only and is NOT
        subtracted again in total_cost. Brokerage/exchange/tax figures use
        standard Indian discount-broker intraday-equity approximations —
        there is no canonical registry parameter for them yet, so this is a
        documented modeling choice, not a calibrated cost."""
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
            "slippage_cost": round(slippage_cost, 4),
            "brokerage": round(brokerage, 4),
            "exchange_charges": round(exchange_charges, 4),
            "taxes": round(taxes, 4),
            "stt": round(stt, 4), "sebi_charges": round(sebi, 4),
            "stamp_duty": round(stamp, 4), "gst": round(gst, 4),
            "total_cost": round(total_cost, 4),
        }

    # ---- main loop --------------------------------------------------------
    def run(self, bars: pd.DataFrame, warmup: int = 60, trace_sink: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """`trace_sink`, if given a list, gets one record appended per
        processed bar describing what every box did with it — purely for
        observability (e.g. driving a live display); it changes no control
        flow and every field it holds was already computed above it."""
        bars, data_certification = certify_bars(bars)

        funnel = {
            "bars_processed": 0,
            "pa_signals": 0,
            "id_approvals": 0,
            "id_rejections": 0,
            "mpc_plans": 0,
            "safety_approvals": 0,
            "safety_rejections": 0,
            "gates_evaluated": 0,
            "gates_passed": 0,
            "gates_rejected": 0,
            "orders_submitted": 0,
            "exit_orders_submitted": 0,
            "fills": 0,
        }

        n = len(bars)
        # Calibrate PA's normalization strictly from the warm-up window that
        # precedes the run. This never touches a bar the loop hasn't reached
        # yet — the leakage fix for the legacy engine's tail(50)-on-the-whole
        # -dataframe initialization.
        self.pa.calibrate(self.symbol, bars.iloc[:warmup])

        for bar_idx in range(warmup, n - 1):
            funnel["bars_processed"] += 1
            timestamp = str(bars.iloc[bar_idx]["timestamp"]) if "timestamp" in bars.columns else str(bar_idx)
            event_ts = pd.Timestamp(bars.iloc[bar_idx]["timestamp"])
            next_ts = pd.Timestamp(bars.iloc[bar_idx + 1]["timestamp"])
            if self._active_trading_date != event_ts.date():
                self._active_trading_date = event_ts.date()
                self._day_start_equity = self._equity()
            end_time = str(self.config.require("trading_hours_end"))
            session_last_bar = next_ts.date() != event_ts.date() or event_ts.strftime("%H:%M") >= end_time
            record: Dict[str, Any] = {
                "bar_idx": bar_idx, "timestamp": timestamp, "close": float(bars.iloc[bar_idx]["close"]),
                "stage": "data_ingestion", "equity": self._equity(), "open_position": self._open_trade is not None,
            }

            def _emit():
                if trace_sink is not None:
                    trace_sink.append(dict(record))

            admitted, _, trace = self.data_ingestion.admit(self.symbol, self.config)
            self._record(trace)
            record["data_ingestion"] = admitted
            if not admitted:
                record["stage"] = "rejected_data_ingestion"
                _emit()
                continue

            certified, _, trace = self.l2_certifier.certify(bars.iloc[max(0, bar_idx - 5):bar_idx + 1], self.config)
            self._record(trace)
            record["l2_certifier"] = certified
            if not certified:
                record["stage"] = "rejected_l2_certifier"
                _emit()
                continue

            in_window, _exploration_bias, trace = self.unified_execution.check_window(timestamp, self.config)
            self._record(trace)
            record["in_window"] = in_window

            # PA runs every admitted/certified bar — both to look for new
            # entries and to keep exit_confidence current for any open
            # position's signal-exit check.
            snapshot = MarketSnapshot(
                symbol=self.symbol, timestamp=timestamp,
                bars=bars.iloc[max(0, bar_idx - SNAPSHOT_LOOKBACK_BARS + 1):bar_idx + 1],
            )
            signal, trace = self.pa.evaluate(snapshot, self.config)
            self._record(trace)
            funnel["pa_signals"] += 1
            record["pa"] = {
                "confidence": signal.confidence, "direction": signal.direction,
                "exit_confidence": signal.exit_confidence, "momentum": signal.momentum,
                "volatility": signal.volatility,
            }

            trades_before = len(self.completed_trades)
            self._maybe_exit(bar_idx, bars, signal, funnel, session_last_bar=session_last_bar)
            if len(self.completed_trades) > trades_before:
                record["exit"] = self.completed_trades[-1]

            if self._open_trade is not None or not in_window:
                record["stage"] = "holding_or_out_of_window"
                _emit()
                continue

            cutoff = str(self.safety_contract.values["no_entry_cutoff_time"])
            if next_ts.date() != event_ts.date() or next_ts.strftime("%H:%M") >= cutoff:
                record["stage"] = "rejected_no_same_session_fill"
                _emit()
                continue

            decision, trace = self.id_box.evaluate(signal, self.config)
            self._record(trace)
            record["id"] = {"approved": decision.approved, "reason": decision.reason, "risk_reward_ratio": decision.risk_reward_ratio}
            if not decision.approved:
                funnel["id_rejections"] += 1
                record["stage"] = "rejected_id"
                _emit()
                continue
            funnel["id_approvals"] += 1

            atr = signal.volatility * bars.iloc[bar_idx]["close"]
            next_open = float(bars.iloc[bar_idx + 1]["open"])
            plan, pid_info, trace = self.mpc.build_plan(signal, decision, next_open, atr, self.config)
            self._record(trace)
            if plan is None:
                record["stage"] = "rejected_mpc"
                _emit()
                continue
            funnel["mpc_plans"] += 1
            record["mpc"] = {"side": plan.side, "entry_price": plan.entry_price, "stop_price": plan.stop_price, "target_price": plan.target_price}

            approved, _, size_mult, trace = self.safety_gates_target.evaluate_pre_sizing(self._equity_curve, self.config)
            self._record(trace)
            if not approved:
                funnel["safety_rejections"] += 1
                record["stage"] = "rejected_safety_pre_sizing"
                _emit()
                continue

            # The entry PID's timing-quality multiplier reaches the trade
            # ledger here: it scales the same size_multiplier the drawdown/
            # lambda checks already produce, so a poor-timing entry is
            # sized down (or a good one sized fully), not just diagnosed.
            size_mult *= pid_info["entry_timing_multiplier"]
            quantity, trace = self.position_manager.size(plan, self._equity(), size_mult, self.config)
            self._record(trace)
            record["position_manager"] = {"quantity": quantity, "size_multiplier": size_mult}
            if quantity <= 0:
                record["stage"] = "rejected_zero_quantity"
                _emit()
                continue

            post_ok, _, trace = self.safety_gates_target.evaluate_post_sizing(self._equity_curve, plan, quantity, self.config)
            self._record(trace)
            if not post_ok:
                funnel["safety_rejections"] += 1
                record["stage"] = "rejected_safety_post_sizing"
                _emit()
                continue
            funnel["safety_approvals"] += 1

            order, trace = self.p01d.create_order(self.symbol, plan, quantity, self.config)
            self._record(trace)
            if order is None:
                record["stage"] = "rejected_p01d"
                _emit()
                continue

            # 18-gate EntryDecisionEngine: a distinct, independent
            # enforcement layer from ExecutionGate's fixed-value identity
            # check below — this one runs real threshold logic (drawdown,
            # concentration, signal quality, market-close cutoff, ...).
            state = self._build_system_state()
            entry_signal = EntrySignal(
                symbol=self.symbol,
                entry_price=plan.entry_price,
                stop_loss_price=plan.stop_price,
                profit_target_price=plan.target_price,
                confidence=decision.confidence,
                suggested_quantity=quantity,
                position_notional=quantity * plan.entry_price,
                risk_reward_ratio=decision.risk_reward_ratio,
            )
            gate_result = self.entry_decision_engine.evaluate_pre_submit(
                state,
                signal=entry_signal,
                current_time=self._parse_timestamp(str(next_ts)),
                proposed_quantity=quantity,
                target_price=plan.entry_price,
                fill_price=plan.entry_price,
                expected_qty=quantity,
                actual_qty=quantity,
                symbol=self.symbol,
                proposed_notional=quantity * plan.entry_price,
            )
            funnel["gates_evaluated"] += 1
            record["gates"] = {"passed": gate_result["passed"], "gate": gate_result["gate"], "reason": gate_result["reason"]}
            if not gate_result["passed"]:
                funnel["gates_rejected"] += 1
                record["stage"] = "rejected_18_gates"
                _emit()
                continue
            funnel["gates_passed"] += 1
            quantity = max(0, int(gate_result["adjusted_quantity"]))
            if quantity <= 0:
                record["stage"] = "rejected_gate_derated_to_zero"
                _emit()
                continue

            gate_result2 = ExecutionGate().validate_pre_submit(
                self.safety_contract.as_dict(),
                {"symbol": order.symbol, "side": order.side, "quantity": quantity, "order_type": order.order_type},
                parameter_registry=self.registry,
            )
            if not gate_result2["passed"]:
                funnel["safety_rejections"] += 1
                record["stage"] = "rejected_execution_gate"
                _emit()
                continue

            fill = self.broker.place_order(
                symbol=order.symbol,
                side=order.side,
                quantity=quantity,
                order_type=order.order_type,
                market_price=next_open,
                config=self.safety_contract.as_dict(),
                parameter_registry=self.registry,
                event_time=str(bars.iloc[bar_idx + 1].get("timestamp", bar_idx + 1)),
            )
            funnel["orders_submitted"] += 1
            if fill["passed"]:
                funnel["fills"] += 1
                post_fill = self.entry_decision_engine.evaluate_post_fill(
                    quantity, int(fill["filled_quantity"]), next_open, float(fill["filled_price"]),
                )
                record["post_fill_gates"] = {
                    "passed": post_fill["passed"], "gate": post_fill["gate"], "reason": post_fill["reason"],
                }
                if not post_fill["passed"]:
                    close_side = "SELL" if order.side == "BUY" else "BUY"
                    corrective = self.broker.place_order(
                        symbol=order.symbol, side=close_side, quantity=int(fill["filled_quantity"]),
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
                            equity_intraday_leg(fill["filled_price"], int(fill["filled_quantity"]), order.side).total
                            + equity_intraday_leg(corrective["filled_price"], int(fill["filled_quantity"]), close_side).total
                        )
                        self.completed_trades.append({
                            "side": order.side, "entry_price": fill["filled_price"],
                            "exit_price": corrective["filled_price"], "quantity": int(fill["filled_quantity"]),
                            "entry_bar_idx": bar_idx + 1, "exit_bar_idx": bar_idx + 1,
                            "reason": "post_fill_safety_flatten", "pnl": pnl,
                            "costs": trade_costs, "net_pnl": pnl - trade_costs,
                        })
                    funnel["gates_rejected"] += 1
                    record["stage"] = "post_fill_safety_flatten"
                    _emit()
                    continue
                self._open_trade = {
                    "side": plan.side,
                    "entry_price": fill["filled_price"],
                    "stop_price": plan.stop_price,
                    "target_price": plan.target_price,
                    "quantity": quantity,
                    "entry_bar_idx": bar_idx + 1,
                    "entry_timestamp": str(next_ts),
                    "minimum_hold_bars": plan.minimum_hold_bars,
                    "maximum_hold_bars": plan.maximum_hold_bars,
                    "exit_confidence_threshold": decision.timing_quality,
                }
                record["stage"] = "entry_filled"
                record["entry"] = {"side": plan.side, "price": fill["filled_price"], "quantity": quantity}
            else:
                record["stage"] = "entry_fill_rejected"
            _emit()

        # End-of-run reconciliation: never leave a position un-marked.
        open_position_reconciled = False
        if self._open_trade is not None:
            final_close = float(bars.iloc[n - 1]["close"])
            self._execute_exit(n - 1, self._open_trade, final_close, "end_of_run_reconciliation", funnel, str(bars.iloc[n - 1].get("timestamp", n - 1)))
            open_position_reconciled = True

        # broker.realized_pnl is the single source of truth (accumulated
        # directly by PaperBrokerAdapter on every fill); the sum over
        # completed_trades should always agree with it exactly.
        gross_pnl = self.broker.realized_pnl
        assert abs(gross_pnl - sum(t["pnl"] for t in self.completed_trades)) < 1e-6, "gross_pnl / trade-ledger mismatch"
        costs = self._transaction_costs()
        net_pnl = gross_pnl - costs["total_cost"]
        ledger_net_pnl = sum(t["net_pnl"] for t in self.completed_trades)
        assert abs(net_pnl - ledger_net_pnl) < 1e-3, "net_pnl / trade-ledger mismatch"

        target_names = set(self.registry.params)
        safety_names = set(self.registry.safety_params)
        coverage_target = sorted(target_names & self.consumed_parameters)
        coverage_missing = sorted(target_names - self.consumed_parameters)
        # Every order/exit fill validates the FULL safety_contract.values
        # dict through ExecutionGate, so all 20 keys are checked on every
        # call; report that explicitly rather than leaving it implicit.
        safety_consumed = sorted(safety_names) if (funnel["orders_submitted"] or funnel["exit_orders_submitted"]) else []

        return {
            **funnel,
            "completed_trades": len(self.completed_trades),
            "open_position_reconciled_at_end": open_position_reconciled,
            "gross_pnl": gross_pnl,
            **costs,
            "net_pnl": net_pnl,
            "ledger_net_pnl": ledger_net_pnl,
            "ending_equity": self.starting_equity + net_pnl,
            "config_hash": self.config.config_hash,
            "safety_contract_hash": self.safety_contract.contract_hash,
            "parameter_coverage": {
                "target_total": len(target_names),
                "target_consumed": len(coverage_target),
                "target_missing": coverage_missing,
                "safety_total": len(safety_names),
                "safety_consumed": len(safety_consumed),
                "safety_missing": sorted(safety_names - set(safety_consumed)),
            },
            "data_certification": data_certification,
            "trades": self.completed_trades,
        }
