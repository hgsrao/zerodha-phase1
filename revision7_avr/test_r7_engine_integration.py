from collections import Counter
from dataclasses import replace, asdict
from types import SimpleNamespace
import json
import pandas as pd
import pytest
from .r7_engine import R7EngineMixin, Revision7Engine
from .r7_state import ControlConfig, Context
from .conftest import make_bar
from revision5.topology import SYMBOL_TO_BAY, BAY_IDS


class NativeStudies:
    def __init__(self):
        self.calls = 0

    def evaluate(self, symbol, bars):
        self.calls += 1
        return {'direction': 1, 'confidence': 0.8}


class NativeSeams:
    def __init__(self, symbols):
        self.symbols = symbols
        self.chart_studies = NativeStudies()
        self.dynamic_profile_mode = 'off'
        self._current_bar_idx = 0
        self.quantity = 0
        self.native_qty = 7
        self.native_calls = 0

    def _observe_conviction(self, *args):
        pass

    def _equity(self):
        return 100000

    def _broker_position(self, symbol):
        return {'quantity': self.quantity}

    # Minimal stand-ins for the native gross-exposure interfaces used by R7 allocation.
    safety_contract = SimpleNamespace(values={'max_gross_exposure_fraction': 1.0})
    _last_close = {}

    def _gross_exposure_notional(self):
        return abs(self.quantity) * 100.0

    def _governor_grid_return(self, ts):
        return 0.006

    def _record_controller_event(self, *args):
        pass

    def _plant_entry_gate(self, *args):
        self.native_calls += 1
        return self.native_qty

    def _admit_entry_candidate(self, pending, timestamp):
        self.native_calls += 1
        return 'native-candidate-path'


class TestEngine(R7EngineMixin, NativeSeams):
    __test__ = False


def test_handoff_studies_once_signed_exposure_and_native_cap(config):
    e = TestEngine(['INFY'], r7_configs=[config])
    for i in range(30):
        bar = make_bar(i)
        frame = pd.DataFrame([asdict(bar)], index=[i])
        result = e.chart_studies.evaluate('INFY', frame)
        e._observe_conviction('INFY', None, result)
    assert e.chart_studies.calls == 30
    assert e.r7_gate_counts['BARS_PROCESSED'] == 30
    e.quantity = -10
    assert e._r7_actual('INFY', 100) == -0.5
    e.quantity = 100
    assert e._r7_actual('INFY', 100) == 5  # Do not hide over-budget positions.
    c = e.r7_symbol_controllers['INFY']
    assert c.last_result.pulse == 'BUY'
    e._current_bar_idx = 29
    args = ('INFY', c.last_result.timestamp, 'BUY', 5, 100, {}, 'x', {})
    assert e._plant_entry_gate(*args) == 5
    assert e.native_calls == 1
    assert e.r7_symbol_controllers['INFY'].steps == 30


def test_engine_atomic_checkpoint_includes_bays_and_physical(config):
    a = TestEngine(['INFY'], r7_configs=[config])
    for i in range(10):
        b = make_bar(i)
        a._r7_step('INFY', i, asdict(b), 0.8, True)
    state = json.loads(json.dumps(a.r7_snapshot()))
    b = TestEngine(['INFY'], r7_configs=[config])
    b.r7_restore(state)
    assert b.r7_snapshot() == a.r7_snapshot()
    state['bay_state'][config.bay_id]['INFY']['actual'] = 999
    old = b.r7_snapshot()
    with pytest.raises(ValueError):
        b.r7_restore(state)
    assert b.r7_snapshot() == old


def test_all_five_bays_and_unmapped_symbol():
    configs = [ControlConfig(s, b, 1 / 48) for s, b in SYMBOL_TO_BAY.items()]
    e = TestEngine(list(SYMBOL_TO_BAY), r7_configs=configs)
    assert set(e.r7_bay_state) == set(BAY_IDS)
    for s in e.symbols:
        e._r7_step(s, 0, asdict(make_bar(0)), 0.8, True)
    assert e.r7_gate_counts['BARS_PROCESSED'] == 48
    with pytest.raises(ValueError):
        TestEngine(['UNKNOWN'], r7_configs=[ControlConfig('UNKNOWN', BAY_IDS[0], 0.02)])


