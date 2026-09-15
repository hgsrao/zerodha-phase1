from run_local_replay_staging import assert_safety_lock


def test_local_replay_requires_literal_live_trading_lock():
    assert_safety_lock()

