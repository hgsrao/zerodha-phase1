import importlib.util
from pathlib import Path


def test_horizon_runner_declares_a_single_parameter_override():
    script = Path("scripts/run_external_horizon_paper_48symbol.py")
    spec = importlib.util.spec_from_file_location("horizon_runner", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert "max_hold_bars" in script.read_text(encoding="utf-8")
    assert 'closed_loop_mode="shadow"' in script.read_text(encoding="utf-8")
    assert 'telemetry_mode="compact"' in script.read_text(encoding="utf-8")
