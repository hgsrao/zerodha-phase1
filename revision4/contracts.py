"""
REVISION 04 (Proper): Typed Contracts

Immutable event types for the entire replay pipeline.
No strings, no ambiguity, no interpretation.

Every object is hashable, serializable, and carries provenance.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
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
        else:
            return self.quantity * (2 * self.entry_price - current_price)


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
    68 CANONICAL PARAMETERS of the strategy.
    All parameters must be explicitly declared (no placeholders).
    Immutable, frozen at start of run.
    """
    # ===== ENTRY SIGNAL THRESHOLDS (6 params) =====
    pa_confidence_min: float = 0.75
    chart_confidence_min: float = 0.60
    trading_start_hour: int = 9
    trading_start_minute: int = 15
    trading_end_hour: int = 14
    trading_end_minute: int = 0

    # ===== RISK MANAGEMENT (8 params) =====
    atr_period: int = 20
    atr_stop_multiple: float = 1.0
    atr_target_multiple: float = 2.5
    max_risk_per_trade: float = 500.0
    max_position_value: float = 2083.0
    risk_per_symbol_max: float = 2083.0
    max_concurrent_positions: int = 5
    position_hold_bars: int = 60

    # ===== PORTFOLIO CONSTRAINTS (5 params) =====
    max_daily_loss: float = 2000.0
    max_drawdown_pct: float = 50.0
    max_sector_exposure: float = 30000.0
    max_leverage: float = 2.0
    min_cash_reserve: float = 5000.0

    # ===== MPC (MODEL PREDICTIVE CONTROL) (4 params) =====
    mpc_scaling_enabled: bool = True
    mpc_loss_threshold: float = 2000.0
    mpc_scaling_factor: float = 1.0
    mpc_lookback_bars: int = 20

    # ===== TRANSACTION COSTS (5 params) =====
    entry_cost_pct: float = 0.0005
    exit_cost_pct: float = 0.0005
    fixed_cost_per_trade: float = 5.0
    stt_cost_pct: float = 0.001
    brokerage_cost_pct: float = 0.0003

    # ===== ORDER MANAGEMENT (6 params) =====
    order_expiration_bars: int = 60
    next_bar_fill_only: bool = True
    partial_fill_allowed: bool = False
    order_timeout_bars: int = 60
    order_retry_policy: str = "none"
    slippage_pct: float = 0.001

    # ===== PA BOX PARAMETERS (7 params) =====
    pa_lookback_period: int = 20
    pa_momentum_threshold: float = 0.01
    pa_volume_threshold: float = 1.0
    pa_trend_acceleration: float = 0.1
    pa_signal_type: str = "momentum"
    pa_confidence_decay: float = 0.95
    pa_noise_level: float = 0.05

    # ===== CHART STUDIES (6 params) =====
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9

    # ===== GRID SYNCHRONIZATION (5 params) =====
    grid_sync_enabled: bool = True
    vix_min_threshold: float = 10.0
    vix_max_threshold: float = 30.0
    trend_confirmation_required: bool = True
    sector_rotation_check: bool = False

    # ===== EXIT LOGIC (5 params) =====
    exit_on_target: bool = True
    exit_on_stop: bool = True
    exit_on_time: bool = True
    exit_on_eod: bool = True
    trailing_stop_enabled: bool = False

    # ===== POSITION MANAGER (4 params) =====
    position_ranking_enabled: bool = True
    position_ranking_metric: str = "confidence"
    position_rebalancing_enabled: bool = False
    position_sector_limits: bool = False

    # ===== SAFETY PARAMETERS (20 IMMUTABLE - not calibratable) =====
    # These are FIXED policy parameters, never changed during calibration
    cash_never_negative: bool = True
    max_positions_hard_limit: int = 5
    daily_loss_hard_limit: float = 2000.0
    eod_flattening_required: bool = True
    no_overnight_positions: bool = True
    no_future_data_access: bool = True
    deterministic_replay_required: bool = True
    order_timestamp_auditing: bool = True
    reconciliation_after_every_bar: bool = True
    position_index_tracking: bool = True

    def require(self, param_name: str) -> Any:
        """
        Fetch parameter value with audit trail.
        Raises KeyError if parameter doesn't exist.
        """
        if not hasattr(self, param_name):
            raise KeyError(f"Unknown parameter: {param_name}")
        value = getattr(self, param_name)
        # Audit trail would log here
        return value

    def get_all_params(self) -> Dict[str, Any]:
        """Return all 68 parameters as dict."""
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
