"""Map + Context historical replay - vetting pass (2026-08-25).

Map+Context has never had a historical backtest - it has run live,
paper-trading, since this afternoon, with 0 trades so far (too new for
any statistics). This is its first honest historical vetting.

FIDELITY: reuses the actual, already-tested (34/34) strategy logic from
map_context_indicators.py UNCHANGED (ReactionState, build_map,
compute_stop_and_target, classify_index_regime, sizing_guidance,
consecutive_directional_days) and the real cost-netting dataclass from
map_context_live_engine.py (MapContextTrade, whose buy_cost/sell_cost
use zerodha_delivery_costs - see the flagged note below). This is NOT a
reimplementation of the strategy - only the bar-feeding/day-loop
mechanics differ from the live engine, out of necessity (see below).

WHY NOT THE LIVE ENGINE'S EXACT CLASS/LOOP: MapContextWatcher.poll()
re-resamples its ENTIRE accumulated 1-min bar history on every single
poll call - fine for a live process that only ever holds a few months
of bars, computationally intractable for a 3-year, 280K-bar-per-symbol
replay (tens of thousands of poll calls x hundreds of thousands of rows
each). Using aggregate_1min_to_5min_with_quality (already validated
today) to pre-resample once, then driving the SAME indicator functions
bar-by-bar, is an engineering adaptation for tractable replay - not a
change to the strategy's entry/exit/stop/target logic itself.

TWO NECESSARY, DISCLOSED ADAPTATIONS for multi-day replay (the live
engine's main() loop only ever runs within one calendar startup, so
these never had to be decided there):
  1. Daily map + NIFTY regime are recomputed once per SIMULATED trading
     day, causally (only data strictly BEFORE that day) - the live
     engine's own bootstrap() docstring already says maps should be
     rebuilt "at the start of each new trading day"; this replay is the
     first place that actually has to implement that for real.
  2. ReactionState is reset at each new trading day (a level tested/
     reclaimed yesterday against YESTERDAY's map does not carry into a
     new day's freshly-rebuilt map).
  3. Any open position is force-closed at end of day (EOD square-off),
     matching _generate_eod_statement's real daily behavior in the live
     engine - applied every simulated day, not just at the end of the
     whole run.

FLAGGED, NOT SILENTLY FIXED: MapContextTrade.buy_cost/sell_cost use
zerodha_delivery_costs (CNC/delivery schedule), even though every
position is force-closed same-day (matching the live engine's own
choice - not a decision made here). Delivery costs include STT on BOTH
sides (0.1%) versus MIS-intraday's sell-only 0.025%, and a DP charge, so
this is almost certainly MORE conservative than the economically
correct schedule for a same-day-closed position - if it makes any
difference, it should bias the result AGAINST passing, not toward it.
Replicated exactly as the live engine already does it, not corrected.

GAP-AWARE ELIGIBILITY: the two known-contaminated dates (2024-03-02,
2024-05-18) excluded, same as every other replay today.

SCOPE: 8-symbol sample (same sample used throughout this session).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from map_context_indicators import (
    MIN_LEVEL_TOUCHES, ReactionState, build_map, classify_index_regime,
    compute_stop_and_target, consecutive_directional_days, sizing_guidance,
)
from map_context_live_engine import LOOKBACK_TRADING_DAYS, NOTIONAL_PER_POSITION, MapContextTrade
from map_context_observer import NIFTY_INDEX_PATH, P02_ROOT
from P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825.acquire_v10_history import (
    END_DATE as V10C_END_DATE, OUTPUT_DIR as V10C_MINUTE_DATA_DIR, WARMUP_START as V10C_WARMUP_START,
)
from study_layer_v2_forward_information_study import KNOWN_BAD_DATES, SAMPLE_SYMBOLS, load_symbol_1min
from study_layer_v2_state_vector import aggregate_1min_to_5min_with_quality

ROOT = Path(__file__).parent
OUT_PATH = ROOT / "MAP_CONTEXT_VETTING_RESULT_20260825.json"

_DAILY_CACHE: dict[str, pd.DataFrame] = {}


def _load_daily_universe_full(symbol: str) -> pd.DataFrame:
    """Same file-resolution logic as map_context_live_engine._load_daily_universe -
    NOT re-tuned, just loading the FULL history instead of an unconditional
    .tail(250), so a caller can apply its own AS-OF cutoff causally."""
    direct_path = P02_ROOT / "kite_nifty50_data" / f"{symbol}_daily.csv"
    if direct_path.is_file():
        df = pd.read_csv(direct_path)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        return df.sort_values("date").reset_index(drop=True)
    minute_path = V10C_MINUTE_DATA_DIR / f"NSE_{symbol}_minute_{V10C_WARMUP_START}_{V10C_END_DATE}.csv"
    minute = pd.read_csv(minute_path)
    minute["timestamp"] = pd.to_datetime(minute["timestamp"]).dt.tz_localize(None)
    minute = minute.set_index("timestamp").sort_index()
    daily = minute.resample("1D").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna(subset=["open"]).reset_index().rename(columns={"timestamp": "date"})
    return daily


def _load_daily_universe_asof(symbol: str, as_of_day: pd.Timestamp) -> pd.DataFrame:
    if symbol not in _DAILY_CACHE:
        _DAILY_CACHE[symbol] = _load_daily_universe_full(symbol)
    full = _DAILY_CACHE[symbol]
    return full[full["date"] < as_of_day].tail(LOOKBACK_TRADING_DAYS).reset_index(drop=True)


def _load_index_daily() -> pd.DataFrame:
    df = pd.read_csv(NIFTY_INDEX_PATH)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def replay_symbol(symbol: str, index_daily_full: pd.DataFrame) -> list[MapContextTrade]:
    df_1min = load_symbol_1min(symbol)
    bars5 = aggregate_1min_to_5min_with_quality(df_1min)
    bars5 = bars5.sort_values("timestamp").reset_index(drop=True)
    bars5 = bars5[~bars5["timestamp"].dt.normalize().dt.date.isin(KNOWN_BAD_DATES)].reset_index(drop=True)

    reaction = ReactionState()
    open_trade: MapContextTrade | None = None
    trades: list[MapContextTrade] = []
    current_day = None
    daily_map = None
    index_regime = None

    for i in range(len(bars5)):
        row = bars5.iloc[i]
        day = row["timestamp"].normalize()

        if current_day is not None and day != current_day:
            if open_trade is not None:
                prev = bars5.iloc[i - 1]
                open_trade.close(str(prev["timestamp"]), float(prev["close"]), "EOD")
                trades.append(open_trade)
                open_trade = None

        if day != current_day:
            daily_universe = _load_daily_universe_asof(symbol, day)
            if len(daily_universe) < 20:
                current_day = day
                daily_map, index_regime = None, None
                reaction = ReactionState()
                continue
            seed_price = float(daily_universe["close"].iloc[-1])
            built = build_map(daily_universe, seed_price)
            daily_map = {
                "support": [lvl for lvl in built["all_support"] if lvl["touches"] >= MIN_LEVEL_TOUCHES],
                "resistance": [lvl for lvl in built["all_resistance"] if lvl["touches"] >= MIN_LEVEL_TOUCHES],
            }
            idx_asof = index_daily_full[index_daily_full["date"] < day].tail(250)
            index_regime = classify_index_regime(idx_asof) if len(idx_asof) >= 50 else None
            reaction = ReactionState()
            current_day = day

        if index_regime is None or daily_map is None:
            continue

        close = float(row["close"])
        if open_trade is not None:
            if close <= open_trade.stop_price:
                open_trade.close(str(row["timestamp"]), close, "STOP")
                trades.append(open_trade)
                open_trade = None
            elif open_trade.target_price is not None and close >= open_trade.target_price:
                open_trade.close(str(row["timestamp"]), close, "TARGET")
                trades.append(open_trade)
                open_trade = None
            continue

        support = daily_map["support"][0] if daily_map["support"] else None
        pending_tested_level = reaction.tested_level
        result = reaction.update(float(row["low"]), close, support)
        if result != "ENTRY":
            continue

        guidance = sizing_guidance(index_regime["regime"], "LONG", consecutive_directional_days(bars5.iloc[: i + 1]))
        if guidance.tier == "PASS":
            continue

        resistance = daily_map["resistance"][0] if daily_map["resistance"] else None
        levels = compute_stop_and_target(close, pending_tested_level, resistance)
        open_trade = MapContextTrade(
            symbol=symbol, entry_ts=str(row["timestamp"]), entry_price=close,
            stop_price=levels["stop_price"], target_price=levels["target_price"],
        )

    if open_trade is not None:
        last = bars5.iloc[-1]
        open_trade.close(str(last["timestamp"]), float(last["close"]), "EOD")
        trades.append(open_trade)

    return trades


def block_bootstrap_mean(values_by_date: pd.DataFrame, n_iterations: int = 2000, seed: int = 20260825):
    sums, counts = values_by_date["sum"].to_numpy(), values_by_date["count"].to_numpy()
    n_blocks = len(values_by_date)
    rng = np.random.RandomState(seed)
    point = float(sums.sum() / counts.sum())
    stats = np.empty(n_iterations)
    for it in range(n_iterations):
        idx = rng.randint(0, n_blocks, size=n_blocks)
        stats[it] = sums[idx].sum() / counts[idx].sum()
    ci_low, ci_high = np.percentile(stats, [2.5, 97.5])
    p_le, p_ge = float((stats <= 0).mean()), float((stats >= 0).mean())
    p_value = max(min(2 * min(p_le, p_ge), 1.0), 1.0 / n_iterations)
    return {"point": point, "ci_low": float(ci_low), "ci_high": float(ci_high),
            "excludes_zero": bool(ci_low > 0 or ci_high < 0), "p_value": p_value}


def main():
    index_daily_full = _load_index_daily()
    all_trades: list[MapContextTrade] = []
    for symbol in SAMPLE_SYMBOLS:
        print(f"Replaying {symbol}...")
        trades = replay_symbol(symbol, index_daily_full)
        print(f"  {len(trades)} trades")
        all_trades.extend(trades)

    n_trades = len(all_trades)
    print(f"\nTotal Map+Context trades: {n_trades} across {len(SAMPLE_SYMBOLS)} symbols")
    if n_trades == 0:
        print("No trades generated - cannot vet.")
        OUT_PATH.write_text(json.dumps({"experiment_id": "MAP_CONTEXT_VETTING_20260825",
                                         "n_trades": 0, "verdict": "NO TRADES GENERATED"}, indent=2), encoding="utf-8")
        return

    rows = []
    for t in all_trades:
        entry_ts = pd.Timestamp(t.entry_ts)
        risk_per_share_pct = abs(t.entry_price - t.stop_price) / t.entry_price if t.entry_price else None
        rows.append({
            "session_date": entry_ts.normalize(), "symbol": t.symbol, "exit_reason": t.exit_reason,
            "net_pnl": t.net_pnl, "gross_pnl": t.gross_pnl,
            "risk_per_share_pct": risk_per_share_pct,
        })
    trades_df = pd.DataFrame(rows).dropna(subset=["net_pnl"])

    win_rate = float((trades_df["net_pnl"] > 0).mean())
    exit_counts = trades_df["exit_reason"].value_counts().to_dict()
    net_pnl_total = float(trades_df["net_pnl"].sum())
    net_pnl_mean = float(trades_df["net_pnl"].mean())

    # DISCOVERED DURING THIS RUN, not assumed: compute_stop_and_target's
    # stop = the reclaimed level itself, with no minimum-distance floor -
    # a MARGINAL reclaim (entry only fractionally above the level) creates
    # a near-zero-risk stop. That made R-multiple numerically unsafe as a
    # test statistic (division by near-zero blew the mean up to ~-2.85e10
    # on the first run) even though the underlying rupee P&L per trade was
    # always a normal, bounded number. Switching the PRIMARY statistic to
    # net rupee P&L (exactly what the live engine's own EOD statement
    # already reports) - not a strategy change, a test-statistic fix.
    near_zero_risk_frac = float((trades_df["risk_per_share_pct"] < 0.001).mean())
    print(f"Win rate (net): {win_rate:.1%}")
    print(f"Exit reasons: {exit_counts}")
    print(f"Total net P&L (notional, real delivery-cost schedule): Rs.{net_pnl_total:,.2f}")
    print(f"Mean NET P&L per trade: Rs.{net_pnl_mean:+.2f}")
    print(f"Risk-per-share distribution (% of entry price): min={trades_df['risk_per_share_pct'].min():.4%}  "
          f"median={trades_df['risk_per_share_pct'].median():.4%}")
    print(f"Fraction of trades with risk-per-share < 0.1% of entry price (near-zero-risk stops): "
          f"{near_zero_risk_frac:.1%}")

    by_date = trades_df.groupby("session_date")["net_pnl"].agg(["sum", "count"]).reset_index()
    boot = block_bootstrap_mean(by_date)
    print(f"\nBlock-bootstrap (by trading date) on mean NET P&L per trade (Rs.):")
    print(f"  point=Rs.{boot['point']:+.2f}  95% CI=[Rs.{boot['ci_low']:+.2f}, Rs.{boot['ci_high']:+.2f}]  "
          f"p={boot['p_value']:.4f}  excludes_zero={boot['excludes_zero']}")

    if boot["point"] <= 0:
        verdict = "FAIL"
    elif not boot["excludes_zero"]:
        verdict = "STATISTICAL-ONLY (not distinguishable from zero after costs)"
    else:
        verdict = "ECONOMIC PASS (net P&L positive, CI excludes zero)"
    print(f"\nVERDICT: {verdict}")

    input_hashes = {}
    for s in SAMPLE_SYMBOLS:
        p = Path("P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824"
                  f"/NSE_{s}_minute_2023-07-03_2026-08-24.csv")
        input_hashes[s] = hashlib.sha256(p.read_bytes()).hexdigest()

    manifest = {
        "experiment_id": "MAP_CONTEXT_VETTING_20260825", "frozen_2026_08_25": True,
        "note_cost_model": "MapContextTrade uses zerodha_delivery_costs (the live engine's own choice, "
                            "replicated not corrected) despite same-day forced EOD close - flagged as "
                            "likely conservative (higher cost than the correct MIS schedule), not fixed.",
        "note_r_multiple_unstable": "R-multiple was abandoned as the test statistic - compute_stop_and_target "
                                     "has no minimum-distance floor, so a marginal reclaim creates a near-zero-"
                                     "risk stop and an unstable R-multiple ratio, even though rupee P&L per "
                                     "trade stays normal and bounded. See near_zero_risk_stop_fraction.",
        "symbols": list(SAMPLE_SYMBOLS), "input_sha256": input_hashes,
        "n_trades": n_trades, "win_rate_net": win_rate, "exit_reason_counts": exit_counts,
        "net_pnl_total_notional": net_pnl_total, "net_pnl_mean_per_trade": net_pnl_mean,
        "near_zero_risk_stop_fraction": near_zero_risk_frac,
        "bootstrap": boot, "verdict": verdict,
    }
    OUT_PATH.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    h = hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()
    OUT_PATH.with_suffix(".json.sha256").write_text(f"{h}  {OUT_PATH.name}\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH.name} (sha256 {h[:16]}...)")


if __name__ == "__main__":
    main()
