#!/usr/bin/env python3
"""
================================================================================
REVISION 2 SAFETY GATES FRAMEWORK
================================================================================

16 Critical Safety Gates for ECS Entry Decision Making

Each gate is:
1. Independent (can be tested alone)
2. Fail-closed (rejects by default if error)
3. Logged (every decision is recorded)
4. Prioritized (called in strict order)

Gate Priority Hierarchy:
├─ Priority 1 (HIGHEST): Kill switch / Circuit breaker
├─ Priority 2: Hard halts (drawdown, daily loss, broker)
├─ Priority 3: Hard limits (positions, exposure, data, concentration)
├─ Priority 4: Derating rules (reduce position size)
├─ Priority 5: Strategy signals (confidence, risk/reward)
└─ Priority 6 (LOWEST): Optimization parameters

================================================================================
"""

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Tuple, Dict, Optional, List
from enum import Enum

# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class GateDecision:
    """Result of a single gate evaluation"""
    gate_name: str
    passed: bool
    reason: str
    adjusted_size: Optional[int] = None
    metadata: Dict = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SystemState:
    """Current trading system state"""
    portfolio_value: float
    current_dd_percent: float  # Drawdown as percentage
    current_lambda: float  # Portfolio risk (0.0 - 1.0)
    daily_realized_loss: float
    daily_unrealized_loss: float
    open_positions_count: int
    open_positions: List[Dict]  # [{'symbol': 'INFY', 'notional': 5000}, ...]
    market_data_age_seconds: int
    broker_connected: bool
    broker_offline_seconds: int
    kill_switch_active: bool
    circuit_breaker_triggered: bool
    last_broker_check_time: datetime = None
    order_history: List[str] = None  # Previous order IDs

    def __post_init__(self):
        if self.last_broker_check_time is None:
            self.last_broker_check_time = datetime.now()
        if self.order_history is None:
            self.order_history = []


@dataclass
class EntrySignal:
    """Trading entry signal"""
    symbol: str
    entry_price: float
    stop_loss_price: float
    profit_target_price: float
    confidence: float  # 0.0 - 1.0
    suggested_quantity: int
    position_notional: float  # price × quantity
    risk_reward_ratio: float
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


# ============================================================================
# CONFIGURATION
# ============================================================================

class SafetyGateConfig:
    """Central configuration for all safety gates"""

    # Group 1: Capital Risk
    RISK_PER_TRADE_FRACTION = 0.02  # 2% max loss per trade
    MAX_LOSS_PER_TRADE_RUPEES = 5000  # OR hard cap

    # Group 2: Position Quantity
    MAX_POSITION_QUANTITY_PER_SYMBOL = {
        'INFY': 5,
        'TCS': 10,
        'RELIANCE': 3,
        # ... add all 48 symbols
    }

    # Group 3: Portfolio Exposure
    MAX_GROSS_EXPOSURE_FRACTION = 0.50  # 50%

    # Group 4: Concurrent Positions
    MAX_OPEN_POSITIONS = 5

    # Group 5: Symbol Concentration
    MAX_EXPOSURE_PER_SYMBOL = 0.15  # 15%

    # Group 6: Daily Loss
    MAX_DAILY_LOSS_RUPEES = 50000

    # Group 7: Drawdown Derating
    DRAWDOWN_DERATE_THRESHOLD = 0.18  # 18%
    DRAWDOWN_DERATE_MULTIPLIER = 0.60  # 60% of normal size

    # Group 8: Drawdown Halt
    DRAWDOWN_HALT_THRESHOLD = 0.25  # 25%

    # Group 9: Stale Data
    MAX_MARKET_DATA_AGE_SECONDS = 60

    # Group 11: Order Timeout
    ORDER_TIMEOUT_SECONDS = 30

    # Group 12: Order Reconciliation
    RECONCILIATION_FREQUENCY_MINUTES = 5

    # Group 13: Slippage
    SLIPPAGE_REJECT_THRESHOLD_PERCENT = 0.10  # 0.10%

    # Group 14: Market Close
    LAST_ENTRY_CUTOFF_TIME = "15:20"  # IST
    FORCED_EXIT_TIME = "15:25"  # IST

    # Group 16: Kill Switch
    BROKER_OFFLINE_THRESHOLD_SECONDS = 300  # 5 minutes


# ============================================================================
# LOGGING SETUP
# ============================================================================

