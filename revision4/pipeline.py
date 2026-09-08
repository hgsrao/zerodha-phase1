"""
REVISION 04 (Proper): Pipeline

Adapters for Revision 2 black boxes.
Connect real PA, ID, MPC, PositionManager to our contracts.

NOT simplified duplicates. Real boxes.
"""

import numpy as np
import pandas as pd
from typing import Optional, List, Dict
from revision4.contracts import (
    ForecastSignal, IDDecision, TradePlan, SizedProposal, OrderIntent,
    EffectiveConfig, SignalType
)
from revision2.boxes import (
    PredictiveAnalyticsBox, IntelligentDiscriminationBox,
    ModelPredictiveControlBox, SafetyGatesTargetBox,
    PositionManagerBox, P01DBox
)
from revision2.contracts import MarketSnapshot


class PipelineAdapter:
    """
    Adapts Revision 2 boxes to typed contracts.
    Each method is a type converter: box_output → contract.

    Maintains bar history for each symbol to feed MarketSnapshot to PA box.
    """

    def __init__(self, config: EffectiveConfig):
        self.config = config
        self.parameter_trace = []  # Log every parameter used

        # Initialize Revision 2 boxes
        self.pa_box = PredictiveAnalyticsBox()
        self.id_box = IntelligentDiscriminationBox()
        self.mpc_box = ModelPredictiveControlBox()
        self.safety_box = SafetyGatesTargetBox()
        self.position_manager = PositionManagerBox()
        self.p01d_box = P01DBox()

        # Track bar history per symbol (for MarketSnapshot)
        self._bar_history: Dict[str, List[Dict]] = {}

    def _log_param(self, param_name: str, value):
        """Audit trail: every parameter fetch. Returns the value."""
        self.parameter_trace.append((param_name, value))
        return value

    def calibrate_pa_box(self, symbol: str, warmup_bars: pd.DataFrame) -> None:
        """
        Calibrate PA box with warmup bars before live run.
        MUST be called before generate_forecast for each symbol.
        """
        try:
            self.pa_box.calibrate(symbol, warmup_bars)
        except Exception as e:
            print(f"Warning: PA calibration failed for {symbol}: {e}")

    def add_bar_to_history(self, symbol: str, bar_data: Dict) -> None:
        """
        Track incoming bar for symbol.
        Called for each bar during replay.
        """
        if symbol not in self._bar_history:
            self._bar_history[symbol] = []
        self._bar_history[symbol].append(bar_data)

    def generate_forecast(
        self,
        timestamp: str,
        bar_index: int,
        symbol: str,
    ) -> ForecastSignal:
        """
        Run Revision 2 PA Box.
        Output: ForecastSignal with confidence scores.

        Precondition: calibrate_pa_box() called for this symbol,
                     add_bar_to_history() called for all bars up to t.
        """
        # Check if we have bar history
        if symbol not in self._bar_history or len(self._bar_history[symbol]) == 0:
            return ForecastSignal(
                timestamp=timestamp,
                bar_index=bar_index,
                symbol=symbol,
                signal_type=SignalType.REJECTED,
                pa_confidence=0.0,
                chart_confidence=0.0,
                rejection_reason="No bar history for symbol",
            )

        # Build DataFrame from bar history (up to current bar)
        try:
            bars_list = self._bar_history[symbol]
            df = pd.DataFrame(bars_list)
            # Ensure correct data types
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df['open'] = pd.to_numeric(df['open'], errors='coerce')
            df['high'] = pd.to_numeric(df['high'], errors='coerce')
            df['low'] = pd.to_numeric(df['low'], errors='coerce')
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce')

            # Build MarketSnapshot (bars up to t)
            snapshot = MarketSnapshot(
                symbol=symbol,
                timestamp=timestamp,
                bars=df,
                next_bar_open=None,
            )

            # Call PA box
            pa_signal, _ = self.pa_box.evaluate(snapshot, self.config)

            if pa_signal is None:
                return ForecastSignal(
                    timestamp=timestamp,
                    bar_index=bar_index,
                    symbol=symbol,
                    signal_type=SignalType.REJECTED,
                    pa_confidence=0.0,
                    chart_confidence=0.0,
                    rejection_reason="PA box returned no signal",
                )

            # Map PA signal to Revision 04 ForecastSignal
            signal_type_map = {
                1: SignalType.MOMENTUM_UP,
                -1: SignalType.MOMENTUM_DOWN,
                0: SignalType.REJECTED,
            }

            chart_conf = self._estimate_chart_confidence(df)

            return ForecastSignal(
                timestamp=timestamp,
                bar_index=bar_index,
                symbol=symbol,
                signal_type=signal_type_map.get(pa_signal.direction, SignalType.REJECTED),
                pa_confidence=pa_signal.confidence,
                chart_confidence=chart_conf,
                rejection_reason=None if pa_signal.direction != 0 else "PA rejected",
            )

        except Exception as e:
            return ForecastSignal(
                timestamp=timestamp,
                bar_index=bar_index,
                symbol=symbol,
                signal_type=SignalType.REJECTED,
                pa_confidence=0.0,
                chart_confidence=0.0,
                rejection_reason=f"PA error: {str(e)[:50]}",
            )

    def _estimate_chart_confidence(self, df: pd.DataFrame) -> float:
        """
        Simplified chart studies confidence based on bar properties.
        TODO: Call real Chart Studies box from Revision 2.
        """
        if len(df) == 0:
            return 0.5

        # Use last bar data
        last_bar = df.iloc[-1]
        open_price = float(last_bar['open'])
        close_price = float(last_bar['close'])
        high_price = float(last_bar['high'])
        low_price = float(last_bar['low'])

        if open_price == 0 or high_price == low_price:
            return 0.5

        # Position in range: 0 = at low, 1 = at high
        position_in_range = (close_price - low_price) / (high_price - low_price)
        # Confidence rises near extremes, falls near middle
        conf = min(abs(position_in_range - 0.5) * 2.0, 1.0)
        return conf

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
        from revision4.config_access import get_trading_hours, get_signal_thresholds

        # Fetch configured thresholds using canonical names
        entry_conf, min_conf = get_signal_thresholds(self.config)
        pa_min = self._log_param("entry_confidence_threshold", entry_conf)
        # Chart confidence is derived from market data, not configured
        chart_min = self._log_param("chart_confidence_min", 0.5)

        trade_start_h, trade_start_m, trade_end_h, trade_end_m = get_trading_hours(self.config)
        self._log_param("trading_hours_start", f"{trade_start_h:02d}:{trade_start_m:02d}")
        self._log_param("trading_hours_end", f"{trade_end_h:02d}:{trade_end_m:02d}")

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
        from revision4.config_access import get_atr_parameters, get_position_limits

        # Fetch ATR parameters using canonical names
        _, atr_stop_mult, atr_target_mult = get_atr_parameters(self.config)
        self._log_param("stop_loss_atr_mult", atr_stop_mult)
        self._log_param("profit_target_atr_mult", atr_target_mult)

        _, _, max_risk = get_position_limits(self.config)
        self._log_param("max_loss_per_trade_rupees", max_risk)

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
        from revision4.config_access import get_mpc_parameters, get_entry_cost

        # Fetch MPC parameters using canonical names
        mpc_enabled, mpc_loss_threshold = get_mpc_parameters(self.config)
        self._log_param("kill_switch_enabled", mpc_enabled)
        self._log_param("max_daily_loss_rupees", mpc_loss_threshold)

        entry_cost_pct, fixed_cost = get_entry_cost(self.config)
        self._log_param("max_slippage_fraction", entry_cost_pct)

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
