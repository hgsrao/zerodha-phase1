"""Conversion identity and durable trade/protection coherence component tests."""
import pandas as pd
import pytest
from test_combined_cycle_handoff import setup, request


@pytest.mark.parametrize('quantity', [9, 11])
def test_request_must_match_authoritative_trade_quantity(tmp_path, quantity):
    store, manager = setup(tmp_path / 'cycle.db')
    before = store.load('p1')
    with pytest.raises(ValueError, match='authoritative trade quantity'):
        manager.request('p1', quantity, pd.Timestamp('2023-12-05 15:10'),
                        current_r=.8, mfe_r=1, trend_aligned=True)
    assert store.requests() == []
    assert store.load('p1').revision == before.revision


def test_duplicate_request_cannot_change_quantity(tmp_path):
    store, manager = setup(tmp_path / 'cycle.db')
    original = request(manager)
    with pytest.raises(ValueError, match='authoritative trade quantity'):
        manager.request('p1', 9, pd.Timestamp('2023-12-05 15:10'),
                        current_r=.8, mfe_r=1, trend_aligned=True)
    assert store.requests()[0]['request_id'] == original.request_id


@pytest.mark.parametrize('change', [
    {'quantity': 0}, {'quantity': True}, {'quantity': 10.5},
    {'symbol': 'INFY'}, {'trade_id': 'different'}, {'side': 'SELL'},
    {'entry_price': 101}, {'quantity': 11},
])
def test_incoherent_durable_trade_rejected_without_mutating_snapshot(tmp_path, change):
    store, _ = setup(tmp_path / 'cycle.db')
    old = store.load('p1')
    trade = dict(old.trade, **change)
    with pytest.raises(ValueError):
        store.save(old.record, trade, old.protection, old.revision)
    assert store.load('p1') == old


def test_durable_protective_stop_must_match_lifecycle(tmp_path):
    store, _ = setup(tmp_path / 'cycle.db')
    old = store.load('p1')
    with pytest.raises(ValueError, match='protective stop'):
        store.save(old.record, old.trade, {'stop': 89}, old.revision)
    assert store.load('p1') == old


def test_valid_request_and_snapshot_remain_compatible(tmp_path):
    store, manager = setup(tmp_path / 'cycle.db')
    conversion = request(manager)
    assert conversion.quantity == 10
    result = manager.resolve(conversion.request_id, 'ACKNOWLEDGED', 'CNC', 10,
                             pd.Timestamp('2023-12-05 15:11'))
    assert result.record.lifecycle_state == 'B_OPEN'


def test_ack_quantity_must_be_an_integer_not_a_numeric_lookalike(tmp_path):
    store, manager = setup(tmp_path / 'cycle.db')
    conversion = request(manager)
    before = store.load('p1')
    with pytest.raises(ValueError, match='exact product, quantity and timestamp'):
        manager.resolve(conversion.request_id, 'ACKNOWLEDGED', 'CNC', 10.0,
                        pd.Timestamp('2023-12-05 15:11'))
    assert store.load('p1') == before
