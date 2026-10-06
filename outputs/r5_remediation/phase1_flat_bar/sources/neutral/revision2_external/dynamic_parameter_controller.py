"""Dynamic Parameter Controller (DPC) for ECS 6 Black Boxes.

Provides Tier 1 (Continuous Volatility Scaling), Tier 2 (Regime Probability Modulation),
and Tier 3 (Gain Scheduling) dynamic adaptation with hard safety clamps.
"""
from dataclasses import dataclass
from typing import Dict, Any, Tuple
import numpy as np
import pandas as pd
from revision2_external.bb05_bb06_parameters import default_config, require

# Fixed safety envelopes, not runtime/optimizer knobs.
_NORMALIZED_VOL_ENVELOPE = (0.5, 2.5)
_ATR_FLOOR_ENVELOPE = (0.0025, 0.0120)
_SLIPPAGE_SCALE_ENVELOPE = (0.6, 3.0)
_KP_SCALE_ENVELOPE = (0.5, 2.5)
_KI_SCALE_ENVELOPE = (1.0, 4.0)
_KD_SCALE_ENVELOPE = (0.5, 2.0)

@dataclass
class MarketEnvironmentState:
    symbol: str
    close: float
    current_atr: float
    historical_atr_sma: float
    regime_probs: Dict[str, float]  # e.g., {"trend": 0.6, "chop": 0.3, "turbulent": 0.1}
    normalized_vol: float = 1.0

    @classmethod
    def from_bars(cls, symbol: str, bars: pd.DataFrame, current_bar_idx: int, regime: str = "trend", config=None) -> "MarketEnvironmentState":
        config = config if config is not None else default_config()
        lookback = require(config, "mpc_environment_lookback")
        period = require(config, "mpc_range_atr_period")
        window = bars.iloc[max(0, current_bar_idx - lookback):current_bar_idx + 1]
        close = float(bars.iloc[current_bar_idx]["close"])
        
        # High-Low Range proxy for rolling ATR
        highs = window["high"].values
        lows = window["low"].values
        closes = window["close"].values
        
        tr = np.maximum(highs - lows, np.maximum(np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1))))
        tr[0] = highs[0] - lows[0]
        
        current_atr = float(np.mean(tr[-period:])) if len(tr) >= period else close * require(config, "mpc_range_fallback_fraction")
        atr_sma = float(np.mean(tr)) if len(tr) > 0 else current_atr
        
        nv = np.clip(current_atr / max(1e-6, atr_sma), *_NORMALIZED_VOL_ENVELOPE)
        
        # Regime priors
        if regime == "trend":
            probs = {"trend": 0.70, "chop": 0.20, "turbulent": 0.10}
        elif regime == "chop":
            probs = {"trend": 0.20, "chop": 0.70, "turbulent": 0.10}
        else:
            probs = {"trend": 0.10, "chop": 0.30, "turbulent": 0.60}
            
        return cls(
            symbol=symbol,
            close=close,
            current_atr=current_atr,
            historical_atr_sma=atr_sma,
            regime_probs=probs,
            normalized_vol=nv
        )

class DynamicParameterController:
    """Calculates bounded dynamic parameters across Black Boxes 1 through 6."""

    @staticmethod
    def get_tier1_vol_parameters(env: MarketEnvironmentState, config=None) -> Dict[str, float]:
        """Tier 1: Continuous Volatility Scaling (Box 1, Box 4, Box 6)."""
        config = config if config is not None else default_config()
        if not np.isfinite(env.close) or not np.isfinite(env.normalized_vol) or env.close <= 0:
            raise ValueError("BB06 environment must have a positive finite price and finite volatility")
        # Dynamic ATR floor multiplier (0.25% to 1.20%)
        atr_floor_mult = np.clip(require(config, "mpc_atr_floor_gain") * env.normalized_vol, *_ATR_FLOOR_ENVELOPE)
        min_atr_floor = env.close * atr_floor_mult

        # Dynamic slippage model (3 bps to 15 bps)
        slippage_mult = np.clip(1.0 + require(config, "mpc_slippage_vol_gain") * (env.normalized_vol - 1.0), *_SLIPPAGE_SCALE_ENVELOPE)
        base_slippage = require(config, "mpc_base_slippage_fraction") * slippage_mult

        return {
            "min_atr_floor": float(min_atr_floor),
            "dynamic_slippage": float(base_slippage),
            "atr_floor_mult": float(atr_floor_mult)
        }

    @staticmethod
    def get_tier2_regime_parameters(env: MarketEnvironmentState) -> Dict[str, Any]:
        """Tier 2: Discrete Regime Adaptor (Box 2, Box 3, Box 4)."""
        p = env.regime_probs
        p_trend = p.get("trend", 0.33)
        p_chop = p.get("chop", 0.33)
        p_turb = p.get("turbulent", 0.34)

        # Dynamic confidence threshold: [0.42, 0.70]
        dyn_entry_conf = 0.45 * p_trend + 0.58 * p_chop + 0.75 * p_turb
        entry_conf = float(np.clip(dyn_entry_conf, 0.42, 0.70))

        # Dynamic Hold Windows: [2, 6] min hold, [15, 60] max hold
        min_hold = int(np.clip(np.round(2 * p_trend + 4 * p_chop + 6 * p_turb), 2, 6))
        max_hold = int(np.clip(np.round(60 * p_trend + 25 * p_chop + 12 * p_turb), 12, 60))

        # Bay Cooldown Bars: [8, 30]
        cooldown_bars = int(np.clip(np.round(10 * p_trend + 18 * p_chop + 30 * p_turb), 8, 30))

        return {
            "entry_confidence_threshold": entry_conf,
            "min_hold_bars": min_hold,
            "max_hold_bars": max_hold,
            "bay_cooldown_bars": cooldown_bars
        }

    @staticmethod
    def get_tier3_pid_schedule(base_kp: float, base_ki: float, base_kd: float, 
                               error_delta: float, config=None) -> Tuple[float, float, float]:
        """Tier 3: Feedback Gain Scheduling for Box 6 MPC / Actuators."""
        config = config if config is not None else default_config()
        if not np.isfinite(error_delta):
            raise ValueError('error_delta must be finite')
        abs_err = abs(error_delta)
        # Scale Kp up on rapid error divergence
        kp_dyn = base_kp * np.clip(1.0 + require(config, "mpc_schedule_kp_gain") * abs_err, *_KP_SCALE_ENVELOPE)
        # Bleed Ki during transient spikes to eliminate integral windup
        ki_dyn = base_ki / np.clip(1.0 + require(config, "mpc_schedule_ki_gain") * abs_err, *_KI_SCALE_ENVELOPE)
        # Damping Kd scaled with vol
        kd_dyn = base_kd * np.clip(1.0 + require(config, "mpc_schedule_kd_gain") * abs_err, *_KD_SCALE_ENVELOPE)
        return float(kp_dyn), float(ki_dyn), float(kd_dyn)
