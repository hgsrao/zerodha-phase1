import numpy as np
import pandas as pd

from revision2_external.grid_context import SealedGridContextProvider
from revision3.macro_grid_synchronizer import SyncResult


class _CaptureSynchronizer:
    def __init__(self):
        self.calls = []

    def check_synchronization(self, plant_close, grid_close, current_vix, trade_direction):
        self.calls.append((plant_close.copy(), grid_close.copy(), current_vix, trade_direction))
        return SyncResult(True, 7.5, "Grid synchronized")


def _context_frames(periods=70):
    timestamps = pd.date_range("2024-01-02 09:15", periods=periods, freq="15min", tz="Asia/Kolkata")
    nifty = pd.DataFrame({"timestamp": timestamps, "close": 20_000 + np.arange(periods)})
    vix = pd.DataFrame({"date": timestamps, "close": 15.0 + np.arange(periods) * 0.01})
    stock = pd.DataFrame({"timestamp": timestamps, "close": 1_000 + np.arange(periods) * 2.0})
    return nifty, vix, stock


def test_uses_only_context_strictly_before_the_decision_timestamp():
    nifty, vix, stock = _context_frames()
    capture = _CaptureSynchronizer()
    provider = SealedGridContextProvider(nifty, vix, synchronizer=capture)
    decision = pd.Timestamp("2024-01-03 02:30:00+00:00")  # 08:00 IST; prior source is 17:30 IST previous day
    observation = provider.observe("INFY", stock, decision, 1)

    assert not observation.available
    assert observation.reason == "GRID_CONTEXT_STALE"
    assert not capture.calls


def test_uses_real_symbol_prices_not_nifty_as_the_plant_series():
    nifty, vix, stock = _context_frames()
    capture = _CaptureSynchronizer()
    provider = SealedGridContextProvider(nifty, vix, synchronizer=capture)
    decision = pd.Timestamp("2024-01-03 02:31:00+05:30")
    observation = provider.observe("INFY", stock, decision, 1)

    assert observation.available
    plant, grid, _, direction = capture.calls[0]
    assert direction == 1
    assert not np.array_equal(plant, grid)
    assert observation.source_timestamp < observation.decision_timestamp


def test_rejects_mismatched_nifty_and_vix_context_timestamps():
    nifty, vix, stock = _context_frames()
    vix = vix.iloc[:-1].copy()
    provider = SealedGridContextProvider(nifty, vix, synchronizer=_CaptureSynchronizer())
    decision = pd.Timestamp("2024-01-03 02:31:00+05:30")

    observation = provider.observe("INFY", stock, decision, -1)

    assert not observation.available
    assert observation.reason == "GRID_CONTEXT_TIMESTAMP_MISMATCH"


def test_requires_warmup_instead_of_fabricating_a_grid_state():
    nifty, vix, stock = _context_frames(periods=20)
    provider = SealedGridContextProvider(nifty, vix, synchronizer=_CaptureSynchronizer())
    decision = pd.Timestamp("2024-01-02 14:16:00+05:30")

    observation = provider.observe("INFY", stock, decision, 1)

    assert not observation.available
    assert observation.reason == "GRID_CONTEXT_WARMUP_INSUFFICIENT"
