"""Parallel evidence droop controllers and PID execution exposure governor."""
from math import exp
from .r8_state import GovernorOutput, clamp, number


class PulseState:
    BULL = "BULL"
    BEAR = "BEAR"
    FLAT = "FLAT"


class BaseParallelDroopPID:
    def __init__(self, kp=0.8, ki_per_second=0.0005, kd_seconds=0.2,
                 droop_r=0.04, enter_threshold=0.12, integral_limit=0.5):
        self.kp = number(kp)
        self.ki_per_second = number(ki_per_second)
        self.kd_seconds = number(kd_seconds)
        self.droop_r = number(droop_r)
        self.enter_threshold = number(enter_threshold)
        self.integral_limit = number(integral_limit)
        self.integral = 0.0
        self.previous_measurement = None
        self.derivative = 0.0
        self.output = 0.0
        self.droop_penalty = 0.0

    def set_context(self, **kwargs):
        raise NotImplementedError

    def calculate_droop(self) -> float:
        return clamp(self.droop_r, 0.0, 0.95)

    def step(self, measurement, dt_seconds, enabled=True):
        measurement = clamp(measurement)
        dt_seconds = number(dt_seconds)

        if dt_seconds <= 0:
            raise ValueError("dt_seconds must be positive")
        if type(enabled) is not bool:
            raise ValueError("enabled must be bool")

        if not enabled:
            self.integral *= exp(-dt_seconds / 120.0)
            self.output *= exp(-dt_seconds / 120.0)
            self.derivative = 0.0
            self.previous_measurement = measurement
            self.droop_penalty = clamp(self.calculate_droop(), 0.0, 0.95)
            return PulseState.FLAT

        previous = self.previous_measurement
        derivative = 0.0 if previous is None else (
            self.kd_seconds * (measurement - previous) / dt_seconds
        )
        derivative = clamp(derivative, -0.5, 0.5)

        proportional = self.kp * measurement
        trial_integral = clamp(
            self.integral + self.ki_per_second * measurement * dt_seconds,
            -self.integral_limit,
            self.integral_limit,
        )

        raw = proportional + trial_integral + derivative
        if abs(raw) <= 1.0 or raw * measurement <= 0.0:
            self.integral = trial_integral

        self.derivative = derivative
        self.previous_measurement = measurement
        self.droop_penalty = clamp(self.calculate_droop(), 0.0, 0.95)
        self.output = clamp(
            (proportional + self.integral + derivative)
            * (1.0 - self.droop_penalty)
        )
        return self.vote()

    def vote(self) -> str:
        if self.output >= self.enter_threshold:
            return PulseState.BULL
        if self.output <= -self.enter_threshold:
            return PulseState.BEAR
        return PulseState.FLAT

    def snapshot_state(self) -> dict:
        return {
            "integral": self.integral,
            "previous_measurement": self.previous_measurement,
            "derivative": self.derivative,
            "output": self.output,
            "droop_penalty": self.droop_penalty,
        }

    def restore_state(self, state: dict) -> None:
        expected = {"integral", "previous_measurement", "derivative", "output", "droop_penalty"}
        if not isinstance(state, dict) or set(state) != expected:
            raise ValueError("invalid parallel PID checkpoint schema")
        integral = number(state["integral"])
        previous = None if state["previous_measurement"] is None else number(state["previous_measurement"])
        derivative = number(state["derivative"])
        output = number(state["output"])
        droop = number(state["droop_penalty"])
        # Validate everything before assigning, so a rejected checkpoint changes nothing.
        if abs(integral) > self.integral_limit or abs(derivative) > 0.5 or abs(output) > 1.0 \
                or not 0.0 <= droop <= 0.95 or (previous is not None and abs(previous) > 1.0):
            raise ValueError("parallel PID checkpoint value out of range")
        self.integral, self.previous_measurement, self.derivative = integral, previous, derivative
        self.output, self.droop_penalty = output, droop



def _config_gains(prefix, kp, ki_per_second, kd_seconds):
    """Gains passed by SymbolController from ControlConfig; a standalone PID uses the ControlConfig
    defaults, so every gain has exactly one source."""
    from .r8_state import ControlConfig
    defaults = ControlConfig.__dataclass_fields__
    names = (f'{prefix}_pid_kp', f'{prefix}_pid_ki_per_second', f'{prefix}_pid_kd_seconds')
    return tuple(defaults[n].default if v is None else v for n, v in zip(names, (kp, ki_per_second, kd_seconds)))


class TrendGovernorPID(BaseParallelDroopPID):
    """1. Governor PID: price only - close vs its own moving reference (no volume)."""
    def __init__(self, droop_r=0.04, enter_threshold=0.10, integral_limit=0.25,
                 kp=None, ki_per_second=None, kd_seconds=None):
        g = _config_gains('governor', kp, ki_per_second, kd_seconds)
        super().__init__(kp=g[0], ki_per_second=g[1], kd_seconds=g[2], droop_r=droop_r,
                         enter_threshold=enter_threshold, integral_limit=integral_limit)
        self.stagnant_bars = 0

    def set_context(self, *, price_moved: bool):
        if type(price_moved) is not bool:
            raise ValueError("price_moved must be bool")
        self.stagnant_bars = 0 if price_moved else min(50, self.stagnant_bars + 1)

    def calculate_droop(self) -> float:
        return clamp(self.droop_r + 0.005 * self.stagnant_bars, 0.0, 0.50)

    def snapshot_state(self) -> dict:
        d = super().snapshot_state()
        d["stagnant_bars"] = self.stagnant_bars
        return d

    def restore_state(self, state: dict) -> None:
        if not isinstance(state, dict) or "stagnant_bars" not in state:
            raise ValueError("missing stagnant_bars in TrendGovernorPID checkpoint")
        sb = state["stagnant_bars"]
        if type(sb) is not int or not 0 <= sb <= 50:
            raise ValueError("invalid stagnant_bars")
        base = {k: v for k, v in state.items() if k != "stagnant_bars"}
        super().restore_state(base)
        self.stagnant_bars = sb


