#!/usr/bin/env python3
"""
================================================================================
SAFETY GATES CONFIGURATION
================================================================================

Hard-coded operational risk parameters for live trading (R1).
These are NEVER calibrated - they are set once and remain constant.
These are the operational policy, independent from strategy parameters.

IMPORTANT: Do NOT change these without explicit operator review and testing.
These are not strategy parameters - they are safety infrastructure.

================================================================================
"""

# ============================================================================
# GROUP 1: CAPITAL RISK (Hard Limits)
# ============================================================================

class CapitalRiskConfig:
    """Maximum capital risk per trade"""

    # Choose ONE approach:
    # Approach A: Percentage of portfolio
    risk_per_trade_fraction = 0.02  # 2% of portfolio max loss per trade

    # Approach B: Fixed rupee cap
    max_loss_per_trade_rupees = 5000  # OR hard cap of ₹5000 per trade

    # Use whichever is MORE restrictive
    # Example: 2% of ₹1M portfolio = ₹20,000
    # But ₹5,000 cap is stricter, so use ₹5,000


# ============================================================================
# GROUP 2-5: POSITION MANAGEMENT (Hard Limits)
# ============================================================================

class PositionManagementConfig:
    """Position sizing and portfolio limits"""

    # Symbol-specific maximum quantities
    max_position_quantity_per_symbol = {
        'INFY': 5,
        'TCS': 10,
        'RELIANCE': 3,
        'HDFC': 4,
        'SBIN': 8,
        'ICICIBANK': 6,
        'LT': 2,
        'ITC': 15,
        'MARUTI': 2,
        'ONGC': 20,
        # ... add all 48 NIFTY symbols
    }

    # Maximum concurrent open positions
    max_open_positions = 5

    # Maximum gross portfolio exposure
    max_gross_exposure_fraction = 0.50  # 50% of portfolio

    # Maximum exposure per symbol
    max_exposure_per_symbol = 0.15  # 15% per symbol


# ============================================================================
# GROUP 6: DAILY LOSS (Hard Halt)
# ============================================================================

class DailyLossConfig:
    """Hard stop on daily losses"""

    # Maximum daily loss (realized + unrealized)
    max_daily_loss_rupees = 50000  # ₹50k max loss per day

    # When this is hit: HALT ALL ENTRIES
    # Only position closes allowed
    # This is HARD STOP, not negotiable


# ============================================================================
# GROUP 7-8: DRAWDOWN (Hard Limits + Derating + Halt)
# ============================================================================

class DrawdownConfig:
    """Portfolio drawdown control (separate from lambda)"""

    # Drawdown Derating: Reduce position sizes
    drawdown_derate_threshold = 0.18  # 18% portfolio DD
    drawdown_derate_multiplier = 0.60  # Reduce to 60% of normal size

    # When portfolio DD ≥ 18%: All new positions become 60% size
    # This is PREEMPTIVE - catches risk early

    # Drawdown Halt: Hard stop on ALL entries
    drawdown_halt_threshold = 0.25  # 25% portfolio DD

    # When portfolio DD ≥ 25%: HALT ALL NEW ENTRIES
    # Only position closes allowed
    # This is HARD STOP


# ============================================================================
# GROUP 9-10: PORTFOLIO RISK (Lambda-based, Independent from Drawdown)
# ============================================================================

class PortfolioRiskConfig:
    """
    Portfolio risk management (different from drawdown).

    Lambda measures EXPOSURE RISK (preemptive)
    Drawdown measures REALIZED LOSS (reactive)

    BOTH should be present for complete risk coverage.
    """

    # Lambda-based derating: Reduce position sizes when exposure is high
    portfolio_risk_derate_trigger = 0.15  # When lambda ≥ 0.15 (15% portfolio risk)
    portfolio_derated_size_multiplier = 0.80  # Reduce to 80% of normal size

    # When portfolio lambda ≥ 15%: All new positions become 80% size
    # This is PREEMPTIVE - catches exposure early

    # Note: Different from drawdown derating (60% vs 80%)
    # If both trigger: use the more restrictive (60%)


