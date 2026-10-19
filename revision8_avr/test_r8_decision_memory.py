"""Decision-memory limit: bounded in-process window, complete external journal, constant-size checkpoints."""
from hashlib import sha256
import json
import pytest

from . import r8_engine
from .r8_engine import R8_DECISION_MEMORY_LIMIT
from .test_r8_demand_allocation import engine, run_bars


def test_hard_limit_is_one_thousand():
    assert R8_DECISION_MEMORY_LIMIT == 1000


@pytest.mark.parametrize('requested, expected', [(1, 1), (5, 5), (1000, 1000), (1001, 1000), (5000, 1000), (10**9, 1000)])
def test_window_is_the_smaller_of_request_and_limit(requested, expected):
    assert engine(['INFY'], r8_decision_memory=requested).r8_decisions.maxlen == expected


def test_default_request_is_capped_at_the_limit():
    assert engine(['INFY']).r8_decisions.maxlen == 1000


@pytest.mark.parametrize('bad', [0, -1, 1.5, True, False, '5', None, float('nan')])
def test_invalid_request_is_rejected(bad):
    with pytest.raises(ValueError):
        engine(['INFY'], r8_decision_memory=bad)


def test_window_stays_bounded_while_count_and_digest_keep_advancing(tmp_path):
    log = tmp_path / 'decisions.jsonl'
    e = engine(['INFY'], r8_decision_log_path=str(log))
    run_bars(e, 1150)
    e.r8_close_decision_log()
    assert len(e.r8_decisions) == 1000 and e.r8_decision_count == 1150
    lines = log.read_text().splitlines()
    assert len(lines) == 1150                                                  # the journal is complete
    digest = '0' * 64
    for line in lines:
        digest = sha256((digest + line).encode()).hexdigest()
    assert digest == e.r8_decision_digest                                      # and verifiable against the engine chain
    assert [json.loads(l)['bar_index'] for l in lines[-1000:]] == [d['bar_index'] for d in e.r8_decisions]


def test_checkpoint_size_does_not_grow_with_replay_length():
    a = engine(['INFY']); run_bars(a, 200)
    b = engine(['INFY']); run_bars(b, 1200)
    sa, sb = len(json.dumps(a.r8_snapshot())), len(json.dumps(b.r8_snapshot()))
    assert sb < sa * 1.05


def test_checkpoint_carries_count_and_chain_not_history():
    e = engine(['INFY']); run_bars(e, 50)
    snap = e.r8_snapshot()
    assert snap['decision_count'] == 50 and len(snap['decision_digest']) == 64
    assert 'decisions' not in snap and 'r8_decisions' not in snap


def test_restore_clears_the_window_but_keeps_the_chain():
    a = engine(['INFY']); run_bars(a, 30)
    snap = json.loads(json.dumps(a.r8_snapshot()))
    b = engine(['INFY']); run_bars(b, 5)
    b.r8_restore(snap)
    assert len(b.r8_decisions) == 0 and b.r8_decision_count == 30 and b.r8_decision_digest == a.r8_decision_digest
