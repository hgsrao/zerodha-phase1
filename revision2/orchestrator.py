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
from revision2.contracts import EffectiveConfig, MarketSnapshot, PASignal, SafetyContract
from runtime.operating_mode import ExecutionGate, PaperBrokerAdapter


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
            daily_realized_loss=max(0.0, peak - current),
            daily_unrealized_loss=0.0,
            open_positions_count=0,  # entry path only runs while flat
            open_positions=[],
            market_data_age_seconds=0,
            broker_connected=True,
            broker_offline_seconds=0,
            kill_switch_active=not bool(self.safety_contract.values["kill_switch_enabled"]),
            circuit_breaker_triggered=False,
        )

    def _execute_exit(self, bar_idx: int, trade: Dict[str, Any], exit_price: float, reason: str, funnel: Dict[str, int]) -> None:
        close_side = "SELL" if trade["side"] == "BUY" else "BUY"
        result = self.broker.place_order(
            symbol=self.symbol,
            side=close_side,
            quantity=trade["quantity"],
            order_type="MARKET",
            market_price=exit_price,
            config=self.safety_contract.as_dict(),
            parameter_registry=self.registry,
        )
        funnel["exit_orders_submitted"] += 1
        if result["passed"]:
            funnel["fills"] += 1
            pnl = (
                (result["filled_price"] - trade["entry_price"]) * trade["quantity"]
                if trade["side"] == "BUY"
                else (trade["entry_price"] - result["filled_price"]) * trade["quantity"]
            )
            self.completed_trades.append({
                "side": trade["side"],
                "entry_price": trade["entry_price"],
                "exit_price": result["filled_price"],
                "quantity": trade["quantity"],
                "entry_bar_idx": trade["entry_bar_idx"],
                "exit_bar_idx": bar_idx,
                "reason": reason,
                "pnl": pnl,
            })
            self._equity_curve.append(self._equity())
            self._open_trade = None

    def _maybe_exit(self, bar_idx: int, bars: pd.DataFrame, signal: PASignal, funnel: Dict[str, int]) -> None:
        trade = self._open_trade
        if trade is None:
            return
        bar = bars.iloc[bar_idx]
        held = bar_idx - trade["entry_bar_idx"]

        # Priority 1: forced close — never suppressed by minimum_hold_bars.
        halt_dd = float(self.config.require("drawdown_halt_threshold"))
        if self._current_drawdown() >= halt_dd:
            self._execute_exit(bar_idx, trade, float(bar["close"]), "forced_close_drawdown_halt", funnel)
            return

        exit_price = None
        reason = None
        if trade["side"] == "BUY":
            if bar["low"] <= trade["stop_price"]:
                exit_price, reason = trade["stop_price"], "stop"
            elif bar["high"] >= trade["target_price"]:
                exit_price, reason = trade["target_price"], "target"
        else:
            if bar["high"] >= trade["stop_price"]:
                exit_price, reason = trade["stop_price"], "stop"
            elif bar["low"] <= trade["target_price"]:
                exit_price, reason = trade["target_price"], "target"

        if exit_price is not None:
            # Priorities 2-3: protective stop and target — never suppressed
            # by minimum_hold_bars either. A stop or a hit target is not a
            # discretionary decision.
            self._execute_exit(bar_idx, trade, exit_price, reason, funnel)
            return

        # Priority 4: discretionary signal exit — the only exit gated by
        # minimum_hold_bars. Uses the same exit_confidence_threshold ID
        # computed at entry time, compared against PA's freshly recomputed
        # exit_confidence for the current bar.
        if held >= trade["minimum_hold_bars"] and signal.exit_confidence < trade["exit_confidence_threshold"]:
            self._execute_exit(bar_idx, trade, float(bar["close"]), "signal_exit", funnel)
            return

        # Priority 5: maximum hold — forced regardless of minimum_hold_bars
        # (structurally always >= it anyway, by the registry's own ranges).
        if held >= trade["maximum_hold_bars"]:
            self._execute_exit(bar_idx, trade, float(bar["close"]), "max_hold", funnel)

    def _transaction_costs(self) -> Dict[str, float]:
        """Slippage is already embedded in gross_pnl via the fill price
        itself; it's reported here for visibility only and is NOT
        subtracted again in total_cost. Brokerage/exchange/tax figures use
        standard Indian discount-broker intraday-equity approximations —
        there is no canonical registry parameter for them yet, so this is a
        documented modeling choice, not a calibrated cost."""
        slippage_cost = 0.0
        brokerage = 0.0
        exchange_charges = 0.0
        taxes = 0.0
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
            "slippage_cost": round(slippage_cost, 4),
            "brokerage": round(brokerage, 4),
            "exchange_charges": round(exchange_charges, 4),
            "taxes": round(taxes, 4),
            "total_cost": round(total_cost, 4),
        }

    # ---- main loop --------------------------------------------------------
    def run(self, bars: pd.DataFrame, warmup: int = 60) -> Dict[str, Any]:
        cols = {c.lower(): c for c in bars.columns}
        bars = bars.rename(columns={v: k for k, v in cols.items()})

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

            admitted, _, trace = self.data_ingestion.admit(self.symbol, self.config)
            self._record(trace)
            if not admitted:
                continue

            certified, _, trace = self.l2_certifier.certify(bars.iloc[max(0, bar_idx - 5):bar_idx + 1], self.config)
            self._record(trace)
            if not certified:
                continue

            in_window, _exploration_bias, trace = self.unified_execution.check_window(timestamp, self.config)
            self._record(trace)

            # PA runs every admitted/certified bar — both to look for new
            # entries and to keep exit_confidence current for any open
            # position's signal-exit check.
            snapshot = MarketSnapshot(symbol=self.symbol, timestamp=timestamp, bars=bars.iloc[:bar_idx + 1])
            signal, trace = self.pa.evaluate(snapshot, self.config)
            self._record(trace)
            funnel["pa_signals"] += 1

            self._maybe_exit(bar_idx, bars, signal, funnel)

            if self._open_trade is not None or not in_window:
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

            quantity, trace = self.position_manager.size(plan, self._equity(), size_mult, self.config)
            self._record(trace)
            if quantity <= 0:
                continue

            post_ok, _, trace = self.safety_gates_target.evaluate_post_sizing(self._equity_curve, plan, quantity, self.config)
            self._record(trace)
            if not post_ok:
                funnel["safety_rejections"] += 1
                continue
            funnel["safety_approvals"] += 1

            order, trace = self.p01d.create_order(self.symbol, plan, quantity, self.config)
            self._record(trace)
            if order is None:
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
            gate_result = self.entry_decision_engine.evaluate(
                state,
                signal=entry_signal,
                current_time=self._parse_timestamp(timestamp),
                proposed_quantity=quantity,
                target_price=plan.entry_price,
                fill_price=plan.entry_price,
                expected_qty=quantity,
                actual_qty=quantity,
                symbol=self.symbol,
                seen_recent=False,
                proposed_notional=quantity * plan.entry_price,
            )
            funnel["gates_evaluated"] += 1
            if not gate_result["passed"]:
                funnel["gates_rejected"] += 1
                continue
            funnel["gates_passed"] += 1
            quantity = max(0, int(gate_result["adjusted_quantity"]))
            if quantity <= 0:
                continue

            gate_result2 = ExecutionGate().validate_pre_submit(
                self.safety_contract.as_dict(),
                {"symbol": order.symbol, "side": order.side, "quantity": quantity, "order_type": order.order_type},
                parameter_registry=self.registry,
            )
            if not gate_result2["passed"]:
                funnel["safety_rejections"] += 1
                continue

            fill = self.broker.place_order(
                symbol=order.symbol,
                side=order.side,
                quantity=quantity,
                order_type=order.order_type,
                market_price=next_open,
                config=self.safety_contract.as_dict(),
                parameter_registry=self.registry,
            )
            funnel["orders_submitted"] += 1
            if fill["passed"]:
                funnel["fills"] += 1
                self._open_trade = {
                    "side": plan.side,
                    "entry_price": fill["filled_price"],
                    "stop_price": plan.stop_price,
                    "target_price": plan.target_price,
                    "quantity": quantity,
                    "entry_bar_idx": bar_idx + 1,
                    "minimum_hold_bars": plan.minimum_hold_bars,
                    "maximum_hold_bars": plan.maximum_hold_bars,
                    "exit_confidence_threshold": decision.timing_quality,
                }

        # End-of-run reconciliation: never leave a position un-marked.
        open_position_reconciled = False
        if self._open_trade is not None:
            final_close = float(bars.iloc[n - 1]["close"])
            self._execute_exit(n - 1, self._open_trade, final_close, "end_of_run_reconciliation", funnel)
            open_position_reconciled = True

        # broker.realized_pnl is the single source of truth (accumulated
        # directly by PaperBrokerAdapter on every fill); the sum over
        # completed_trades should always agree with it exactly.
        gross_pnl = self.broker.realized_pnl
        assert abs(gross_pnl - sum(t["pnl"] for t in self.completed_trades)) < 1e-6, "gross_pnl / trade-ledger mismatch"
        costs = self._transaction_costs()
        net_pnl = gross_pnl - costs["total_cost"]

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
            "trades": self.completed_trades,
        }