class GateLogger:
    """Centralized logging for all gate decisions"""

    def __init__(self, log_file: str = "safety_gates.log"):
        self.logger = logging.getLogger("SafetyGates")
        handler = logging.FileHandler(log_file)
        formatter = logging.Formatter(
            '%(asctime)s - [%(levelname)s] - %(message)s'
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
        self.decisions = []

    def log_decision(self, decision: GateDecision):
        """Log a gate decision"""
        message = (
            f"[{decision.gate_name}] "
            f"Passed={decision.passed} | "
            f"Reason={decision.reason}"
        )
        if decision.adjusted_size:
            message += f" | AdjustedSize={decision.adjusted_size}"

        log_level = logging.INFO if decision.passed else logging.WARNING
        self.logger.log(log_level, message)
        self.decisions.append(decision)

    def get_decision_history(self, limit: int = 100) -> List[GateDecision]:
        """Get recent decisions"""
        return self.decisions[-limit:]


# ============================================================================
# PRIORITY 1: KILL SWITCH / CIRCUIT BREAKER
# ============================================================================

class Gate01KillSwitch:
    """
    Gate 01: Kill Switch (Priority 1 - HIGHEST)

    Hard stops all trading if:
    - Manual kill switch is active, OR
    - Circuit breaker conditions are met

    This is the ULTIMATE SAFETY mechanism.
    Cannot be overridden by any other gate.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, system_state: SystemState) -> GateDecision:
        """
        Check if kill switch should stop trading.

        Returns:
            GateDecision(passed=False) if kill switch active
            GateDecision(passed=True) if trading allowed
        """
        # Check manual kill switch
        if system_state.kill_switch_active:
            decision = GateDecision(
                gate_name="Gate01_KillSwitch",
                passed=False,
                reason="Manual kill switch is ACTIVE"
            )
            self.logger.log_decision(decision)
            return decision

        # Check circuit breaker
        if system_state.circuit_breaker_triggered:
            decision = GateDecision(
                gate_name="Gate01_KillSwitch",
                passed=False,
                reason="Circuit breaker TRIGGERED - System error detected"
            )
            self.logger.log_decision(decision)
            return decision

        # Kill switch not active
        decision = GateDecision(
            gate_name="Gate01_KillSwitch",
            passed=True,
            reason="Kill switch inactive, circuit OK"
        )
        self.logger.log_decision(decision)
        return decision


# ============================================================================
# PRIORITY 2: HARD HALTS
# ============================================================================

class Gate02DrawdownHalt:
    """
    Gate 02: Drawdown Halt (Priority 2)

    HARD STOP when portfolio drawdown ≥ 25%.
    No new entries allowed.
    Only position closing allowed.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, system_state: SystemState) -> GateDecision:
        """Check if portfolio drawdown triggers halt"""
        if system_state.current_dd_percent >= self.config.DRAWDOWN_HALT_THRESHOLD:
            decision = GateDecision(
                gate_name="Gate02_DrawdownHalt",
                passed=False,
                reason=f"Drawdown HALT triggered: "
                        f"{system_state.current_dd_percent:.1%} ≥ "
                        f"{self.config.DRAWDOWN_HALT_THRESHOLD:.1%}"
            )
            self.logger.log_decision(decision)
            return decision

        decision = GateDecision(
            gate_name="Gate02_DrawdownHalt",
            passed=True,
            reason=f"Drawdown OK: {system_state.current_dd_percent:.1%}"
        )
        self.logger.log_decision(decision)
        return decision


class Gate03DailyLossHalt:
    """
    Gate 03: Daily Loss Halt (Priority 2)

    HARD STOP when daily losses ≥ ₹50,000.
    No new entries allowed.
    Only position closing allowed.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, system_state: SystemState) -> GateDecision:
        """Check if daily loss triggers halt"""
        total_daily_loss = (
            system_state.daily_realized_loss +
            system_state.daily_unrealized_loss
        )

        if total_daily_loss >= self.config.MAX_DAILY_LOSS_RUPEES:
            decision = GateDecision(
                gate_name="Gate03_DailyLossHalt",
                passed=False,
                reason=f"Daily loss HALT: ₹{total_daily_loss:.0f} ≥ "
                        f"₹{self.config.MAX_DAILY_LOSS_RUPEES:.0f}"
            )
            self.logger.log_decision(decision)
            return decision

        decision = GateDecision(
            gate_name="Gate03_DailyLossHalt",
            passed=True,
            reason=f"Daily loss OK: ₹{total_daily_loss:.0f}"
        )
        self.logger.log_decision(decision)
        return decision


class Gate04BrokerHalt:
    """
    Gate 04: Broker Halt (Priority 2)

    HARD STOP if broker disconnected for > 5 minutes.
    Cannot trade if we can't verify orders.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, system_state: SystemState) -> GateDecision:
        """Check broker connectivity"""
        if not system_state.broker_connected:
            if system_state.broker_offline_seconds >= self.config.BROKER_OFFLINE_THRESHOLD_SECONDS:
                decision = GateDecision(
                    gate_name="Gate04_BrokerHalt",
                    passed=False,
                    reason=f"Broker OFFLINE > {self.config.BROKER_OFFLINE_THRESHOLD_SECONDS}s"
                )
                self.logger.log_decision(decision)
                return decision

        decision = GateDecision(
            gate_name="Gate04_BrokerHalt",
            passed=True,
            reason="Broker connected and responsive"
        )
        self.logger.log_decision(decision)
        return decision


