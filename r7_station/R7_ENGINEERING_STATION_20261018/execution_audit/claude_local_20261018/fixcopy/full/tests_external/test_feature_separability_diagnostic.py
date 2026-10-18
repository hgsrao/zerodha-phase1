from revision2_external.feature_separability_diagnostic import diagnose


def _row(value, win):
    setup = {name: value for name in ("volume_ratio", "breakout_extension_atr", "vwap_distance_atr", "ema_slope_atr")}
    return {"setup": setup, "target_before_stop": win, "net_pnl_per_share": 1 if win else -1}


def test_median_is_frozen_from_training_data():
    train = [_row(1, False), _row(3, True), _row(5, True)]
    report = diagnose(train, {"train": train, "validation": [_row(2, False), _row(4, True)], "test": [_row(1, False), _row(6, True)]})
    detail = report["features"]["volume_ratio"]
    assert detail["train_median_threshold"] == 3
    assert detail["windows"]["validation"]["low"]["count"] == 1
    assert detail["windows"]["validation"]["high"]["count"] == 1