class AvrParallelPID(BaseParallelDroopPID):
    """2. AVR PID: participation - signed candle pressure x relative volume (no price trend)."""
    def __init__(self, droop_r=0.04, enter_threshold=0.08, integral_limit=0.25,
                 kp=None, ki_per_second=None, kd_seconds=None):
        g = _config_gains('avr', kp, ki_per_second, kd_seconds)
        super().__init__(kp=g[0], ki_per_second=g[1], kd_seconds=g[2], droop_r=droop_r,
                         enter_threshold=enter_threshold, integral_limit=integral_limit)
        self.participation = 1.0

    def set_context(self, *, participation: float):
        self.participation = clamp(participation, -1.0, 1.0)

    def calculate_droop(self) -> float:
        deficiency = max(0.0, 0.5 - abs(self.participation))
        return clamp(self.droop_r + 0.50 * deficiency, 0.0, 0.85)

    def snapshot_state(self) -> dict:
        d = super().snapshot_state()
        d["participation"] = self.participation
        return d

    def restore_state(self, state: dict) -> None:
        if not isinstance(state, dict) or "participation" not in state:
            raise ValueError("missing participation in AvrParallelPID checkpoint")
        part = number(state["participation"])
        if not -1.0 <= part <= 1.0:
            raise ValueError("invalid participation in checkpoint")
        base = {k: v for k, v in state.items() if k != "participation"}
        super().restore_state(base)
        self.participation = part


class DispatchParallelPID(BaseParallelDroopPID):
    """3. Dispatch PID: Measures NIFTY Macro Grid Deviation."""
    def __init__(self, droop_r=0.05, enter_threshold=0.10, integral_limit=0.25,
                 kp=None, ki_per_second=None, kd_seconds=None):
        g = _config_gains('dispatch', kp, ki_per_second, kd_seconds)
        super().__init__(kp=g[0], ki_per_second=g[1], kd_seconds=g[2], droop_r=droop_r,
                         enter_threshold=enter_threshold, integral_limit=integral_limit)
        self.grid_deviation = 0.0

    def set_context(self, *, grid_deviation: float):
        self.grid_deviation = clamp(grid_deviation, -1.0, 1.0)

    def calculate_droop(self) -> float:
        return clamp(self.droop_r + 0.35 * abs(self.grid_deviation), 0.0, 0.75)

    def snapshot_state(self) -> dict:
        d = super().snapshot_state()
        d["grid_deviation"] = self.grid_deviation
        return d

    def restore_state(self, state: dict) -> None:
        if not isinstance(state, dict) or "grid_deviation" not in state:
            raise ValueError("missing grid_deviation in DispatchParallelPID checkpoint")
        gd = number(state["grid_deviation"])
        if not -1.0 <= gd <= 1.0:
            raise ValueError("invalid grid_deviation in checkpoint")
        base = {k: v for k, v in state.items() if k != "grid_deviation"}
        super().restore_state(base)
        self.grid_deviation = gd


class StudiesParallelPID(BaseParallelDroopPID):
    """4. Studies PID: Measures Technical Projection Confidence."""
    def __init__(self, droop_r=0.05, enter_threshold=0.10, integral_limit=0.25,
                 kp=None, ki_per_second=None, kd_seconds=None):
        g = _config_gains('studies', kp, ki_per_second, kd_seconds)
        super().__init__(kp=g[0], ki_per_second=g[1], kd_seconds=g[2], droop_r=droop_r,
                         enter_threshold=enter_threshold, integral_limit=integral_limit)
        self.confidence = 1.0

    def set_context(self, *, confidence: float):
        self.confidence = clamp(confidence, 0.0, 1.0)

    def calculate_droop(self) -> float:
        return clamp(self.droop_r + 0.45 * (1.0 - self.confidence), 0.0, 0.80)

    def snapshot_state(self) -> dict:
        d = super().snapshot_state()
        d["confidence"] = self.confidence
        return d

    def restore_state(self, state: dict) -> None:
        if not isinstance(state, dict) or "confidence" not in state:
            raise ValueError("missing confidence in StudiesParallelPID checkpoint")
        conf = number(state["confidence"])
        if not 0.0 <= conf <= 1.0:
            raise ValueError("invalid confidence in checkpoint")
        base = {k: v for k, v in state.items() if k != "confidence"}
        super().restore_state(base)
        self.confidence = conf


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
        # R8 cannot size native orders, so a native fill beyond the target is an
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
        the R8 target, the tracking error can be negative even though the market
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