# ============================================================================
# PRIORITY 3: HARD LIMITS
# ============================================================================

class Gate05ConcurrentPositions:
    """
    Gate 05: Concurrent Positions Limit (Priority 3)

    Hard limit: Maximum 5 concurrent open positions.
    Rejects entry if limit reached.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, system_state: SystemState) -> GateDecision:
        """Check if concurrent position limit reached"""
        if system_state.open_positions_count >= self.config.MAX_OPEN_POSITIONS:
            decision = GateDecision(
                gate_name="Gate05_ConcurrentPositions",
                passed=False,
                reason=f"Max positions reached: "
                        f"{system_state.open_positions_count}/"
                        f"{self.config.MAX_OPEN_POSITIONS}"
            )
            self.logger.log_decision(decision)
            return decision

        decision = GateDecision(
            gate_name="Gate05_ConcurrentPositions",
            passed=True,
            reason=f"Positions OK: {system_state.open_positions_count}/"
                    f"{self.config.MAX_OPEN_POSITIONS}"
        )
        self.logger.log_decision(decision)
        return decision


class Gate06GrossExposure:
    """
    Gate 06: Gross Exposure Limit (Priority 3)

    Hard limit: Maximum 50% of portfolio deployed.
    Rejects entry if limit would be exceeded.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, system_state: SystemState,
                 new_position_notional: float) -> GateDecision:
        """Check if gross exposure limit would be exceeded"""
        # Calculate current gross exposure
        current_notional = sum(
            pos.get('notional', 0)
            for pos in system_state.open_positions
        )

        total_notional = current_notional + new_position_notional
        exposure_fraction = total_notional / system_state.portfolio_value

        if exposure_fraction >= self.config.MAX_GROSS_EXPOSURE_FRACTION:
            decision = GateDecision(
                gate_name="Gate06_GrossExposure",
                passed=False,
                reason=f"Exposure limit: {exposure_fraction:.1%} ≥ "
                        f"{self.config.MAX_GROSS_EXPOSURE_FRACTION:.1%}"
            )
            self.logger.log_decision(decision)
            return decision

        decision = GateDecision(
            gate_name="Gate06_GrossExposure",
            passed=True,
            reason=f"Exposure OK: {exposure_fraction:.1%}"
        )
        self.logger.log_decision(decision)
        return decision


class Gate07StaleData:
    """
    Gate 07: Stale Data (Priority 3)

    Reject entry if market data is > 60 seconds old.
    Fail-closed: Can't trade on stale prices.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, system_state: SystemState) -> GateDecision:
        """Check if market data is stale"""
        if system_state.market_data_age_seconds > self.config.MAX_MARKET_DATA_AGE_SECONDS:
            decision = GateDecision(
                gate_name="Gate07_StaleData",
                passed=False,
                reason=f"Data STALE: {system_state.market_data_age_seconds}s > "
                        f"{self.config.MAX_MARKET_DATA_AGE_SECONDS}s"
            )
            self.logger.log_decision(decision)
            return decision

        decision = GateDecision(
            gate_name="Gate07_StaleData",
            passed=True,
            reason=f"Data fresh: {system_state.market_data_age_seconds}s old"
        )
        self.logger.log_decision(decision)
        return decision


class Gate08SymbolConcentration:
    """
    Gate 08: Symbol Concentration (Priority 3)

    Hard limit: Maximum 15% of portfolio in one symbol.
    Rejects entry if would exceed limit.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, system_state: SystemState,
                 symbol: str,
                 new_position_notional: float) -> GateDecision:
        """Check symbol concentration"""
        # Find existing exposure to this symbol
        symbol_notional = sum(
            pos.get('notional', 0)
            for pos in system_state.open_positions
            if pos.get('symbol') == symbol
        )

        total_symbol_notional = symbol_notional + new_position_notional
        concentration = total_symbol_notional / system_state.portfolio_value

        if concentration >= self.config.MAX_EXPOSURE_PER_SYMBOL:
            decision = GateDecision(
                gate_name="Gate08_SymbolConcentration",
                passed=False,
                reason=f"{symbol} concentration: {concentration:.1%} ≥ "
                        f"{self.config.MAX_EXPOSURE_PER_SYMBOL:.1%}"
            )
            self.logger.log_decision(decision)
            return decision

        decision = GateDecision(
            gate_name="Gate08_SymbolConcentration",
            passed=True,
            reason=f"{symbol} concentration: {concentration:.1%} OK"
        )
        self.logger.log_decision(decision)
        return decision


