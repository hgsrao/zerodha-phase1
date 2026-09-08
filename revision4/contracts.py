"""
REVISION 04 (Proper): Typed Contracts

Immutable event types for the entire replay pipeline.
No strings, no ambiguity, no interpretation.

Every object is hashable, serializable, and carries provenance.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Tuple
from enum import Enum
from datetime import datetime


# ============= ENUMS =============

class OrderState(Enum):
    """Order lifecycle state machine."""
    PENDING = "PENDING"           # Created, awaiting fill
    PARTIAL = "PARTIAL"           # Some quantity filled
    FILLED = "FILLED"             # 100% filled
    REJECTED = "REJECTED"         # Entry gate rejected
    EXPIRED = "EXPIRED"           # Unfilled after 60 bars
    CANCELLED = "CANCELLED"       # Manually cancelled


class ExitReason(Enum):
    """Why position closed."""
    TARGET_HIT = "TARGET_HIT"
    STOP_HIT = "STOP_HIT"
    TIME_EXIT = "TIME_EXIT"       # Held 60 bars
    EOD_FLATTENING = "EOD_FLATTENING"
    LIQUIDATION = "LIQUIDATION"   # Daily loss limit
    ERROR = "ERROR"


class SignalType(Enum):
    """Type of forecast signal."""
    MOMENTUM_UP = "MOMENTUM_UP"
    MOMENTUM_DOWN = "MOMENTUM_DOWN"
    MEAN_REVERSION = "MEAN_REVERSION"
    BREAKOUT = "BREAKOUT"
    REJECTED = "REJECTED"


# ============= MARKET DATA =============

@dataclass(frozen=True)
class Bar:
    """One OHLCV bar. Immutable."""
    timestamp: str          # ISO format, UTC
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int

    def __hash__(self):
        return hash((self.timestamp, self.symbol))


@dataclass(frozen=True)
class MarketSnapshot:
    """All bars at one timestamp. Immutable."""
    timestamp: str
    bars: Dict[str, Bar]    # {symbol: Bar}

    def __hash__(self):
        return hash(self.timestamp)


# ============= DECISION FLOW =============

@dataclass(frozen=True)
class ForecastSignal:
    """
    Output from PA Box (Revision 2).
    Immutable forecast about symbol.
    """
    timestamp: str
    bar_index: int
    symbol: str

    signal_type: SignalType
    pa_confidence: float           # 0-1 from PA box
    chart_confidence: float        # 0-1 from Chart Studies
    rejection_reason: Optional[str]  # If signal_type == REJECTED

    def is_valid(self) -> bool:
        return self.signal_type != SignalType.REJECTED


@dataclass(frozen=True)
class IDDecision:
    """
    Output from Entry Validator Box (ID Box).
    Decision: should we enter or not?
    """
    timestamp: str
    bar_index: int
    symbol: str

    forecast: ForecastSignal
    entry_valid: bool
    entry_reason: str           # Reason for valid or invalid
    current_hour: int
    current_minute: int
    grid_sync: bool             # Market regime check
    grid_reason: str


@dataclass(frozen=True)
class TradePlan:
    """
    Output from Risk Manager Box + Grid Sync.
    Stop/target sizing but NOT position size.
    """
    timestamp: str
    bar_index: int
    symbol: str
    direction: int              # +1 BUY, -1 SELL

    entry_price: float
    stop_price: float           # From ATR × stop_mult
    target_price: float         # From ATR × target_mult
    risk_per_share: float       # abs(entry - stop)
    position_size_base: float   # From Risk Manager (before MPC)


@dataclass(frozen=True)
class SizedProposal:
    """
    Output from MPC Box.
    Final position size after dynamic scaling.
    """
    timestamp: str
    bar_index: int
    symbol: str

    plan: TradePlan
    mpc_scaling_factor: float   # Daily loss based
    final_quantity: float       # plan.position_size_base * scaling
    cost_estimate: float        # Estimated entry cost


@dataclass(frozen=True)
class OrderIntent:
    """
    Intent to trade. Immutable, created at bar t.
    Will be submitted for fill at bar t+1.
    """
    order_id: str               # Monotonic UUID
    timestamp_created: str      # Bar t (decision time)
    bar_index_created: int
    symbol: str
    direction: int

    quantity: float
    stop_price: float
    target_price: float
    proposal: SizedProposal     # Full provenance

    state: OrderState = OrderState.PENDING
    rejection_reason: Optional[str] = None


@dataclass(frozen=True)
class FillEvent:
    """
    Order was filled. Immutable record.
    Contains decision time, submit time, fill time.
    """
    fill_id: str                # Unique fill ID
    order_id: str               # Which order
    timestamp_filled: str       # Bar t+1
    bar_index_filled: int

    symbol: str
    direction: int
    quantity_filled: float
    fill_price: float
    cost_paid: float            # Transaction cost

    timestamp_created: str      # For causality tracking
    timestamp_submitted: str    # When order was viable


@dataclass(frozen=True)
class ExitEvent:
    """
    Position was closed. Immutable record.
    Tracks both entry and exit costs for reconciliation.
    """
    exit_id: str
    symbol: str
    timestamp_exit: str
    bar_index_exit: int

    entry_price: float
    exit_price: float
    quantity: float
    direction: int
    bars_held: int

    # Cost accounting (entry from fill, exit from close)
    entry_cost_paid: float  # Brokerage + exchange on entry
    exit_cost_paid: float   # Brokerage + exchange + STT on exit

    exit_reason: ExitReason
    pnl_realized: float
    pnl_pct: float


# ============= PORTFOLIO STATE =============

@dataclass
class Position:
    """Active position. Mutable only during ledger update."""
    symbol: str
    direction: int
    entry_price: float
    entry_bar_index: int
    entry_bar_timestamp: str
    quantity: float
    stop_price: float
    target_price: float
    cost_paid: float
    fill_id: str                # Which FillEvent created this

    def bars_held(self, current_bar_index: int) -> int:
        return current_bar_index - self.entry_bar_index

    def marked_value(self, current_price: float) -> float:
        if self.direction == 1:
            return self.quantity * current_price
        # A short position is a liability at the current market price.  The
        # short-sale proceeds are already reflected in ledger cash at fill;
        # adding a synthetic entry-price component here would double count
        # those proceeds and overstate equity.
        return -self.quantity * current_price


@dataclass
class PortfolioSnapshot:
    """Frozen portfolio state at one timestamp."""
    timestamp: str
    bar_index: int

    cash: float
    reserved_cash: float        # Committed to pending orders
    positions: Dict[str, Position]  # {symbol: Position}
    pending_orders: Dict[str, OrderIntent]  # {order_id: OrderIntent}

    realized_pnl: float
    unrealized_pnl: float
    total_costs: float
    daily_pnl: float            # Today only

    marked_equity: float        # cash + unrealized
    exposure: float
    sector_exposure: Dict[str, float]  # {sector: value}

    def validate(self) -> Tuple[bool, str]:
        """Check ledger invariants."""
        if self.cash < 0:
            return False, f"Cash negative: {self.cash}"
        if len(self.positions) > 5:
            return False, f"More than 5 positions: {len(self.positions)}"
        if self.exposure > 100_000 * 2:
            return False, f"Exposure > 2x: {self.exposure}"
        if abs(self.daily_pnl) > 2000:
            return False, f"Daily loss > ₹2,000: {self.daily_pnl}"
        return True, "Valid"


# ============= RUN CONFIGURATION =============

@dataclass(frozen=True)
class DatasetSeal:
    """Frozen dataset identities."""
    month_start: str            # "2024-08-01"
    month_end: str              # "2024-08-31"
    warmup_bars: int            # 60
    warmup_start: str           # First bar before month

    symbols: List[str]          # Exactly 48
    symbol_count: int           # Must be 48
    symbol_hashes: Dict[str, str]  # {symbol: file_hash}

    code_commit: str            # Git commit
    registry_hash: str          # Config parameter registry
    config_hash: str            # Active config


@dataclass(frozen=True)
class EffectiveConfig:
    """
    All 89 canonical parameters (69 target + 20 safety).
    Derived from canonical_parameter_registry.
    All parameters must be explicitly declared (no placeholders).
    Immutable, frozen at start of run.
    """
    amber_threshold_lower: float = 0.5
    atr_calculation_period: int = 20
    base_dp_dt_multiplier: float = 1.0
    base_dv_dt_multiplier: float = 1.0
    capital_allocation_mode: str = 'equal'
    capital_per_trade_fraction: float = 0.02
    confirmation_2bar_weight: float = 0.25
    data_validation_mode: str = 'strict'
    drawdown_derated_threshold: float = 0.18
    drawdown_halt_threshold: float = 0.25
    drawdown_normal_threshold: float = 0.1
    entry_confidence_threshold: float = 0.15
    entry_signal_smoothing_window: int = 3
    exclude_symbols: List = field(default_factory=list)
    exit_confidence_threshold: float = 0.6
    exit_signal_smoothing_window: int = 2
    green_threshold: float = 0.25
    high_vol_regime_multiplier: float = 1.0
    learning_rate_exploration_factor: float = 0.05
    limit_order_offset_percent: float = 0.02
    lot_size_by_symbol: Dict = field(default_factory=dict)
    low_vol_regime_multiplier: float = 1.0
    max_hold_bars: int = 60
    max_loss_per_day_rupees: int = 50000
    max_loss_per_trade_rupees: int = 5000
    max_positions_live: int = 5
    max_positions_per_symbol: int = 1
    max_retry_attempts: int = 2
    max_sector_exposure_fraction: float = 0.3
    max_symbol_concentration: float = 0.05
    medium_vol_regime_multiplier: float = 1.0
    min_capital_buffer_fraction: float = 0.1
    min_hold_bars: int = 2
    min_risk_reward_ratio: float = 1.5
    minimum_profit_margin_over_cost: float = 0.5
    momentum_calculation_period: int = 20
    momentum_weight: float = 0.25
    order_timeout_seconds: int = 30
    order_type: str = 'MARKET'
    phase1_exploration_intensity: int = 50
    phase2_optimization_intensity: int = 250
    pid_derivative_smoothing: int = 3
    pid_integral_max_clamp: float = 0.1
    pid_integral_window_bars: int = 10
    pid_kd_entry: float = 0.08
    pid_kd_exit: float = 0.06
    pid_ki_entry: float = 0.05
    pid_ki_exit: float = 0.04
    pid_kp_entry: float = 0.15
    pid_kp_exit: float = 0.12
    portfolio_lambda_risk_limit: float = 0.15
    profit_target_atr_mult: float = 1.5
    profit_target_margin_buffer: float = 0.1
    red_threshold: float = 0.3
    retry_delay_seconds: int = 5
    saturation_exit_bars: int = 5
    signal_persistence_requirement: float = 1.5
    slippage_cost_multiplier: float = 1.0
    slippage_guard_threshold: float = 0.05
    slippage_tolerance_percent: float = 0.1
    stop_loss_atr_mult: float = 1.2
    symbols_to_trade: List = field(default_factory=list)
    trading_hours_end: str = '15:30'
    trading_hours_start: str = '09:15'
    trailing_stop_atr_mult: float = 5.5
    volatility_regime_multiplier: float = 1.0
    volatility_weight: float = 0.25
    vwap_calculation_period: int = 20
    vwap_weight: float = 0.25

    # Safety parameters (immutable)
    authorized_cross_session: bool = False  # Allow fills after session close (dangerous)
    drawdown_derate_multiplier: float = 0.8
    drawdown_derate_threshold: float = 0.18
    kill_switch_enabled: bool = True
    lambda_derate_multiplier: float = 0.8
    lambda_derate_threshold: float = 0.15
    max_concurrent_positions: int = 5
    max_daily_loss_rupees: int = 50000
    max_exposure_per_symbol_fraction: float = 0.15
    max_gross_exposure_fraction: float = 0.5
    max_market_data_age_seconds: int = 30
    max_position_quantity: int = 100
    max_reconciliation_qty_diff: int = 0
    max_slippage_fraction: float = 0.001
    min_position_quantity: int = 1
    min_signal_confidence: float = 0.55
    no_entry_cutoff_time: str = '15:20'
    order_dedup_window_seconds: int = 5
    order_timeout_seconds_execution: int = 30
    safety_drawdown_halt_threshold: float = 0.25
    safety_min_risk_reward_ratio: float = 1.5

    def require(self, param_name: str) -> Any:
        """
        Fetch parameter value with audit trail.
        Raises KeyError if parameter doesn't exist.
        """
        if not hasattr(self, param_name):
            raise KeyError(f"Unknown parameter: {param_name}")
        return getattr(self, param_name)

    def get_all_params(self) -> Dict[str, Any]:
        """Return all 89 parameters as dict."""
        import dataclasses
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)}


# ============= RUN RESULT =============

@dataclass
class RunResult:
    """
    Sealed result of one calibration run.
    Contains all data for audit and re-analysis.
    """
    seal: DatasetSeal
    config: EffectiveConfig

    # Execution
    start_timestamp: str
    end_timestamp: str
    bars_processed: int

    # Events (immutable ledger)
    signals: List[ForecastSignal]
    decisions: List[IDDecision]
    orders: List[OrderIntent]
    fills: List[FillEvent]
    exits: List[ExitEvent]

    # Final state
    starting_equity: float
    ending_equity: float
    final_snapshot: PortfolioSnapshot

    # Metrics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    realized_pnl: float
    total_costs: float
    max_drawdown: float
    sharpe_ratio: float
    profit_factor: float

    # Evaluation
    target_pnl: float = 1000.0
    evaluation: str = "INSUFFICIENT_DATA"  # PASS, FAIL, INSUFFICIENT_TRADES

    def is_deterministic_run(self) -> bool:
        """Can this run be exactly reproduced?"""
        return bool(self.seal and self.config)


# ============= TYPE HELPERS =============

from typing import Tuple

def validate_order(order: OrderIntent) -> Tuple[bool, str]:
    """Validate order intent."""
    if order.quantity <= 0:
        return False, "Quantity must be positive"
    if order.direction not in [-1, 1]:
        return False, "Direction must be ±1"
    if order.stop_price >= order.target_price and order.direction == 1:
        return False, "Stop must be < target"
    return True, "Valid"
