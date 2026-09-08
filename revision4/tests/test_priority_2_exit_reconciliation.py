"""Priority 2: completed-trade emission and exit-cost reconciliation."""

import pytest

from revision4.config_access import calculate_transaction_cost
from revision4.contracts import (
    Bar, EffectiveConfig, ExitEvent, ExitReason, OrderIntent, Position,
    SizedProposal, TradePlan,
)
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger
from revision4.research_target import SealedRunEvaluation


def test_long_exit_emits_reconciled_completed_trade():
    """Closing a filled long must reconcile cash, both cost legs, and the evaluator."""
    config = EffectiveConfig()
    ledger = PortfolioLedger(starting_cash=100_000.0)

    entry_price = 100.0
    exit_price = 110.0
    quantity = 10.0
    entry_cost = calculate_transaction_cost(entry_price, quantity, "BUY")
    exit_cost = calculate_transaction_cost(exit_price, quantity, "SELL")
    gross_pnl = (exit_price - entry_price) * quantity
    net_pnl = gross_pnl - entry_cost - exit_cost

    # Equivalent state immediately after an authoritative entry fill.
    ledger.cash = ledger.starting_cash - entry_price * quantity - entry_cost
    ledger.total_costs = entry_cost
    ledger.positions["INFY"] = Position(
        symbol="INFY",
        direction=1,
        entry_price=entry_price,
        entry_bar_index=1,
        entry_bar_timestamp="2024-08-01T09:16:00+00:00",
        quantity=quantity,
        stop_price=95.0,
        target_price=110.0,
        cost_paid=entry_cost,
        fill_id="fill-1",
    )

    exit_event = ExitEvent(
        exit_id="exit-1",
        symbol="INFY",
        timestamp_exit="2024-08-01T10:00:00+00:00",
        bar_index_exit=45,
        entry_price=entry_price,
        exit_price=exit_price,
        quantity=quantity,
        direction=1,
        bars_held=44,
        entry_cost_paid=entry_cost,
        exit_cost_paid=exit_cost,
        exit_reason=ExitReason.TARGET_HIT,
        pnl_realized=net_pnl,
        pnl_pct=net_pnl / (entry_price * quantity),
    )

    ok, message = ledger.close_position(exit_event, config)

    assert ok, message
    assert ledger.cash == pytest.approx(ledger.starting_cash + net_pnl)
    assert ledger.total_costs == pytest.approx(entry_cost + exit_cost)
    assert ledger.realized_pnl == pytest.approx(net_pnl)
    assert len(ledger.completed_trades) == 1

    trade = ledger.completed_trades[0]
    assert trade.entry_cost == pytest.approx(entry_cost)
    assert trade.exit_cost == pytest.approx(exit_cost)
    assert trade.gross_pnl == pytest.approx(gross_pnl)
    assert trade.net_pnl == pytest.approx(net_pnl)

    evaluation = SealedRunEvaluation.create(
        completed_trades=ledger.completed_trades,
        starting_equity=ledger.starting_cash,
        ending_equity=ledger.cash,
    )
    assert evaluation.total_net_pnl == pytest.approx(net_pnl)


