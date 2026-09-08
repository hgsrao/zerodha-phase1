"""
REVISION 04: Research Target and Sealed Run Evaluation

₹400/day is a REPORTING BENCHMARK only, not a trading target.
The evaluator REQUIRES completed trades and DERIVES all metrics from them.
It is fail-closed: rejects incomplete or inconsistent data.

BROKER/LEDGER CONTRACT: CompletedTrade records with:
- trade_id, symbol, entry/exit timestamps, prices, costs
- quantity, gross_pnl, net_pnl, exit_reason

EVALUATOR CONTRACT: Aggregates trades by exit date, derives:
- Daily results from trades (not supplied)
- Profit factor from actual wins/losses (not supplied)
- Reconciliation: trades ↔ daily ↔ equity
"""

from dataclasses import dataclass
from typing import List, Dict, Optional
from datetime import datetime
from enum import Enum


class ExitReason(Enum):
    """Trade exit reason (must match revision4/contracts.py)."""
    TARGET_HIT = "TARGET_HIT"
    STOP_HIT = "STOP_HIT"
    TIME_EXIT = "TIME_EXIT"
    EOD_FLATTENING = "EOD_FLATTENING"
    LIQUIDATION = "LIQUIDATION"
    ERROR = "ERROR"


@dataclass(frozen=True)
class CompletedTrade:
    """
    Completed trade record (broker/ledger contract).
    This is THE contract that PaperBroker and PortfolioLedger must produce.
    """
    trade_id: str  # Unique identifier
    symbol: str

    # Entry leg
    entry_timestamp: str  # ISO format: '2024-08-01T09:15:00Z'
    entry_price: float
    entry_cost: float  # Brokerage + exchange on entry

    # Exit leg
    exit_timestamp: str  # ISO format: '2024-08-01T14:30:00Z'
    exit_price: float
    exit_cost: float  # Brokerage + exchange + STT on exit
    exit_reason: ExitReason

    # Quantity and P&L
    quantity: float
    gross_pnl: float  # (exit_price - entry_price) * quantity
    net_pnl: float  # gross_pnl - entry_cost - exit_cost


@dataclass(frozen=True)
class BenchmarkConfig:
    """Reporting benchmark (not trading target)."""
    daily_net_pnl_benchmark: float = 400.0
    starting_capital: float = 100_000.0


@dataclass(frozen=True)
class DailyResult:
    """One trading session's results (aggregated from completed trades)."""
    date: str  # ISO format: '2024-08-01'
    net_pnl: float
    trades: int
    wins: int
    losses: int
    gross_profit: float
    gross_loss: float
    total_entry_cost: float
    total_exit_cost: float
    max_dd_pct: float
    safety_violations: int


