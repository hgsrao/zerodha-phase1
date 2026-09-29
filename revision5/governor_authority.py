"""
Revision 5 governor authority: causal market telemetry, the Speedtronic-Mark-V-style
minimum value gate (MVG) and the bay governor's ENTRY / HOLD / EXIT decision.

Responsibility matrix (top wins):

    1. Safety      BB07 safety gates, kill switch, drawdown halt, forced close  (orchestrator)
    2. Protection  ANSI 86 / unit trips / cooldown / MiCOM grid protection        (native plant)
    3. Governor    this module: the only entry and discretionary exit authority
    4. Advisors    PA, ID, chart studies, MPC plan, exit controller, path loop    (inputs only)

Telemetry (one owner per market measurement; every value is causal -- it uses the
completed bars supplied by the caller and nothing later):

    z-score        (close - mean(close, N)) / std(close, N)          governor comparator input
    ATR            mean true range over gov_telemetry_atr_bars
    vibration      ((high - low) - |close - open|) / ATR              wick turbulence (damper only)
    velocity       |close - previous close| / ATR                    Mark V FSRA input
    exhaust spread std(bay member 1-bar returns) / mean(member ATR/price)   bay hold / trip

Mark V limiters (each clamped to [0, 1]):

    FSRN  signal conviction              side-aligned min of PA and studies conviction, each as its
                                         causal percentile rank over gov_z_window_bars
    FSRT  exhaust temperature            1 - (mark-to-market drawdown / span) * slope
    FSRA  acceleration                   base - slope * velocity
    FSRS  startup ramp                   floor -> 1 over the first warm-up bars of the session
    FSRM  manual run limit               operator limit (fixed)

    FSR_selected  = min(FSRN, FSRT, FSRA, FSRS, FSRM)      (minimum value gate)
    FSR_effective = max(FSR_min_floor, FSR_selected)      (flameout floor: exits stay possible;
                                                           the floor never admits an entry)

Every numeric operating value is registry-owned (``PARAMETER_NAMES``).  Invalid or missing
inputs fail closed: no entry, and an open position is unwound.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite, sqrt
from statistics import fmean, pstdev
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

from revision2_external.bb05_bb06_parameters import require

PARAMETER_NAMES = (
    "gov_z_window_bars", "gov_telemetry_atr_bars",
    "mv_fsr_entry_threshold", "mv_fsr_exit_threshold", "mv_fsr_min_floor",
    "mv_fsrt_drawdown_span", "mv_fsrt_slope",
    "mv_fsra_base", "mv_fsra_slope",
    "mv_fsrs_warmup_bars", "mv_fsrs_floor",
    "mv_fsrm_manual_limit",
    "mv_vibration_damper_start", "mv_vibration_damper_gain",
    "mv_exhaust_spread_hold", "mv_exhaust_spread_trip",
    "gov_path_error_sigma",
)

LIMITER_NAMES = ("FSRN", "FSRT", "FSRA", "FSRS", "FSRM")


class GovernorInputError(ValueError):
    """A governor input is missing, non-finite or outside its physical range."""


def _finite(name: str, value: Any) -> float:
    if isinstance(value, bool):
        raise GovernorInputError(f"{name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GovernorInputError(f"{name} must be numeric") from exc
    if not isfinite(number):
        raise GovernorInputError(f"{name} must be finite")
    return number


def _unit(name: str, value: Any) -> float:
    number = _finite(name, value)
    if not 0.0 <= number <= 1.0:
        raise GovernorInputError(f"{name} must lie in [0, 1]")
    return number


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class GovernorAuthorityConfig:
    z_window_bars: int
    atr_bars: int
    fsr_entry_threshold: float
    fsr_exit_threshold: float
    fsr_min_floor: float
    fsrt_drawdown_span: float
    fsrt_slope: float
    fsra_base: float
    fsra_slope: float
    fsrs_warmup_bars: int
    fsrs_floor: float
    fsrm_manual_limit: float
    vibration_damper_start: float
    vibration_damper_gain: float
    exhaust_spread_hold: float
    exhaust_spread_trip: float
    path_error_sigma: float

    @classmethod
    def from_config(cls, config) -> "GovernorAuthorityConfig":
        v = {name: require(config, name) for name in PARAMETER_NAMES}
        built = cls(
            z_window_bars=int(v["gov_z_window_bars"]), atr_bars=int(v["gov_telemetry_atr_bars"]),
            fsr_entry_threshold=float(v["mv_fsr_entry_threshold"]),
            fsr_exit_threshold=float(v["mv_fsr_exit_threshold"]),
            fsr_min_floor=float(v["mv_fsr_min_floor"]),
            fsrt_drawdown_span=float(v["mv_fsrt_drawdown_span"]), fsrt_slope=float(v["mv_fsrt_slope"]),
            fsra_base=float(v["mv_fsra_base"]), fsra_slope=float(v["mv_fsra_slope"]),
            fsrs_warmup_bars=int(v["mv_fsrs_warmup_bars"]), fsrs_floor=float(v["mv_fsrs_floor"]),
            fsrm_manual_limit=float(v["mv_fsrm_manual_limit"]),
            vibration_damper_start=float(v["mv_vibration_damper_start"]),
            vibration_damper_gain=float(v["mv_vibration_damper_gain"]),
            exhaust_spread_hold=float(v["mv_exhaust_spread_hold"]),
            exhaust_spread_trip=float(v["mv_exhaust_spread_trip"]),
            path_error_sigma=float(v["gov_path_error_sigma"]),
        )
        built.validate()
        return built

    def validate(self) -> None:
        if not self.fsr_min_floor < self.fsr_exit_threshold < self.fsr_entry_threshold <= 1.0:
            raise ValueError("Mark V requires min_floor < exit_threshold < entry_threshold <= 1")
        if not self.exhaust_spread_hold < self.exhaust_spread_trip:
            raise ValueError("exhaust spread hold must be below trip")
        if self.z_window_bars < 2 or self.atr_bars < 1 or self.fsrs_warmup_bars < 1:
            raise ValueError("telemetry windows must be positive (z window >= 2)")
        if self.fsrt_drawdown_span <= 0.0:
            raise ValueError("FSRT drawdown span must be positive")
        if self.path_error_sigma <= 0.0:
            raise ValueError("path error sigma must be positive")


# ------------------------------------------------------------------------ telemetry

@dataclass(frozen=True)
class BarTelemetry:
    available: bool
    reason: str
    z_score: Optional[float] = None
    atr: Optional[float] = None
    atr_fraction: Optional[float] = None
    vibration: Optional[float] = None
    velocity: Optional[float] = None
    one_bar_return: Optional[float] = None


def bar_telemetry(bars, cfg: GovernorAuthorityConfig) -> BarTelemetry:
    """Causal measurements of the LAST row of ``bars`` (a completed-bar prefix)."""
    needed = max(cfg.z_window_bars, cfg.atr_bars + 1)
    if len(bars) < needed:
        return BarTelemetry(False, "TELEMETRY_WARMUP_INSUFFICIENT")
    tail = bars.iloc[-needed:]
    try:
        high, low, close = (tail[col].to_numpy(dtype=float) for col in ("high", "low", "close"))
        open_ = float(tail["open"].iloc[-1])
    except (TypeError, ValueError) as exc:
        return BarTelemetry(False, f"TELEMETRY_INVALID_BAR:{exc}")
    if not (np.isfinite(high).all() and np.isfinite(low).all() and np.isfinite(close).all()
            and isfinite(open_)):
        return BarTelemetry(False, "TELEMETRY_INVALID_BAR:non-finite OHLC")
    window = close[-cfg.z_window_bars:]
    spread = float(window.std())
    if spread <= 0.0:
        return BarTelemetry(False, "TELEMETRY_ZERO_DISPERSION")
    z_score = (float(close[-1]) - float(window.mean())) / spread
    h, l = high[-cfg.atr_bars:], low[-cfg.atr_bars:]
    prev = close[-cfg.atr_bars - 1:-1]
    atr = float(np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev))).mean())
    last, before = float(close[-1]), float(close[-2])
    if atr <= 0.0 or last <= 0.0 or before <= 0.0:
        return BarTelemetry(False, "TELEMETRY_ZERO_ATR_OR_PRICE")
    body = abs(last - open_)
    vibration = max(0.0, (float(high[-1]) - float(low[-1])) - body) / atr
    velocity = abs(last - before) / atr
    return BarTelemetry(True, "TELEMETRY_AVAILABLE", z_score=z_score, atr=atr,
                        atr_fraction=atr / last, vibration=vibration, velocity=velocity,
                        one_bar_return=last / before - 1.0)


def exhaust_spread(members: Sequence[BarTelemetry]) -> Optional[float]:
    """Bay 'thermocouple dispersion': cross-member 1-bar return dispersion in units of the
    members' mean ATR fraction.  None when fewer than two members have telemetry."""
    usable = [m for m in members if m.available]
    if len(usable) < 2:
        return None
    scale = fmean(m.atr_fraction for m in usable)
    if scale <= 0.0:
        return None
    return pstdev([m.one_bar_return for m in usable]) / scale


