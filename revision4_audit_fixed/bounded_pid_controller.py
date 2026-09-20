"""
CRITICAL FIX #3: Bounded PID Controller with Integral-Term-Only Clamping

Purpose: Replace simple-pid's OUTPUT clamping with proper INTEGRAL-TERM clamping.

The Problem:
  simple-pid library clamps the final OUTPUT after computing:
    output = Kp*e + Ki*integral + Kd*de_dt
    output_clamped = clamp(output, -limit, +limit)

  With low Kp (0.055), the integral term dominates. Under sustained error,
  integral grows unbounded, output hits clamp, and stays pinned forever.

The Solution:
  Clamp ONLY the integral accumulation, not the total output:
    integral_state = clamp(integral_state + Ki*error*dt, -limit/Ki, +limit/Ki)
    output = Kp*e + integral_state + Kd*de_dt  [UNBOUNDED]

  This allows proportional and derivative terms to recover when error sign changes.

Author: Professional Code Audit (Critical Fix Implementation)
Date: September 20, 2026
"""

from typing import Optional


class BoundedPIDController:
    """PID controller with clamping on integral term only.

    CRITICAL DIFFERENCE from simple-pid:
    - simple-pid: Clamps output after computing it → integral can saturate
    - BoundedPID: Clamps integral term before computing output → proportional recovery

    Mathematical Foundation:
      Standard PID: u(t) = Kp*e(t) + Ki*∫e(t)dt + Kd*de/dt
      With integral anti-windup (proper): Clamp the integral state, not output

    Why This Matters:
      When confidence sits above setpoint for bars 1-10:
      - integral accumulates = 10 * error * Ki
      - with Ki=0.125, integral = 10 * 0.15 * 0.125 = 0.1875
      - this grows unbounded internally in simple-pid
      - when output is clamped to -0.0997, proportional term can't recover

      With our approach:
      - integral clamped at ±0.1 (adjustable)
      - proportional term = Kp*e still contributes
      - derivative term = Kd*de_dt still responds to changes
      - output can recover when error sign changes
    """

    def __init__(self, kp: float, ki: float, kd: float,
                 integral_limit: float, dt: float = 1.0,
                 name: str = "PID"):
        """Initialize PID controller.

        Args:
            kp: Proportional gain (e.g., 0.055)
            ki: Integral gain (e.g., 0.125)
            kd: Derivative gain (e.g., 0.475)
            integral_limit: Maximum integral state magnitude
                           Integral will be clamped to ±integral_limit
            dt: Time step (1.0 for bar-by-bar updates)
            name: Controller name for logging/debugging
        """
        self.kp = float(kp)
        self.ki = float(ki)
        self.kd = float(kd)
        self.integral_limit = float(integral_limit)
        self.dt = float(dt)
        self.name = name

        # State variables
        self.integral_state = 0.0
        self.previous_error = 0.0
        self.setpoint = 0.0

        # Telemetry (for debugging)
        self.last_output = 0.0
        self.last_proportional = 0.0
        self.last_integral = 0.0
        self.last_derivative = 0.0

    def update(self, measured_value: float, setpoint: Optional[float] = None) -> float:
        """Compute PID output.

        Args:
            measured_value: Current process variable (e.g., confidence)
            setpoint: Target value. If None, uses self.setpoint

        Returns:
            Control signal (unbounded)

        Algorithm:
            1. Compute error = setpoint - measured_value
            2. Compute proportional: Kp * error
            3. Accumulate integral: integral_state += Ki * error * dt
            4. Clamp integral_state ONLY (not total output)
            5. Compute derivative: Kd * (error - previous_error) / dt
            6. Output = Kp + Ki + Kd (UNBOUNDED)
        """
        # Update setpoint if provided
        if setpoint is not None:
            self.setpoint = float(setpoint)

        # 1. Error computation
        error = self.setpoint - measured_value

        # 2. Proportional term
        p_term = self.kp * error

        # 3. Integral term with INTEGRAL-ONLY clamping
        #    CRITICAL: Add to integral BEFORE clamping
        self.integral_state += self.ki * error * self.dt

        #    CRITICAL: Clamp INTEGRAL TERM, not output
        self.integral_state = max(
            -self.integral_limit,
            min(self.integral_limit, self.integral_state)
        )

        # 4. Derivative term
        de_dt = (error - self.previous_error) / self.dt
        d_term = self.kd * de_dt
        self.previous_error = error

        # 5. Total output (UNBOUNDED - no clamping here)
        output = p_term + self.integral_state + d_term

        # Store telemetry
        self.last_proportional = p_term
        self.last_integral = self.integral_state
        self.last_derivative = d_term
        self.last_output = output

        return output

    def reset(self) -> None:
        """Reset integral state (e.g., on trade exit)."""
        self.integral_state = 0.0
        self.previous_error = 0.0

    def get_telemetry(self) -> dict:
        """Return current controller state for logging."""
        return {
            "output": self.last_output,
            "proportional": self.last_proportional,
            "integral": self.last_integral,
            "derivative": self.last_derivative,
            "integral_state": self.integral_state,
        }

    def __repr__(self) -> str:
        return (f"{self.name}(Kp={self.kp:.3f}, Ki={self.ki:.3f}, "
                f"Kd={self.kd:.3f}, integral_limit={self.integral_limit:.3f})")


