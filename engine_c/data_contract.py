"""Historical-data contract for Engine C (and the Engine A reference runner).

Frozen NSE 48-symbol 1-minute dataset:

* every file is verified against ``revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json`` (SHA-256, size,
  rows) and the NIFTY/VIX grid feeds against their own manifest before anything is read;
* raw CSV rows are audited BEFORE any sorting: unparseable or duplicated timestamps, out-of-order
  rows, timestamps off the 1-minute grid or outside the 09:15-15:29 IST session, non-finite or
  non-positive prices and inconsistent OHLC refuse the run; missing minutes and sessions that end
  before R5's force-close bar are reported, never filled in;
* the only exception to the 09:15-15:29 window is ``SPECIAL_SESSIONS``: an explicit, sourced list of
  exchange-scheduled special sessions (the Diwali Muhurat evening sessions), each valid on its own
  date and minutes only.  Any other out-of-hours row stays fatal;
* timestamps: the dataset carries an explicit +05:30 offset.  Each label is the bar's OPEN time in
  exchange-local time (Asia/Kolkata); a bar is complete, and its values usable, at label + 1 minute;
* the replay window is the last ``bars`` rows of each symbol up to the latest date covered by the
  grid feeds (the same selection the Engine A paper replay and Engine B use).
"""
from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from datetime import date
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


@dataclass(frozen=True)
class SpecialSession:
    """One exchange-scheduled special session.  ``first_bar``..``last_bar`` are the permitted
    1-minute OPEN-time labels (IST) of the NSE capital-market normal-market segment; pre-open,
    block-deal and closing-session windows are not part of it.  ``regular_session`` is False when
    the ordinary 09:15-15:30 session did not run that date, so ordinary-hours rows stay fatal."""
    day: date
    first_bar: str
    last_bar: str
    kind: str
    regular_session: bool
    source: str

    @property
    def bars(self) -> int:
        start = pd.Timestamp(f"{self.day} {self.first_bar}")
        return int((pd.Timestamp(f"{self.day} {self.last_bar}") - start) / pd.Timedelta(minutes=1)) + 1


SPECIAL_SESSIONS: Dict[date, SpecialSession] = {s.day: s for s in (
    SpecialSession(date(2023, 11, 12), "18:15", "19:14", "DIWALI_MUHURAT", False,
                   "NSE circular NSE/CMTR/59124 (27 Oct 2023): normal market 18:15-19:15 IST, Sunday "
                   "12 Nov 2023; https://archives.nseindia.com/content/circulars/CMTR59124.pdf"),
    SpecialSession(date(2024, 11, 1), "18:00", "18:59", "DIWALI_MUHURAT", False,
                   "NSE Muhurat trading notice for Friday 1 Nov 2024 (regular session a trading "
                   "holiday): normal market 18:00-19:00 IST, as reported by Business Standard, Fyers "
                   "and Angel One; NSE Clearing circular NCL/CMPT/64632 gives the same 18:00-19:00 "
                   "window. The NSE CM circular itself was not retrieved."),
)}