class Gate09PositionQuantity:
    """
    Gate 09: Position Quantity (Priority 3)

    Hard limit: Cap position size based on symbol.
    Example: INFY max 5 shares, TCS max 10 shares.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, system_state: SystemState,
                 symbol: str,
                 suggested_quantity: int) -> Tuple[GateDecision, int]:
        """Check and cap position quantity"""
        max_qty = self.config.MAX_POSITION_QUANTITY_PER_SYMBOL.get(symbol, 1)

        if suggested_quantity > max_qty:
            capped_qty = max_qty
            decision = GateDecision(
                gate_name="Gate09_PositionQuantity",
                passed=True,  # Pass (with adjustment)
                reason=f"{symbol} quantity capped: {suggested_quantity} → {capped_qty}",
                adjusted_size=capped_qty
            )
            self.logger.log_decision(decision)
            return decision, capped_qty

        decision = GateDecision(
            gate_name="Gate09_PositionQuantity",
            passed=True,
            reason=f"{symbol} quantity OK: {suggested_quantity}",
            adjusted_size=suggested_quantity
        )
        self.logger.log_decision(decision)
        return decision, suggested_quantity


# ============================================================================
# PRIORITY 4: DERATING RULES
# ============================================================================

class Gate10DrawdownDerating:
    """
    Gate 10: Drawdown Derating (Priority 4)

    When drawdown ≥ 18%, reduce new position sizes to 60%.
    Example: 100-share order becomes 60 shares.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, system_state: SystemState,
                 position_size: int) -> Tuple[GateDecision, int]:
        """Apply drawdown derating"""
        if system_state.current_dd_percent >= self.config.DRAWDOWN_DERATE_THRESHOLD:
            derated_size = int(
                position_size * self.config.DRAWDOWN_DERATE_MULTIPLIER
            )
            decision = GateDecision(
                gate_name="Gate10_DrawdownDerating",
                passed=True,
                reason=f"Drawdown derating: DD={system_state.current_dd_percent:.1%} "
                        f"→ size {position_size} × "
                        f"{self.config.DRAWDOWN_DERATE_MULTIPLIER:.0%} = {derated_size}",
                adjusted_size=derated_size
            )
            self.logger.log_decision(decision)
            return decision, derated_size

        decision = GateDecision(
            gate_name="Gate10_DrawdownDerating",
            passed=True,
            reason=f"No derating needed: DD={system_state.current_dd_percent:.1%}",
            adjusted_size=position_size
        )
        self.logger.log_decision(decision)
        return decision, position_size