# ============================================================================
# GROUP 11: DATA QUALITY (Fail-Closed)
# ============================================================================

class DataQualityConfig:
    """Stale data protection"""

    # Maximum acceptable market data age
    max_market_data_age_seconds = 60  # Reject if data > 60 seconds old

    # If data is stale: REJECT ALL ENTRIES
    # Fail-closed: Cannot trade on old prices


# ============================================================================
# GROUP 12-13: ORDER EXECUTION (Operational)
# ============================================================================

class OrderExecutionConfig:
    """Order placement and timeout"""

    # Order timeout - don't retry blindly
    order_timeout_seconds = 30  # Wait max 30 seconds for fill

    # After timeout: RECONCILE with broker before retry
    # Don't just keep retrying - check what actually happened

    # Order reconciliation frequency
    reconciliation_frequency_minutes = 5  # Check broker every 5 min

    # Unique order ID format (for deduplication)
    unique_order_id_format = "algo_{timestamp}_{nonce}"

    # Slippage tolerance
    slippage_reject_threshold_percent = 0.10  # Max 0.10% slippage

    # If fill > 0.10% from target: REJECT, try again


# ============================================================================
# GROUP 14: MARKET CLOSE (Hard Operational Policy)
# ============================================================================

class MarketCloseConfig:
    """Last trading cutoff and forced closing"""

    # Last time to enter new positions
    last_entry_cutoff_time = "15:20"  # IST (3:20 PM)

    # After 15:20: NO NEW ENTRIES

    # Force close all positions
    forced_exit_time = "15:25"  # IST (3:25 PM)

    # At 15:25: CLOSE ALL POSITIONS (market order)
    # Don't carry positions overnight


# ============================================================================
# GROUP 15-16: CIRCUIT BREAKER & KILL SWITCH (Critical Safety)
# ============================================================================

class CircuitBreakerConfig:
    """System-wide emergency stops"""

    # Broker connectivity
    broker_offline_threshold_seconds = 300  # 5 minutes

    # If broker offline > 5 min: HALT ALL TRADING

    # Circuit breaker conditions (any ONE triggers halt)
    circuit_breaker_conditions = [
        "broker_offline_5min",
        "unhandled_exception",
        "health_check_failed",
        "all_orders_rejected",
        "position_sync_error"
    ]

    # If ANY condition true: COMPLETE HALT
    # Only manual override (kill switch) can restart


class KillSwitchConfig:
    """Emergency manual and automatic shutdown"""

    # Operator can trigger manually (highest priority)
    manual_kill_switch_enabled = True

    # Automatic triggers
    auto_kill_triggers = [
        "daily_loss_exceeded",
        "drawdown_halt_exceeded",
        "broker_fatal_error",
        "unrecoverable_state"
    ]

    # If ANY auto-kill: COMPLETE HALT
    # Automatic recovery: None (manual restart required)


# ============================================================================
# SUMMARY: WHAT'S NOT HERE
# ============================================================================

"""
These are NOT safety parameters - they live elsewhere:

CALIBRATION-ONLY (calibration_config.py):
├─ phase1_exploration_intensity
├─ phase2_optimization_intensity
└─ learning_rate_exploration_factor

LIVE STRATEGY PARAMETERS (ecs_parameter_management.py):
├─ entry_confidence_threshold
├─ exit_confidence_threshold
├─ vwap_weight
├─ momentum_weight
├─ ... (all Tier 1 + Tier 2 params)
"""


# ============================================================================
# IMPLEMENTATION NOTE
# ============================================================================

"""
CRITICAL: Do NOT change these values without:

1. Understanding the impact
2. Reviewing with risk management team
3. Testing on paper trading first
4. Getting operator approval
5. Documenting the change with timestamp and reason

These are not tuning parameters - they are safety infrastructure.
"""

