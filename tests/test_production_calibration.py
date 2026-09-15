from pathlib import Path

from production_calibration_runner import ProductionCalibrationRunner


def test_window_plan_and_config_lock():
    runner = ProductionCalibrationRunner(symbol='ADANIENT')
    windows = runner.build_windows()
    assert list(windows.keys()) == ['warmup', 'train', 'validation', 'test']
    assert windows['test'][0] < windows['test'][1]

    config = runner.default_config()
    assert config['symbols_to_trade'] == ['ADANIENT']
    assert config['data_validation_mode'] == 'strict'

    frozen = runner.freeze_config(config)
    assert Path(frozen).exists()
