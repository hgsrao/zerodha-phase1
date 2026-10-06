"""In-process feedback rollback/retry; no claim of live crash atomicity."""
from copy import deepcopy
import pytest
from revision5.topology import bay_for_symbol
from test_r5_d01_orchestrator_run_integration import build_orchestrator


@pytest.mark.parametrize('branch', ['merit', 'bay'])
def test_partial_feedback_failure_rolls_back_both_owners_and_retry_updates_once(tmp_path, monkeypatch, branch):
    orch, runtime, store, observed, titan, warmup = build_orchestrator(tmp_path)
    bay_id = bay_for_symbol('TITAN')
    merit = orch.plant_control.dispatch_controller.merit_source
    bay = orch.real_plant_dcs.bays[bay_id]
    merit_before = deepcopy(merit.trade_history_r)
    governor_before = bay.governor.snapshot()
    bay_before = (bay.consecutive_stops, bay.cooldown_until_bar_exclusive, bay.tripped_offline)
    owner, method = (merit, 'register_trade') if branch == 'merit' else (bay, 'register_outcome')
    real = getattr(owner, method)

    def fail_after_mutation(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError('injected feedback failure')

    monkeypatch.setattr(owner, method, fail_after_mutation)
    kwargs = dict(symbol='TITAN', trade={'trade_id': 'feedback-fault'}, bay_id=bay_id,
                  realized_r=-1.0, reason='STOP')
    with pytest.raises(RuntimeError, match='injected feedback failure'):
        orch._register_realized_r_close_feedback(**kwargs)
    assert merit.trade_history_r == merit_before
    assert bay.governor.snapshot() == governor_before
    assert (bay.consecutive_stops, bay.cooldown_until_bar_exclusive, bay.tripped_offline) == bay_before
    assert 'trade_id:feedback-fault' not in orch._close_feedback_receipts
    monkeypatch.setattr(owner, method, real)
    orch._register_realized_r_close_feedback(**kwargs)
    orch._register_realized_r_close_feedback(**kwargs)
    assert merit.trade_history_r[bay_id] == [-1.0]
    assert bay.governor.history_r == [-1.0]
    assert bay.consecutive_stops == 1
    assert orch._close_feedback_receipts['trade_id:feedback-fault'] == 'DONE'
