import logging

from run_production_p01d_candidate import _capture_shutdown_broker_snapshot


class Broker:
    def __init__(self, positions=None, orders=None):
        self.positions = positions or []
        self.orders = orders or []
        self.write_calls = 0

    def get_positions(self):
        return self.positions

    def get_orders(self):
        return self.orders


def test_clean_shutdown_snapshot_is_read_only_and_passes(caplog):
    broker = Broker()
    with caplog.at_level(logging.INFO):
        assert _capture_shutdown_broker_snapshot(broker) is True
    assert broker.write_calls == 0
    assert "SHUTDOWN_BROKER_SNAPSHOT | active_positions=0 active_orders=0" in caplog.text


def test_shutdown_snapshot_with_position_fails_closed(caplog):
    broker = Broker(positions=[{"quantity": 1}])
    with caplog.at_level(logging.INFO):
        assert _capture_shutdown_broker_snapshot(broker) is False
    assert broker.write_calls == 0
    assert "SHUTDOWN_BROKER_STATE_NOT_CLEAN" in caplog.text


def test_shutdown_snapshot_with_active_order_fails_closed():
    broker = Broker(orders=[{"status": "OPEN"}])
    assert _capture_shutdown_broker_snapshot(broker) is False
    assert broker.write_calls == 0


def test_shutdown_snapshot_malformed_observation_fails_closed():
    broker = Broker(orders=[{"status": ""}])
    assert _capture_shutdown_broker_snapshot(broker) is False
    assert broker.write_calls == 0

