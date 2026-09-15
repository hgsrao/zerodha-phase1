"""V2-C corporate-action SCREENING pass. LOCAL FILES ONLY - no Kite calls,
no credentials, no network.

This script does NOT determine whether a corporate action occurred. It
flags CANDIDATE dislocations in the already-acquired 15-minute price
series - large day-to-day close-to-close (previous session's last bar
close -> next session's first bar close) percentage jumps - as places to
independently investigate. Same discipline as the calendar work: the
data tells us WHERE to look, never WHETHER something happened. A flagged
candidate requires independent verification (NSE corporate-action
records, news) before any verdict is assigned; an unflagged interval is
NOT thereby proven action-free - it only means no discontinuity is
VISIBLE in this data, which is consistent with either genuinely no
action, or an action that Kite's historical API already adjusted for
(Kite is documented to return split/bonus-adjusted OHLC, so a real
split/bonus commonly produces NO visible jump at all).

Threshold: flag any inter-session jump where abs(ln(next_open_bar_close
/ prev_session_last_close)) exceeds a fixed threshold, chosen to be well
above ordinary single-stock day-to-day noise for NSE large/mid-caps but
low enough to catch small-ratio bonus issues (e.g. 1:10 ~ -9% has to
clear the bar too) - 12% log-move, i.e. roughly a 12.7% simple move.
Frozen before running, not tuned after seeing results.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

DATA = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/V2C_15MIN_DATA_ACQUIRED")
SUMMARY_CSV = DATA / "V2C_15MIN_ACQUISITION_SUMMARY.csv"
THRESHOLD_LOG_MOVE = 0.12  # frozen before running


def load_daily_last_bars(path: Path) -> list[tuple[str, float]]:
    """Returns [(date_str, last_bar_close), ...] sorted by date - one
    row per session using that session's LAST available bar's close
    (not necessarily 15:15 if the day has trailing gaps), which is the
    right anchor for a close-to-close overnight jump check regardless
    of any missing-bar certification issues on that specific day."""
    per_date_last: dict[str, tuple[str, float]] = {}
    with path.open(newline="", encoding="utf-8") as h:
        for row in csv.DictReader(h):
            raw = row["timestamp"]
            date_s = raw[:10]
            close = float(row["close"])
            prev = per_date_last.get(date_s)
            if prev is None or raw > prev[0]:
                per_date_last[date_s] = (raw, close)
    return sorted((d, v[1]) for d, v in per_date_last.items())


def screen_file(security_key: str, path: Path) -> list[dict]:
    bars = load_daily_last_bars(path)
    flags = []
    for (d0, c0), (d1, c1) in zip(bars, bars[1:]):
        if c0 <= 0 or c1 <= 0:
            continue
        log_move = math.log(c1 / c0)
        if abs(log_move) >= THRESHOLD_LOG_MOVE:
            flags.append({
                "security_key": security_key,
                "prev_date": d0, "prev_close": c0,
                "next_date": d1, "next_close": c1,
                "log_move": round(log_move, 4),
                "simple_move_pct": round((c1 / c0 - 1) * 100, 2),
                "ratio": round(c1 / c0, 4),
            })
    return flags


def main() -> int:
    with SUMMARY_CSV.open(newline="", encoding="utf-8") as h:
        summary_rows = list(csv.DictReader(h))

    all_flags = []
    n_files = 0
    for row in summary_rows:
        sec_key = row["security_key"]
        w_start, w_end = row["from"], row["through"]
        candidates = list(DATA.glob(f"NSE_{sec_key}_15minute_{w_start}_{w_end}.csv"))
        if not candidates:
            continue
        n_files += 1
        flags = screen_file(sec_key, candidates[0])
        for f in flags:
            f["window"] = f"{w_start}..{w_end}"
        all_flags.extend(flags)

    out_path = DATA / "V2C_CORPORATE_ACTION_SCREEN_CANDIDATES_20260818.json"
    out_path.write_text(json.dumps(all_flags, indent=2), encoding="utf-8")

    print(f"Screened {n_files} security-interval files (threshold: {THRESHOLD_LOG_MOVE} log-move).")
    print(f"{len(all_flags)} candidate dislocations flagged -> {out_path}")
    print("These are SCREENING CANDIDATES ONLY - not a verdict. Each requires independent")
    print("verification (NSE corporate-action record / news) before any disposition.")
    print()
    for f in sorted(all_flags, key=lambda x: x["security_key"]):
        print(f"  {f['security_key']:14s} {f['prev_date']} -> {f['next_date']}  "
              f"{f['simple_move_pct']:+7.2f}%  (ratio {f['ratio']})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