class BoundedPIDControllerComparison:
    """Helper class to compare old simple-pid vs new BoundedPID behavior."""

    @staticmethod
    def simulate_simple_pid_behavior(kp, ki, kd, output_limit, error_sequence):
        """Simulate simple-pid output clamping behavior."""
        integral = 0.0
        previous_error = 0.0
        outputs = []

        for error in error_sequence:
            p = kp * error
            integral += ki * error
            d = kd * (error - previous_error)

            # simple-pid clamps TOTAL output
            output = max(-output_limit, min(output_limit, p + integral + d))
            outputs.append(output)
            previous_error = error

        return outputs

    @staticmethod
    def simulate_bounded_pid_behavior(kp, ki, kd, integral_limit, error_sequence):
        """Simulate BoundedPID integral-only clamping behavior."""
        controller = BoundedPIDController(kp, ki, kd, integral_limit)
        outputs = []

        for i, error in enumerate(error_sequence):
            # Simulate: measure = setpoint - error
            setpoint = 0.5
            measured = setpoint - error
            output = controller.update(measured, setpoint=setpoint)
            outputs.append(output)

        return outputs

    @staticmethod
    def compare_sustained_error(kp=0.055, ki=0.125, kd=0.475,
                                output_limit=0.0997, integral_limit=0.1):
        """Compare behavior under sustained upward error (confidence > setpoint)."""

        # Scenario: confidence rises from 0.5 to 0.75
        # Setpoint: 0.5 (fixed)
        # Error = setpoint - measured = 0.5 - confidence (negative when above)
        errors = [-0.0, -0.05, -0.10, -0.15, -0.20, -0.20, -0.20,
                  -0.15, -0.10, -0.05, 0.0]  # Eventually confidence drops

        simple_pid_outputs = BoundedPIDControllerComparison.simulate_simple_pid_behavior(
            kp, ki, kd, output_limit, errors
        )

        bounded_outputs = BoundedPIDControllerComparison.simulate_bounded_pid_behavior(
            kp, ki, kd, integral_limit, errors
        )

        print("\nComparison: Sustained upward error (confidence > setpoint)")
        print("-" * 70)
        print(f"{'Bar':<5} {'Error':<10} {'simple-pid':<15} {'BoundedPID':<15}")
        print("-" * 70)

        for i, (error, simple, bounded) in enumerate(zip(errors, simple_pid_outputs, bounded_outputs)):
            print(f"{i+1:<5} {error:<10.4f} {simple:<15.6f} {bounded:<15.6f}")

        print("-" * 70)
        print(f"simple-pid final: {simple_pid_outputs[-1]:.6f} (should recover from -0.0997)")
        print(f"BoundedPID final: {bounded_outputs[-1]:.6f} (should show recovery)")

        # Check for recovery
        mid_point_simple = simple_pid_outputs[5]  # At peak sustained error
        final_simple = simple_pid_outputs[-1]

        mid_point_bounded = bounded_outputs[5]
        final_bounded = bounded_outputs[-1]

        print("\nRecovery Analysis:")
        print(f"simple-pid: {mid_point_simple:.6f} → {final_simple:.6f} " +
              ("✓ Recovered" if abs(final_simple - mid_point_simple) > 0.01 else "✗ Pinned"))
        print(f"BoundedPID: {mid_point_bounded:.6f} → {final_bounded:.6f} " +
              ("✓ Recovered" if abs(final_bounded - mid_point_bounded) > 0.01 else "✗ Pinned"))


if __name__ == "__main__":
    # Show the behavioral difference
    print("\n" + "="*70)
    print("CRITICAL FIX #3: Integral Anti-Windup - Behavior Comparison")
    print("="*70)

    BoundedPIDControllerComparison.compare_sustained_error()

    print("\n" + "="*70)
    print("Controller Ready for Integration")
    print("="*70)
