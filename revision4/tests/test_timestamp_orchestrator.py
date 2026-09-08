"""Ordering and shared-portfolio tests for chronological replay."""

from revision4.contracts import Bar, EffectiveConfig, OrderIntent, SizedProposal, TradePlan
from revision4.timestamp_orchestrator import RankedOrderCandidate, TimestampOrchestrator


def _bar(timestamp: str, symbol: str, price: float) -> Bar:
    return Bar(timestamp=timestamp, symbol=symbol, open=price, high=price + 1,
               low=price - 1, close=price, volume=1_000)


def _order(order_id: str, timestamp: str, bar_index: int, symbol: str, price: float) -> OrderIntent:
    plan = TradePlan(timestamp=timestamp, bar_index=bar_index, symbol=symbol, direction=1,
                     entry_price=price, stop_price=price - 2, target_price=price + 3,
                     risk_per_share=2, position_size_base=1)
    proposal = SizedProposal(timestamp=timestamp, bar_index=bar_index, symbol=symbol, plan=plan,
                             mpc_scaling_factor=1, final_quantity=1, cost_estimate=1)
    return OrderIntent(order_id=order_id, timestamp_created=timestamp, bar_index_created=bar_index,
                       symbol=symbol, direction=1, quantity=1, stop_price=plan.stop_price,
                       target_price=plan.target_price, proposal=proposal)


def test_orders_fill_on_later_timestamp_before_new_candidates_see_portfolio():
    observations = []

    def candidates(snapshot, bars, index):
        observations.append((index, tuple(snapshot.positions), tuple(snapshot.pending_orders)))
        if index == 0:
            return [RankedOrderCandidate(_order("order-1", snapshot.timestamp, index, "INFY", 100), 1.0)]
        return []

    result = TimestampOrchestrator(EffectiveConfig(), candidates).run({
        "INFY": [_bar("2024-08-01T09:15:00+00:00", "INFY", 100),
                 _bar("2024-08-01T09:16:00+00:00", "INFY", 101)],
    })

    assert observations == [(0, (), ()), (1, ("INFY",), ())]
    assert [event[1] for event in result.event_log] == ["SUBMIT", "FILL"]
    assert result.fills[0].fill_price == 101


def test_candidates_are_globally_ranked_from_one_shared_timestamp_snapshot():
    seen_snapshot_ids = []

    def candidates(snapshot, bars, index):
        seen_snapshot_ids.append(id(snapshot))
        return [
            RankedOrderCandidate(_order("aaa-low", snapshot.timestamp, index, "AAA", 100), 1.0),
            RankedOrderCandidate(_order("zzz-high", snapshot.timestamp, index, "ZZZ", 100), 2.0),
        ]

    result = TimestampOrchestrator(EffectiveConfig(), candidates).run({
        "AAA": [_bar("2024-08-01T09:15:00+00:00", "AAA", 100)],
        "ZZZ": [_bar("2024-08-01T09:15:00+00:00", "ZZZ", 100)],
    })

    assert len(seen_snapshot_ids) == 1
    assert [order.order_id for order in result.orders_submitted] == ["zzz-high", "aaa-low"]
    assert [event[2] for event in result.event_log] == ["zzz-high", "aaa-low"]
