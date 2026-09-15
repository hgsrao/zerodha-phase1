from nontrading_entry_soak import run_campaign


def test_p03be_deterministic_10000_cycle_nontrading_soak():
    counts = run_campaign(cycles=10_000, seed=34035)
    assert sum(counts.values()) == 10_000
    assert all(counts[mode] > 0 for mode in counts)

