"""Engine-level checkpoint/restore atomicity: a rejected payload must leave every piece of R7 state untouched."""
import copy
import json
import pytest

from .test_r7_demand_allocation import engine, run_bars


def fingerprint(e):
    return json.dumps(e.r7_snapshot(), sort_keys=True), id(e.r7_symbol_controllers), id(e.r7_physical_units), id(e.r7_bay_state), \
        e.r7_decision_count, e.r7_decision_digest, len(e.r7_decisions), json.dumps(dict(e.r7_gate_counts), sort_keys=True), \
        json.dumps(e.r7_normal_exit_state, sort_keys=True)


def source_snapshot(symbols=('INFY', 'TCS'), bars=40):
    a = engine(list(symbols)); run_bars(a, bars)
    return json.loads(json.dumps(a.r7_snapshot()))


MUTATIONS = {
    'schema': lambda s: s.__setitem__('schema', 1),
    'adaptation_on': lambda s: s.__setitem__('adaptation', 'on'),
    'dispatch_scale': lambda s: s.__setitem__('dispatch_scale', 0.5),
    'topology': lambda s: s['topology'].__setitem__('INFY', 'CSTG1_BFSI'),
    'controller_missing': lambda s: s['controllers'].pop('TCS'),
    'controller_nan_integral': lambda s: s['controllers']['INFY']['governor'].__setitem__('integral', float('nan')),
    'controller_bad_pulse': lambda s: s['controllers']['INFY']['pulse'].__setitem__('pulse', 'X'),
    'controller_emergency_age_negative': lambda s: s['controllers']['INFY']['pulse'].__setitem__('emergency_age', -1),
    'physical_missing': lambda s: s['physical'].pop(next(iter(s['physical']))),
    'bay_state_tampered': lambda s: s['bay_state'][next(b for b, v in s['bay_state'].items() if v)].clear(),
    'counts_negative': lambda s: s['counts'].__setitem__('BARS_PROCESSED', -1),
    'counts_float': lambda s: s['counts'].__setitem__('BARS_PROCESSED', 1.5),
    'normal_exit_bad_state': lambda s: s['normal_exit_state'].__setitem__('INFY', {'position_id': 'p', 'last_bar': -3, 'count': 1}),
    'normal_exit_unknown_symbol': lambda s: s['normal_exit_state'].__setitem__('ZZZZ', {'position_id': 'p', 'last_bar': 1, 'count': 1}),
    'decision_count_negative': lambda s: s.__setitem__('decision_count', -1),
    'decision_count_float': lambda s: s.__setitem__('decision_count', 3.0),
    'decision_digest_short': lambda s: s.__setitem__('decision_digest', 'abc'),
    'decision_digest_not_str': lambda s: s.__setitem__('decision_digest', 12345),
    'nan_anywhere': lambda s: s.__setitem__('dispatch_scale', float('nan')),
}


@pytest.mark.parametrize('name', sorted(MUTATIONS))
def test_rejected_restore_leaves_engine_state_untouched(name):
    target = engine(['INFY', 'TCS'])
    run_bars(target, 12)                                        # distinct, non-empty prior state
    before = fingerprint(target)
    bad = copy.deepcopy(source_snapshot())
    MUTATIONS[name](bad)
    with pytest.raises((ValueError, KeyError, TypeError)):
        target.r7_restore(bad)
    assert fingerprint(target) == before


def test_valid_restore_reproduces_source_state_and_future_decisions():
    src = engine(['INFY', 'TCS']); run_bars(src, 40)
    snap = json.loads(json.dumps(src.r7_snapshot()))
    dst = engine(['INFY', 'TCS']); run_bars(dst, 7)
    dst.r7_restore(snap)
    assert json.dumps(dst.r7_snapshot(), sort_keys=True) == json.dumps(snap, sort_keys=True)
    assert dst.r7_decision_count == src.r7_decision_count and dst.r7_decision_digest == src.r7_decision_digest
    from dataclasses import asdict
    from .conftest import make_bar
    for e in (src, dst):
        for i in range(40, 55):
            for s in ('INFY', 'TCS'):
                e._r7_queue(s, i, asdict(make_bar(i)), -0.6, True)
            e.r7_flush()
    strip = lambda e: [(d['symbol'], d['bar_index'], d['pulse'], d['target']) for d in list(e.r7_decisions)[-30:]]
    assert strip(src) == strip(dst)


def test_snapshot_refuses_while_a_handoff_is_incomplete():
    from dataclasses import asdict
    from .conftest import make_bar
    e = engine(['INFY']); run_bars(e, 5)
    e._r7_queue('INFY', 5, asdict(make_bar(5)), 0.9, True)
    with pytest.raises(RuntimeError):
        e.r7_snapshot()
