"""Causal per-symbol OHLCV features; relative volume is not measured order-book liquidity."""
from collections import deque
from datetime import datetime
import math
from .r7_state import Features, clamp


class SymbolFeatureExtractor:
    def __init__(self, config):
        self.config = config
        self.volumes = deque(maxlen=config.feature_window)
        self.ranges = deque(maxlen=config.feature_window)
        self.last_bar = None
        self.last_result = None
        self.pvt = 0.0
        self.speed_reference = None

    def extract(self, bar):
        previous = self.last_bar
        if previous is not None:
            if bar.index == previous.index:
                if bar != previous:
                    raise ValueError('conflicting same-bar OHLCV')
                return self.last_result
            delta = (datetime.fromisoformat(bar.timestamp) - datetime.fromisoformat(previous.timestamp)).total_seconds()
            if bar.index < previous.index or delta <= 0:
                raise ValueError('bar clock moved backwards')
            gap = bar.index != previous.index + 1 or not math.isclose(delta, self.config.bar_seconds)
            # Exchange-session time: an overnight close-to-open interval is one bar of
            # session time, so integrators, slew and lags do not run through the night.
            same_session = (datetime.fromisoformat(bar.timestamp).date()
                            == datetime.fromisoformat(previous.timestamp).date())
            dt_seconds = max(self.config.bar_seconds, delta) if same_session else self.config.bar_seconds
        else:
            gap = False
            dt_seconds = self.config.bar_seconds
        # Keep all observations, including zero-volume intervals, in the bounded window.
        baseline = sum(self.volumes) / len(self.volumes) if self.volumes else 0.0
        ready = len(self.volumes) >= self.config.min_history and baseline > 0
        ratio = bar.volume / baseline if baseline > 0 else 0.0
        # Previous EMA only: current close cannot dilute its own displacement.
        reference = self.speed_reference if self.speed_reference is not None else bar.close
        market_imbalance = (bar.close / reference - 1) * math.sqrt(min(ratio, 3)) if ready else 0.0
        # Diagnostic telemetry only. It is NOT physical speed and is never passed to
        # the R6 speed-droop equation or to the trading governor.
        market_speed = 1 - clamp(market_imbalance, -0.5, 0.5)
        liquidity = clamp(ratio / 2, 0, 1) if ready and bar.volume > 0 else 0.0
        spread = bar.high - bar.low
        body = (bar.close - bar.open) / spread if spread > 0 else 0.0
        location = (2 * bar.close - bar.high - bar.low) / spread if spread > 0 else 0.0
        pressure = clamp((body + location) / 2)
        previous_close = previous.close if previous is not None else bar.open
        ret = (bar.close - previous_close) / previous_close
        alpha = 2 / (self.config.feature_window + 1)
        self.speed_reference = reference + alpha * (bar.close - reference)
        # Explicit strategy scaling: 1% volume-adjusted return corresponds to full scale.
        self.pvt += alpha * (clamp(ret * min(ratio, 3) / 0.01) - self.pvt)
        self.volumes.append(bar.volume)
        self.ranges.append(spread / bar.close)
        volatility = clamp(sum(self.ranges) / len(self.ranges) / 0.02, 0, 1)
        frozen = previous is not None and bar.volume == 0 and bar.open == bar.high == bar.low == bar.close == previous.close
        result = Features(bar, self.pvt, pressure, liquidity, volatility, ready,
                          frozen or bar.volume == 0, gap, market_speed, reference, ratio, dt_seconds)
        self.last_bar, self.last_result = bar, result
        return result
