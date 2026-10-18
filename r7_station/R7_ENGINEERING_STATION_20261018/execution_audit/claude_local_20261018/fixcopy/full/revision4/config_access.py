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


def calculate_transaction_cost(
    price: float, quantity: float, side: str
) -> float:
    """
    Calculate transaction cost for one trade leg (entry or exit).

    Real NSE/India cost model (from Revision 2):
    - Brokerage: min(₹20, 0.03% of turnover)
    - Exchange charges: 0.00345% of turnover
    - STT (taxes): 0.025% of turnover (SELL only)

    Args:
        price: Execution price per unit
        quantity: Number of units
        side: 'BUY' or 'SELL'

    Returns:
        Total cost in rupees
    """
    turnover = price * quantity
    cost = min(20.0, 0.0003 * turnover) + 0.0000345 * turnover
    if side.upper() == "SELL":
        cost += 0.00025 * turnover
    return cost


def get_atr_parameters(config: EffectiveConfig) -> Tuple[int, float, float]:
    """
    Get ATR-based risk parameters.
    Returns: (atr_period, stop_loss_atr_mult, profit_target_atr_mult)
    """
    period = config.require("atr_calculation_period")
    stop_mult = config.require("stop_loss_atr_mult")
    target_mult = config.require("profit_target_atr_mult")

    return period, stop_mult, target_mult


def get_kill_switch_status(config: EffectiveConfig) -> bool:
    """
    Get kill switch authorization status.

    Kill switch is a safety control that BLOCKS all orders when engaged.
    Do NOT use this as an MPC enable/disable flag.

    Returns: bool (True = orders permitted, False = all orders blocked)
    """
    return config.require("kill_switch_enabled")


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
