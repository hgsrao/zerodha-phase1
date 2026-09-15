"""ORB (Opening Range Breakout) historical replay - vetting pass
(2026-08-25, owner-authorized: "same rigor... native evaluation
appropriate to ORB").

WHY THIS IS NOW POSSIBLE: orb_shadow_observer.py's own docstring states
ORB "has NOT been backtested at all - it can't be, honestly: everything
downloaded is 60-minute bars." That constraint no longer holds - the
1-minute V10-C archive (hash-verified, acquired earlier this same
session) has exactly the resolution ORB's opening-range premise needs.
This is the first honest ORB backtest this project has ever had.

ENTRY LOGIC: imported unchanged in spirit from orb_shadow_observer.py -
first ORB_MINUTES of the session set an opening range (high/low); a
later bar whose HIGH crosses BREAKOUT_BUFFER_BPS above the range high is
a breakout candidate. BREAKOUT_BUFFER_BPS=10.0 matches orb_shadow_
observer.py's own default exactly - not re-tuned for this test.

TRADE-MANAGEMENT RULE (NEW - none existed before this file; the shadow
observer only ever emitted a HYPOTHETICAL_BUY signal, never a complete
trade): a standard, UN-OPTIMIZED convention, disclosed explicitly rather
than silently chosen:
  - Entry: next 1-min bar's OPEN after the breakout bar (next-bar-open,
    this project's established convention).
  - Stop: the opening-range LOW - the single most conventional ORB stop
    (a pullback all the way back into/through the opening range
    invalidates the breakout thesis). Not swept, not optimized.
  - Target: 2R (matching this project's own V10-C convention of
    reporting 1R/2R targets).
  - Forced square-off: 15:15 IST (CAS-aware, matching replay_v10c_
    history.py's own session-close convention), or the day's last
    available bar if data ends before that.
  - Long only (this project does not short - same as the live shadow
    observer).

GAP-AWARE ELIGIBILITY: trading days on the two known-contaminated dates
(2024-03-02, 2024-05-18 - the systematic 90-min gap found earlier this
session, affecting all 48 symbols identically) are excluded outright.

COSTS: real Zerodha MIS statutory schedule (zerodha_intraday_costs.py)
+ 5bp/side slippage stress (V10-C's own established convention) - same
cost model as study_layer_v2_cost_overlay.py, reused not reinvented.

SIGNIFICANCE: block bootstrap by TRADING DATE (pooling all symbols'
trades on a date into one block, same joint-date correction applied to
the momentum finding after the owner caught the first draft's flaw).

SCOPE: 8-symbol sample first (same sample used throughout this session,
for speed and consistency) - 48-symbol replication is a candidate
follow-up only if this passes, not run blind.
"""
from __future__ import annotations

import hashlib
import json
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

from study_layer_v2_forward_information_study import KNOWN_BAD_DATES, SAMPLE_SYMBOLS, load_symbol_1min
from zerodha_intraday_costs import round_trip_bps_equivalent

ORB_MINUTES = 15
BREAKOUT_BUFFER_BPS = 10.0  # unchanged from orb_shadow_observer.py's own default
TARGET_R_MULTIPLE = 2.0
SQUARE_OFF_TIME = dtime(15, 15)
REPRESENTATIVE_TRADE_VALUE = 50_000.0
SLIPPAGE_STRESS_BPS_ROUND_TRIP = 10.0  # 5bp/side, same as study_layer_v2_cost_overlay.py
STATUTORY_ROUND_TRIP_BPS = round_trip_bps_equivalent(REPRESENTATIVE_TRADE_VALUE)
TOTAL_COST_BPS = STATUTORY_ROUND_TRIP_BPS + SLIPPAGE_STRESS_BPS_ROUND_TRIP

ROOT = Path(__file__).parent
OUT_PATH = ROOT / "ORB_VETTING_RESULT_20260825.json"