def session_bar_index(timestamps) -> int:
    """Completed bars of the current session before the last row (0 = session opening bar)."""
    last = timestamps.iloc[-1]
    same = timestamps[timestamps.dt.date == last.date()]
    return max(0, len(same) - 1)


# ------------------------------------------------------------------- Mark V limiters

def limiters(cfg: GovernorAuthorityConfig, *, conviction: float, drawdown: float,
             velocity: float, session_bar: int) -> Dict[str, float]:
    conviction = _unit("conviction", conviction)
    drawdown = _finite("drawdown", drawdown)
    velocity = _finite("velocity", velocity)
    if drawdown < 0.0 or velocity < 0.0:
        raise GovernorInputError("drawdown and velocity must be non-negative")
    try:
        valid_bar = not isinstance(session_bar, bool) and int(session_bar) == session_bar >= 0
    except (TypeError, ValueError, OverflowError):
        valid_bar = False
    if not valid_bar:
        raise GovernorInputError("session_bar must be a non-negative integer")
    ramp = min(1.0, int(session_bar) / cfg.fsrs_warmup_bars)
    return {
        "FSRN": conviction,
        "FSRT": _clip01(1.0 - (drawdown / cfg.fsrt_drawdown_span) * cfg.fsrt_slope),
        "FSRA": _clip01(cfg.fsra_base - cfg.fsra_slope * velocity),
        "FSRS": _clip01(cfg.fsrs_floor + (1.0 - cfg.fsrs_floor) * ramp),
        "FSRM": _clip01(cfg.fsrm_manual_limit),
    }


