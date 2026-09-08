"""
REVISION 04: Research Target Configuration

This is an EVALUATION METRIC, not a trading instruction.
The engine must NEVER override risk gates or bypass authorization to achieve this target.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ResearchTarget:
    """
    Research target for sealed month evaluation.

    Used for reporting only. Risk gates and authorization are never overridden
    to achieve these numbers.
    """
    starting_capital: float = 100_000.0  # ₹1 lakh
    daily_net_pnl_benchmark: float = 400.0  # ₹400/day (~0.4% daily)

    # These are computed from actual data
    total_trading_sessions: Optional[int] = None
    expected_monthly_pnl: Optional[float] = None

    def compute_monthly_target(self, session_count: int) -> float:
        """
        Compute expected monthly P&L based on actual trading sessions.

        Args:
            session_count: Number of actual trading sessions in sealed data

        Returns:
            Expected monthly P&L (daily_benchmark × session_count)
        """
        return self.daily_net_pnl_benchmark * session_count


@dataclass
class RunEvaluation:
    """
    Evaluation report after sealed month run.
    Shows attainment of research target, but never influenced by it.
    """
    starting_equity: float
    ending_equity: float
    net_pnl: float  # ending - starting

    total_trading_sessions: int
    avg_daily_net_pnl: float  # net_pnl / sessions

    sessions_above_benchmark: int  # count of days where daily_pnl >= ₹400
    sessions_below_benchmark: int  # count of days where daily_pnl < ₹400
    attainment_rate: float  # above / total

    max_drawdown_pct: float
    max_drawdown_rupees: float

    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float

    safety_violations: int  # Must be 0

    def is_acceptable(self) -> bool:
        """
        Determine if run meets minimal acceptance criteria.

        This is NOT about hitting the ₹400 benchmark.
        This is about fundamental soundness.
        """
        # Safety violations are deal-breaker
        if self.safety_violations > 0:
            return False

        # Positive expectancy after costs
        if self.net_pnl <= 0:
            return False

        # Minimum trades for statistical validity
        if self.total_trades < 5:
            return False

        # Controlled drawdown
        if self.max_drawdown_pct > 50.0:  # Don't lose more than 50% equity
            return False

        return True


def evaluate_run(
    seal_config: dict,
    run_result: dict,
    benchmark: ResearchTarget = None,
) -> RunEvaluation:
    """
    Evaluate a sealed month run against research target.

    Args:
        seal_config: DatasetSeal info (symbol count, date range, etc.)
        run_result: RunResult from replay
        benchmark: ResearchTarget (default: ₹400/day)

    Returns:
        RunEvaluation with all metrics
    """
    if benchmark is None:
        benchmark = ResearchTarget()

    # Extract from run_result
    starting_equity = run_result.get("starting_equity", 100_000.0)
    ending_equity = run_result.get("ending_equity", starting_equity)
    net_pnl = ending_equity - starting_equity

    total_trades = run_result.get("total_trades", 0)
    winning_trades = run_result.get("winning_trades", 0)
    losing_trades = run_result.get("losing_trades", 0)
    max_drawdown = run_result.get("max_drawdown", 0.0)

    # Compute from seal_config (actual trading sessions)
    # For now, assume NSE 1-min data = ~6.5 hours/day × 60 min = ~390 bars/day
    # Sealed month has approximately 21-22 trading days
    trading_sessions = seal_config.get("trading_sessions", 21)

    avg_daily_pnl = net_pnl / trading_sessions if trading_sessions > 0 else 0.0

    # Count days above/below benchmark
    # (This requires per-day P&L from run_result; simplified for now)
    sessions_above = run_result.get("sessions_above_benchmark", 0)
    sessions_below = trading_sessions - sessions_above
    attainment_rate = sessions_above / trading_sessions if trading_sessions > 0 else 0.0

    win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
    profit_factor = run_result.get("profit_factor", 1.0)

    safety_violations = run_result.get("safety_violations", 0)
    max_drawdown_rupees = starting_equity * (max_drawdown / 100.0)

    return RunEvaluation(
        starting_equity=starting_equity,
        ending_equity=ending_equity,
        net_pnl=net_pnl,
        total_trading_sessions=trading_sessions,
        avg_daily_net_pnl=avg_daily_pnl,
        sessions_above_benchmark=sessions_above,
        sessions_below_benchmark=sessions_below,
        attainment_rate=attainment_rate,
        max_drawdown_pct=max_drawdown,
        max_drawdown_rupees=max_drawdown_rupees,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        safety_violations=safety_violations,
    )
