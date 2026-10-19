"""Market participation PI, separate from position tracking and physical voltage."""
from math import exp
from .r8_avr_core import PositionalPI, PIConfig
from .r8_state import MarketVote, clamp


class MarketAVR:
    def __init__(self, config=None):
        # Gains have explicit seconds units; provisional strategy tuning owned by ControlConfig.
        if config is None:
            from .r8_state import ControlConfig
            gains = {f: ControlConfig.__dataclass_fields__[f].default
                     for f in ('market_avr_kp', 'market_avr_ki', 'market_avr_kaw', 'market_avr_integral_limit')}
        else:
            gains = {f: getattr(config, f)
                     for f in ('market_avr_kp', 'market_avr_ki', 'market_avr_kaw', 'market_avr_integral_limit')}
        self.pi = PositionalPI(PIConfig(gains['market_avr_kp'], gains['market_avr_ki'],
                                        gains['market_avr_kaw'], gains['market_avr_integral_limit']))
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
