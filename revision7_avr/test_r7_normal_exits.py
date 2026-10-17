"""Controlled pulse fixtures through the real native paper broker and exit ledger."""
from dataclasses import replace, asdict
import pytest
from .r7_engine import Revision7Engine
from .r7_state import Context
from .conftest import make_bar
from tests_external.test_audit_remediation import open_position, signal


def opened(config, side):
    engine = Revision7Engine(['INFY'], r7_configs=[config])
    trade = open_position(engine, side=side)
    trade['trade_id'] = 'r7-exit-test'
    # Neutralize the native discretionary advisor only. Stops, hard limits,
    # broker fills and close bookkeeping execute their actual implementations.
    engine._governor_position_step = lambda *a, **kw: {'action': 'HOLD'}
    return engine, trade


def pulse_fixture(e, i, pulse, target=0.4):
    ctrl = e.r7_symbol_controllers['INFY']
    b = make_bar(i, close=100)
    if ctrl.last_result is None:
        ctrl.step(b, Context(0, 0.5, 0.5))
    ctrl.last_result = replace(ctrl.last_result, bar_index=i, timestamp=b.timestamp,
        pulse=pulse, target=target, signal=0.4 if pulse == 'BUY' else -0.4 if pulse == 'SELL' else 0,
        eligible=True)
    e._current_bar_idx = i
    return b, {k: v for k, v in asdict(b).items() if k != 'index'}


def tick(e, i, pulse, held=5, *, low=99.5, high=100.5, opening=100, last=False, target=0.4):
    b, row = pulse_fixture(e, i, pulse, target)
    row.update(open=opening, low=low, high=high)
    e._last_close['INFY'] = row['close']
    e._maybe_exit('INFY', b.timestamp, row, signal(direction=1 if pulse == 'BUY' else -1),
                  held, last, 0.8, {'direction': 1, 'confidence': 0.8})
    return b, row


@pytest.mark.parametrize('side,opposite', [('BUY', 'SELL'), ('SELL', 'BUY')])
def test_normal_reversal_exits_next_open_with_native_fill(config, side, opposite):
    e, trade = opened(config, side)
    tick(e, 30, opposite)
    assert 'controller_exit_pending' not in trade
    tick(e, 31, opposite)
    assert trade['controller_exit_pending']['exit_reason'] == 'r7_coordinated_exit'
    assert len(e.completed_trades) == 0
    # Repeated evaluation of the arming bar cannot execute the exit at its open.
    tick(e, 31, opposite)
    assert len(e.completed_trades) == 0
    tick(e, 32, opposite, opening=100.2)
    assert 'INFY' not in e.open_trades
    assert e._broker_position('INFY')['quantity'] == 0
    assert len(e.completed_trades) == 1
    assert e.completed_trades[0]['reason'] == 'r7_coordinated_exit'
    assert e.completed_trades[0]['exit_timestamp'] == make_bar(32).timestamp
    assert 'INFY' not in e.r7_normal_exit_state


def test_pulse_hold_does_not_exit_and_protection_still_does(config):
    e, trade = opened(config, 'BUY')
    for i in range(30, 35):
        tick(e, i, 'BUY')
    assert 'controller_exit_pending' not in trade
    tick(e, 35, 'BUY', held=0, low=90)
    assert e.completed_trades[-1]['reason'] == 'stop'
    assert e._broker_position('INFY')['quantity'] == 0


def test_pending_normal_exit_preserves_stop_gap_precedence(config):
    e, trade = opened(config, 'BUY')
    tick(e, 30, 'SELL')
    tick(e, 31, 'SELL')
    tick(e, 32, 'SELL', opening=90, low=89)
    assert e.completed_trades[-1]['reason'] == 'stop_gap'


def test_minimum_hold_and_neutral_release(config):
    e, trade = opened(config, 'BUY')
    tick(e, 30, 'HOLD', held=0, target=0)
    tick(e, 31, 'HOLD', held=1, target=0)
    assert 'controller_exit_pending' not in trade
    tick(e, 32, 'HOLD', held=2, target=0)
    assert trade['controller_exit_pending']['reason'] == 'R7_NEUTRAL_RELEASE'


@pytest.mark.parametrize('hard', ['max_hold', 'mis_session_close'])
def test_hard_limits_outrank_same_direction_pulse(config, hard):
    e, _ = opened(config, 'BUY')
    tick(e, 30, 'BUY', held=60 if hard == 'max_hold' else 0, last=hard == 'mis_session_close')
    assert e.completed_trades[-1]['reason'] == hard


PROTECTIVE_NATIVE_EXITS = [
    'governor_exit:FSRN_SUSTAINED_DETERIORATION', 'governor_exit:FSR_BELOW_EXIT:FSRN',
    'governor_exit:FSR_BELOW_EXIT:FSRT', 'governor_exit:FSR_BELOW_EXIT:FSRD',
    'regime_stressed_exit', 'saturation_exit_pa', 'saturation_exit_studies',
    'governor_exit:GOVERNOR_PATH_ERROR', 'governor_exit:EXHAUST_SPREAD_TRIP',
    'controller_path_exit', 'final_controller_exit:STUDIES_PID_SUSTAINED_LOW_CONFIDENCE',
    'final_controller_exit:BEHIND_REFERENCE_PATH', 'some_unknown_future_reason']


@pytest.mark.parametrize('reason', PROTECTIVE_NATIVE_EXITS)
def test_protective_and_unknown_native_exits_execute_inside_r7_scope(config, reason):
    e, trade = opened(config, 'BUY')
    e._r7_exit_scope = True
    e._execute_exit('INFY', make_bar(30).timestamp, trade, 100, reason)
    assert 'INFY' not in e.open_trades
    assert e.completed_trades[-1]['reason'] == reason
    assert e.r7_gate_counts['NATIVE_NORMAL_EXIT_ADVISORY'] == 0


def test_only_governor_z_exit_is_discretionary(config):
    e, trade = opened(config, 'BUY')
    e._r7_exit_scope = True
    e._execute_exit('INFY', make_bar(30).timestamp, trade, 100, 'governor_exit:GOVERNOR_Z_EXIT')
    assert 'INFY' in e.open_trades
    assert e.r7_gate_counts['NATIVE_NORMAL_EXIT_ADVISORY'] == 1
    assert type(e).R7_DISCRETIONARY_NATIVE_EXITS == frozenset({'governor_exit:GOVERNOR_Z_EXIT'})


@pytest.mark.parametrize('pulse', ['BUY', 'HOLD'])
def test_protective_pending_exit_not_withdrawn_by_pulse_hold(config, pulse):
    # A native protective exit armed for the next open must survive R7 evaluation,
    # whatever the pulse, hold or cooldown state.
    e, trade = opened(config, 'BUY')
    trade['controller_exit_pending'] = {'armed_timestamp': make_bar(29).timestamp, 'reason': 'behind_reference_path',
                                        'exit_reason': 'final_controller_exit:BEHIND_REFERENCE_PATH',
                                        'event': 'NATIVE'}
    e._r7_remove_normal_pending(trade)
    assert trade['controller_exit_pending']['exit_reason'] == 'final_controller_exit:BEHIND_REFERENCE_PATH'
    trade['controller_exit_pending']['exit_reason'] = 'governor_exit:GOVERNOR_Z_EXIT'
    e._r7_remove_normal_pending(trade)
    assert 'controller_exit_pending' not in trade