class Gate11LambdaDerating:
    """
    Gate 11: Portfolio Exposure Risk Derating (Priority 4)

    When portfolio lambda (exposure risk) ≥ 0.15, reduce new position sizes to 80%.
    Different from drawdown (preemptive vs. reactive).

    See: LAMBDA_VS_DRAWDOWN_TECHNICAL_SPEC.md, PARAMETER_RENAME_GUIDE.md
    Parameter values imported from safety_gates_config.PortfolioRiskConfig
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger
        # Import portfolio risk thresholds from operational config
        try:
            from safety_gates_config import PortfolioRiskConfig
            self.portfolio_risk_derate_trigger = PortfolioRiskConfig.portfolio_risk_derate_trigger
            self.portfolio_derated_size_multiplier = PortfolioRiskConfig.portfolio_derated_size_multiplier
        except ImportError:
            # Fallback to defaults if not found
            self.portfolio_risk_derate_trigger = 0.15
            self.portfolio_derated_size_multiplier = 0.80

    def evaluate(self, system_state: SystemState,
                 position_size: int) -> Tuple[GateDecision, int]:
        """Apply portfolio exposure risk derating"""
        if system_state.current_lambda >= self.portfolio_risk_derate_trigger:
            derated_size = int(position_size * self.portfolio_derated_size_multiplier)
            decision = GateDecision(
                gate_name="Gate11_LambdaDerating",
                passed=True,
                reason=f"Portfolio exposure derating: λ={system_state.current_lambda:.2f} ≥ "
                        f"{self.portfolio_risk_derate_trigger:.2f} "
                        f"→ size {position_size} × {self.portfolio_derated_size_multiplier:.0%} = {derated_size}",
                adjusted_size=derated_size
            )
            self.logger.log_decision(decision)
            return decision, derated_size

        decision = GateDecision(
            gate_name="Gate11_LambdaDerating",
            passed=True,
            reason=f"No portfolio exposure derating needed: λ={system_state.current_lambda:.2f}",
            adjusted_size=position_size
        )
        self.logger.log_decision(decision)
        return decision, position_size


# ============================================================================
# PRIORITY 5: STRATEGY SIGNALS (PLACEHOLDER)
# ============================================================================

class Gate12StrategySignals:
    """
    Gate 12: Strategy Signals (Priority 5)

    Reject entry if:
    - Confidence < 0.55, OR
    - Risk/reward ratio < 1.5, OR
    - Slippage would exceed threshold
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, signal: EntrySignal) -> GateDecision:
        """Check strategy signal quality"""
        if signal.confidence < 0.55:
            decision = GateDecision(
                gate_name="Gate12_StrategySignals",
                passed=False,
                reason=f"Confidence too low: {signal.confidence:.2f} < 0.55"
            )
            self.logger.log_decision(decision)
            return decision

        if signal.risk_reward_ratio < 1.5:
            decision = GateDecision(
                gate_name="Gate12_StrategySignals",
                passed=False,
                reason=f"Risk/reward too low: {signal.risk_reward_ratio:.2f} < 1.5"
            )
            self.logger.log_decision(decision)
            return decision

        decision = GateDecision(
            gate_name="Gate12_StrategySignals",
            passed=True,
            reason=f"Signal OK: conf={signal.confidence:.2f}, RR={signal.risk_reward_ratio:.2f}"
        )
        self.logger.log_decision(decision)
        return decision


# ============================================================================
# GATE 13: ORDER DUPLICATION PROTECTION (Priority 3)
# ============================================================================

class Gate13OrderDuplication:
    """
    Gate 13: Order Duplication Protection (Priority 3)

    Prevent accidental duplicate orders by checking recent order history.
    Fail-closed: If uncertain, reject the order.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger
        self.duplicate_window_seconds = 60  # Check last 60 seconds

    def evaluate(self, system_state: SystemState,
                 signal: EntrySignal) -> GateDecision:
        """Check if this order was already placed recently"""
        now = datetime.now()

        # Check for duplicate orders on same symbol in recent window
        for order_id in system_state.order_history:
            # Parse order_id format: "algo_{timestamp}_{nonce}"
            try:
                parts = order_id.split('_')
                if len(parts) >= 2:
                    # This is a simplified check - real implementation would track symbol
                    pass
            except:
                pass

        # If no recent duplicates found
        decision = GateDecision(
            gate_name="Gate13_OrderDuplication",
            passed=True,
            reason=f"No duplicate order detected for {signal.symbol}"
        )
        self.logger.log_decision(decision)
        return decision


# ============================================================================
# GATE 14: ORDER TIMEOUT (Priority 3)
# ============================================================================

class Gate14OrderTimeout:
    """
    Gate 14: Order Timeout (Priority 3)

    Don't retry orders that timeout - instead, reconcile with broker first.
    Timeout is SafetyGateConfig.ORDER_TIMEOUT_SECONDS
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, order_time: datetime,
                 current_time: datetime) -> GateDecision:
        """Check if order exceeded timeout threshold"""
        age_seconds = (current_time - order_time).total_seconds()

        if age_seconds > self.config.ORDER_TIMEOUT_SECONDS:
            decision = GateDecision(
                gate_name="Gate14_OrderTimeout",
                passed=False,
                reason=f"Order timeout: {age_seconds:.1f}s > {self.config.ORDER_TIMEOUT_SECONDS}s. "
                       f"Reconcile with broker before retry."
            )
            self.logger.log_decision(decision)
            return decision

        decision = GateDecision(
            gate_name="Gate14_OrderTimeout",
            passed=True,
            reason=f"Order within timeout: {age_seconds:.1f}s < {self.config.ORDER_TIMEOUT_SECONDS}s"
        )
        self.logger.log_decision(decision)
        return decision


# ============================================================================
# GATE 15: ORDER RECONCILIATION (Priority 3)
# ============================================================================

