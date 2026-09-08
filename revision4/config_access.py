"""
REVISION 04: Canonical parameter access helpers.

Single interface for all config access: config.require(param_name)
This module provides wrappers for common config patterns.
"""

from revision4.contracts import EffectiveConfig
from typing import Tuple


def get_trading_hours(config: EffectiveConfig) -> Tuple[int, int, int, int]:
    """
    Parse trading hours from canonical config.
    Returns: (start_hour, start_minute, end_hour, end_minute)
    """
    start_str = config.require("trading_hours_start")  # '09:15'
    end_str = config.require("trading_hours_end")      # '15:30'

    start_h, start_m = map(int, start_str.split(':'))
    end_h, end_m = map(int, end_str.split(':'))

    return start_h, start_m, end_h, end_m


def get_entry_cost(config: EffectiveConfig) -> Tuple[float, float]:
    """
    Get entry cost parameters.
    Returns: (slippage_fraction, fixed_cost_per_trade_rupees)
    """
    slippage_frac = config.require("max_slippage_fraction")  # ~0.001
    # Note: no explicit fixed_cost in canonical registry; use 0
    return slippage_frac, 0.0


def get_atr_parameters(config: EffectiveConfig) -> Tuple[int, float, float]:
    """
    Get ATR-based risk parameters.
    Returns: (atr_period, stop_loss_atr_mult, profit_target_atr_mult)
    """
    period = config.require("atr_calculation_period")
    stop_mult = config.require("stop_loss_atr_mult")
    target_mult = config.require("profit_target_atr_mult")

    return period, stop_mult, target_mult


def get_mpc_parameters(config: EffectiveConfig) -> Tuple[bool, float]:
    """
    Get MPC (Model Predictive Control) parameters.
    Returns: (mpc_enabled, loss_threshold_rupees)

    Note: Canonical registry doesn't have explicit mpc_scaling_enabled.
    Use kill_switch_enabled as proxy (if kill_switch is on, MPC is active).
    """
    # Infer MPC enabled from safety parameters
    kill_switch = config.require("kill_switch_enabled")
    loss_threshold = config.require("max_daily_loss_rupees")

    return kill_switch, float(loss_threshold)


def get_position_limits(config: EffectiveConfig) -> Tuple[int, int, float]:
    """
    Get position sizing limits.
    Returns: (max_positions_live, position_hold_bars, max_loss_per_trade_rupees)
    """
    max_pos = config.require("max_positions_live")
    hold_bars = config.require("max_hold_bars")
    max_loss = config.require("max_loss_per_trade_rupees")

    return max_pos, hold_bars, float(max_loss)


def get_signal_thresholds(config: EffectiveConfig) -> Tuple[float, float]:
    """
    Get entry signal confidence thresholds.
    Returns: (entry_confidence_threshold, min_signal_confidence)
    """
    entry_conf = config.require("entry_confidence_threshold")
    min_conf = config.require("min_signal_confidence")

    return entry_conf, min_conf
