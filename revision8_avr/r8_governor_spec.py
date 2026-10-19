"""R8 vendored copy of revision5/governor.py (source commit 3a764cc, sha256 70c53223efb50267).
Copied: BayGovernorSpec, _InnerLoopState and PositionControlV3, the classes the parameter registry
builds.  The R5 governor trading loop is not part of R8 (it runs in the host plant)."""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite, sqrt, tanh
from statistics import fmean
from typing import Dict, Optional
from .r8_controller_parameters import GOVERNOR_COMMON_DEFAULTS as _GOV_DEFAULTS
from .r8_topology import BAY_IDS


@dataclass(frozen=True)
class BayGovernorSpec:
    """Per-bay governor configuration.  Values are owned by the canonical registry
    (``gov_<bay>_<field>`` in revision5/controller_parameters.py); runtime consumers receive them
    from ``R5ControllerParameters.from_config``.  The field defaults below are the registry
    defaults, not an independent source."""

    bay_id: str
    droop_r: float

    kp: float
    ki: float
    kd: float

    base_z: float
    z_exit: float

    atr_barrier: float
    target_m: float
    capital_weight: float

    target_r: float = _GOV_DEFAULTS["target_r"]

    outcome_window: int = _GOV_DEFAULTS["outcome_window"]
    integral_clamp: float = _GOV_DEFAULTS["integral_clamp"]

    dynamic_offset_min: float = _GOV_DEFAULTS["dynamic_offset_min"]
    dynamic_offset_max: float = _GOV_DEFAULTS["dynamic_offset_max"]

    grid_droop_gain: float = _GOV_DEFAULTS["grid_droop_gain"]
    grid_droop_max: float = _GOV_DEFAULTS["grid_droop_max"]

    # dynamic_z feedback offset gain: offset = clip(-feedback_gain * u, offset_min, offset_max).
    feedback_gain: float = _GOV_DEFAULTS["feedback_gain"]


@dataclass
class _InnerLoopState:
    """Fast inner-loop state of ONE open position."""

    active: bool = False
    integral_error: float = 0.0
    last_error: float = 0.0
    last_control_u: float = 0.0
    # Monotonic protective ratchet. It may advance only.
    protected_r_floor: float = -1.0
    trailing_active: bool = False
    # Protocol V3 position control only.
    v3_samples: int = 0
    v3_trailing_active: bool = False


@dataclass(frozen=True)
class PositionControlV3:
    """Protocol V3 closed-loop position control (opt-in engine mode ``closed_loop_v3``).

    Per completed bar k of one position (all quantities in R):

        e_k      = reference_r - measured_r
        gamma_k  = gamma_fast if |e_k| > tau_error_multiplier * noise_r else gamma_slow
        I_k      = clip(gamma_k * I_{k-1} + e_k, -I_max, +I_max)     (I_max = runtime integral clamp)
        D_k      = 0 on the first sample and on the first trailing-active sample, else e_k - e_{k-1}
        u_k      = Kp e_k + Ki I_k + Kd D_k                            (Ki applied once)
        gap_k    = max(base_gap * (1 - kappa * tanh(u_k)), noise_floor_mult * noise_r, minimum_gap)
        floor_k  = max(floor_{k-1}, hard_stop, MFE_k - gap_k)          once trailing is active

    noise_r = ATR / initial risk (one bar's typical move in R).  Trailing becomes active (and
    latches) once elapsed_bars >= grace_bars and MFE >= mfe_activation_r.  The binary path-error
    exit is not used: the controller acts only through the one-way protective floor, which the
    engine arms for the NEXT bar (no intrabar look-ahead).
    """

    mfe_activation_r: float
    kappa: float
    gamma_fast: float
    tau_error_multiplier: float
    gamma_slow: float
    base_gap_r: float
    minimum_gap_r: float
    noise_floor_mult: float
    grace_bars: int

    def __post_init__(self):
        values = (self.mfe_activation_r, self.kappa, self.gamma_fast, self.tau_error_multiplier,
                  self.gamma_slow, self.base_gap_r, self.minimum_gap_r, self.noise_floor_mult)
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(float(v))
                   for v in values):
            raise ValueError("V3 position control values must be finite numbers")
        if not 0.0 <= self.mfe_activation_r:
            raise ValueError("mfe_activation_r must be non-negative")
        if not 0.0 < self.kappa < 1.0:
            raise ValueError("kappa must lie in (0, 1)")
        if not 0.0 < self.gamma_fast <= self.gamma_slow <= 1.0:
            raise ValueError("require 0 < gamma_fast <= gamma_slow <= 1")
        if self.tau_error_multiplier <= 0.0 or self.noise_floor_mult <= 0.0:
            raise ValueError("tau_error_multiplier and noise_floor_mult must be positive")
        if not 0.0 < self.minimum_gap_r <= self.base_gap_r:
            raise ValueError("require 0 < minimum_gap_r <= base_gap_r")
        if isinstance(self.grace_bars, bool) or int(self.grace_bars) != self.grace_bars or self.grace_bars < 0:
            raise ValueError("grace_bars must be a non-negative integer")
