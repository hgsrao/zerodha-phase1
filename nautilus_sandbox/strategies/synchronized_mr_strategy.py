import numpy as np
from decimal import Decimal
from nautilus_trader.trading import Strategy
from nautilus_trader.model.enums import OrderSide, TimeInForce

class GainScheduledPID:
    def __init__(self, kp_base: float, ki_base: float, setpoint: float):
        self.kp_base = kp_base
        self.ki_base = ki_base
        self.setpoint = setpoint
        self.integral = 0.0
        
    def update(self, pv: float, vol_ratio: float) -> tuple[float, float]:
        error = self.setpoint - pv
        self.integral += error
        kp = self.kp_base * (1.0 + 0.3 * max(0.0, vol_ratio - 1.0))
        ki = self.ki_base / (1.0 + 0.1 * max(0.0, vol_ratio - 1.0))
        output = (kp * error) + (ki * self.integral)
        return output, error

class RecedingHorizonMPC:
    def __init__(self, horizon: int = 5, q_weight: float = 1.0, r_weight: float = 0.2):
        self.horizon = horizon
        self.q = q_weight
        self.r = r_weight
        
    def optimize_trajectory(self, price_series: list[float], current_z: float) -> tuple[float, float]:
        if len(price_series) < self.horizon:
            return 0.0, 1.0
        prices = np.array(price_series)
        dt_prices = np.diff(prices) / prices[:-1]
        predicted_drift = float(np.sum(dt_prices))
        
        tracking_error = abs(current_z)
        control_effort = abs(predicted_drift)
        cost = (self.q * tracking_error**2) + (self.r * control_effort**2)
        confidence = 1.0 / (1.0 + cost)
        return predicted_drift, confidence

class SynchronizedMeanReversionStrategy(Strategy):
    def __init__(self, symbol: str):
        super().__init__()
        self.symbol_str = symbol
        self.instrument = None
        
        self.lifecycle_pid = GainScheduledPID(kp_base=0.10, ki_base=0.02, setpoint=0.20)
        self.studies_pid = GainScheduledPID(kp_base=0.50, ki_base=0.08, setpoint=0.0)
        self.mpc_engine = RecedingHorizonMPC(horizon=5, q_weight=1.0, r_weight=0.2)
        
        self.executed_trades_outcomes = []
        self.last_exit_bar = -999
        self.bar_counter = 0
        
        self.close_prices = []
        self.volumes = []
        self.highs = []
        self.lows = []

    def on_start(self):
        self.instrument = self.cache.instrument(self.symbol_str)
        if self.instrument is None:
            self.log.error(f"Instrument {self.symbol_str} not found in cache.")

    def on_bar(self, bar):
        self.bar_counter += 1
        self.close_prices.append(float(bar.close))
        self.highs.append(float(bar.high))
        self.lows.append(float(bar.low))
        self.volumes.append(float(bar.volume))
        
        if len(self.close_prices) > 200:
            self.close_prices.pop(0)
            self.highs.pop(0)
            self.lows.pop(0)
            self.volumes.pop(0)

        if self.bar_counter < 100 or (self.bar_counter < self.last_exit_bar + 15):
            return

        import pandas as pd
        highs_s = pd.Series(self.highs)
        lows_s = pd.Series(self.lows)
        closes_s = pd.Series(self.close_prices)
        vols_s = pd.Series(self.volumes)
        
        atr = float((highs_s - lows_s).rolling(14).mean().iloc[-1])
        atr_baseline = float((highs_s - lows_s).rolling(14).mean().rolling(100).mean().iloc[-1])
        vol_ratio = atr / atr_baseline if atr_baseline > 0 else 1.0
        vol_median = float(vols_s.rolling(50).median().iloc[-1])
        
        delta = closes_s.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = float(100 - (100 / (1 + rs)).iloc[-1])
        
        vwap = float((closes_s * vols_s).rolling(20).sum().iloc[-1] / vols_s.rolling(20).sum().iloc[-1])
        std = float(closes_s.rolling(20).std().iloc[-1])
        current_z = (float(bar.close) - vwap) / std if std > 0 else 0.0

        if len(self.executed_trades_outcomes) >= 5:
            rolling_exp = float(np.mean(self.executed_trades_outcomes[-10:]))
            lifecycle_output, _ = self.lifecycle_pid.update(rolling_exp, vol_ratio)
            dynamic_z_bias = -2.5 + lifecycle_output
            dynamic_z_bias = float(np.clip(dynamic_z_bias, -3.6, -2.2))
        else:
            dynamic_z_bias = -2.5

        studies_output, _ = self.studies_pid.update(current_z, vol_ratio)
        predicted_drift, mpc_confidence = self.mpc_engine.optimize_trajectory(self.close_prices[-5:], current_z)

        is_lifecycle_trigger = current_z < dynamic_z_bias
        is_studies_trigger = rsi < 28 and studies_output > 1.0
        is_mpc_trigger = mpc_confidence > 0.0 and predicted_drift >= 0.0
        is_vol_valid = float(bar.volume) > vol_median if not np.isnan(vol_median) else True

        if is_lifecycle_trigger and is_studies_trigger and is_mpc_trigger and is_vol_valid:
            order = self.order_factory.market(
                instrument_id=self.instrument.id,
                order_side=OrderSide.BUY,
                quantity=self.instrument.make_qty(1),
                time_in_force=TimeInForce.IOC,
            )
            self.submit_order(order)
            self.last_exit_bar = self.bar_counter + 25

    def on_stop(self):
        self.log.info(f"Strategy stopped. Total trades executed: {len(self.executed_trades_outcomes)}")
