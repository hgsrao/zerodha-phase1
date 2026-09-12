import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from revision2_external.causal_mtf_aligner import CausalMTFAligner


def _minute_frame() -> pd.DataFrame:
    index = pd.date_range("2026-01-02 09:15", "2026-01-02 10:16", freq="1min")
    close = np.arange(len(index), dtype=float)
    return pd.DataFrame({"open": close, "high": close + 0.2, "low": close - 0.2, "close": close, "volume": 1.0}, index=index)


def test_15min_alignment_excludes_same_timestamp_completion_bar():
    frame = _minute_frame()
    result = CausalMTFAligner("15min").process(frame)
    available = result["htf_15min_available_at"]
    day = "2026-01-02 "
    assert pd.isna(available.loc[day + "09:29"])
    assert pd.isna(available.loc[day + "09:30"])
    assert available.loc[day + "09:31"] == pd.Timestamp(day + "09:30")
    assert available.loc[day + "09:59"] == pd.Timestamp(day + "09:45")
    assert available.loc[day + "10:00"] == pd.Timestamp(day + "09:45")
    assert available.loc[day + "10:14"] == pd.Timestamp(day + "10:00")
    assert available.loc[day + "10:15"] == pd.Timestamp(day + "10:00")
    assert available.loc[day + "10:16"] == pd.Timestamp(day + "10:15")


def test_mtf_features_are_calculated_from_completed_bars_then_joined():
    script = Path("scripts/extract_mtf_causal_features_2026.py")
    spec = importlib.util.spec_from_file_location("mtf_extract", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    extractor = module.MTFFeatureExtractor("revision2_external/research_dataset_registry.json")
    result = extractor.extract_features(_minute_frame())
    assert {"5m_trend", "15m_trend", "htf_5min_available_at", "htf_15min_available_at"} <= set(result.columns)
    assert pd.isna(result.loc["2026-01-02 09:30", "htf_15min_available_at"])