def minimum_value_gate(values: Mapping[str, float], floor: float) -> Dict[str, Any]:
    if set(values) != set(LIMITER_NAMES):
        raise GovernorInputError("minimum value gate requires exactly FSRN/FSRT/FSRA/FSRS/FSRM")
    checked = {name: _unit(name, values[name]) for name in LIMITER_NAMES}
    controlling = min(LIMITER_NAMES, key=lambda name: (checked[name], name))
    selected = checked[controlling]
    return {"limiters": checked, "controlling_limiter": controlling,
            "fsr_selected": selected, "fsr_effective": max(float(floor), selected)}


def causal_percentile_rank(prior: Sequence[float], value: float, window: int) -> Optional[float]:
    """Fraction of the ``window`` values observed BEFORE this bar that are <= ``value``.

    Places a confidence on its own recent distribution, so FSRN is on the same [0, 1] scale as
    the Mark V thresholds whatever the absolute scale of the upstream signal.  None until a full
    window of prior values exists, or when any value is non-finite (the caller fails closed).
    """
    if len(prior) < window or not isfinite(float(value)):
        return None
    recent = [float(x) for x in list(prior)[-window:]]
    if not all(isfinite(x) for x in recent):
        return None
    return sum(1 for x in recent if x <= float(value)) / window


def side_aligned_conviction(side: str, *, pa_direction: int, pa_confidence: float,
                            studies_direction: Optional[int], studies_confidence: float) -> float:
    """FSRN from two conviction measures (normally their causal percentile ranks): a signal that
    opposes ``side`` contributes zero; a neutral or aligned signal contributes its value."""
    expected = 1 if side == "BUY" else -1
    pa = 0.0 if int(pa_direction) == -expected else _unit("pa_confidence", pa_confidence)
    studies = (0.0 if studies_direction is not None and int(studies_direction) == -expected
               else _unit("studies_confidence", studies_confidence))
    return min(pa, studies)


# ------------------------------------------------------------------- decisions

def entry_decision(governor, cfg: GovernorAuthorityConfig, *, side: str, telemetry: BarTelemetry,
                   conviction: float, drawdown: float, session_bar: int,
                   grid_return_fraction: float = 0.0,
                   bay_exhaust_spread: Optional[float] = None,
                   entry_mode: str = "mean_reversion") -> Dict[str, Any]:
    """The governor's ENTRY / NO_ACTION decision.  Fails closed to NO_ACTION."""
    base = {"side": side, "bay_exhaust_spread": bay_exhaust_spread}
    if not telemetry.available:
        return {**base, "action": "NO_ACTION", "reason": telemetry.reason, "size_multiplier": 0.0}
    try:
        comparator = governor.evaluate_entry_request(
            z_score=telemetry.z_score, grid_return_fraction=grid_return_fraction, side=side,
            entry_mode=entry_mode)
        gate = minimum_value_gate(limiters(cfg, conviction=conviction, drawdown=drawdown,
                                           velocity=telemetry.velocity, session_bar=session_bar),
                                  cfg.fsr_min_floor)
        spread = None if bay_exhaust_spread is None else _finite("bay_exhaust_spread", bay_exhaust_spread)
    except (GovernorInputError, ValueError) as exc:
        return {**base, "action": "NO_ACTION", "reason": f"INVALID_GOVERNOR_INPUT:{exc}",
                "size_multiplier": 0.0}
    hurdle = cfg.fsr_entry_threshold + cfg.vibration_damper_gain * max(
        0.0, telemetry.vibration - cfg.vibration_damper_start)
    detail = {**base, **gate, "comparator": comparator, "entry_hurdle": hurdle,
              "vibration": telemetry.vibration, "velocity": telemetry.velocity,
              "z_score": telemetry.z_score}
    if spread is not None and spread >= cfg.exhaust_spread_hold:
        return {**detail, "action": "NO_ACTION", "reason": "EXHAUST_SPREAD_HOLD", "size_multiplier": 0.0}
    if comparator["action"] != "ENTRY":
        return {**detail, "action": "NO_ACTION", "reason": comparator["reason"], "size_multiplier": 0.0}
    if gate["fsr_selected"] < hurdle:
        return {**detail, "action": "NO_ACTION", "reason": f"FSR_BELOW_ENTRY_HURDLE:{gate['controlling_limiter']}",
                "size_multiplier": 0.0}
    # Size can only be derated: the selected FSR (<= 1) scales the already-approved quantity.
    return {**detail, "action": "ENTRY", "reason": "GOVERNOR_ENTRY", "size_multiplier": gate["fsr_selected"]}