def test_short_exit_reconciles_cash_and_uses_buy_to_cover_costs():
    """A short must receive entry proceeds and pay the buy-to-cover leg."""
    config = EffectiveConfig()
    ledger = PortfolioLedger(starting_cash=100_000.0)
    entry_price, exit_price, quantity = 100.0, 90.0, 10.0
    entry_cost = calculate_transaction_cost(entry_price, quantity, "SELL")
    exit_cost = calculate_transaction_cost(exit_price, quantity, "BUY")
    gross_pnl = (entry_price - exit_price) * quantity
    net_pnl = gross_pnl - entry_cost - exit_cost

    # Authoritative state immediately after a short sale: sale proceeds are
    # cash, while the open short is a negative marked position value.
    ledger.cash = ledger.starting_cash + entry_price * quantity - entry_cost
    ledger.total_costs = entry_cost
    ledger.positions["INFY"] = Position(
        symbol="INFY", direction=-1, entry_price=entry_price,
        entry_bar_index=1, entry_bar_timestamp="2024-08-01T09:16:00+00:00",
        quantity=quantity, stop_price=105.0, target_price=90.0,
        cost_paid=entry_cost, fill_id="short-fill-1",
    )
    exit_event = ExitEvent(
        exit_id="short-exit-1", symbol="INFY",
        timestamp_exit="2024-08-01T10:00:00+00:00", bar_index_exit=45,
        entry_price=entry_price, exit_price=exit_price, quantity=quantity,
        direction=-1, bars_held=44, entry_cost_paid=entry_cost,
        exit_cost_paid=exit_cost, exit_reason=ExitReason.TARGET_HIT,
        pnl_realized=net_pnl, pnl_pct=net_pnl / (entry_price * quantity),
    )

    ok, message = ledger.close_position(exit_event, config)

    assert ok, message
    assert ledger.cash == pytest.approx(ledger.starting_cash + net_pnl)
    assert ledger.realized_pnl == pytest.approx(net_pnl)
    assert ledger.completed_trades[0].direction == -1
    assert ledger.completed_trades[0].exit_cost == pytest.approx(exit_cost)


@pytest.mark.parametrize("field", ["exit_cost_paid", "pnl_realized"])
def test_close_rejects_mismatched_cost_or_pnl_without_mutating_ledger(field):
    """A rejected exit cannot change cash, positions, or the trade ledger."""
    config = EffectiveConfig()
    ledger = PortfolioLedger(starting_cash=100_000.0)
    entry_price, exit_price, quantity = 100.0, 110.0, 10.0
    entry_cost = calculate_transaction_cost(entry_price, quantity, "BUY")
    exit_cost = calculate_transaction_cost(exit_price, quantity, "SELL")
    net_pnl = (exit_price - entry_price) * quantity - entry_cost - exit_cost
    ledger.cash = ledger.starting_cash - entry_price * quantity - entry_cost
    ledger.total_costs = entry_cost
    ledger.positions["INFY"] = Position(
        symbol="INFY", direction=1, entry_price=entry_price,
        entry_bar_index=1, entry_bar_timestamp="2024-08-01T09:16:00+00:00",
        quantity=quantity, stop_price=95.0, target_price=110.0,
        cost_paid=entry_cost, fill_id="fill-1",
    )
    kwargs = {"exit_cost_paid": exit_cost, "pnl_realized": net_pnl}
    kwargs[field] += 1.0
    event = ExitEvent(
        exit_id="bad-exit", symbol="INFY", timestamp_exit="2024-08-01T10:00:00+00:00",
        bar_index_exit=45, entry_price=entry_price, exit_price=exit_price,
        quantity=quantity, direction=1, bars_held=44,
        entry_cost_paid=entry_cost, exit_cost_paid=kwargs["exit_cost_paid"],
        exit_reason=ExitReason.TARGET_HIT, pnl_realized=kwargs["pnl_realized"],
        pnl_pct=0.0,
    )
    cash_before, costs_before = ledger.cash, ledger.total_costs

    ok, _ = ledger.close_position(event, config)

    assert not ok
    assert ledger.cash == pytest.approx(cash_before)
    assert ledger.total_costs == pytest.approx(costs_before)
    assert "INFY" in ledger.positions
    assert ledger.completed_trades == []