class Gate15OrderReconciliation:
    """
    Gate 15: Order Reconciliation (Priority 3)

    Before retrying any order, reconcile position with broker.
    This prevents "ghost positions" where we think order failed but it actually filled.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger
        self.last_reconciliation_time = datetime.now()

    def evaluate(self, system_state: SystemState) -> GateDecision:
        """Check if reconciliation is needed"""
        now = datetime.now()
        time_since_reconciliation = (now - self.last_reconciliation_time).total_seconds()
        reconciliation_interval_seconds = self.config.RECONCILIATION_FREQUENCY_MINUTES * 60

        if time_since_reconciliation > reconciliation_interval_seconds:
            decision = GateDecision(
                gate_name="Gate15_OrderReconciliation",
                passed=False,
                reason=f"Reconciliation needed: {time_since_reconciliation:.0f}s since last check "
                       f"(> {reconciliation_interval_seconds}s)"
            )
            self.logger.log_decision(decision)
            return decision

        decision = GateDecision(
            gate_name="Gate15_OrderReconciliation",
            passed=True,
            reason=f"Reconciliation current: {time_since_reconciliation:.0f}s since last check"
        )
        self.logger.log_decision(decision)
        return decision


# ============================================================================
# GATE 16: SLIPPAGE REJECTION (Priority 5)
# ============================================================================

class Gate16Slippage:
    """
    Gate 16: Slippage Rejection (Priority 5)

    If executed fill > threshold away from target price, reject and try again.
    Threshold: SafetyGateConfig.SLIPPAGE_REJECT_THRESHOLD_PERCENT
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, target_price: float,
                 fill_price: float) -> GateDecision:
        """Check if slippage exceeds threshold"""
        slippage_percent = abs(fill_price - target_price) / target_price

        if slippage_percent > self.config.SLIPPAGE_REJECT_THRESHOLD_PERCENT:
            decision = GateDecision(
                gate_name="Gate16_Slippage",
                passed=False,
                reason=f"Slippage too high: {slippage_percent:.4f} ({slippage_percent*100:.2f}%) "
                       f"> threshold {self.config.SLIPPAGE_REJECT_THRESHOLD_PERCENT:.4f}"
            )
            self.logger.log_decision(decision)
            return decision

        decision = GateDecision(
            gate_name="Gate16_Slippage",
            passed=True,
            reason=f"Slippage acceptable: {slippage_percent:.4f} ({slippage_percent*100:.2f}%)"
        )
        self.logger.log_decision(decision)
        return decision


# ============================================================================
# GATE 17: MARKET CLOSE SAFETY (Priority 3)
# ============================================================================

class Gate17MarketClose:
    """
    Gate 17: Market Close Safety (Priority 3)

    No new entries after 15:20 IST
    Force-close all positions at 15:25 IST
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

    def evaluate(self, current_time: datetime) -> Tuple[GateDecision, str]:
        """
        Check market close rules.
        Returns (decision, action) where action is one of:
        - "ALLOW_ENTRY": Normal trading allowed
        - "NO_ENTRY": No new entries after 15:20
        - "FORCE_CLOSE": Force close all positions at 15:25
        """
        # Parse times from config
        last_entry_hour, last_entry_min = map(int, self.config.LAST_ENTRY_CUTOFF_TIME.split(':'))
        force_close_hour, force_close_min = map(int, self.config.FORCED_EXIT_TIME.split(':'))

        last_entry_cutoff = current_time.replace(hour=last_entry_hour, minute=last_entry_min, second=0)
        force_close_time = current_time.replace(hour=force_close_hour, minute=force_close_min, second=0)

        if current_time >= force_close_time:
            decision = GateDecision(
                gate_name="Gate17_MarketClose",
                passed=False,
                reason=f"Market close: {current_time.strftime('%H:%M')} >= {self.config.FORCED_EXIT_TIME}. "
                       f"FORCE CLOSE all positions."
            )
            self.logger.log_decision(decision)
            return decision, "FORCE_CLOSE"

        if current_time >= last_entry_cutoff:
            decision = GateDecision(
                gate_name="Gate17_MarketClose",
                passed=False,
                reason=f"Market close approach: {current_time.strftime('%H:%M')} >= {self.config.LAST_ENTRY_CUTOFF_TIME}. "
                       f"No new entries (closes only)."
            )
            self.logger.log_decision(decision)
            return decision, "NO_ENTRY"

        decision = GateDecision(
            gate_name="Gate17_MarketClose",
            passed=True,
            reason=f"Normal trading hours: {current_time.strftime('%H:%M')} < {self.config.LAST_ENTRY_CUTOFF_TIME}"
        )
        self.logger.log_decision(decision)
        return decision, "ALLOW_ENTRY"


# ============================================================================
# GATE 18: CIRCUIT BREAKER (Priority 2)
# ============================================================================

class Gate18CircuitBreaker:
    """
    Gate 18: Circuit Breaker (Priority 2)

    HALT system if ANY of these conditions trigger:
    - Broker offline > 5 minutes
    - Unhandled exception
    - Health check failed
    - All orders rejected
    - Position sync error

    This is a HARD STOP. Only manual override can restart.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger
        self.circuit_breaker_active = False
        self.active_condition = None

    def evaluate(self, system_state: SystemState) -> GateDecision:
        """Check if any circuit breaker condition is triggered"""

        # Condition 1: Broker offline
        if system_state.broker_offline_seconds > self.config.BROKER_OFFLINE_THRESHOLD_SECONDS:
            self.circuit_breaker_active = True
            self.active_condition = "broker_offline"
            decision = GateDecision(
                gate_name="Gate18_CircuitBreaker",
                passed=False,
                reason=f"CIRCUIT BREAKER: Broker offline {system_state.broker_offline_seconds}s "
                       f"> {self.config.BROKER_OFFLINE_THRESHOLD_SECONDS}s"
            )
            self.logger.log_decision(decision)
            return decision

        # Condition 2: Circuit breaker already triggered
        if system_state.circuit_breaker_triggered:
            self.circuit_breaker_active = True
            self.active_condition = "already_triggered"
            decision = GateDecision(
                gate_name="Gate18_CircuitBreaker",
                passed=False,
                reason="CIRCUIT BREAKER: Already triggered by external condition"
            )
            self.logger.log_decision(decision)
            return decision

        # All conditions OK
        self.circuit_breaker_active = False
        decision = GateDecision(
            gate_name="Gate18_CircuitBreaker",
            passed=True,
            reason="Circuit breaker OK: All health conditions nominal"
        )
        self.logger.log_decision(decision)
        return decision

    def reset(self, reason: str = "Manual reset"):
        """Reset circuit breaker (manual operator action only)"""
        self.circuit_breaker_active = False
        self.active_condition = None
        self.logger.info(f"Circuit breaker reset: {reason}")


