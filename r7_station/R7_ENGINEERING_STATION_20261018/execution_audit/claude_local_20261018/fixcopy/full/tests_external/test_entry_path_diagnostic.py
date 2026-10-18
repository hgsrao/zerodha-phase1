from revision2_external.entry_path_diagnostic import diagnose
from revision2_external.study_entry_shadow import StudyEntryShadowLedger


def test_path_diagnostic_classifies_safe_excursions_without_terminal_bar_assumptions():
    rows = [{"safe_mfe_r": .10, "net_pnl_per_share": -2}, {"safe_mfe_r": .50, "net_pnl_per_share": -1}, {"safe_mfe_r": 1.10, "net_pnl_per_share": 3}, {"safe_mfe_r": None, "net_pnl_per_share": -9}]
    report = diagnose(rows)
    assert report["safe_excursion_rows"] == 3
    assert report["archetypes"]["immediate_rejection"]["count"] == 1
    assert report["archetypes"]["stalled"]["count"] == 1
    assert report["archetypes"]["near_target_reversal"]["count"] == 1


def test_ledger_records_only_completed_non_terminal_bars_for_excursions():
    ledger = StudyEntryShadowLedger(max_hold_bars=3)
    ledger.schedule(symbol="X", index=0, side="BUY", setup_extreme=99, atr=1, observation={"timestamp": "t0"})
    ledger.advance("X", 1, "t1", {"open": 100., "high": 100., "low": 100., "close": 100.})
    ledger.advance("X", 2, "t2", {"open": 100., "high": 100.5, "low": 99.8, "close": 100.})
    ledger.advance("X", 3, "t3", {"open": 100., "high": 100.1, "low": 98., "close": 99.})
    assert ledger.resolved[0]["safe_excursion_bars"] == 1
    assert ledger.resolved[0]["safe_mae_r"] > -1.0