def test_broker_fill_to_ledger_close_is_reconciled_end_to_end():
    """A live paper order must become one reconciled completed trade."""
    config = EffectiveConfig()
    broker = PaperBroker()
    ledger = PortfolioLedger(starting_cash=100_000.0)
    plan = TradePlan(
        timestamp="2024-08-01T09:15:00+00:00", bar_index=1, symbol="INFY",
        direction=1, entry_price=100.0, stop_price=95.0, target_price=110.0,
        risk_per_share=5.0, position_size_base=10.0,
    )
    proposal = SizedProposal(
        timestamp=plan.timestamp, bar_index=plan.bar_index, symbol=plan.symbol,
        plan=plan, mpc_scaling_factor=1.0, final_quantity=10.0,
        cost_estimate=calculate_transaction_cost(100.0, 10.0, "BUY"),
    )
    order = OrderIntent(
        order_id="order-e2e", timestamp_created=plan.timestamp,
        bar_index_created=plan.bar_index, symbol="INFY", direction=1,
        quantity=10.0, stop_price=plan.stop_price, target_price=plan.target_price,
        proposal=proposal,
    )
    ok, message = ledger.create_order(order.order_id, order)
    assert ok, message
    ok, message = broker.submit_order(order, config)
    assert ok, message
    fill_bar = Bar(
        timestamp="2024-08-01T09:16:00+00:00", symbol="INFY", open=101.0,
        high=102.0, low=100.0, close=101.5, volume=1000,
    )
    fill = broker.try_fill_order(order.order_id, fill_bar, fill_bar_index=2, config=config)
    assert fill is not None
    ok, message = ledger.fill_order(order.order_id, fill)
    assert ok, message
    assert broker.retire_filled_orders() == 1

    exit_cost = calculate_transaction_cost(110.0, fill.quantity_filled, "SELL")
    gross_pnl = (110.0 - fill.fill_price) * fill.quantity_filled
    net_pnl = gross_pnl - fill.cost_paid - exit_cost
    exit_event = ExitEvent(
        exit_id="exit-e2e", symbol="INFY", timestamp_exit="2024-08-01T10:00:00+00:00",
        bar_index_exit=46, entry_price=fill.fill_price, exit_price=110.0,
        quantity=fill.quantity_filled, direction=1, bars_held=44,
        entry_cost_paid=fill.cost_paid, exit_cost_paid=exit_cost,
        exit_reason=ExitReason.TARGET_HIT, pnl_realized=net_pnl, pnl_pct=0.0,
    )
    ok, message = ledger.close_position(exit_event, config)
    assert ok, message
    assert not ledger.positions
    assert not broker.get_active_orders()
    assert len(ledger.completed_trades) == 1
    assert ledger.cash == pytest.approx(ledger.starting_cash + net_pnl)


def test_multiple_completed_trades_reconcile_into_daily_results():
    """Two broker/ledger-compatible trades must aggregate without hidden P&L."""
    from revision4.research_target import CompletedTrade

    first_entry_cost = calculate_transaction_cost(100.0, 10.0, "BUY")
    first_exit_cost = calculate_transaction_cost(110.0, 10.0, "SELL")
    first_net = 100.0 - first_entry_cost - first_exit_cost
    second_entry_cost = calculate_transaction_cost(200.0, 5.0, "SELL")
    second_exit_cost = calculate_transaction_cost(190.0, 5.0, "BUY")
    second_net = 50.0 - second_entry_cost - second_exit_cost
    trades = [
        CompletedTrade("trade-1", "INFY", 1, "2024-08-01T09:16:00+00:00", 100.0,
                       first_entry_cost, "2024-08-01T10:00:00+00:00", 110.0,
                       first_exit_cost, ExitReason.TARGET_HIT, 10.0, 100.0, first_net),
        CompletedTrade("trade-2", "TCS", -1, "2024-08-02T09:16:00+00:00", 200.0,
                       second_entry_cost, "2024-08-02T10:00:00+00:00", 190.0,
                       second_exit_cost, ExitReason.TARGET_HIT, 5.0, 50.0, second_net),
    ]
    starting_equity = 100_000.0
    evaluation = SealedRunEvaluation.create(
        completed_trades=trades,
        starting_equity=starting_equity,
        ending_equity=starting_equity + first_net + second_net,
    )

    assert evaluation.total_trades == 2
    assert len(evaluation.daily_results) == 2
    assert evaluation.total_net_pnl == pytest.approx(first_net + second_net)