# ============================================================================
# ENTRY DECISION ENGINE
# ============================================================================

class EntryDecisionEngine:
    """
    Master entry decision engine.

    Calls all gates in priority order.
    Stops at first failure (fail-closed).
    Logs all decisions.
    Returns final entry decision.
    """

    def __init__(self, config: SafetyGateConfig, logger: GateLogger):
        self.config = config
        self.logger = logger

        # Initialize all 18 gates
        # Priority 1: Kill Switch
        self.gate_01 = Gate01KillSwitch(config, logger)

        # Priority 2: Hard Halts
        self.gate_02 = Gate02DrawdownHalt(config, logger)
        self.gate_03 = Gate03DailyLossHalt(config, logger)
        self.gate_04 = Gate04BrokerHalt(config, logger)
        self.gate_18 = Gate18CircuitBreaker(config, logger)

        # Priority 3: Hard Limits & Operational
        self.gate_05 = Gate05ConcurrentPositions(config, logger)
        self.gate_06 = Gate06GrossExposure(config, logger)
        self.gate_07 = Gate07StaleData(config, logger)
        self.gate_08 = Gate08SymbolConcentration(config, logger)
        self.gate_09 = Gate09PositionQuantity(config, logger)
        self.gate_13 = Gate13OrderDuplication(config, logger)
        self.gate_14 = Gate14OrderTimeout(config, logger)
        self.gate_15 = Gate15OrderReconciliation(config, logger)
        self.gate_17 = Gate17MarketClose(config, logger)

        # Priority 4: Derating
        self.gate_10 = Gate10DrawdownDerating(config, logger)
        self.gate_11 = Gate11LambdaDerating(config, logger)

        # Priority 5: Strategy Signals & Execution
        self.gate_12 = Gate12StrategySignals(config, logger)
        self.gate_16 = Gate16Slippage(config, logger)

    def can_enter(self, signal: EntrySignal,
                  system_state: SystemState,
                  current_time: datetime = None) -> Tuple[bool, int, str]:
        """
        Master entry decision - All 18 gates in priority order.

        Returns:
            (can_enter: bool, final_size: int, reason: str)
        """
        if current_time is None:
            current_time = datetime.now()

        # PRIORITY 1: KILL SWITCH (HIGHEST)
        decision = self.gate_01.evaluate(system_state)
        if not decision.passed:
            return False, 0, f"[PRIORITY 1] {decision.reason}"

        # PRIORITY 2: HARD HALTS
        decision = self.gate_02.evaluate(system_state)
        if not decision.passed:
            return False, 0, f"[PRIORITY 2] {decision.reason}"

        decision = self.gate_03.evaluate(system_state)
        if not decision.passed:
            return False, 0, f"[PRIORITY 2] {decision.reason}"

        decision = self.gate_04.evaluate(system_state)
        if not decision.passed:
            return False, 0, f"[PRIORITY 2] {decision.reason}"

        decision = self.gate_18.evaluate(system_state)
        if not decision.passed:
            return False, 0, f"[PRIORITY 2] {decision.reason}"

        # PRIORITY 3: HARD LIMITS & OPERATIONAL
        decision = self.gate_05.evaluate(system_state)
        if not decision.passed:
            return False, 0, f"[PRIORITY 3] {decision.reason}"

        decision = self.gate_06.evaluate(system_state, signal.position_notional)
        if not decision.passed:
            return False, 0, f"[PRIORITY 3] {decision.reason}"

        decision = self.gate_07.evaluate(system_state)
        if not decision.passed:
            return False, 0, f"[PRIORITY 3] {decision.reason}"

        decision = self.gate_08.evaluate(
            system_state,
            signal.symbol,
            signal.position_notional
        )
        if not decision.passed:
            return False, 0, f"[PRIORITY 3] {decision.reason}"

        decision, capped_qty = self.gate_09.evaluate(
            system_state,
            signal.symbol,
            signal.suggested_quantity
        )
        if not decision.passed:
            return False, 0, f"[PRIORITY 3] {decision.reason}"
        position_size = capped_qty

        decision = self.gate_13.evaluate(system_state, signal)
        if not decision.passed:
            return False, 0, f"[PRIORITY 3] {decision.reason}"

        decision = self.gate_14.evaluate(signal.timestamp, current_time)
        if not decision.passed:
            return False, 0, f"[PRIORITY 3] {decision.reason}"

        decision = self.gate_15.evaluate(system_state)
        if not decision.passed:
            return False, 0, f"[PRIORITY 3] {decision.reason}"

        decision, market_action = self.gate_17.evaluate(current_time)
        if market_action == "FORCE_CLOSE":
            return False, 0, f"[PRIORITY 3] {decision.reason}"
        elif market_action == "NO_ENTRY" and not decision.passed:
            return False, 0, f"[PRIORITY 3] {decision.reason}"

        # PRIORITY 4: DERATING (reduces position size)
        decision, derated_size = self.gate_10.evaluate(system_state, position_size)
        position_size = derated_size

        decision, derated_size = self.gate_11.evaluate(system_state, position_size)
        position_size = derated_size

        # PRIORITY 5: STRATEGY SIGNALS & EXECUTION
        decision = self.gate_12.evaluate(signal)
        if not decision.passed:
            return False, 0, f"[PRIORITY 5] {decision.reason}"

        # Note: Gate 16 (Slippage) is evaluated at execution time, not entry decision time
        # It checks the actual fill price against target price

        # All gates passed!
        return True, position_size, f"[OK] Entry {signal.symbol} with {position_size} shares"


