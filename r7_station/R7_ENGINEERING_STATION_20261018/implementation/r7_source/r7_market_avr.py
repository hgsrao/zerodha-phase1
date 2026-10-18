"""Market participation PI, separate from position tracking and physical voltage."""
from math import exp
from revision6_avr.r6_avr_core import PositionalPI, PIConfig
from .r7_state import MarketVote, clamp


class MarketAVR:
    def __init__(self):
        # Gains have explicit seconds units; this is provisional strategy tuning.
        self.pi = PositionalPI(PIConfig(0.3, 0.002, 0.01, 0.5))
        self.level = 0.0

    def step(self, features, dt_seconds):
        if features.degraded or features.gap or not features.ready:
            self.pi.i_term *= exp(-dt_seconds / 120)
            self.level *= exp(-dt_seconds / 120)
            return MarketVote(self.level, 0.0)
        quality = features.liquidity * (1 - 0.5 * features.volatility)
        desired = clamp((0.6 * features.pvt + 0.4 * features.pressure) * quality)
        command, _telemetry = self.pi.step(error=desired - self.level, dt=dt_seconds,
                                          bias=desired, low=-1, high=1)
        # An explicit internal first-order participation model closes this loop.
        self.level += (1 - exp(-dt_seconds / 60)) * (command - self.level)
        return MarketVote(clamp(self.level), quality)