def simulate_orb_day(day_bars: pd.DataFrame) -> dict | None:
    """One symbol, one trading day, 1-min bars sorted ascending. Returns
    a trade dict or None if no eligible trade occurred that day (no
    breakout, or a breakout with no room for a next-bar-open fill)."""
    day_bars = day_bars.reset_index(drop=True)
    if len(day_bars) < ORB_MINUTES + 2:
        return None

    orb_window = day_bars.iloc[:ORB_MINUTES]
    orb_high = float(orb_window["high"].max())
    orb_low = float(orb_window["low"].min())
    if orb_low <= 0 or orb_high <= orb_low:
        return None
    breakout_level = orb_high * (1 + BREAKOUT_BUFFER_BPS / 10_000)

    rest = day_bars.iloc[ORB_MINUTES:]
    breakout_positions = rest.index[rest["high"] >= breakout_level]
    if len(breakout_positions) == 0:
        return None
    breakout_idx = int(breakout_positions[0])
    if breakout_idx + 1 >= len(day_bars):
        return None

    entry_price = float(day_bars.loc[breakout_idx + 1, "open"])
    stop_price = orb_low
    if entry_price <= stop_price:
        return None
    risk_per_share = entry_price - stop_price
    target_price = entry_price + TARGET_R_MULTIPLE * risk_per_share

    exit_price, exit_reason = None, None
    for i in range(breakout_idx + 1, len(day_bars)):
        row = day_bars.iloc[i]
        if row["low"] <= stop_price:
            exit_price, exit_reason = stop_price, "STOP"
            break
        if row["high"] >= target_price:
            exit_price, exit_reason = target_price, "TARGET"
            break
        if row["timestamp"].time() >= SQUARE_OFF_TIME:
            exit_price, exit_reason = float(row["close"]), "EOD_SQUAREOFF"
            break
    if exit_price is None:
        exit_price, exit_reason = float(day_bars.iloc[-1]["close"]), "EOD_FORCED_LAST_BAR"

    gross_return_pct = (exit_price - entry_price) / entry_price
    net_return_pct = gross_return_pct - TOTAL_COST_BPS / 10_000
    r_multiple_gross = (exit_price - entry_price) / risk_per_share
    r_multiple_net = (net_return_pct * entry_price) / risk_per_share

    return {
        "session_date": day_bars.loc[0, "timestamp"].normalize(),
        "orb_high": orb_high, "orb_low": orb_low, "entry_price": entry_price,
        "stop_price": stop_price, "target_price": target_price, "exit_price": exit_price,
        "exit_reason": exit_reason, "risk_per_share": risk_per_share,
        "gross_return_pct": gross_return_pct, "net_return_pct": net_return_pct,
        "r_multiple_gross": r_multiple_gross, "r_multiple_net": r_multiple_net,
    }


def build_symbol_trades(symbol: str) -> pd.DataFrame:
    df = load_symbol_1min(symbol)
    df["session_date"] = df["timestamp"].dt.normalize()
    trades = []
    for date, day_bars in df.groupby("session_date"):
        if date.date() in KNOWN_BAD_DATES:
            continue
        trade = simulate_orb_day(day_bars)
        if trade is not None:
            trade["symbol"] = symbol
            trades.append(trade)
    return pd.DataFrame(trades)


def block_bootstrap_mean(trades: pd.DataFrame, value_col: str, n_iterations: int = 2000, seed: int = 20260825):
    blocks = trades.groupby("session_date")[value_col].agg(["sum", "count"]).reset_index()
    sums, counts = blocks["sum"].to_numpy(), blocks["count"].to_numpy()
    n_blocks = len(blocks)
    rng = np.random.RandomState(seed)
    point = float(sums.sum() / counts.sum())
    stats = np.empty(n_iterations)
    for i in range(n_iterations):
        idx = rng.randint(0, n_blocks, size=n_blocks)
        stats[i] = sums[idx].sum() / counts[idx].sum()
    ci_low, ci_high = np.percentile(stats, [2.5, 97.5])
    p_le, p_ge = float((stats <= 0).mean()), float((stats >= 0).mean())
    p_value = max(min(2 * min(p_le, p_ge), 1.0), 1.0 / n_iterations)
    return {"point": point, "ci_low": float(ci_low), "ci_high": float(ci_high),
            "excludes_zero": bool(ci_low > 0 or ci_high < 0), "p_value": p_value}


