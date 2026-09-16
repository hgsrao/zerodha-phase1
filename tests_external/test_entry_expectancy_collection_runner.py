from pathlib import Path


def test_entry_expectancy_collection_runner_is_observational_and_canonical() -> None:
    source = Path("scripts/run_external_entry_expectancy_collection.py").read_text(encoding="utf-8")
    assert 'closed_loop_mode="shadow"' in source
    assert 'telemetry_mode="compact"' in source
    assert "canonical defaults" in source
    assert "future, unobserved chronological block" in source