def session_mask(labels: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """(regular, special) boolean masks for IST labels.  A row is in-session only if it falls
    09:15-15:29 on a date with a regular session, or inside the special window of its own date."""
    hhmm = labels.dt.strftime("%H:%M")
    days = labels.dt.date
    special_day = days.map(lambda d: d in SPECIAL_SESSIONS).astype(bool)
    no_regular = days.map(lambda d: d in SPECIAL_SESSIONS and not SPECIAL_SESSIONS[d].regular_session).astype(bool)
    regular = (hhmm >= SESSION_OPEN) & (hhmm <= SESSION_LAST_BAR) & ~no_regular
    first = days.map(lambda d: SPECIAL_SESSIONS[d].first_bar if d in SPECIAL_SESSIONS else "")
    last = days.map(lambda d: SPECIAL_SESSIONS[d].last_bar if d in SPECIAL_SESSIONS else "")
    special = special_day & (hhmm >= first) & (hhmm <= last)
    return regular, special


class DataContractError(ValueError):
    """The input data violates the Engine C historical-data contract; the run is refused."""


def audit_bars(symbol: str, raw: pd.DataFrame) -> Dict[str, Any]:
    """Audit one symbol's rows in file order.  Returns counts; fatal findings are listed under
    ``fatal`` and the caller refuses the run."""
    out: Dict[str, Any] = {"symbol": symbol, "rows": int(len(raw))}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)         # mixed offsets are handled below
        try:
            stamps = pd.to_datetime(raw["timestamp"], errors="coerce", utc=False)
        except (TypeError, ValueError):                        # later pandas refuse mixed offsets
            stamps = pd.Series(raw["timestamp"], dtype=object)
    mixed_offsets = 0
    if not pd.api.types.is_datetime64_any_dtype(stamps):
        # Rows carry different UTC offsets: the file has no single exchange-local convention.
        each = raw["timestamp"].map(lambda v: pd.to_datetime(v, errors="coerce"))
        offsets = each.dropna().map(lambda t: t.utcoffset())
        mixed_offsets = int((offsets != offsets.mode().iloc[0]).sum())
        stamps = pd.to_datetime(raw["timestamp"], errors="coerce", utc=True)
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
    regular, special = session_mask(valid)
    off_session = int((~(regular | special)).sum())
    prices = raw[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    bad_price = int((~np.isfinite(prices.to_numpy()) | (prices.to_numpy() <= 0)).any(axis=1).sum())
    ohlc = int(((prices["high"] < prices[["open", "close", "low"]].max(axis=1))
                | (prices["low"] > prices[["open", "close", "high"]].min(axis=1))).sum())
    volume = pd.to_numeric(raw["volume"], errors="coerce")
    bad_volume = int((~np.isfinite(volume.to_numpy()) | (volume.to_numpy() < 0)).sum())
    days = valid.dt.date
    per_day = valid.groupby(days.to_numpy()).agg(["count", "max"])
    expected = pd.Series([SPECIAL_SESSIONS[d].bars if d in SPECIAL_SESSIONS and not SPECIAL_SESSIONS[d].regular_session
                          else SESSION_BARS for d in per_day.index], index=per_day.index)
    missing = int((expected - per_day["count"]).clip(lower=0).sum())
    short = [str(d) for d, last in zip(per_day.index, per_day["max"])
             if d not in SPECIAL_SESSIONS and last.strftime("%H:%M") < FORCE_CLOSE_BAR]
    special_days = valid[special].dt.date
    special_sessions = {str(d): {"kind": SPECIAL_SESSIONS[d].kind, "rows": int(n), "expected": SPECIAL_SESSIONS[d].bars,
                                 "window": f"{SPECIAL_SESSIONS[d].first_bar}-{SPECIAL_SESSIONS[d].last_bar}"}
                        for d, n in special_days.value_counts().sort_index().items()}
    out.update(unparseable_timestamps=unparseable, mixed_utc_offsets=mixed_offsets, duplicated_timestamps=duplicated, out_of_order_rows=out_of_order,
               off_minute_grid=off_grid, outside_session=off_session, invalid_prices=bad_price,
               inconsistent_ohlc=ohlc, invalid_volume=bad_volume, sessions=int(len(per_day)),
               missing_minutes=missing, sessions_ending_before_force_close=short,
               special_session_rows=int(special.sum()), special_sessions=special_sessions,
               first=str(valid.min()) if len(valid) else None, last=str(valid.max()) if len(valid) else None)
    out["fatal"] = [k for k in ("unparseable_timestamps", "mixed_utc_offsets", "duplicated_timestamps", "out_of_order_rows",
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


def audit_and_select(raw: Dict[str, pd.DataFrame], end: date, bars: int, warmup: int
                     ) -> Tuple[Dict[str, pd.DataFrame], Dict[str, Dict[str, Any]]]:
    """Audit every raw row of every file first (the whole file, not just the replay window) and
    refuse the run on any fatal finding; only then select each symbol's replay window: the last
    ``bars`` rows dated on or before ``end``.  Consumes ``raw``."""
    audits = {symbol: audit_bars(symbol, frame) for symbol, frame in sorted(raw.items())}
    require_clean(audits)
    data = {}
    for symbol in sorted(raw):
        frame = normalise(raw.pop(symbol))
        frame = frame[frame["timestamp"].dt.date <= end].tail(bars).reset_index(drop=True)
        if len(frame) <= warmup:
            raise DataContractError(f"insufficient real data for {symbol}: {len(frame)} bars <= warm-up {warmup}")
        data[symbol] = frame
        window = audit_bars(symbol, frame)
        audits[symbol]["window"] = {k: window[k] for k in ("rows", "first", "last", "sessions", "missing_minutes",
                                                          "sessions_ending_before_force_close",
                                                          "special_session_rows", "special_sessions")}
    return data, audits


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
    raw = {symbol: pd.read_csv(loader._resolve_csv(symbol)) for symbol in sorted(symbols)}   # file order
    data, audits = audit_and_select(raw, end, bars, warmup)
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
