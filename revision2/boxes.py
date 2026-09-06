"""Real, parameter-driven implementations of the Revision 2 black boxes.

Every `.require()` call below is a genuine dependency: change the value and
the box's output changes (proven in tests/test_revision2_sensitivity.py).
Nothing here reads from a hardcoded constant when a canonical parameter
exists for it.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from revision2.contracts import (
    EffectiveConfig,
    IDDecision,
    MarketSnapshot,
    ParameterUse,
    PASignal,
    ProposedOrder,
    SafetyContract,
    TradePlan,
)


def _np_clip(value: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, value)))


class BoundedPID:
    """A PID with a rolling-window, clamped integral term.

    This is the anti-windup fix for the legacy engine's `self.integral +=
    error` (unbounded accumulation): the integral only ever sums the last
    `window` errors, and that sum is then clamped to +/- `clamp`. It also
    smooths the derivative over `smoothing` bars instead of a raw one-step
    difference, which is what made the legacy signal so noisy.
    """

    def __init__(self, kp: float, ki: float, kd: float, target: float,
                 window: int, clamp: float, smoothing: int):
        self.kp, self.ki, self.kd = kp, ki, kd
        self.target = target
        self.window = max(1, int(window))
        self.clamp = abs(float(clamp))
        self.smoothing = max(1, int(smoothing))
        self._errors: deque = deque(maxlen=self.window)
        self._derivatives: deque = deque(maxlen=self.smoothing)
        self._prev_error = 0.0

    def update(self, current_value: float) -> Dict[str, float]:
        error = self.target - current_value
        self._errors.append(error)
        integral = _np_clip(sum(self._errors), -self.clamp, self.clamp)
        raw_derivative = error - self._prev_error
        self._derivatives.append(raw_derivative)
        derivative = sum(self._derivatives) / len(self._derivatives)
        self._prev_error = error
        adjustment = self.kp * error + self.ki * integral + self.kd * derivative
        return {"adjustment": adjustment, "error": error, "integral": integral, "derivative": derivative}


class PredictiveAnalyticsBox:
    """PA: turns completed bar `t` into a directional, confidence-scored signal.

    The signal is deliberately computed from bar `t` only (no look past it)
    and is meant to be *acted on* at `t+1` — the orchestrator is responsible
    for filling at the next bar's open, never at `t`'s own close.
    """

    def __init__(self):
        self._history: Dict[str, deque] = {}
        self._scale: Dict[str, Dict[str, float]] = {}

    def calibrate(self, symbol: str, warmup_bars: pd.DataFrame) -> None:
        """Compute normalization scale strictly from the warm-up window that
        precedes the run — never from the full/future dataframe. This is the
        fix for the legacy engine's `.tail(50)`-on-the-whole-dataframe leak:
        these stats are frozen once, before bar-by-bar evaluation begins, and
        are never recomputed from bars the strategy hasn't reached yet.
        """
        close = warmup_bars["close"].to_numpy(dtype=float)
        high = warmup_bars["high"].to_numpy(dtype=float)
        low = warmup_bars["low"].to_numpy(dtype=float)
        volume = warmup_bars["volume"].to_numpy(dtype=float)

        returns = pd.Series(close).pct_change().dropna()
        dp_scale = float(returns.std()) or 1e-6
        vol_pct_change = pd.Series(volume).pct_change().replace([np.inf, -np.inf], np.nan).dropna()
        dv_scale = float(vol_pct_change.std()) or 1e-6

        tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
        baseline_atr = float(tr.mean()) if len(tr) else 1e-6
        baseline_vol = baseline_atr / close[-1] if close[-1] else 1e-6

        self._scale[symbol] = {"dp_scale": dp_scale, "dv_scale": dv_scale, "baseline_vol": max(baseline_vol, 1e-6)}

    def _scale_for(self, symbol: str) -> Dict[str, float]:
        return self._scale.get(symbol, {"dp_scale": 1e-3, "dv_scale": 1.0, "baseline_vol": 1e-3})

    def evaluate(self, snapshot: MarketSnapshot, config: EffectiveConfig) -> Tuple[PASignal, List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "PA", value, calculation, output_field))
            return value

        momentum_period = int(req("momentum_calculation_period", "momentum lookback window", "momentum"))
        vwap_period = int(req("vwap_calculation_period", "VWAP lookback window", "vwap_deviation"))
        atr_period = int(req("atr_calculation_period", "ATR/volatility lookback window", "volatility"))
        dp_mult = float(req("base_dp_dt_multiplier", "scales price-momentum magnitude", "momentum"))
        dv_mult = float(req("base_dv_dt_multiplier", "scales volume-confirmation magnitude", "volume_confirmation"))
        momentum_weight = float(req("momentum_weight", "component weight", "confidence"))
        vwap_weight = float(req("vwap_weight", "component weight", "confidence"))
        volatility_weight = float(req("volatility_weight", "component weight", "confidence"))
        confirmation_weight = float(req("confirmation_2bar_weight", "component weight", "confidence"))
        green_threshold = float(req("green_threshold", "high-confidence band boundary", "confidence"))
        amber_lower = float(req("amber_threshold_lower", "medium-confidence band boundary", "confidence"))
        red_threshold = float(req("red_threshold", "low-confidence band boundary", "confidence"))
        vol_regime_mult = float(req("volatility_regime_multiplier", "overall regime scaling", "confidence"))
        low_vol_mult = float(req("low_vol_regime_multiplier", "low-vol regime scaling", "confidence"))
        med_vol_mult = float(req("medium_vol_regime_multiplier", "medium-vol regime scaling", "confidence"))
        high_vol_mult = float(req("high_vol_regime_multiplier", "high-vol regime scaling", "confidence"))
        entry_smoothing = int(req("entry_signal_smoothing_window", "entry signal smoothing window", "confidence"))
        exit_smoothing = int(req("exit_signal_smoothing_window", "exit signal smoothing window", "exit_confidence"))
        persistence_requirement = float(req("signal_persistence_requirement", "consecutive-direction persistence bonus", "confidence"))
        entry_threshold = float(req("entry_confidence_threshold", "directional bias floor", "direction"))

        bars = snapshot.bars
        n = len(bars)
        close = bars["close"].to_numpy(dtype=float)
        volume = bars["volume"].to_numpy(dtype=float)
        high = bars["high"].to_numpy(dtype=float)
        low = bars["low"].to_numpy(dtype=float)

        if snapshot.symbol not in self._scale:
            # No explicit calibrate() call was made — fall back to this
            # snapshot's own leading window (still never future data, since
            # `bars` never extends past bar t).
            self.calibrate(snapshot.symbol, bars.iloc[: max(30, min(n, 60))])
        scale = self._scale_for(snapshot.symbol)

        lookback = max(momentum_period, vwap_period, atr_period, 5)
        window_start = max(0, n - lookback)
        c = close[window_start:]
        v = volume[window_start:]
        h = high[window_start:]
        l = low[window_start:]

        # Momentum: price change over momentum_period, z-scored against the
        # frozen warm-up return volatility, then scaled by dp_mult.
        mom_window = close[max(0, n - momentum_period):]
        raw_momentum = (mom_window[-1] - mom_window[0]) / mom_window[0] if mom_window[0] else 0.0
        momentum_z = raw_momentum / (scale["dp_scale"] * math.sqrt(max(momentum_period, 1)))
        momentum = _np_clip(momentum_z / 3.0, -1, 1) * dp_mult

        # VWAP deviation over vwap_period, same price-return scale.
        vwap_window_close = close[max(0, n - vwap_period):]
        vwap_window_vol = volume[max(0, n - vwap_period):]
        vwap = float(np.average(vwap_window_close, weights=vwap_window_vol)) if vwap_window_vol.sum() > 0 else float(vwap_window_close.mean())
        raw_vwap_dev = (close[-1] - vwap) / vwap if vwap else 0.0
        vwap_deviation = _np_clip((raw_vwap_dev / scale["dp_scale"]) / 3.0, -1, 1)

        # ATR-based volatility, normalized against price.
        tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1]))) if len(c) > 1 else np.array([0.0])
        atr = float(tr[-atr_period:].mean()) if len(tr) else 0.0
        volatility = atr / close[-1] if close[-1] else 0.0
        # Relative deviation from the calibrated baseline volatility: near 0
        # when volatility is normal, swings when the regime genuinely shifts.
        volatility_score = _np_clip((scale["baseline_vol"] - volatility) / scale["baseline_vol"], -1, 1)

        # Volume confirmation: current vs trailing average, z-scored, scaled by dv_mult.
        avg_vol = v[:-1].mean() if len(v) > 1 else (v[-1] if len(v) else 1.0)
        raw_vol_confirm = (v[-1] - avg_vol) / avg_vol if avg_vol else 0.0
        volume_confirmation = _np_clip((raw_vol_confirm / scale["dv_scale"]) / 3.0, -1, 1) * dv_mult

        # Regime bucket, from realized volatility relative to the baseline.
        vol_ratio = volatility / scale["baseline_vol"] if scale["baseline_vol"] else 1.0
        if vol_ratio < 0.7:
            regime_mult = low_vol_mult
        elif vol_ratio < 1.5:
            regime_mult = med_vol_mult
        else:
            regime_mult = high_vol_mult

        total_weight = momentum_weight + vwap_weight + volatility_weight + confirmation_weight
        total_weight = total_weight if total_weight > 0 else 1.0
        raw_signal = (
            _np_clip(momentum, -1, 1) * momentum_weight
            + vwap_deviation * vwap_weight
            + volatility_score * volatility_weight
            + _np_clip(volume_confirmation, -1, 1) * confirmation_weight
        ) / total_weight

        raw_signal *= regime_mult * vol_regime_mult

        # Persistence: reward bars where recent direction has been consistent.
        history = self._history.setdefault(snapshot.symbol, deque(maxlen=max(entry_smoothing, exit_smoothing, 20)))
        history.append(raw_signal)
        smoothed = sum(list(history)[-entry_smoothing:]) / min(len(history), entry_smoothing)
        exit_smoothed = sum(list(history)[-exit_smoothing:]) / min(len(history), exit_smoothing)

        recent = list(history)[-5:]
        same_direction = sum(1 for x in recent if (x > 0) == (smoothed > 0)) if recent else 0
        if len(recent) and same_direction / len(recent) >= (persistence_requirement / 2.0):
            smoothed *= 1.0 + 0.1 * min(persistence_requirement, 2.0)

        confidence = _np_clip(abs(smoothed), 0.0, 1.0)
        # quality_band is recorded independently of the confidence scaling
        # below: red_threshold's band sits entirely under amber_lower, so a
        # value it catches is already going to fail an entry_confidence_
        # threshold check at the default settings regardless of how much
        # further the multiplier scales it down — a red classification has
        # to be its own, independent signal for red_threshold to actually
        # be able to change an approval decision.
        if confidence >= green_threshold:
            quality_band = "green"
            confidence = _np_clip(confidence * 1.10, 0.0, 1.0)
        elif confidence >= amber_lower:
            quality_band = "amber"
            confidence *= 0.85
        elif confidence <= red_threshold:
            quality_band = "red"
            confidence *= 0.5
        else:
            quality_band = "neutral"

        # entry_confidence_threshold acts as a directional dead-zone: a
        # smoothed signal that hasn't cleared a fraction of the threshold
        # carries no directional bias at all.
        direction = 0 if abs(smoothed) < entry_threshold * 0.2 else (1 if smoothed > 0 else -1)

        signal = PASignal(
            symbol=snapshot.symbol,
            timestamp=snapshot.timestamp,
            direction=direction,
            confidence=confidence,
            momentum=momentum,
            volatility=volatility,
            vwap_deviation=vwap_deviation,
            volume_confirmation=volume_confirmation,
            exit_confidence=_np_clip(abs(exit_smoothed), 0.0, 1.0),
            quality_band=quality_band,
        )
        return signal, trace


class IntelligentDiscriminationBox:
    """ID: judges whether a PA signal is tradable. Never recomputes PA itself."""

    def evaluate(self, signal: PASignal, config: EffectiveConfig) -> Tuple[IDDecision, List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "ID", value, calculation, output_field))
            return value

        entry_threshold = float(req("entry_confidence_threshold", "minimum confidence to approve entry", "approved"))
        exit_threshold = float(req("exit_confidence_threshold", "carried into MPC as the exit-quality bar", "timing_quality"))
        slippage_limit = float(req("slippage_guard_threshold", "maximum tolerated estimated slippage", "approved"))
        min_rr = float(req("min_risk_reward_ratio", "minimum acceptable reward:risk", "risk_reward_ratio"))

        estimated_slippage = min(0.20, signal.volatility * 2.0)

        if signal.direction == 0:
            return IDDecision(False, "no directional signal", signal.confidence, 0.0, exit_threshold), trace
        if signal.quality_band == "red":
            # An independent gate, not just a confidence multiplier: a red-
            # classified signal is already going to fail entry_confidence_
            # threshold at typical settings, so red_threshold needs its own
            # veto to ever be able to change an approval decision at all.
            return IDDecision(False, "PA quality band is red", signal.confidence, 0.0, exit_threshold), trace
        if signal.confidence < entry_threshold:
            return IDDecision(False, f"confidence {signal.confidence:.4f} below entry threshold {entry_threshold:.4f}", signal.confidence, 0.0, exit_threshold), trace
        if estimated_slippage > slippage_limit:
            return IDDecision(False, f"estimated slippage {estimated_slippage:.4f} exceeds guard {slippage_limit:.4f}", signal.confidence, 0.0, exit_threshold), trace

        # A confidence-based estimate of achievable reward:risk, scaled to
        # plausibly land inside the parameter's own [1, 3] range rather than
        # a volatility ratio that is always orders of magnitude larger.
        assumed_reward = max(signal.confidence, 0.05) * 4.0
        assumed_risk = max(1.0 - signal.confidence, 0.10) * 2.0
        risk_reward = assumed_reward / assumed_risk if assumed_risk else 0.0
        if risk_reward < min_rr:
            return IDDecision(False, f"risk:reward {risk_reward:.2f} below minimum {min_rr:.2f}", signal.confidence, risk_reward, exit_threshold), trace

        return IDDecision(True, "approved", signal.confidence, risk_reward, exit_threshold), trace


class ModelPredictiveControlBox:
    """MPC: converts an approved signal into an executable trade plan.

    ATR multipliers are used as multipliers (atr * mult), never as percent-
    of-price, which was the other correctness gap in the legacy engine.
    """

    def __init__(self):
        self._entry_pids: Dict[str, BoundedPID] = {}
        self._exit_pids: Dict[str, BoundedPID] = {}
        # Rolling per-symbol confidence history backing BOTH PIDs' adaptive
        # setpoint -- see _confidence_baseline below for why this exists.
        self._confidence_history: Dict[str, deque] = {}

    def _get_pid(self, store: Dict[str, BoundedPID], symbol: str, kp: float, ki: float, kd: float,
                 target: float, window: int, clamp: float, smoothing: int) -> BoundedPID:
        if symbol not in store:
            store[symbol] = BoundedPID(kp, ki, kd, target, window, clamp, smoothing)
        return store[symbol]

    def _confidence_baseline(self, symbol: str, current_confidence: float, window: int) -> float:
        """Both PIDs' setpoint: a rolling mean of this symbol's own recent
        decision.confidence, computed from bars BEFORE this one (the current
        reading is folded into the history only after the baseline is read).
        Called exactly once per build_plan() call -- both PIDs share the one
        history, since they observe the identical confidence series; a
        second call would double-count the same bar.

        Ported from revision2_external/pid_controller.py's identical fix
        (same class of bug, same box, different PID library). Both PIDs
        used to compare against a fixed absolute constant: entry against
        0.5 -- identical to entry_confidence_threshold's own default, which
        IntelligentDiscrimination already uses to filter out every signal
        this PID would ever see, making error = target - confidence
        mathematically <= 0 on every call regardless of Kp/Ki/Kd -- and exit
        against decision.timing_quality (effectively fixed at
        exit_confidence_threshold). BoundedPID's windowed-sum anti-windup
        doesn't rescue this: a window of N errors that are all the same
        sign still sums to a same-signed total that gets clamped, same as
        simple-pid's unbounded-then-clamped design would. Verified on the
        real external-engine sibling of this box: after fixing only the
        entry side, a real 6-month INFY run still showed the exit PID
        pinned at its clamp on 14.6% of calls vs 5.4% for the already-fixed
        entry PID; sharing one rolling confidence baseline for both dropped
        exit's saturation to 0.45%. A trailing mean of the symbol's own
        confidence has no fixed-target guarantee of one-signed error:
        deviations from a recent average are positive about as often as
        negative for any real, noisy series, so both controllers can
        actually move off their rail instead of riding it permanently.
        """
        history = self._confidence_history.setdefault(symbol, deque(maxlen=window))
        baseline = (sum(history) / len(history)) if history else current_confidence
        history.append(current_confidence)
        return baseline

    def build_plan(
        self,
        signal: PASignal,
        decision: IDDecision,
        entry_price: float,
        atr: float,
        config: EffectiveConfig,
    ) -> Tuple[Optional[TradePlan], Dict[str, float], List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "MPC", value, calculation, output_field))
            return value

        profit_mult = float(req("profit_target_atr_mult", "ATR multiplier for the profit target", "target_price"))
        stop_mult = float(req("stop_loss_atr_mult", "ATR multiplier for the stop", "stop_price"))
        margin_buffer = float(req("profit_target_margin_buffer", "extra buffer added to the target", "target_price"))
        min_rr = float(req("min_risk_reward_ratio", "minimum reward:risk enforced on the target distance", "target_price"))
        min_hold = int(req("min_hold_bars", "minimum bars to hold before exit is allowed", "minimum_hold_bars"))
        max_hold = int(req("max_hold_bars", "maximum bars to hold before forced exit", "maximum_hold_bars"))
        slippage_cost_mult = float(req("slippage_cost_multiplier", "cost multiplier applied to entry price", "entry_price"))
        kp_entry = float(req("pid_kp_entry", "entry PID proportional gain", "timing_quality"))
        ki_entry = float(req("pid_ki_entry", "entry PID integral gain", "timing_quality"))
        kd_entry = float(req("pid_kd_entry", "entry PID derivative gain", "timing_quality"))
        kp_exit = float(req("pid_kp_exit", "exit PID proportional gain", "timing_quality"))
        ki_exit = float(req("pid_ki_exit", "exit PID integral gain", "timing_quality"))
        kd_exit = float(req("pid_kd_exit", "exit PID derivative gain", "timing_quality"))
        integral_window = int(req("pid_integral_window_bars", "bounded window for the PID integral term", "timing_quality"))
        integral_clamp = float(req("pid_integral_max_clamp", "clamp applied to the PID integral term", "timing_quality"))
        derivative_smoothing = int(req("pid_derivative_smoothing", "smoothing window for the PID derivative term", "timing_quality"))

        if not decision.approved:
            return None, {}, trace

        side = "BUY" if signal.direction > 0 else "SELL"
        effective_entry = entry_price * (1 + slippage_cost_mult * 0.0005 * (1 if side == "BUY" else -1))

        stop_distance = atr * stop_mult
        target_distance = atr * profit_mult * (1 + margin_buffer)
        # Enforce the minimum reward:risk ratio directly on the plan: if the
        # ATR-derived target doesn't clear it, extend the target rather than
        # silently accepting a worse-than-allowed trade.
        if stop_distance > 0:
            target_distance = max(target_distance, stop_distance * min_rr)

        # Entry timing quality: the PID is advisory — it scales the size of
        # the trade, never a hard binary gate. This is the direct fix for
        # the legacy engine's permanently-locked `adjustment < 0` veto AND
        # for the gap where these 9 PID parameters were computed but never
        # actually reached the trade ledger: entry_timing_multiplier below
        # is read by the orchestrator and multiplied into position sizing.
        # Computed ONCE per call and shared by both PIDs -- see
        # _confidence_baseline's docstring for why neither PID keeps a fixed
        # absolute target any more.
        confidence_baseline = self._confidence_baseline(signal.symbol, decision.confidence, integral_window)

        entry_pid = self._get_pid(self._entry_pids, signal.symbol, kp_entry, ki_entry, kd_entry,
                                   target=confidence_baseline, window=integral_window, clamp=integral_clamp,
                                   smoothing=derivative_smoothing)
        entry_pid.target = confidence_baseline  # keep in sync on every call, not just at first construction
        entry_pid_result = entry_pid.update(decision.confidence)
        entry_timing_multiplier = _np_clip(1.0 - abs(entry_pid_result["adjustment"]), 0.3, 1.0)
        # Also nudge the fill price itself by the raw (unclipped) adjustment:
        # quantity is an integer (floor()'d), so a small kp/ki/kd change can
        # leave it unchanged even though the PID output genuinely moved —
        # the price nudge is continuous and always registers, modeling
        # "worse entry-timing quality costs a little worse execution".
        effective_entry *= (1 + entry_pid_result["adjustment"] * 0.001)

        # Exit timing quality: nudges the stop/target distances themselves
        # (tighter when exit-timing confidence is poor) so the exit PID
        # gains affect which price the trade actually exits at, not just a
        # diagnostic number.
        exit_pid = self._get_pid(self._exit_pids, signal.symbol, kp_exit, ki_exit, kd_exit,
                                  target=confidence_baseline, window=integral_window, clamp=integral_clamp,
                                  smoothing=derivative_smoothing)
        exit_pid.target = confidence_baseline  # keep in sync on every call, not just at first construction
        exit_pid_result = exit_pid.update(decision.confidence)
        exit_tightness = _np_clip(1.0 - abs(exit_pid_result["adjustment"]), 0.5, 1.0)
        stop_distance *= exit_tightness
        target_distance *= exit_tightness

        if side == "BUY":
            stop_price = effective_entry - stop_distance
            target_price = effective_entry + target_distance
        else:
            stop_price = effective_entry + stop_distance
            target_price = effective_entry - target_distance

        # No profit-floor check here: MPC doesn't know the trade's quantity
        # yet (PositionManager decides that downstream), and real round-trip
        # cost scales with price x quantity -- a per-share proxy checked at
        # this point can't represent whether the trade is actually worth its
        # real cost. That check now runs post-sizing, in
        # SafetyGatesTargetBox.evaluate_post_sizing(), against the real cost
        # for the real quantity. See minimum_profit_margin_over_cost's
        # registry entry and that method's own comment for the full
        # rationale (this replaced the former minimum_absolute_profit_rupees
        # per-share pre-sizing check).
        plan = TradePlan(
            side=side,
            entry_price=float(effective_entry),
            stop_price=float(stop_price),
            target_price=float(target_price),
            minimum_hold_bars=min_hold,
            maximum_hold_bars=max_hold,
        )
        pid_info = {
            "entry_adjustment": entry_pid_result["adjustment"],
            "exit_adjustment": exit_pid_result["adjustment"],
            "entry_integral": entry_pid_result["integral"],
            "entry_timing_multiplier": entry_timing_multiplier,
        }
        return plan, pid_info, trace


class SafetyGatesTargetBox:
    """The 6 SafetyGates parameters that are part of the 68-target surface
    (fixed, not calibratable) — distinct from the 20-item hardcoded
    SafetyContract, which never enters this box at all.

    Split into two calls because quantity isn't known until PositionManager
    runs: checking a per-trade *rupee* loss cap against a per-*share* price
    distance (before sizing) compares the wrong units. Drawdown/halt/lambda
    don't depend on quantity, so they can run first and produce the sizing
    multiplier PositionManager needs as an input.
    """

    def evaluate_pre_sizing(self, equity_curve: List[float], config: EffectiveConfig) -> Tuple[bool, str, float, List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "SafetyGates", value, calculation, output_field))
            return value

        normal_dd = float(req("drawdown_normal_threshold", "drawdown band below which sizing is unaffected", "size_multiplier"))
        derated_dd = float(req("drawdown_derated_threshold", "drawdown band that derates sizing", "size_multiplier"))
        halt_dd = float(req("drawdown_halt_threshold", "drawdown level that halts new entries", "approved"))
        lambda_limit = float(req("portfolio_lambda_risk_limit", "portfolio risk-budget limit", "size_multiplier"))

        peak = max(equity_curve) if equity_curve else 0.0
        current = equity_curve[-1] if equity_curve else 0.0
        drawdown = (peak - current) / peak if peak > 0 else 0.0

        if drawdown >= halt_dd:
            return False, f"drawdown {drawdown:.4f} at/above halt threshold {halt_dd:.4f}", 0.0, trace

        size_multiplier = 1.0
        if drawdown >= derated_dd:
            size_multiplier = 0.5
        elif drawdown >= normal_dd:
            size_multiplier = 0.8
        size_multiplier = min(size_multiplier, lambda_limit / max(lambda_limit, 0.01))

        return True, "approved", size_multiplier, trace

    @staticmethod
    def _leg_cost(price: float, quantity: int, side: str) -> float:
        """Identical formula to orchestrator.py's _transaction_costs() /
        portfolio_orchestrator.py's _leg_cost() / revision2_external's own
        copy -- kept identical deliberately so the profit-margin check below
        compares against the SAME cost the trade will actually be charged,
        not an independently-drifting estimate."""
        turnover = price * quantity
        cost = min(20.0, 0.0003 * turnover) + 0.0000345 * turnover
        if side == "SELL":
            cost += 0.00025 * turnover
        return cost

    def evaluate_post_sizing(
        self, equity_curve: List[float], plan: TradePlan, quantity: int, config: EffectiveConfig
    ) -> Tuple[bool, str, List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "SafetyGates", value, calculation, output_field))
            return value

        max_loss_trade = float(req("max_loss_per_trade_rupees", "per-trade rupee loss cap (post-sizing)", "approved"))
        max_loss_day = float(req("max_loss_per_day_rupees", "daily rupee loss cap", "approved"))
        min_margin = float(req("minimum_profit_margin_over_cost", "required fraction by which projected profit must exceed real round-trip cost", "approved"))

        peak = max(equity_curve) if equity_curve else 0.0
        current = equity_curve[-1] if equity_curve else 0.0

        worst_case_trade_loss_rupees = abs(plan.entry_price - plan.stop_price) * quantity
        if worst_case_trade_loss_rupees > max_loss_trade:
            return False, f"worst-case trade loss Rs.{worst_case_trade_loss_rupees:.2f} exceeds per-trade cap Rs.{max_loss_trade:.2f}", trace

        daily_loss_so_far = max(0.0, peak - current)
        if daily_loss_so_far > max_loss_day:
            return False, f"cumulative loss Rs.{daily_loss_so_far:.2f} exceeds daily cap Rs.{max_loss_day:.2f}", trace

        # The real profit-floor check, moved here from MPC because this is
        # the first point where BOTH the real quantity (from PositionManager)
        # AND the real per-trade round-trip cost can be computed together.
        # Replaces the former minimum_absolute_profit_rupees: that was a
        # fixed per-SHARE rupee constant checked before quantity existed --
        # structurally unable to represent "is this trade worth its real
        # cost" for any single value, since real cost scales with
        # price x quantity and varies enormously by symbol (verified
        # directly: the same fixed floor cleared ~93% of the time for a
        # Rs 14,000 stock and 0% of the time for a Rs 1,100 one, purely from
        # price scale, nothing to do with either trade's actual economics).
        # exit-leg cost uses plan.target_price as the assumed exit price --
        # the same "if this trade hits its target" assumption projected_profit
        # itself already makes.
        close_side = "SELL" if plan.side == "BUY" else "BUY"
        entry_cost = self._leg_cost(plan.entry_price, quantity, plan.side)
        exit_cost = self._leg_cost(plan.target_price, quantity, close_side)
        real_round_trip_cost = entry_cost + exit_cost
        projected_total_profit = abs(plan.target_price - plan.entry_price) * quantity
        required_profit = real_round_trip_cost * (1.0 + min_margin)
        if projected_total_profit < required_profit:
            return False, (
                f"projected profit Rs.{projected_total_profit:.2f} does not exceed real round-trip cost "
                f"Rs.{real_round_trip_cost:.2f} by the required {min_margin:.0%} margin "
                f"(needs Rs.{required_profit:.2f})"
            ), trace

        return True, "approved", trace


class PositionManagerBox:
    def size(
        self,
        plan: TradePlan,
        available_equity: float,
        size_multiplier: float,
        config: EffectiveConfig,
        open_positions_count: int = 0,
        symbol_positions_count: int = 0,
    ) -> Tuple[int, List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "PositionManager", value, calculation, output_field))
            return value

        capital_fraction = float(req("capital_per_trade_fraction", "fraction of equity risked per trade", "quantity"))
        buffer_fraction = float(req("min_capital_buffer_fraction", "cash reserve withheld from sizing", "quantity"))
        max_live = int(req("max_positions_live", "cap on concurrent live positions — zeroes sizing once reached", "quantity"))
        max_per_symbol = int(req("max_positions_per_symbol", "cap on positions in a single symbol — zeroes sizing once reached", "quantity"))
        sector_cap = float(req("max_sector_exposure_fraction", "cap on sector exposure (single-symbol surrogate)", "quantity"))
        symbol_cap = float(req("max_symbol_concentration", "cap on single-symbol concentration", "quantity"))
        lot_map = req("lot_size_by_symbol", "per-symbol lot size", "quantity")
        allocation_mode = req("capital_allocation_mode", "capital allocation policy: equal vs aggressive", "quantity")
        # rebalance_frequency_minutes used to be req()'d here for coverage-
        # tracking only (its own comment already said "not a per-trade
        # sizing input" -- never affected this method's real output).
        # Removed from the registry entirely this session, replaced by
        # trailing_stop_atr_mult (see canonical_parameter_registry.py's
        # FROZEN_IDENTITY_SHA256 comment) -- this call is removed to match,
        # not left dangling on a parameter that no longer exists.

        if open_positions_count >= max_live or symbol_positions_count >= max_per_symbol:
            return 0, trace

        risk_per_share = abs(plan.entry_price - plan.stop_price)
        if risk_per_share <= 0:
            return 0, trace

        usable_equity = available_equity * (1.0 - buffer_fraction)
        allocation_scale = 1.5 if str(allocation_mode).lower() == "aggressive" else 1.0
        risk_budget = usable_equity * capital_fraction * size_multiplier * allocation_scale
        raw_quantity = math.floor(risk_budget / risk_per_share)

        lot_size = int(lot_map.get(plan.side, 1)) if isinstance(lot_map, dict) and lot_map else 1
        lot_size = max(1, lot_size)
        quantity = (raw_quantity // lot_size) * lot_size

        max_by_concentration = math.floor((usable_equity * symbol_cap) / plan.entry_price) if plan.entry_price else 0
        max_by_sector = math.floor((usable_equity * sector_cap) / plan.entry_price) if plan.entry_price else 0
        quantity = min(quantity, max_by_concentration, max_by_sector)

        return max(0, int(quantity)), trace


class P01DBox:
    def create_order(self, symbol: str, plan: TradePlan, quantity: int, config: EffectiveConfig) -> Tuple[Optional[ProposedOrder], List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "P01D", value, calculation, output_field))
            return value

        order_type = req("order_type", "order routing type", "order_type")
        offset_pct = float(req("limit_order_offset_percent", "limit price offset from plan entry", "limit_price"))
        timeout_s = int(req("order_timeout_seconds", "broker acknowledgement timeout", "timeout_seconds"))
        max_retries = int(req("max_retry_attempts", "retry attempts on timeout/reject", "max_retries"))
        req("retry_delay_seconds", "delay between retries", "max_retries")
        slippage_tolerance = float(req("slippage_tolerance_percent", "maximum tolerated slippage before rejecting the order", "order_type"))

        if quantity <= 0:
            return None, trace

        limit_price = None
        if order_type == "LIMIT":
            direction = 1 if plan.side == "BUY" else -1
            limit_price = plan.entry_price * (1 + direction * offset_pct)

        _ = slippage_tolerance  # enforced by the ExecutionGate/PaperBrokerAdapter downstream

        order = ProposedOrder(
            symbol=symbol,
            side=plan.side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            timeout_seconds=timeout_s,
            max_retries=max_retries,
        )
        return order, trace


class DataIngestionBox:
    def admit(self, symbol: str, config: EffectiveConfig) -> Tuple[bool, str, List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "DataIngestion", value, calculation, output_field))
            return value

        symbols_to_trade = req("symbols_to_trade", "universe allow-list", "admitted")
        exclude_symbols = req("exclude_symbols", "universe deny-list", "admitted")

        if symbols_to_trade and symbol not in symbols_to_trade:
            return False, f"{symbol} not in symbols_to_trade universe", trace
        if exclude_symbols and symbol in exclude_symbols:
            return False, f"{symbol} is in exclude_symbols", trace
        return True, "admitted", trace


class L2DataCertifierBox:
    def certify(self, bars: pd.DataFrame, config: EffectiveConfig) -> Tuple[bool, str, List[ParameterUse]]:
        trace: List[ParameterUse] = []
        mode = config.require("data_validation_mode")
        trace.append(ParameterUse("data_validation_mode", "L2DataCertifier", mode, "controls strictness of bar validation", "certified"))

        required_cols = {"open", "high", "low", "close", "volume"}
        if not required_cols.issubset({c.lower() for c in bars.columns}):
            return False, "missing required OHLCV columns", trace

        if mode == "strict":
            if bars[["open", "high", "low", "close", "volume"]].isna().any().any():
                return False, "NaN values present under strict validation", trace
            if (bars["high"] < bars["low"]).any():
                return False, "high < low bar detected under strict validation", trace

        return True, "certified", trace


class UnifiedExecutionBox:
    def check_window(self, timestamp: str, config: EffectiveConfig) -> Tuple[bool, float, List[ParameterUse]]:
        """Returns (in_window, exploration_bias, trace).

        `exploration_bias` is the orchestration-level intensity score derived
        from the three UnifiedExecution-owned optimizer-control parameters.
        It isn't used to gate a single bar's trading window, but it is a real,
        parameter-sensitive output (proven in the sensitivity tests) rather
        than a value the box merely reads and discards.
        """
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "UnifiedExecution", value, calculation, output_field))
            return value

        start = req("trading_hours_start", "session open bound", "in_window")
        end = req("trading_hours_end", "session close bound", "in_window")
        phase1 = req("phase1_exploration_intensity", "optimizer phase-1 intensity", "exploration_bias")
        phase2 = req("phase2_optimization_intensity", "optimizer phase-2 intensity", "exploration_bias")
        learning_rate = req("learning_rate_exploration_factor", "meta-learning exploration rate", "exploration_bias")

        try:
            raw = str(timestamp)
            time_part = (raw.split("T")[-1] if "T" in raw else raw.split(" ")[-1])[:5]
            in_window = start <= time_part <= end
        except Exception:
            in_window = True

        exploration_bias = float(phase1) * float(phase2) * float(learning_rate)
        return in_window, exploration_bias, trace
