"""R1-C composite engine — live V2-C (context filter) evaluator. LOCAL
FILES / IN-MEMORY BUFFERS ONLY in this module - no Kite calls here (the
live observer that feeds this module handles the real connection
separately, with the owner's own credentials). No broker writes, no
orders, anywhere in this module.

**Reuse, not reimplement**: this module calls V2-C's own frozen event-
detection (`v2c_train_label_generation.compute_event_z20`,
`compute_atr14`), feature-construction (`v2c_train_feature_construction
.through_1500_hilo`, `get_daily_series`), and scoring
(`v2c_validation_harness.load_frozen_classifier`,
`score_feature_vector`) functions directly - the exact same functions
V2-C's own VALIDATION harness calls, unmodified. `CLASSIFIER_FIT_JSON`
is a real, frozen, already-closed project artifact - read-only here,
never touched.

**Why the 8-line feature formula is copied rather than called as one
function**: `v2c_validation_harness.build_validation_features` computes
these 9 features inline as part of a larger batch loop keyed to
`ValidationEvent` objects and VALIDATION-specific paths - no smaller,
already-frozen, single-event function exists to call. The formula block
below is reproduced character-for-character from that function (and
from `v2c_train_feature_construction.py`'s own module docstring, which
states the same eight formulas independently) - not re-derived - and
`test_r1c_live_v2c_evaluator.py` includes a direct byte-for-byte
comparison against `v2c_validation_harness.build_validation_features`'s
own output on real, already-open TRAIN data, so any future drift
between the two copies is caught mechanically, not just by inspection.

**Why this module never resolves an outcome label**: `discover_
validation_events` (VALIDATION harness) additionally resolves each
event forward to REVERTING/DETERIORATING/UNRESOLVED - meaningless for a
live, still-unfolding event, and irrelevant to R1-C's use of V2-C
(R1-C only needs the PREDICTIVE score to grant or withhold
`REVERSION_PERMITTED`, never an outcome label). This module stops at
scoring, deliberately, not incompletely.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import v2c_train_label_generation as labelgen  # noqa: E402
import v2c_train_feature_construction as featgen  # noqa: E402
import v2c_validation_harness as vh  # noqa: E402

from r1c_bridge_1min_to_15min import (  # noqa: E402
    OneMinuteBar, aggregate_1min_to_15min, write_15min_csv,
)

EVENT_Z20_THRESHOLD = labelgen.EVENT_Z20_THRESHOLD  # -2.0, imported not duplicated

# Reuses Pillar I/II's own frozen 19-symbol universe (identical lists in
# both) - R1-C's state machine can never fire outside that universe
# regardless of V2-C's own broader NIFTY-50 domain, so scanning beyond
# it here would be wasted computation, not a safety gap.
from r1c_live_pillar1_evaluator import pillar1 as _pillar1_module  # noqa: E402
LIVE_UNIVERSE = tuple(_pillar1_module.UNIVERSE)


@dataclass(frozen=True)
class LiveV2CScore:
    security_key: str
    event_t0: str
    event_z20: float | None
    abstain: bool
    abstain_reason: str
    features: dict | None
    p_reverting: float | None
    permitted: bool  # True iff p_reverting >= the frozen classifier threshold


def _compute_features_for_live_event(
    *, symbol_csv: Path, nifty_csv: Path, today: date,
) -> tuple[dict | None, str]:
    """Reproduces `build_validation_features`'s per-event computation
    exactly, for exactly one live event on `today` - returns
    (features_dict_or_None, abstain_reason). See module docstring for
    why this is a faithful copy, not a reimplementation, and how that
    copy is kept honest."""
    daily = featgen.get_daily_series(symbol_csv, today)
    daily_dates = [b.session_date for b in daily]
    try:
        t0_idx = daily_dates.index(today)
    except ValueError:
        return None, "TODAY_NOT_IN_DAILY_SERIES"

    previous_completed_stock_close = daily[t0_idx - 1].close if t0_idx >= 1 else None
    stock_1500_close = daily[t0_idx].close_1500
    hilo = featgen.through_1500_hilo(symbol_csv, today)
    prior_completed_atr14 = labelgen.compute_atr14(daily, t0_idx - 1) if t0_idx - 1 >= 0 else None

    if hilo is None:
        return None, "NO_THROUGH_1500_BARS"
    if prior_completed_atr14 is None:
        return None, "ATR14_INSUFFICIENT_HISTORY_PRIOR_SESSION"
    if not previous_completed_stock_close or previous_completed_stock_close <= 0:
        return None, "PREVIOUS_CLOSE_NON_POSITIVE"
    if stock_1500_close is None:
        return None, "STOCK_1500_CLOSE_MISSING"

    stock_high, stock_low, hilo_close = hilo
    if abs(hilo_close - stock_1500_close) > 1e-6:
        raise vh.HarnessRefusal(
            f"INTEGRITY VIOLATION: through-1500 close mismatch for {symbol_csv} {today}."
        )
    day_range = stock_high - stock_low
    if not math.isfinite(day_range) or day_range <= 0:
        return None, "ZERO_OR_INVALID_INTRADAY_RANGE"

    nifty_daily = featgen.get_daily_series(nifty_csv, today)
    nifty_dates = [b.session_date for b in nifty_daily]
    try:
        nifty_idx = nifty_dates.index(today)
    except ValueError:
        return None, "NIFTY_DATE_NOT_FOUND"
    if nifty_idx < 1:
        return None, "NIFTY_INSUFFICIENT_HISTORY"
    nifty_1500_close = nifty_daily[nifty_idx].close_1500
    previous_completed_nifty_close = nifty_daily[nifty_idx - 1].close
    if nifty_1500_close is None:
        return None, "NIFTY_1500_CLOSE_MISSING"
    if not previous_completed_nifty_close or previous_completed_nifty_close <= 0:
        return None, "NIFTY_PREVIOUS_CLOSE_NON_POSITIVE"

    # --- The eight frozen formulas, reproduced verbatim - see module
    # docstring for provenance and the drift-detection test.
    downside_return = stock_1500_close / previous_completed_stock_close - 1
    drawdown_from_day_high = stock_1500_close / stock_high - 1
    recovery_from_day_low = stock_1500_close / stock_low - 1
    close_location = (stock_1500_close - stock_low) / day_range
    atr14_pct = prior_completed_atr14 / previous_completed_stock_close
    intraday_range_pct = day_range / previous_completed_stock_close
    nifty_return = nifty_1500_close / previous_completed_nifty_close - 1
    relative_return = downside_return - nifty_return

    event_z20 = labelgen.compute_event_z20(daily, t0_idx)
    feats = {
        "event_z20": event_z20, "downside_return": downside_return,
        "drawdown_from_day_high": drawdown_from_day_high, "recovery_from_day_low": recovery_from_day_low,
        "close_location": close_location, "atr14_pct": atr14_pct,
        "intraday_range_pct": intraday_range_pct, "nifty_return": nifty_return,
        "relative_return": relative_return,
    }
    if not all(v is not None and math.isfinite(v) for v in feats.values()):
        return None, "NON_FINITE_COMPUTED_FEATURE"
    return feats, ""


@dataclass
class LiveV2CEvaluator:
    """Holds a live-growing, MULTI-DAY, per-symbol 1-minute bar buffer
    (plus NIFTY), bridges it to V2-C's exact 15-minute input schema on
    demand, and scores whatever qualifying events appear on `today`.

    Multi-day accumulation for the same reason as Pillar I/II's
    evaluators: `compute_event_z20` needs 20 prior completed daily
    closes, and `compute_atr14` needs 15 - both fail closed (return
    `None`) rather than default when history is short, exactly like the
    live Pillar evaluators' own trailing-window checks."""

    universe: tuple[str, ...] = LIVE_UNIVERSE
    retention_days: int = 40  # >= 20 (event_z20) + 15 (ATR14) trading days, with margin
    _buffers: dict[str, list[OneMinuteBar]] = None
    _nifty_buffer: list[OneMinuteBar] = None

    def __post_init__(self):
        self._buffers = {sym: [] for sym in self.universe}
        self._nifty_buffer = []

    def ingest_1m_bar(self, symbol: str, bar: dict) -> None:
        row = OneMinuteBar(
            timestamp=bar["timestamp"], open=float(bar["open"]), high=float(bar["high"]),
            low=float(bar["low"]), close=float(bar["close"]), volume=float(bar.get("volume") or 0.0),
        )
        if symbol == "NIFTY 50":
            self._nifty_buffer.append(row)
        elif symbol in self._buffers:
            self._buffers[symbol].append(row)

    def prune_older_than(self, cutoff: date) -> None:
        def _keep(rows):
            return [r for r in rows if date.fromisoformat(r.timestamp[:10]) >= cutoff]
        self._nifty_buffer = _keep(self._nifty_buffer)
        for sym in self._buffers:
            self._buffers[sym] = _keep(self._buffers[sym])

    def _write_bridged_csv(self, rows: list[OneMinuteBar], out_path: Path) -> Path:
        fifteen = aggregate_1min_to_15min(sorted(rows, key=lambda b: b.timestamp))
        return write_15min_csv(fifteen, out_path)

    def evaluate(self, today: date, *, out_dir: Path) -> list[LiveV2CScore]:
        """`out_dir` is a scratch directory this evaluator writes its
        live-bridged 15-minute CSVs into (V2-C's real functions all take
        file paths, not in-memory frames - reused as-is rather than
        adapted). Caller owns the directory's lifetime."""
        out_dir.mkdir(parents=True, exist_ok=True)
        classifier = vh.load_frozen_classifier()

        if not self._nifty_buffer:
            return []
        nifty_csv = self._write_bridged_csv(self._nifty_buffer, out_dir / "NSE_NIFTY_50_15minute_live.csv")

        results: list[LiveV2CScore] = []
        for sym, rows in self._buffers.items():
            if not rows:
                continue
            sym_csv = self._write_bridged_csv(rows, out_dir / f"NSE_{sym}_15minute_live.csv")

            daily = labelgen.build_daily_series(sym_csv, today)
            daily_dates = [b.session_date for b in daily]
            if today not in daily_dates:
                continue
            t0_idx = daily_dates.index(today)

            event_z20 = labelgen.compute_event_z20(daily, t0_idx)
            if event_z20 is None or not (event_z20 < EVENT_Z20_THRESHOLD):
                continue  # no qualifying event for this symbol today - not an abstain, just no event

            feats, abstain_reason = _compute_features_for_live_event(
                symbol_csv=sym_csv, nifty_csv=nifty_csv, today=today,
            )
            if feats is None:
                results.append(LiveV2CScore(
                    security_key=sym, event_t0=today.isoformat(), event_z20=event_z20,
                    abstain=True, abstain_reason=abstain_reason, features=None,
                    p_reverting=None, permitted=False,
                ))
                continue

            p = vh.score_feature_vector(feats, classifier)
            results.append(LiveV2CScore(
                security_key=sym, event_t0=today.isoformat(), event_z20=event_z20,
                abstain=False, abstain_reason="", features=feats,
                p_reverting=p, permitted=p >= classifier["threshold"],
            ))
        return results
