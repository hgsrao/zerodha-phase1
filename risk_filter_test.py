"""ORB/Map+Context RISK-FILTER test (2026-08-25, owner-specified Head-2
test from the three-head self-learning architecture).

Directional information is already closed - see PILLAR_INFORMATION_
DECOMPOSITION_V1_20260825.json: NO_DEMONSTRATED_INFORMATION on
fwd_return and asymmetry, every horizon, both pillars. This does NOT
answer a different question, asked here for the first time:

    "Given that a trade is being considered (from some other source),
    does the ORB/Map condition being ACTIVE change the probability of a
    bad outcome (large adverse excursion)?"

That can be true even though the directional mean is exactly zero - a
pure volatility-regime or tail-risk signal, not a direction signal.
Owner's own worked example: baseline P(MAE>40bp)=28%, same trade + Map
condition P(MAE>40bp)=17% - Map would have real risk-filter value even
with zero directional edge.

METHOD: for each pillar, compare the MAE distribution at bars where the
pillar's ENTRY CONDITION IS ACTIVE against the MAE distribution at all
OTHER eligible bars (the general population) - same symbols, same
horizons, same gap-aware eligibility. Tail probability P(MAE < -thresh)
compared via joint-date block bootstrap on the PROPORTION DIFFERENCE,
reusing _block_table/block_bootstrap/report from study_layer_v2_
bootstrap_significance.py unchanged (same tool, new question).

Map+Context reuses study_layer_v2_forward_information_study.build_symbol_frame's
already-computed per-bar mae_{h} as the control population - re-derives
the exact entry-bar timestamps (map_context_events_for_symbol's own
outcome dicts only keep session_date, not the bar timestamp, so this
file adds a small, disclosed variant that also records it).

ORB required a genuinely NEW per-bar (not per-event) MAE computation at
1-min resolution, since no such control population existed anywhere
before this file - built with the same vectorized rolling-forward-min
pattern already used elsewhere today, applied to every bar instead of
only breakout bars.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from map_context_historical_replay import _load_daily_universe_asof, _load_index_daily
from map_context_indicators import (
    MIN_LEVEL_TOUCHES, ReactionState, build_map, classify_index_regime,
)
from pillar_information_decomposition import BREAKOUT_BUFFER_BPS, ORB_MINUTES
from study_layer_v2_bootstrap_significance import _block_table, _point_estimate, benjamini_hochberg, block_bootstrap, report
from study_layer_v2_forward_information_study import KNOWN_BAD_DATES, SAMPLE_SYMBOLS, build_symbol_frame, load_symbol_1min
from study_layer_v2_state_vector import aggregate_1min_to_5min_with_quality

ROOT = Path(__file__).parent
OUT_PATH = ROOT / "RISK_FILTER_TEST_20260825.json"

ORB_HORIZONS_MIN = [15, 30, 60, 120]
MAP_HORIZONS_BARS = [1, 2, 3, 6, 12]
TAIL_THRESHOLDS_BPS = [20, 40, 80]  # matches the owner's own worked 40bp example, plus a tighter/looser pair


def _rolling_forward_min(s: pd.Series, h: int) -> pd.Series:
    return s[::-1].rolling(h, min_periods=h).min()[::-1]


# ---------------------------------------------------------------------------
# Map+Context - control population reused from build_symbol_frame; entry
# timestamps re-derived (session_date alone isn't a unique-enough key).
# ---------------------------------------------------------------------------
def map_context_population_with_flags(symbol: str, index_daily_full: pd.DataFrame) -> pd.DataFrame:
    frame = build_symbol_frame(symbol)  # timestamp, session_id, mae_{h}, state_clean, label_eligible_h, ...
    frame["symbol"] = symbol

    bars5 = aggregate_1min_to_5min_with_quality(load_symbol_1min(symbol))
    bars5 = bars5.sort_values("timestamp").reset_index(drop=True)
    bars5 = bars5[~bars5["timestamp"].dt.normalize().dt.date.isin(KNOWN_BAD_DATES)].reset_index(drop=True)

    reaction = ReactionState()
    current_day, daily_map, index_regime = None, None, None
    entry_timestamps = set()
    for i in range(len(bars5)):
        row = bars5.iloc[i]
        day = row["timestamp"].normalize()
        if day != current_day:
            daily_universe = _load_daily_universe_asof(symbol, day)
            if len(daily_universe) < 20:
                current_day, daily_map, index_regime = day, None, None
                reaction = ReactionState()
                continue
            seed_price = float(daily_universe["close"].iloc[-1])
            built = build_map(daily_universe, seed_price)
            daily_map = {"support": [lvl for lvl in built["all_support"] if lvl["touches"] >= MIN_LEVEL_TOUCHES],
                         "resistance": [lvl for lvl in built["all_resistance"] if lvl["touches"] >= MIN_LEVEL_TOUCHES]}
            idx_asof = index_daily_full[index_daily_full["date"] < day].tail(250)
            index_regime = classify_index_regime(idx_asof) if len(idx_asof) >= 50 else None
            reaction = ReactionState()
            current_day = day
        if index_regime is None or daily_map is None:
            continue
        close = float(row["close"])
        support = daily_map["support"][0] if daily_map["support"] else None
        result = reaction.update(float(row["low"]), close, support)
        if result == "ENTRY":
            entry_timestamps.add(row["timestamp"])

    frame["map_condition_active"] = frame["timestamp"].isin(entry_timestamps)
    return frame


# ---------------------------------------------------------------------------
# ORB - new per-bar (not per-event) control population at 1-min resolution
# ---------------------------------------------------------------------------
def orb_population_and_flags(symbol: str) -> pd.DataFrame:
    df = load_symbol_1min(symbol)
    df["session_date"] = df["timestamp"].dt.normalize()
    df = df[~df["session_date"].dt.date.isin(KNOWN_BAD_DATES)].reset_index(drop=True)

    rows = []
    for date, day_bars in df.groupby("session_date"):
        day_bars = day_bars.reset_index(drop=True)
        n = len(day_bars)
        if n < ORB_MINUTES + max(ORB_HORIZONS_MIN) + 2:
            continue
        low_fwd1 = day_bars["low"].shift(-1)
        maes = {h: (_rolling_forward_min(low_fwd1, h) - day_bars["close"]) / day_bars["close"] for h in ORB_HORIZONS_MIN}

        orb_window = day_bars.iloc[:ORB_MINUTES]
        orb_high, orb_low = float(orb_window["high"].max()), float(orb_window["low"].min())
        breakout_entry_idx = None
        if orb_low > 0 and orb_high > orb_low:
            breakout_level = orb_high * (1 + BREAKOUT_BUFFER_BPS / 10_000)
            rest = day_bars.iloc[ORB_MINUTES:]
            pos = rest.index[rest["high"] >= breakout_level]
            if len(pos) and int(pos[0]) + 1 < n:
                breakout_entry_idx = int(pos[0]) + 1

        for i in range(ORB_MINUTES, n - max(ORB_HORIZONS_MIN)):
            # NOTE: named session_id, not session_date - _block_table
            # (study_layer_v2_bootstrap_significance.py) hardcodes the
            # groupby column name to "session_id"; this matches Map+
            # Context's population (from build_symbol_frame) so both
            # populations use the same shared, frozen bootstrap utility
            # without modification. Real bug caught on first run (a
            # KeyError, not a silent wrong result) and fixed here.
            row = {"symbol": symbol, "timestamp": day_bars.loc[i, "timestamp"], "session_id": date,
                   "orb_condition_active": (i == breakout_entry_idx)}
            for h in ORB_HORIZONS_MIN:
                row[f"mae_{h}"] = maes[h].iloc[i]
            rows.append(row)
    return pd.DataFrame(rows)


def run_tail_test(population: pd.DataFrame, condition_col: str, horizons: list[int], label: str) -> dict:
    n_active = int(population[condition_col].sum())
    print(f"\n{'=' * 100}\n{label} - {len(population):,} bars, {n_active:,} with condition active\n{'=' * 100}")
    results = {}
    pvals, keys = [], []
    for h in horizons:
        mae_col = f"mae_{h}"
        sub = population.dropna(subset=[mae_col]).copy()
        for thresh in TAIL_THRESHOLDS_BPS:
            tail_col = "_tail"
            sub[tail_col] = (sub[mae_col] < -thresh / 10_000).astype(float)
            blocks = _block_table(sub, sub[condition_col].astype(bool), ~sub[condition_col].astype(bool),
                                   tail_col, pd.Series(True, index=sub.index))
            blocks = blocks[(blocks["n_a"] > 0) & (blocks["n_b"] > 0)]
            if len(blocks) < 5:
                continue
            point = _point_estimate(blocks)
            boot = block_bootstrap(blocks)
            key = f"h={h}_thresh={thresh}bp"
            r = report(f"  {label} {key}", point, boot)
            results[key] = r
            pvals.append(r["p_value"])
            keys.append(key)

    bh_sig = benjamini_hochberg(pvals) if pvals else []
    print(f"\n  --- FDR-corrected summary ({len(keys)} tests) ---")
    for key, sig in zip(keys, bh_sig):
        results[key]["bh_fdr_significant"] = sig
        # point_estimate is a PROBABILITY difference (a plain fraction),
        # not a price return - x100 for percentage POINTS, never x10000
        # ("bp" convention belongs to price returns elsewhere today; a
        # real bug caught here where the two unit conventions collided).
        pt_pp = results[key]["point_estimate"] * 100
        verdict = "REAL RISK-FILTER EFFECT" if (sig and results[key]["point_estimate"] < 0) else \
                  ("CONDITION INCREASES TAIL RISK (opposite of a filter)" if (sig and results[key]["point_estimate"] > 0) else "NO EFFECT")
        print(f"  {key:20s} P(tail|active)-P(tail|inactive)={pt_pp:+.2f}pp  FDR_sig={sig}  -> {verdict}")

    return {"label": label, "n_bars": len(population), "n_condition_active": n_active,
            "results": {k: {kk: vv for kk, vv in v.items()} for k, v in results.items()}}


def main():
    print("Building ORB per-bar risk-filter population (1-min resolution, new)...")
    orb_pop = pd.concat([orb_population_and_flags(s) for s in SAMPLE_SYMBOLS], ignore_index=True)
    orb_result = run_tail_test(orb_pop, "orb_condition_active", ORB_HORIZONS_MIN, "ORB")

    print("\nBuilding Map+Context per-bar risk-filter population (reuses state-vector control pop)...")
    index_daily_full = _load_index_daily()
    map_pop = pd.concat([map_context_population_with_flags(s, index_daily_full) for s in SAMPLE_SYMBOLS], ignore_index=True)
    map_pop = map_pop[map_pop["state_clean"]]  # same gap-aware eligibility as everything else today
    map_result = run_tail_test(map_pop, "map_condition_active", MAP_HORIZONS_BARS, "Map+Context")

    manifest = {
        "experiment_id": "RISK_FILTER_TEST_20260825", "frozen_2026_08_25": True,
        "tail_thresholds_bps": TAIL_THRESHOLDS_BPS, "symbols": list(SAMPLE_SYMBOLS),
        "orb": orb_result, "map_context": map_result,
    }
    OUT_PATH.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    h = hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()
    OUT_PATH.with_suffix(".json.sha256").write_text(f"{h}  {OUT_PATH.name}\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH.name} (sha256 {h[:16]}...)")


if __name__ == "__main__":
    main()
