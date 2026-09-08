"""Integration test for EOD flattening with real positions and completed trades."""

import pytest
import pandas as pd
from revision4.contracts import EffectiveConfig, Bar, ExitReason
from revision4.timestamp_orchestrator import TimestampOrchestrator, RankedOrderCandidate
from revision4.contracts import OrderIntent, SizedProposal, TradePlan
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger
from revision4.gates_proper import ProperGateEvaluator
from revision4.config_access import calculate_transaction_cost


class MockCandidateProviderEOD:
    """Generate one candidate order at specific timestamp."""

    def __init__(self, target_index: int):
        self.target_index = target_index

    def __call__(self, snapshot, bars, event_index):
        if event_index != self.target_index or not bars:
            return []

        symbol = list(bars.keys())[0]
        bar = bars[symbol]

        plan = TradePlan(
            timestamp=bar.timestamp,
            bar_index=event_index,
            symbol=bar.symbol,
            direction=1 if symbol == "LONG_SYM" else -1,  # Long or short
            entry_price=bar.close,
            stop_price=bar.close - 5.0 if symbol == "LONG_SYM" else bar.close + 5.0,
            target_price=bar.close + 10.0 if symbol == "LONG_SYM" else bar.close - 10.0,
            risk_per_share=5.0,
            position_size_base=10.0,
        )

        proposal = SizedProposal(
            timestamp=bar.timestamp,
            bar_index=event_index,
            symbol=bar.symbol,
            plan=plan,
            mpc_scaling_factor=1.0,
            final_quantity=10.0,
            cost_estimate=1000.0,
        )

        order = OrderIntent(
            order_id=f"eod_test_{symbol}",
            symbol=bar.symbol,
            timestamp_created=bar.timestamp,
            bar_index_created=event_index,
            quantity=10,
            direction=1 if symbol == "LONG_SYM" else -1,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            proposal=proposal,
        )

        return [RankedOrderCandidate(order=order, rank=1.0, pa_confidence=0.8, id_risk_reward=2.0)]


def test_eod_flatten_closes_with_ledger_reconciliation():
    """EOD flatten must close positions via ledger with net P&L = gross - costs."""

    from revision4.contracts import ExitEvent, Position

    config = EffectiveConfig()
    ledger = PortfolioLedger()

    # Create two positions: long and short
    long_position = Position(
        symbol="LONG_SYM",
        direction=1,  # Long
        entry_price=100.0,
        entry_bar_index=0,
        entry_bar_timestamp="2024-08-02T09:15:00+00:00",
        quantity=10.0,
        stop_price=95.0,
        target_price=110.0,
        cost_paid=50.0,  # Entry cost (brokerage + exchange)
        fill_id="fill_long",
    )

    short_position = Position(
        symbol="SHORT_SYM",
        direction=-1,  # Short
        entry_price=50.0,
        entry_bar_index=0,
        entry_bar_timestamp="2024-08-02T09:15:00+00:00",
        quantity=10.0,
        stop_price=55.0,
        target_price=40.0,
        cost_paid=25.0,  # Entry cost
        fill_id="fill_short",
    )

    # Add positions to ledger manually (simulating they were already filled)
    # Positions are keyed by symbol in the ledger
    ledger.positions[long_position.symbol] = long_position
    ledger.positions[short_position.symbol] = short_position

    # EOD flatten both positions
    eod_exits = []
    eod_timestamp = "2024-08-02T15:30:00+00:00"
    eod_bar_index = 390

    for position in [long_position, short_position]:
        # Exit at different prices (one profitable, one losing)
        if position.direction == 1:
            exit_price = 101.0  # Long: up 1.0, profitable
            side = "SELL"
        else:
            exit_price = 49.0  # Short: up 1.0 (bad for short), losing
            side = "BUY"

        exit_cost = calculate_transaction_cost(exit_price, abs(position.quantity), side)

        # Calculate P&L: gross - costs
        if position.direction == 1:
            gross_pnl = (exit_price - position.entry_price) * position.quantity
        else:
            gross_pnl = (position.entry_price - exit_price) * position.quantity

        net_pnl = gross_pnl - position.cost_paid - exit_cost

        pnl_pct = (gross_pnl / (position.entry_price * position.quantity)) * 100

        # Create ExitEvent with net P&L
        exit_event = ExitEvent(
            exit_id=f"eod_{position.symbol}",
            symbol=position.symbol,
            timestamp_exit=eod_timestamp,
            bar_index_exit=eod_bar_index,
            entry_price=position.entry_price,
            exit_price=exit_price,
            quantity=position.quantity,
            direction=position.direction,
            bars_held=eod_bar_index - position.entry_bar_index,
            entry_cost_paid=position.cost_paid,
            exit_cost_paid=exit_cost,
            exit_reason=ExitReason.EOD_FLATTENING,
            pnl_realized=net_pnl,  # MUST be net P&L for ledger to accept
            pnl_pct=pnl_pct,
        )

        # Close position via ledger - this must succeed
        ok, reason = ledger.close_position(exit_event, config)
        assert ok, f"ledger.close_position() failed for {position.symbol}: {reason}"

        eod_exits.append(exit_event)

    # Verify results
    assert len(ledger.positions) == 0, f"Should have 0 positions, got {len(ledger.positions)}"
    assert len(ledger.completed_trades) == 2, f"Should have 2 completed trades, got {len(ledger.completed_trades)}"

    # Verify daily P&L from completed trades
    daily_pnl_series = {}
    for ct in ledger.completed_trades:
        exit_date = pd.Timestamp(ct.exit_timestamp).date().isoformat()
        daily_pnl_series[exit_date] = daily_pnl_series.get(exit_date, 0.0) + ct.net_pnl

    total_daily_pnl = sum(daily_pnl_series.values())
    total_trade_pnl = sum(ct.net_pnl for ct in ledger.completed_trades)

    # Must reconcile exactly
    assert abs(total_daily_pnl - total_trade_pnl) < 0.01, \
        f"Daily P&L {total_daily_pnl:.2f} != trade P&L {total_trade_pnl:.2f}"

    print(f"EOD flatten successful:")
    print(f"  Completed trades: {len(ledger.completed_trades)}")
    print(f"  Open positions: {len(ledger.positions)}")
    print(f"  Daily P&L: {daily_pnl_series}")
    print(f"  Total net P&L: {total_trade_pnl:.2f}")

    # Verify long and short trades
    for ct in ledger.completed_trades:
        print(f"  {ct.symbol}: gross={ct.gross_pnl:.2f}, net={ct.net_pnl:.2f} "
              f"(entry_cost={ct.entry_cost:.2f}, exit_cost={ct.exit_cost:.2f})")