def position_decision(governor, cfg: GovernorAuthorityConfig, *, position_id, measured_r: float,
                      reference_r: float, max_favorable_r: float, elapsed_bars: int,
                      min_hold_bars: int, max_hold_bars: int, trade_target_r: float,
                      conviction: float, drawdown: float, velocity: Optional[float],
                      session_bar: int, bay_exhaust_spread: Optional[float] = None,
                      hard_stop_r: float = -1.0, path_noise_r: Optional[float] = None,
                      fuel_cut_confirmed: Optional[bool] = None) -> Dict[str, Any]:
    """The governor's HOLD / EXIT decision for one open position.  Fails closed to EXIT.

    ``path_noise_r`` is one bar's typical move in R (current ATR / initial risk).  The inner loop
    then exits on path error only beyond ``gov_path_error_sigma`` noise envelopes; the legacy
    tolerance (the outer loop's 0.30R setpoint) cut 82 of 146 real trial-0 trades on noise."""
    try:
        if path_noise_r is None:
            raise GovernorInputError("path noise (ATR / risk) unavailable")
        inner = governor.evaluate_position_control(
            measured_r=measured_r, reference_r=reference_r, max_favorable_r=max_favorable_r,
            elapsed_bars=int(elapsed_bars), min_hold_bars=int(min_hold_bars),
            max_hold_bars=int(max_hold_bars), hard_stop_r=hard_stop_r,
            trade_target_r=trade_target_r, position_id=position_id,
            path_noise_r=_finite("path_noise_r", path_noise_r), path_error_sigma=cfg.path_error_sigma)
        if velocity is None:
            raise GovernorInputError("velocity telemetry unavailable")
        gate = minimum_value_gate(limiters(cfg, conviction=conviction, drawdown=drawdown,
                                           velocity=velocity, session_bar=session_bar),
                                  cfg.fsr_min_floor)
        spread = None if bay_exhaust_spread is None else _finite("bay_exhaust_spread", bay_exhaust_spread)
    except (GovernorInputError, ValueError) as exc:
        return {"action": "EXIT", "reason": f"INVALID_GOVERNOR_INPUT:{exc}", "load_shed": False}
    detail = {**gate, "inner": inner, "bay_exhaust_spread": spread,
              "protected_r_floor": inner["protected_r_floor"]}
    if inner["action"] == "EXIT":
        return {**detail, "action": "EXIT", "reason": inner["reason"], "load_shed": False}
    if spread is not None and spread >= cfg.exhaust_spread_trip:
        return {**detail, "action": "EXIT", "reason": "EXHAUST_SPREAD_TRIP", "load_shed": False}
    if fuel_cut_confirmed is not None:
        # Only conviction receives persistence. Independent protective limiters retain authority.
        others = {k: v for k, v in gate["limiters"].items() if k != "FSRN"}
        limiter = min(others, key=lambda k: (others[k], k))
        if others[limiter] < cfg.fsr_exit_threshold:
            return {**detail, "action": "EXIT", "reason": f"FSR_BELOW_EXIT:{limiter}", "load_shed": False}
        if fuel_cut_confirmed:
            return {**detail, "action": "EXIT", "reason": "FSRN_SUSTAINED_DETERIORATION", "load_shed": False}
    elif gate["fsr_selected"] < cfg.fsr_exit_threshold:
        return {**detail, "action": "EXIT", "reason": f"FSR_BELOW_EXIT:{gate['controlling_limiter']}",
                "load_shed": False}
    # Between the exit and entry thresholds Mark V sheds load.  The replay engine carries one
    # indivisible position per symbol, so load shedding is reported and the position is held.
    load_shed = gate["fsr_selected"] < cfg.fsr_entry_threshold
    return {**detail, "action": "HOLD", "reason": "GOVERNOR_LOAD_SHED" if load_shed else "GOVERNOR_TRACKING",
            "load_shed": load_shed}
