"""Market exposure PID using the R6 speed-droop equation on an explicit market proxy.

Measured market speed is derived from price/relative volume, never exposure.
The R6 physical swing/turbine model remains a distinct seconds-based model.
"""
from math import exp
from revision6_avr.r6_turbine_governor import speed_droop_command
from .r7_state import GovernorOutput, clamp, number


class MarketGovernor:
    def __init__(self, config):
        self.config = config
        self.integral = 0.0
        self.previous_actual = None
        self.command = 0.0
        self.output = 0.0

    def step(self, target, actual, dt_seconds, enabled=True, market_speed_pu=1.0):
        c = self.config
        target, actual, dt = clamp(target), number(actual), number(dt_seconds)
        if dt <= 0:
            raise ValueError('dt_seconds must be positive')
        speed = number(market_speed_pu)
        if speed <= 0:
            raise ValueError('market speed must be positive')
        droop_target = speed_droop_command(p_ref_pu=target, speed_pu=speed,
            droop_r=c.droop_r, p_min_pu=-1.0, p_max_pu=1.0)
        error = droop_target - actual
        p = c.kp * error
        d = 0.0 if self.previous_actual is None else -c.kd_seconds * (actual - self.previous_actual) / dt
        trial_i = clamp(self.integral + c.ki_per_second * error * dt, -c.integral_limit, c.integral_limit)
        if not enabled:
            self.integral *= exp(-dt / c.market_response_tau_seconds)
            desired = 0.0
        else:
            requested = droop_target + p + trial_i + d
            # Conditional integration respects saturation and the actuator slew limit.
            achievable = clamp(clamp(requested), self.command - c.command_slew_per_second * dt,
                               self.command + c.command_slew_per_second * dt)
            if (requested - achievable) * error <= 0:
                self.integral = trial_i
            # Feed-forward engagement remains at the target when tracking error
            # reaches zero; zero PID error must not become a spurious exit pulse.
            desired = clamp(droop_target + p + self.integral + d)
        self.command = clamp(desired, self.command - c.command_slew_per_second * dt,
                             self.command + c.command_slew_per_second * dt)
        self.output += (1 - exp(-dt / c.market_response_tau_seconds)) * (self.command - self.output)
        self.previous_actual = actual
        return GovernorOutput(target, actual, error, p, self.integral, d, desired,
                              clamp(self.output), speed, droop_target)