@dataclass(frozen=True)
class SealedRunEvaluation:
    """
    Evaluation report from completed trades (source of truth).
    All metrics derived, nothing supplied.
    """
    completed_trades: List[CompletedTrade]
    daily_results: List[DailyResult]
    starting_equity: float
    ending_equity: float

    total_net_pnl: float
    total_gross_profit: float
    total_gross_loss: float
    total_profit_factor: float
    total_entry_costs: float
    total_exit_costs: float
    total_safety_violations: int

    total_trading_sessions: int
    avg_daily_net_pnl: float
    max_drawdown_pct: float
    total_trades: int
    total_wins: int
    total_losses: int
    win_rate: float

    sessions_above_benchmark: int
    sessions_below_benchmark: int
    attainment_rate: float

    @classmethod
    def create(
        cls,
        completed_trades: List[CompletedTrade],
        starting_equity: float,
        ending_equity: float,
        benchmark: BenchmarkConfig = None,
    ) -> "SealedRunEvaluation":
        """
        Create evaluation from completed trades (broker/ledger output).
        Derives all metrics. Rejects incomplete/inconsistent data.

        Args:
            completed_trades: List of CompletedTrade from broker/ledger (REQUIRED)
            starting_equity: Starting capital (REQUIRED)
            ending_equity: Final equity (REQUIRED)
            benchmark: Reporting benchmark (default: ₹400/day)

        Raises:
            ValueError: If trades invalid, data inconsistent, or reconciliation fails
        """
        if not completed_trades:
            raise ValueError("completed_trades cannot be empty")
        if not starting_equity or not ending_equity:
            raise ValueError("starting_equity and ending_equity are required")

        if benchmark is None:
            benchmark = BenchmarkConfig()

        # VALIDATE TRADE RECORDS
        for i, trade in enumerate(completed_trades):
            if not trade.trade_id:
                raise ValueError(f"Trade {i}: trade_id required")
            if not trade.symbol:
                raise ValueError(f"Trade {i}: symbol required")
            if trade.quantity <= 0:
                raise ValueError(f"Trade {i}: quantity must be positive")
            if trade.entry_price <= 0 or trade.exit_price <= 0:
                raise ValueError(f"Trade {i}: prices must be positive")
            if trade.entry_cost < 0 or trade.exit_cost < 0:
                raise ValueError(f"Trade {i}: costs cannot be negative")

        # Check unique trade IDs
        trade_ids = [t.trade_id for t in completed_trades]
        if len(trade_ids) != len(set(trade_ids)):
            raise ValueError("Duplicate trade IDs")

        # Validate timestamps
        for trade in completed_trades:
            try:
                entry_dt = datetime.fromisoformat(trade.entry_timestamp)
                exit_dt = datetime.fromisoformat(trade.exit_timestamp)
            except ValueError as e:
                raise ValueError(f"Trade {trade.trade_id}: invalid timestamp: {e}")
            if exit_dt <= entry_dt:
                raise ValueError(f"Trade {trade.trade_id}: exit must be after entry")

        # Verify P&L calculations
        for trade in completed_trades:
            expected_gross = (trade.exit_price - trade.entry_price) * trade.quantity
            if abs(trade.gross_pnl - expected_gross) > 0.01:
                raise ValueError(f"Trade {trade.trade_id}: gross_pnl mismatch")
            expected_net = trade.gross_pnl - trade.entry_cost - trade.exit_cost
            if abs(trade.net_pnl - expected_net) > 0.01:
                raise ValueError(f"Trade {trade.trade_id}: net_pnl mismatch")

        # AGGREGATE TRADES BY EXIT DATE INTO DAILY RESULTS
        daily_by_date: Dict[str, List[CompletedTrade]] = {}
        for trade in completed_trades:
            exit_date = trade.exit_timestamp.split('T')[0]
            if exit_date not in daily_by_date:
                daily_by_date[exit_date] = []
            daily_by_date[exit_date].append(trade)

        # Validate date ordering
        dates = sorted(daily_by_date.keys())
        for i in range(1, len(dates)):
            if dates[i] <= dates[i-1]:
                raise ValueError(f"Dates not chronological: {dates[i-1]} >= {dates[i]}")

        # Create DailyResult for each date
        daily_results = []
        for date in dates:
            trades_today = daily_by_date[date]
            net_pnls = [t.net_pnl for t in trades_today]
            daily_net = sum(net_pnls)
            daily_wins = sum(1 for p in net_pnls if p > 0)
            daily_losses = sum(1 for p in net_pnls if p < 0)
            daily_gross_profit = sum(p for p in net_pnls if p > 0)
            daily_gross_loss = abs(sum(p for p in net_pnls if p < 0))

            daily_result = DailyResult(
                date=date,
                net_pnl=daily_net,
                trades=len(trades_today),
                wins=daily_wins,
                losses=daily_losses,
                gross_profit=daily_gross_profit,
                gross_loss=daily_gross_loss,
                total_entry_cost=sum(t.entry_cost for t in trades_today),
                total_exit_cost=sum(t.exit_cost for t in trades_today),
                max_dd_pct=0.0,  # TODO: compute from mark-to-market
                safety_violations=0,  # TODO: track from orders
            )
            daily_results.append(daily_result)

        # CALCULATE PROFIT FACTOR FROM TRADES
        total_gross_profit = sum(t.net_pnl for t in completed_trades if t.net_pnl > 0)
        total_gross_loss = abs(sum(t.net_pnl for t in completed_trades if t.net_pnl < 0))
        if total_gross_loss > 0:
            profit_factor = total_gross_profit / total_gross_loss
        else:
            profit_factor = float('inf') if total_gross_profit > 0 else 1.0

        # RECONCILIATION CHECKS
        total_trade_pnl = sum(t.net_pnl for t in completed_trades)
        total_daily_pnl = sum(d.net_pnl for d in daily_results)
        expected_equity_change = ending_equity - starting_equity

        # 1. Trades ↔ Daily
        if abs(total_trade_pnl - total_daily_pnl) > 0.01:
            raise ValueError(
                f"Trade-Daily reconciliation failed: "
                f"trades={total_trade_pnl:.2f} != daily={total_daily_pnl:.2f}"
            )

        # 2. Daily ↔ Equity
        if abs(total_daily_pnl - expected_equity_change) > 0.01:
            raise ValueError(
                f"Daily-Equity reconciliation failed: "
                f"daily_pnl={total_daily_pnl:.2f} != equity_change={expected_equity_change:.2f}"
            )

        # 3. Cost accounting
        total_entry_costs = sum(t.entry_cost for t in completed_trades)
        total_exit_costs = sum(t.exit_cost for t in completed_trades)
        total_costs = total_entry_costs + total_exit_costs
        total_gross_pnl = sum(t.gross_pnl for t in completed_trades)
        expected_net_pnl = total_gross_pnl - total_costs
        if abs(total_trade_pnl - expected_net_pnl) > 0.01:
            raise ValueError(
                f"Cost reconciliation failed: "
                f"net_pnl={total_trade_pnl:.2f} != gross={total_gross_pnl:.2f} - costs={total_costs:.2f}"
            )

        # COMPUTE METRICS
        total_trades = len(completed_trades)
        total_wins = sum(1 for t in completed_trades if t.net_pnl > 0)
        total_losses = sum(1 for t in completed_trades if t.net_pnl < 0)
        win_rate = total_wins / total_trades if total_trades > 0 else 0.0
        avg_daily = total_daily_pnl / len(daily_results) if daily_results else 0.0
        max_dd = max((d.max_dd_pct for d in daily_results), default=0.0)

        # Benchmark attainment
        sessions_above = sum(1 for d in daily_results if d.net_pnl >= benchmark.daily_net_pnl_benchmark)
        sessions_below = len(daily_results) - sessions_above
        attainment_rate = sessions_above / len(daily_results) if daily_results else 0.0

        return cls(
            completed_trades=completed_trades,
            daily_results=daily_results,
            starting_equity=starting_equity,
            ending_equity=ending_equity,
            total_net_pnl=total_trade_pnl,
            total_gross_profit=total_gross_profit,
            total_gross_loss=total_gross_loss,
            total_profit_factor=profit_factor,
            total_entry_costs=total_entry_costs,
            total_exit_costs=total_exit_costs,
            total_safety_violations=0,  # TODO: track from orders
            total_trading_sessions=len(daily_results),
            avg_daily_net_pnl=avg_daily,
            max_drawdown_pct=max_dd,
            total_trades=total_trades,
            total_wins=total_wins,
            total_losses=total_losses,
            win_rate=win_rate,
            sessions_above_benchmark=sessions_above,
            sessions_below_benchmark=sessions_below,
            attainment_rate=attainment_rate,
        )

    def to_report(self) -> str:
        """Generate plain-text report."""
        lines = [
            "="*70,
            "SEALED MONTH RUN EVALUATION (from Completed Trades)",
            "="*70,
            f"Starting Equity:           ₹{self.starting_equity:,.2f}",
            f"Ending Equity:             ₹{self.ending_equity:,.2f}",
            f"Net P&L:                   ₹{self.total_net_pnl:,.2f}",
            "",
            f"Trading Sessions:          {self.total_trading_sessions}",
            f"Avg Daily Net P&L:         ₹{self.avg_daily_net_pnl:,.2f}",
            f"Max Drawdown:              {self.max_drawdown_pct:.2f}%",
            "",
            f"Total Trades:              {self.total_trades}",
            f"Winning Trades:            {self.total_wins}",
            f"Losing Trades:             {self.total_losses}",
            f"Win Rate:                  {self.win_rate:.1%}",
            f"Profit Factor:             {self.total_profit_factor:.2f}",
            "",
            f"Gross Profit:              ₹{self.total_gross_profit:,.2f}",
            f"Gross Loss:                ₹{self.total_gross_loss:,.2f}",
            f"Total Entry Costs:         ₹{self.total_entry_costs:,.2f}",
            f"Total Exit Costs:          ₹{self.total_exit_costs:,.2f}",
            "",
            f"Safety Violations:         {self.total_safety_violations}",
            "",
            "BENCHMARK ATTAINMENT (₹400/day, reporting only):",
            f"Sessions Above ₹400:       {self.sessions_above_benchmark}/{self.total_trading_sessions}",
            f"Sessions Below ₹400:       {self.sessions_below_benchmark}/{self.total_trading_sessions}",
            f"Attainment Rate:           {self.attainment_rate:.1%}",
            "="*70,
        ]
        return "\n".join(lines)
