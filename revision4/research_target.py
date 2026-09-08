"""
REVISION 04: Research Target and Sealed Run Evaluation

₹400/day is a REPORTING BENCHMARK only, not a trading target.
The evaluator REJECTS incomplete reports and FAILS CLOSED on missing data.
Acceptance thresholds are configured separately for smoke tests, research, calibration.
"""

from dataclasses import dataclass
from typing import List, Tuple, Optional
from datetime import datetime


@dataclass(frozen=True)
class BenchmarkConfig:
    """
    Reporting benchmark (not trading target).
    Used only for displaying "sessions above/below ₹400" in reports.
    """
    daily_net_pnl_benchmark: float = 400.0  # ₹400/day (~0.4% on ₹100k)
    starting_capital: float = 100_000.0


@dataclass(frozen=True)
class DailyResult:
    """
    One trading session's results (required for honest evaluation).
    """
    date: str  # ISO format: '2024-08-01'
    net_pnl: float  # Rupees (can be negative)
    trades: int
    wins: int
    losses: int
    max_dd_pct: float  # Max intraday drawdown %
    safety_violations: int


@dataclass(frozen=True)
class SealedRunEvaluation:
    """
    Evaluation report after sealed month run.
    REJECTS if data is incomplete.
    Shows attainment of ₹400/day benchmark (informational only).
    """
    # Required: actual data
    daily_results: List[DailyResult]  # One entry per trading session
    starting_equity: float
    ending_equity: float
    total_net_pnl: float
    total_profit_factor: float  # (sum of wins) / (sum of losses)
    total_safety_violations: int

    # Derived from daily_results
    total_trading_sessions: int
    avg_daily_net_pnl: float
    max_drawdown_pct: float
    total_trades: int
    total_wins: int
    total_losses: int
    win_rate: float

    # Benchmark comparison (reporting only, not decision-making)
    sessions_above_benchmark: int
    sessions_below_benchmark: int
    attainment_rate: float

    @classmethod
    def create(
        cls,
        daily_results: List[DailyResult],
        starting_equity: float,
        ending_equity: float,
        total_profit_factor: float,
        benchmark: BenchmarkConfig = None,
    ) -> "SealedRunEvaluation":
        """
        Create evaluation from actual daily results.
        FAILS if required fields are missing or data does not reconcile.

        Args:
            daily_results: One DailyResult per trading session (REQUIRED, no defaults)
            starting_equity: Starting capital (REQUIRED)
            ending_equity: Final equity (REQUIRED)
            total_profit_factor: (sum wins) / (sum losses) (REQUIRED, must be derived from ledger)
            benchmark: Reporting benchmark (default: ₹400/day)

        Raises:
            ValueError: If data incomplete, doesn't reconcile, or integrity fails
        """
        if not daily_results:
            raise ValueError("daily_results cannot be empty; report is incomplete")

        if not starting_equity or not ending_equity:
            raise ValueError("starting_equity and ending_equity are required")

        if total_profit_factor is None or total_profit_factor < 0:
            raise ValueError(f"total_profit_factor must be explicit (got {total_profit_factor})")

        # RECONCILIATION CHECKS (fail-closed)

        # 1. Daily P&L sum must equal equity change
        daily_pnl_sum = sum(d.net_pnl for d in daily_results)
        expected_net_pnl = ending_equity - starting_equity

        if abs(daily_pnl_sum - expected_net_pnl) > 0.01:  # Allow 1 paisa rounding error
            raise ValueError(
                f"Daily P&L reconciliation failed: "
                f"sum(daily_net_pnl)=₹{daily_pnl_sum:.2f} != "
                f"ending_equity-starting_equity=₹{expected_net_pnl:.2f}"
            )

        # 2. Validate dates: unique, chronological, valid format
        dates = [d.date for d in daily_results]
        if len(dates) != len(set(dates)):
            raise ValueError("Daily results have duplicate dates")

        try:
            parsed_dates = [datetime.fromisoformat(d) for d in dates]
        except ValueError as e:
            raise ValueError(f"Invalid date format in daily_results: {e}")

        for i in range(1, len(parsed_dates)):
            if parsed_dates[i] <= parsed_dates[i-1]:
                raise ValueError(
                    f"Daily results not chronologically ordered: "
                    f"{parsed_dates[i-1]} >= {parsed_dates[i]}"
                )

        # 3. Validate trade accounting: trades == wins + losses for each day
        for d in daily_results:
            if d.trades != d.wins + d.losses:
                raise ValueError(
                    f"{d.date}: trade accounting failed: "
                    f"trades={d.trades} but wins={d.wins} + losses={d.losses} = {d.wins + d.losses}"
                )

        # 4. Validate non-negative counts
        for d in daily_results:
            if d.trades < 0 or d.wins < 0 or d.losses < 0 or d.safety_violations < 0:
                raise ValueError(
                    f"{d.date}: negative trade count invalid: "
                    f"trades={d.trades}, wins={d.wins}, losses={d.losses}, "
                    f"safety_violations={d.safety_violations}"
                )

        if benchmark is None:
            benchmark = BenchmarkConfig()

        # Derive from daily_results (no defaults)
        total_net_pnl = ending_equity - starting_equity
        total_sessions = len(daily_results)
        avg_daily_pnl = total_net_pnl / total_sessions if total_sessions > 0 else 0.0

        # Aggregate trades, wins, losses, safety
        total_trades = sum(d.trades for d in daily_results)
        total_wins = sum(d.wins for d in daily_results)
        total_losses = sum(d.losses for d in daily_results)
        total_safety_violations = sum(d.safety_violations for d in daily_results)
        win_rate = total_wins / total_trades if total_trades > 0 else 0.0

        # Max drawdown: worst intraday drawdown across all sessions
        max_dd = max((d.max_dd_pct for d in daily_results), default=0.0)

        # Benchmark attainment: count sessions above ₹400
        sessions_above = sum(1 for d in daily_results if d.net_pnl >= benchmark.daily_net_pnl_benchmark)
        sessions_below = total_sessions - sessions_above
        attainment_rate = sessions_above / total_sessions if total_sessions > 0 else 0.0

        return cls(
            daily_results=daily_results,
            starting_equity=starting_equity,
            ending_equity=ending_equity,
            total_net_pnl=total_net_pnl,
            total_profit_factor=total_profit_factor,
            total_safety_violations=total_safety_violations,
            total_trading_sessions=total_sessions,
            avg_daily_net_pnl=avg_daily_pnl,
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
        """
        Generate plain-text report for stdout.
        Shows all metrics and benchmark attainment.
        """
        lines = [
            "="*60,
            "SEALED MONTH RUN EVALUATION",
            "="*60,
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
            f"Safety Violations:         {self.total_safety_violations}",
            "",
            "BENCHMARK ATTAINMENT (₹400/day, reporting only):",
            f"Sessions Above ₹400:       {self.sessions_above_benchmark}/{self.total_trading_sessions}",
            f"Sessions Below ₹400:       {self.sessions_below_benchmark}/{self.total_trading_sessions}",
            f"Attainment Rate:           {self.attainment_rate:.1%}",
            "="*60,
        ]
        return "\n".join(lines)