def test_actual_native_engine_warmup_and_completed_bars(config):
    # This executes the real shared native engine with its existing offline paper broker.
    # It is an integration smoke, not a historical-market or harness claim.
    e = Revision7Engine(['INFY'], r7_configs=[config])
    bars = pd.DataFrame([{k: v for k, v in asdict(make_bar(i)).items() if k != 'index'} for i in range(90)])
    report = e.run({'INFY': bars}, warmup=60)
    assert e.chart_studies.evaluations['INFY'] == 89
    assert e.r7_gate_counts['BARS_PROCESSED'] == 89
    assert e.r7_symbol_controllers['INFY'].steps == 89
    assert report['operating_mode'] == 'paper'


def test_native_short_candidate_and_explicit_short_policy(config):
    e = TestEngine(['INFY'], r7_configs=[config])
    e._governor_grid_return = lambda ts: -0.006
    for i in range(30):
        e._r7_step('INFY', i, asdict(make_bar(i)), -0.9, True)
    c = e.r7_symbol_controllers['INFY']
    assert c.last_result.pulse == 'SELL'
    e._current_bar_idx = 29
    args = ('INFY', c.last_result.timestamp, 'SELL', 10, 100, {}, 's', {})
    assert e._plant_entry_gate(*args) == 7
    e.r7_allow_native_shorts = False
    assert e._plant_entry_gate(*args) == 0
    assert e.native_calls == 1


def test_real_studies_called_once_even_when_no_candidates(config):
    e = Revision7Engine(['INFY'], r7_configs=[config])
    bars = pd.DataFrame([{k: v for k, v in asdict(make_bar(i)).items() if k != 'index'} for i in range(100)])
    e.run({'INFY': bars}, warmup=60)
    assert e.chart_studies.evaluations['INFY'] == 99
    assert e.r7_gate_counts['BARS_PROCESSED'] == 99


def test_one_day_native_engine_reference_allocation_and_context(config):
    # Explicit synthetic Nifty context fixture; this is not historical data.
    e = Revision7Engine(['INFY'], r7_configs=[config])
    e._governor_grid_return = lambda ts: 0.002
    bars = pd.DataFrame([{k: v for k, v in asdict(make_bar(i)).items() if k != 'index'} for i in range(390)])
    report = e.run({'INFY': bars}, warmup=60)
    assert e.r7_gate_counts['BARS_PROCESSED'] == 389
    assert e.chart_studies.evaluations['INFY'] == 389
    eligible = [d for d in e.r7_decisions if d['eligible']]
    assert eligible
    # Market price/volume no longer drives a 1/R amplifier; with ample headroom the
    # approved target equals the strategy reference.
    assert all(d['target'] == pytest.approx(d['reference']) for d in eligible)
    assert all(d['allocation']['utilization'] >= 0 for d in eligible)
    assert report['operating_mode'] == 'paper'


def test_core_without_native_plant_cannot_bypass_r7_candidate_admission(config):
    e = TestEngine(['INFY'], r7_configs=[config])
    disposed = []
    e.entry_candidate_observations = SimpleNamespace(dispose=lambda *a: disposed.append(a))
    p = {'symbol': 'INFY', 'plan': SimpleNamespace(side='BUY'), 'bar_idx': 0, 'candidate_id': 'candidate'}
    assert e._admit_entry_candidate(p, make_bar(0).timestamp) is None
    assert e.native_calls == 0
    assert disposed[-1][-1] == 'r7_pulse_rejected'
    for i in range(30):
        e._r7_step('INFY', i, asdict(make_bar(i)), 0.8, True)
    p['bar_idx'] = 29
    assert e._admit_entry_candidate(p, make_bar(29).timestamp) == 'native-candidate-path'
    assert e.native_calls == 1


def test_normal_exit_confirmation_checkpoint_is_retained(config):
    a = TestEngine(['INFY'], r7_configs=[config])
    for i in range(10):
        a._r7_step('INFY', i, asdict(make_bar(i)), 0.8, True)
    a.r7_normal_exit_state['INFY'] = {'position_id': 'position-1', 'last_bar': 9, 'count': 1}
    snapshot = json.loads(json.dumps(a.r7_snapshot()))
    b = TestEngine(['INFY'], r7_configs=[config])
    b.r7_restore(snapshot)
    assert b.r7_normal_exit_state == a.r7_normal_exit_state