def main():
    print(f"Total round-trip cost assumed: {TOTAL_COST_BPS:.2f}bp "
          f"(statutory {STATUTORY_ROUND_TRIP_BPS:.2f}bp + slippage {SLIPPAGE_STRESS_BPS_ROUND_TRIP:.2f}bp)")
    print(f"Trade rule: entry next-bar-open after ORB({ORB_MINUTES}min) breakout +{BREAKOUT_BUFFER_BPS}bps, "
          f"stop=ORB low, target={TARGET_R_MULTIPLE}R, square-off {SQUARE_OFF_TIME}")

    all_trades = pd.concat([build_symbol_trades(s) for s in SAMPLE_SYMBOLS], ignore_index=True)
    n_trades = len(all_trades)
    n_days = all_trades["session_date"].nunique()
    print(f"\nTotal ORB trades generated: {n_trades:,} across {n_days:,} distinct trading dates, "
          f"{len(SAMPLE_SYMBOLS)} symbols")

    if n_trades == 0:
        print("No trades generated - cannot vet.")
        return

    win_rate = float((all_trades["net_return_pct"] > 0).mean())
    exit_reason_counts = all_trades["exit_reason"].value_counts().to_dict()
    gross_r_mean = float(all_trades["r_multiple_gross"].mean())
    net_r_mean = float(all_trades["r_multiple_net"].mean())

    print(f"\nWin rate (net of costs): {win_rate:.1%}")
    print(f"Exit reason breakdown: {exit_reason_counts}")
    print(f"Mean gross R-multiple: {gross_r_mean:+.4f}   Mean NET R-multiple: {net_r_mean:+.4f}")

    boot = block_bootstrap_mean(all_trades, "r_multiple_net")
    print(f"\nBlock-bootstrap (by trading date) on mean NET R-multiple:")
    print(f"  point={boot['point']:+.4f}  95% CI=[{boot['ci_low']:+.4f}, {boot['ci_high']:+.4f}]  "
          f"p={boot['p_value']:.4f}  excludes_zero={boot['excludes_zero']}")

    if boot["point"] <= 0:
        verdict = "FAIL"
    elif not boot["excludes_zero"]:
        verdict = "STATISTICAL-ONLY (not distinguishable from zero after costs)"
    else:
        verdict = "ECONOMIC PASS (net R positive, CI excludes zero)"
    print(f"\nVERDICT: {verdict}")

    input_hashes = {}
    for s in SAMPLE_SYMBOLS:
        p = Path("P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824"
                  f"/NSE_{s}_minute_2023-07-03_2026-08-24.csv")
        input_hashes[s] = hashlib.sha256(p.read_bytes()).hexdigest()

    manifest = {
        "experiment_id": "ORB_VETTING_20260825", "frozen_2026_08_25": True,
        "trade_rule": {
            "orb_minutes": ORB_MINUTES, "breakout_buffer_bps": BREAKOUT_BUFFER_BPS,
            "stop": "opening_range_low", "target_r_multiple": TARGET_R_MULTIPLE,
            "square_off_time": str(SQUARE_OFF_TIME), "fill": "next_bar_open",
        },
        "cost_model": {"statutory_round_trip_bps": STATUTORY_ROUND_TRIP_BPS,
                        "slippage_bps": SLIPPAGE_STRESS_BPS_ROUND_TRIP, "total_bps": TOTAL_COST_BPS},
        "symbols": list(SAMPLE_SYMBOLS), "input_sha256": input_hashes,
        "n_trades": n_trades, "n_trading_dates": int(n_days),
        "win_rate_net": win_rate, "exit_reason_counts": exit_reason_counts,
        "gross_r_multiple_mean": gross_r_mean, "net_r_multiple_mean": net_r_mean,
        "bootstrap": boot, "verdict": verdict,
    }
    OUT_PATH.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    h = hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()
    OUT_PATH.with_suffix(".json.sha256").write_text(f"{h}  {OUT_PATH.name}\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH.name} (sha256 {h[:16]}...)")


if __name__ == "__main__":
    main()
