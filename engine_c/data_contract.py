"""Historical-data contract for Engine C (and the Engine A reference runner).

Frozen NSE 48-symbol 1-minute dataset:

* every file is verified against ``revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json`` (SHA-256, size,
  rows) and the NIFTY/VIX grid feeds against their own manifest before anything is read;
* raw CSV rows are audited BEFORE any sorting: unparseable or duplicated timestamps, out-of-order
  rows, timestamps off the 1-minute grid or outside the 09:15-15:29 IST session, non-finite or
  non-positive prices and inconsistent OHLC refuse the run; missing minutes and sessions that end
  before R5's force-close bar are reported, never filled in;
* timestamps: the dataset carries an explicit +05:30 offset.  Each label is the bar's OPEN time in
  exchange-local time (Asia/Kolkata); a bar is complete, and its values usable, at label + 1 minute;
* the replay window is the last ``bars`` rows of each symbol up to the latest date covered by the
  grid feeds (the same selection the Engine A paper replay and Engine B use).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from engine_c.config import EXCHANGE_TZ

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
DEFAULT_GRID_MANIFEST = ROOT / "local_workspace/records/grid-manifest-local.json"
SESSION_OPEN, SESSION_LAST_BAR = "09:15", "15:29"
SESSION_BARS = 375
FORCE_CLOSE_BAR = "15:25"


class DataContractError(ValueError):
    """The input data violates the Engine C historical-data contract; the run is refused."""


def audit_bars(symbol: str, raw: pd.DataFrame) -> Dict[str, Any]:
    """Audit one symbol's rows in file order.  Returns counts; fatal findings are listed under
    ``fatal`` and the caller refuses the run."""
    out: Dict[str, Any] = {"symbol": symbol, "rows": int(len(raw))}
    stamps = pd.to_datetime(raw["timestamp"], errors="coerce", utc=False)
    unparseable = int(stamps.isna().sum())
    if stamps.dt.tz is None:
        labels = stamps.dt.tz_localize(EXCHANGE_TZ)
        out["timezone"] = "naive, taken as exchange-local IST"
    else:
        labels = stamps.dt.tz_convert(EXCHANGE_TZ)
        out["timezone"] = "explicit offset, converted to Asia/Kolkata"
    valid = labels.dropna()
    duplicated = int(valid.duplicated().sum())
    out_of_order = int((valid.diff() < pd.Timedelta(0)).sum())
    off_grid = int(((valid.dt.second != 0) | (valid.dt.microsecond != 0)).sum())
    hhmm = valid.dt.strftime("%H:%M")
    off_session = int(((hhmm < SESSION_OPEN) | (hhmm > SESSION_LAST_BAR)).sum())
    prices = raw[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    bad_price = int((~np.isfinite(prices.to_numpy()) | (prices.to_numpy() <= 0)).any(axis=1).sum())
    ohlc = int(((prices["high"] < prices[["open", "close", "low"]].max(axis=1))
                | (prices["low"] > prices[["open", "close", "high"]].min(axis=1))).sum())
    volume = pd.to_numeric(raw["volume"], errors="coerce")
    bad_volume = int((~np.isfinite(volume.to_numpy()) | (volume.to_numpy() < 0)).sum())
    days = valid.dt.date
    per_day = valid.groupby(days.to_numpy()).agg(["count", "max"])
    missing = int((SESSION_BARS - per_day["count"]).clip(lower=0).sum())
    short = [str(d) for d, last in zip(per_day.index, per_day["max"]) if last.strftime("%H:%M") < FORCE_CLOSE_BAR]
    out.update(unparseable_timestamps=unparseable, duplicated_timestamps=duplicated, out_of_order_rows=out_of_order,
               off_minute_grid=off_grid, outside_session=off_session, invalid_prices=bad_price,
               inconsistent_ohlc=ohlc, invalid_volume=bad_volume, sessions=int(len(per_day)),
               missing_minutes=missing, sessions_ending_before_force_close=short,
               first=str(valid.min()) if len(valid) else None, last=str(valid.max()) if len(valid) else None)
    out["fatal"] = [k for k in ("unparseable_timestamps", "duplicated_timestamps", "out_of_order_rows",
                                "off_minute_grid", "outside_session", "invalid_prices", "inconsistent_ohlc",
                                "invalid_volume") if out[k]]
    return out


def require_clean(audits: Dict[str, Dict[str, Any]]) -> None:
    bad = {s: {k: a[k] for k in a["fatal"]} for s, a in audits.items() if a["fatal"]}
    if bad:
        raise DataContractError(f"historical-data contract violated (run refused): {bad}")


def normalise(raw: pd.DataFrame) -> pd.DataFrame:
    """Contract-clean rows -> R5 frame (IST labels, float OHLCV), file order preserved."""
    frame = raw[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    stamps = pd.to_datetime(frame["timestamp"])
    frame["timestamp"] = stamps.dt.tz_localize(EXCHANGE_TZ) if stamps.dt.tz is None else stamps.dt.tz_convert(EXCHANGE_TZ)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = frame[column].astype(float)
    return frame.reset_index(drop=True)


def load_real(symbols: List[str], bars: int, data_dir: Optional[str], grid_manifest: Path, warmup: int
              ) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
    from market_data_loader import MarketDataLoader
    from revision2.dataset_manifest import DatasetManifest, verify_manifest
    from scripts.run_r5_paper_replay import load_verified_grid_feeds

    manifest = DatasetManifest.load(str(MANIFEST))
    data_dir = data_dir or os.environ.get("DATA_DIR") or manifest.data_dir
    verification = verify_manifest(manifest, data_dir=data_dir)
    if not verification.valid:
        raise DataContractError(f"frozen dataset failed manifest verification under {data_dir}: {verification.message}")
    if not Path(grid_manifest).is_file():
        raise DataContractError(f"grid manifest not found: {grid_manifest}")
    feeds = load_verified_grid_feeds(grid_manifest)
    end = min(frame["timestamp"].max() for frame in feeds.values()).tz_convert(EXCHANGE_TZ).date()
    universe = sorted(f.symbol for f in manifest.files)
    if symbols == ["ALL"]:
        symbols = universe
    unknown = set(symbols) - set(universe)
    if unknown or len(set(symbols)) != len(symbols):
        raise DataContractError(f"symbols must be distinct members of the verified frozen universe: {sorted(unknown)}")
    loader = MarketDataLoader(data_dir, synthetic_if_missing=False)
    raw, data, audits = {}, {}, {}
    for symbol in sorted(symbols):
        raw[symbol] = pd.read_csv(loader._resolve_csv(symbol))       # file order, before any sort
        audits[symbol] = audit_bars(symbol, raw[symbol])
    require_clean(audits)
    for symbol in sorted(symbols):
        frame = normalise(raw.pop(symbol))
        frame = frame[frame["timestamp"].dt.date <= end].tail(bars).reset_index(drop=True)
        if len(frame) <= warmup:
            raise DataContractError(f"insufficient real data for {symbol}: {len(frame)} bars <= warm-up {warmup}")
        data[symbol] = frame
        window = audit_bars(symbol, frame)
        audits[symbol]["window"] = {k: window[k] for k in ("rows", "first", "last", "sessions", "missing_minutes",
                                                          "sessions_ending_before_force_close")}
    source = {"kind": "FROZEN_NSE_48_1MIN", "manifest_hash": manifest.manifest_hash,
              "data_dir_name": Path(data_dir).name, "grid_manifest": Path(grid_manifest).name,
              "latest_common_grid_date": str(end), "bars_per_symbol": bars, "warmup": warmup}
    return data, feeds["NIFTY_50_15MIN"], feeds["INDIA_VIX_15MIN"], {"source": source, "audit": audits}


def load_synthetic(symbols: List[str], bars: int, warmup: int):
    """The seeded synthetic smoke feed shared with Engine B (scripts/run_engine_b_nautilus.py), so
    that Engines A, B and C replay byte-identical synthetic bars.  P&L is meaningless."""
    from scripts.run_engine_b_nautilus import load_synthetic as shared_synthetic

    data, nifty, vix, label = shared_synthetic(symbols, bars)
    data = {s: normalise(f) for s, f in data.items()}
    audits = {s: audit_bars(s, f) for s, f in data.items()}
    require_clean(audits)
    return data, nifty, vix, {"source": {"kind": "SYNTHETIC_SMOKE", "label": label, "bars_per_symbol": bars,
                                         "warmup": warmup, "seed_base": 11}, "audit": audits}
