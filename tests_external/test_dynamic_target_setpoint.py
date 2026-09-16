from revision2_external.dynamic_target_setpoint import FrozenTargetSetpointProvider


def _trade(mfe_r, bars, net=1.0):
    return {"mfe_r": mfe_r, "bars_held": bars, "net_pnl": net}


def test_provider_fails_closed_when_pre_cutoff_cost_positive_paths_are_sparse():
    provider = FrozenTargetSetpointProvider.fit([_trade(1.0, 8)] * 3, seed_start="2023-07-03", seed_end_exclusive="2023-09-01")
    proposal = provider.propose(entry_price=100.0, stop_price=90.0, target_price=115.0, maximum_hold_bars=60)
    assert proposal["available"] is False
    assert proposal["proposed_target_r"] == 1.5
    assert proposal["proposed_maximum_hold_bars"] == 60


def test_provider_uses_only_cost_positive_completed_path_samples_and_never_expands_baseline():
    rows = [_trade(1.0, 8) for _ in range(20)] + [_trade(4.0, 60, net=-1.0)]
    provider = FrozenTargetSetpointProvider.fit(rows, seed_start="2023-07-03", seed_end_exclusive="2023-09-01")
    proposal = provider.propose(entry_price=100.0, stop_price=90.0, target_price=115.0, maximum_hold_bars=60)
    assert proposal["available"] is True
    assert proposal["proposed_target_r"] == 1.0
    assert proposal["proposed_maximum_hold_bars"] == 8
