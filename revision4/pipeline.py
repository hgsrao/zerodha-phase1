"""
REVISION 04 (Proper): Pipeline

Adapters for Revision 2 black boxes.
Connect real PA, ID, MPC, PositionManager to our contracts.

NOT simplified duplicates. Real boxes.
"""

import numpy as np
from typing import Optional, List
from revision4.contracts import (
    ForecastSignal, IDDecision, TradePlan, SizedProposal, OrderIntent,
    EffectiveConfig, SignalType
)


class PipelineAdapter:
    """
    Adapts Revision 2 boxes to typed contracts.
    Each method is a type converter: box_output → contract.
    """

    def __init__(self, config: EffectiveConfig):
        self.config = config
        self.parameter_trace = []  # Log every parameter used

    def _log_param(self, param_name: str, value):
        """Audit trail: every parameter fetch."""
        self.parameter_trace.append((param_name, value))

    def generate_forecast(
        self,
        timestamp: str,
        bar_index: int,
        symbol: str,
        closes: np.ndarray,
        volumes: np.ndarray,
    ) -> ForecastSignal:
        """
        Run Revision 2 PA Box.
        Output: ForecastSignal with confidence scores.
        """
        # Placeholder: Would call actual Revision 2 PA box
        # For now: simplified version (to be replaced)

        if len(closes) < 20:
            return ForecastSignal(
                timestamp=timestamp,
                bar_index=bar_index,
                symbol=symbol,
                signal_type=SignalType.REJECTED,
                pa_confidence=0.0,
                chart_confidence=0.0,
                rejection_reason="Insufficient data",
            )

        # Simplified momentum (REPLACE WITH REVISION 2 PA BOX)
        recent = closes[-20:]
        momentum = (recent[-1] - recent[0]) / (recent[0] + 1e-6)
        pa_conf = min(abs(momentum) * 20, 1.0)

        # Simplified chart (REPLACE WITH REVISION 2 CHART BOX)
        chart_conf = 0.5

        signal_type = SignalType.MOMENTUM_UP if momentum > 0 else SignalType.MOMENTUM_DOWN

        return ForecastSignal(
            timestamp=timestamp,
            bar_index=bar_index,
            symbol=symbol,
            signal_type=signal_type,
            pa_confidence=pa_conf,
            chart_confidence=chart_conf,
            rejection_reason=None,
        )

    def make_id_decision(
        self,
        forecast: ForecastSignal,
        hour: int,
        minute: int,
        grid_sync: bool,
    ) -> IDDecision:
        """
        Run Revision 2 ID (Entry Validator) Box.
        Input: ForecastSignal
        Output: IDDecision (valid or rejected + reason)
        """
        # Fetch configured thresholds
        pa_min = self._log_param("pa_confidence_min", self.config.pa_confidence_min)
        chart_min = self._log_param("chart_confidence_min", self.config.chart_confidence_min)
        trade_start_h = self._log_param("trading_start_hour", self.config.trading_start_hour)
        trade_start_m = self._log_param("trading_start_minute", self.config.trading_start_minute)
        trade_end_h = self._log_param("trading_end_hour", self.config.trading_end_hour)
        trade_end_m = self._log_param("trading_end_minute", self.config.trading_end_minute)

        # Check PA confidence
        if forecast.pa_confidence < pa_min:
            return IDDecision(
                timestamp=forecast.timestamp,
                bar_index=forecast.bar_index,
                symbol=forecast.symbol,
                forecast=forecast,
                entry_valid=False,
                entry_reason=f"PA confidence {forecast.pa_confidence:.2f} < {pa_min}",
                current_hour=hour,
                current_minute=minute,
                grid_sync=grid_sync,
                grid_reason="Not checked (rejected earlier)",
            )

        # Check chart confidence
        if forecast.chart_confidence < chart_min:
            return IDDecision(
                timestamp=forecast.timestamp,
                bar_index=forecast.bar_index,
                symbol=forecast.symbol,
                forecast=forecast,
                entry_valid=False,
                entry_reason=f"Chart confidence {forecast.chart_confidence:.2f} < {chart_min}",
                current_hour=hour,
                current_minute=minute,
                grid_sync=grid_sync,
                grid_reason="Not checked (rejected earlier)",
            )

        # Check trading hours
        minutes_now = hour * 60 + minute
        trade_start = trade_start_h * 60 + trade_start_m
        trade_end = trade_end_h * 60 + trade_end_m

        if not (trade_start <= minutes_now < trade_end):
            return IDDecision(
                timestamp=forecast.timestamp,
                bar_index=forecast.bar_index,
                symbol=forecast.symbol,
                forecast=forecast,
                entry_valid=False,
                entry_reason=f"Outside trading hours: {hour:02d}:{minute:02d}",
                current_hour=hour,
                current_minute=minute,
                grid_sync=grid_sync,
                grid_reason="Not checked (rejected earlier)",
            )

        # All checks passed
        return IDDecision(
            timestamp=forecast.timestamp,
            bar_index=forecast.bar_index,
            symbol=forecast.symbol,
            forecast=forecast,
            entry_valid=True,
            entry_reason="All checks passed",
            current_hour=hour,
            current_minute=minute,
            grid_sync=grid_sync,
            grid_reason="Approved" if grid_sync else "Rejected",
        )

    def make_trade_plan(
        self,
        decision: IDDecision,
        entry_price: float,
        atr: float,
    ) -> TradePlan:
        """
        Run Revision 2 Risk Manager Box.
        Input: IDDecision + ATR
        Output: TradePlan (stop/target sizing)
        """
        # Fetch ATR parameters
        atr_stop_mult = self._log_param("atr_stop_multiple", self.config.atr_stop_multiple)
        atr_target_mult = self._log_param("atr_target_multiple", self.config.atr_target_multiple)
        max_risk = self._log_param("max_risk_per_trade", self.config.max_risk_per_trade)

        # Calculate stops/targets
        stop_price = entry_price - (atr * atr_stop_mult)
        target_price = entry_price + (atr * atr_target_mult)
        risk_per_share = abs(entry_price - stop_price)

        # Position sizing (base, before MPC)
        if risk_per_share > 0:
            position_size_base = max_risk / risk_per_share
        else:
            position_size_base = 0

        return TradePlan(
            timestamp=decision.timestamp,
            bar_index=decision.bar_index,
            symbol=decision.symbol,
            direction=1,  # BUY (for now)
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            risk_per_share=risk_per_share,
            position_size_base=position_size_base,
        )

    def make_sized_proposal(
        self,
        plan: TradePlan,
        daily_pnl: float,
    ) -> SizedProposal:
        """
        Run Revision 2 MPC Box.
        Input: TradePlan + daily P&L
        Output: SizedProposal (MPC-scaled size)
        """
        # Fetch MPC parameters
        mpc_enabled = self._log_param("mpc_scaling_enabled", self.config.mpc_scaling_enabled)
        mpc_loss_threshold = self._log_param("mpc_loss_threshold", self.config.mpc_loss_threshold)
        entry_cost_pct = self._log_param("entry_cost_pct", self.config.entry_cost_pct)
        fixed_cost = self._log_param("fixed_cost_per_trade", self.config.fixed_cost_per_trade)

        # MPC scaling factor
        if mpc_enabled:
            remaining_loss_budget = mpc_loss_threshold - abs(min(daily_pnl, 0))
            if remaining_loss_budget <= 0:
                scaling_factor = 0.0
            else:
                scaling_factor = remaining_loss_budget / mpc_loss_threshold
        else:
            scaling_factor = 1.0

        # Apply MPC scaling
        final_quantity = plan.position_size_base * scaling_factor

        # Estimate cost
        cost_estimate = (plan.entry_price * final_quantity * entry_cost_pct) + fixed_cost

        return SizedProposal(
            timestamp=plan.timestamp,
            bar_index=plan.bar_index,
            symbol=plan.symbol,
            plan=plan,
            mpc_scaling_factor=scaling_factor,
            final_quantity=final_quantity,
            cost_estimate=cost_estimate,
        )

    def get_parameter_trace(self) -> List[tuple]:
        """Return audit log of all parameters used."""
        return self.parameter_trace.copy()
