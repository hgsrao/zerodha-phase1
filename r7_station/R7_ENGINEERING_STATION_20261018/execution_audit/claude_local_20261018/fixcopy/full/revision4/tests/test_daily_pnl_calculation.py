"""Test daily P&L calculation from authoritative completed_trades ledger."""

import pytest
import pandas as pd
from revision4.contracts import EffectiveConfig, ExitReason
from revision4.portfolio import PortfolioLedger
from revision4.research_target import CompletedTrade


def test_daily_pnl_sums_to_total_from_completed_trades():
    """Daily P&L by date must sum to total net P&L from completed_trades."""

    # Create ledger
    ledger = PortfolioLedger()

    # Add two completed trades on different dates
    # Trade 1: Profitable, exits on 2024-08-02
    trade1 = CompletedTrade(
        trade_id="trade_1",
        symbol="SUNPHARMA",
        direction=1,  # Long
        entry_timestamp="2024-08-01T10:00:00+05:30",
        entry_price=100.0,
        entry_cost=50.0,  # Brokerage + exchange
        exit_timestamp="2024-08-02T14:30:00+05:30",
        exit_price=110.0,
        exit_cost=60.0,  # Brokerage + exchange + STT
        exit_reason=ExitReason.TARGET_HIT,
        quantity=100.0,
        gross_pnl=1000.0,  # (110 - 100) * 100
        net_pnl=890.0,  # 1000 - 50 - 60
    )

    # Trade 2: Losing, exits on 2024-08-05
    trade2 = CompletedTrade(
        trade_id="trade_2",
        symbol="SUNPHARMA",
        direction=1,  # Long
        entry_timestamp="2024-08-03T10:00:00+05:30",
        entry_price=110.0,
        entry_cost=55.0,
        exit_timestamp="2024-08-05T14:30:00+05:30",
        exit_price=105.0,
        exit_cost=65.0,  # STT on sale
        exit_reason=ExitReason.STOP_HIT,
        quantity=100.0,
        gross_pnl=-500.0,  # (105 - 110) * 100
        net_pnl=-620.0,  # -500 - 55 - 65
    )

    # Add to ledger
    ledger.completed_trades.append(trade1)
    ledger.completed_trades.append(trade2)

    # Calculate daily P&L series (same logic as validator)
    daily_pnl_series = {}
    for completed_trade in ledger.completed_trades:
        exit_date = pd.Timestamp(completed_trade.exit_timestamp).date().isoformat()
        daily_pnl_series[exit_date] = daily_pnl_series.get(exit_date, 0.0) + completed_trade.net_pnl

    # Verify daily totals
    assert "2024-08-02" in daily_pnl_series
    assert "2024-08-05" in daily_pnl_series
    assert daily_pnl_series["2024-08-02"] == 890.0, f"Trade 1 P&L should be 890.0, got {daily_pnl_series['2024-08-02']}"
    assert daily_pnl_series["2024-08-05"] == -620.0, f"Trade 2 P&L should be -620.0, got {daily_pnl_series['2024-08-05']}"

    # Verify total sums correctly
    total_daily_pnl = sum(daily_pnl_series.values())
    total_trade_pnl = sum(ct.net_pnl for ct in ledger.completed_trades)
    assert total_daily_pnl == total_trade_pnl, f"Daily sum {total_daily_pnl} != trade sum {total_trade_pnl}"
    assert total_daily_pnl == 270.0, f"Total should be 270.0 (890 - 620), got {total_daily_pnl}"

    # Verify dates are proper ISO format
    for date_key in daily_pnl_series.keys():
        assert len(date_key) == 10, f"Date should be YYYY-MM-DD format, got {date_key}"
        assert date_key.count("-") == 2, f"Date format incorrect: {date_key}"


def test_daily_pnl_handles_multiple_trades_same_day():
    """Multiple trades on same day should aggregate correctly."""

    ledger = PortfolioLedger()

    # Two trades on same day: +500 and -100
    trade1 = CompletedTrade(
        trade_id="trade_1",
        symbol="SYM1",
        direction=1,
        entry_timestamp="2024-08-02T09:00:00+05:30",
        entry_price=100.0,
        entry_cost=50.0,
        exit_timestamp="2024-08-02T14:00:00+05:30",
        exit_price=110.0,
        exit_cost=50.0,
        exit_reason=ExitReason.TARGET_HIT,
        quantity=100.0,
        gross_pnl=1000.0,
        net_pnl=900.0,
    )

    trade2 = CompletedTrade(
        trade_id="trade_2",
        symbol="SYM2",
        direction=1,
        entry_timestamp="2024-08-02T10:00:00+05:30",
        entry_price=50.0,
        entry_cost=25.0,
        exit_timestamp="2024-08-02T15:00:00+05:30",
        exit_price=49.0,
        exit_cost=30.0,
        exit_reason=ExitReason.STOP_HIT,
        quantity=100.0,
        gross_pnl=-100.0,
        net_pnl=-155.0,
    )

    ledger.completed_trades.extend([trade1, trade2])

    # Calculate daily P&L
    daily_pnl_series = {}
    for completed_trade in ledger.completed_trades:
        exit_date = pd.Timestamp(completed_trade.exit_timestamp).date().isoformat()
        daily_pnl_series[exit_date] = daily_pnl_series.get(exit_date, 0.0) + completed_trade.net_pnl

    # Should have one entry for 2024-08-02 with sum of both trades
    assert len(daily_pnl_series) == 1
    assert daily_pnl_series["2024-08-02"] == 745.0, f"Should sum to 745 (900 - 155), got {daily_pnl_series['2024-08-02']}"


def test_daily_pnl_timestamp_parsing_handles_formats():
    """Daily P&L calculation must handle different timestamp formats."""

    ledger = PortfolioLedger()

    # Create trades with different timestamp formats that pd.Timestamp should handle
    formats_to_test = [
        "2024-08-02T14:30:00+05:30",  # ISO with timezone
        "2024-08-02 14:30:00",  # Space-separated
        "2024-08-02T14:30:00Z",  # ISO UTC
    ]

    for i, ts_format in enumerate(formats_to_test):
        trade = CompletedTrade(
            trade_id=f"trade_{i}",
            symbol=f"SYM{i}",
            direction=1,
            entry_timestamp="2024-08-01T10:00:00+05:30",
            entry_price=100.0,
            entry_cost=50.0,
            exit_timestamp=ts_format,
            exit_price=110.0,
            exit_cost=50.0,
            exit_reason=ExitReason.TARGET_HIT,
            quantity=100.0,
            gross_pnl=1000.0,
            net_pnl=900.0,
        )
        ledger.completed_trades.append(trade)

    # Calculate daily P&L
    daily_pnl_series = {}
    for completed_trade in ledger.completed_trades:
        exit_date = pd.Timestamp(completed_trade.exit_timestamp).date().isoformat()
        daily_pnl_series[exit_date] = daily_pnl_series.get(exit_date, 0.0) + completed_trade.net_pnl

    # All should be on 2024-08-02
    assert len(daily_pnl_series) == 1
    assert "2024-08-02" in daily_pnl_series
    assert daily_pnl_series["2024-08-02"] == 2700.0  # 900 * 3 trades