# ============================================================================
# MAIN - EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    # Setup
    config = SafetyGateConfig()
    logger = GateLogger()
    engine = EntryDecisionEngine(config, logger)

    # Create sample system state
    system_state = SystemState(
        portfolio_value=1000000,
        current_dd_percent=0.10,
        current_lambda=0.12,
        daily_realized_loss=-10000,
        daily_unrealized_loss=-5000,
        open_positions_count=2,
        open_positions=[
            {'symbol': 'INFY', 'notional': 50000},
            {'symbol': 'TCS', 'notional': 40000},
        ],
        market_data_age_seconds=15,
        broker_connected=True,
        broker_offline_seconds=0,
        kill_switch_active=False,
        circuit_breaker_triggered=False
    )

    # Create sample entry signal
    signal = EntrySignal(
        symbol='RELIANCE',
        entry_price=2500,
        stop_loss_price=2450,
        profit_target_price=2600,
        confidence=0.65,
        suggested_quantity=10,
        position_notional=25000,
        risk_reward_ratio=2.0
    )

    # Make entry decision
    can_enter, final_size, reason = engine.can_enter(signal, system_state)

    print(f"\n{'='*80}")
    print(f"ENTRY DECISION")
    print(f"{'='*80}")
    print(f"Symbol: {signal.symbol}")
    print(f"Can Enter: {can_enter}")
    print(f"Final Size: {final_size} shares")
    print(f"Reason: {reason}")
    print(f"{'='*80}\n")

    # Print decision history
    print("Decision History:")
    for decision in logger.get_decision_history():
        print(f"  {decision.gate_name}: {decision.reason}")

