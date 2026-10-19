"""Trading exposure governor: PID tracking of the approved target exposure.

Units are signed symbol-budget pu. The approved target already contains the
droop-inspired portfolio capacity allocation (r7_allocator). No price-derived
"market speed" enters here, and no 1/R amplifier is applied to market data.
The R6 physical turbine model (speed droop, turbine lag, swing equation) remains a
separate seconds-based model in revision6_avr and is not used for execution.
"""
from math import exp
from .r7_state import GovernorOutput, clamp, number


class MarketGovernor:
    def __init__(self, config):
        self.config = config
        self.integral = 0.0
        self.previous_actual = None
        self.derivative = 0.0
        self.command = 0.0
        self.output = 0.0

    def step(self, target, actual, dt_seconds, enabled=True, execution_blocked=False):
        c = self.config
        target, actual, dt = clamp(target), number(actual), number(dt_seconds)
        if dt <= 0:
            raise ValueError('dt_seconds must be positive')
        if type(execution_blocked) is not bool:
            raise ValueError('execution state must be explicit')
        # Actual exposure is measured and never clamped: an overrun stays visible in
        # telemetry. Control acts on it only up to the target on the reference side:
        # R7 cannot size native orders, so a native fill beyond the target is an
        # over-budget state, not tracking error to wind into P/I/D (it would later leak
        # into the pulse as a false opposite signal).
        over_budget = (target > 0 and actual > target) or (target < 0 and actual < target)
        measured = target if over_budget else actual
        error = target - measured
        p = c.kp * error
        # Derivative on measurement: a target step produces no derivative kick.
        raw_d = 0.0 if self.previous_actual is None else -c.kd_seconds * (measured - self.previous_actual) / dt
        if c.derivative_tau_seconds > 0:
            self.derivative += (1 - exp(-dt / c.derivative_tau_seconds)) * (raw_d - self.derivative)
        else:
            self.derivative = raw_d
        d = self.derivative
        trial_i = clamp(self.integral + c.ki_per_second * error * dt, -c.integral_limit, c.integral_limit)
        frozen = False
        if not enabled:
            self.integral *= exp(-dt / c.market_response_tau_seconds)
            desired = 0.0
        else:
            requested = target + p + trial_i + d
            # Conditional integration respects saturation and the actuator slew limit.
            achievable = clamp(clamp(requested), self.command - c.command_slew_per_second * dt,
                               self.command + c.command_slew_per_second * dt)
            # A working native order means the actuator cannot act on new demand yet:
            # hold the integral instead of accumulating impossible execution demand.
            frozen = execution_blocked
            if not frozen and (requested - achievable) * error <= 0:
                self.integral = trial_i
            # Feed-forward engagement remains at the target when tracking error
            # reaches zero; zero PID error must not become a spurious exit pulse.
            desired = clamp(target + p + self.integral + d)
        self.command = clamp(desired, self.command - c.command_slew_per_second * dt,
                             self.command + c.command_slew_per_second * dt)
        self.output += (1 - exp(-dt / c.market_response_tau_seconds)) * (self.command - self.output)
        self.previous_actual = measured
        return GovernorOutput(target, actual, target - actual, p, self.integral, d, desired,
                              clamp(self.output), frozen, over_budget)

    def reset(self):
        """Bumpless restart from flat: no tracking effort survives a closed position."""
        self.integral, self.previous_actual, self.derivative = 0.0, None, 0.0
        self.command = self.output = 0.0

    @staticmethod
    def directional_signal(target, actual, applied):
        """Return the signal used by the pulse FSM, without false reversal churn.

        ``applied`` remains the complete PID tracking output for telemetry and risk
        diagnostics.  When native sizing has already placed a valid position beyond
        the R7 target, the tracking error can be negative even though the market
        reference is still bullish (and vice versa).  That condition is an
        over-budget risk state, not a market-direction reversal.  Preserve the
        reference side for the FSM; native risk/protective exits remain authoritative.
        """
        target, actual, applied = clamp(target), number(actual), clamp(applied)
        if target > 0 and actual > target:
            return max(0.0, applied)
        if target < 0 and actual < target:
            return min(0.0, applied)
        return applied
