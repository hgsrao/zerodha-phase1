"""Pillar Information Decomposition V1 (2026-08-25, owner-specified
correction to the closing record).

The mistake being corrected: "does this pillar's COMPLETE standalone
trading rule (entry + native stop/target/exit) clear a ~20bp round-trip
cost" conflates three different jobs a sensor could do:

    ALPHA SENSOR       - predicts a large enough favorable move to
                          justify INITIATING a trade on its own.
    EXECUTION SENSOR    - small, reliable effect useful for TIMING a
                          trade already authorized by something else -
                          does not need to clear a full round-trip cost,
                          because it does not create an extra one.
    RISK/FILTER SENSOR  - useful mainly for AVOIDING adverse states,
                          even if "trade every occurrence" loses money.
    NO DEMONSTRATED INFORMATION - none of the above, on this evidence.

This module strips each pillar's native exit rule (stop/target/EOD-
square-off) and asks the strategy-independent question: at the moment
this pillar's ENTRY CONDITION fires, what does the subsequent PRICE PATH
actually look like - forward return, MFE, MAE, and which comes first -
at several horizons? Native stop distance is recorded as a DESCRIPTIVE
variable for diagnosis, never as an R-multiple denominator (that
instability is exactly what forced the correction on Map+Context).

Same rigor as everything else today: joint-date block bootstrap,
Benjamini-Hochberg FDR across every horizon x metric tested together,
gap-aware eligibility (known-bad dates + same-session-only labeling
windows). Reuses, does not reimplement, ORB's and Map+Context's actual
entry-detection logic from today's replays.
"""
from __future__ import annotations

import hashlib
import json
from datetime import time as dtime
from pathlib import Path

import numpy as np
import pandas as pd

from map_context_indicators import (
    MIN_LEVEL_TOUCHES, ReactionState, build_map, classify_index_regime,
    consecutive_directional_days, sizing_guidance,
)
from map_context_historical_replay import _load_daily_universe_asof, _load_index_daily
from map_context_observer import NIFTY_INDEX_PATH  # noqa: F401 (imported for parity/clarity with the replay module)
from orb_historical_replay import BREAKOUT_BUFFER_BPS, ORB_MINUTES
from study_layer_v2_forward_information_study import KNOWN_BAD_DATES, SAMPLE_SYMBOLS, load_symbol_1min
from study_layer_v2_state_vector import aggregate_1min_to_5min_with_quality

ROOT = Path(__file__).parent
OUT_PATH = ROOT / "PILLAR_INFORMATION_DECOMPOSITION_V1_20260825.json"

N_BOOTSTRAP_ITERATIONS = 2000
SEED = 20260825


# ---------------------------------------------------------------------------
# Generic, strategy-independent path decomposition
# ---------------------------------------------------------------------------
def decompose_event(bars: pd.DataFrame, event_idx: int, entry_price: float, horizons: list[int],
                     same_session_only: bool) -> dict | None:
    """One event = one (bar index, entry price) pair. Computes, for each
    horizon (in BARS of whatever resolution `bars` is at):
      fwd_return_h, mfe_h, mae_h (all relative to entry_price)
    plus, over the LONGEST horizon's window: which of MFE/MAE's peak is
    reached first (favorable_first) and how many bars each took. Returns
    None if the event is too close to the end of `bars` (or, if
    same_session_only, to the end of its own session) for the longest
    horizon to be evaluable."""
    max_h = max(horizons)
    session = bars["timestamp"].dt.normalize()
    event_session = session.iloc[event_idx]

    end_idx = event_idx + max_h
    if end_idx >= len(bars):
        return None
    if same_session_only and session.iloc[end_idx] != event_session:
        return None

    result = {}
    window_high = bars["high"].iloc[event_idx + 1: end_idx + 1]
    window_low = bars["low"].iloc[event_idx + 1: end_idx + 1]
    for h in horizons:
        h_end = event_idx + h
        if same_session_only and session.iloc[h_end] != event_session:
            result[f"fwd_return_{h}"] = np.nan
            result[f"mfe_{h}"] = np.nan
            result[f"mae_{h}"] = np.nan
            continue
        fwd_close = bars["close"].iloc[h_end]
        result[f"fwd_return_{h}"] = (fwd_close - entry_price) / entry_price
        sub_high = bars["high"].iloc[event_idx + 1: h_end + 1]
        sub_low = bars["low"].iloc[event_idx + 1: h_end + 1]
        mfe = (sub_high.max() - entry_price) / entry_price if len(sub_high) else np.nan
        mae = (sub_low.min() - entry_price) / entry_price if len(sub_low) else np.nan
        result[f"mfe_{h}"] = mfe
        result[f"mae_{h}"] = mae
        # Owner-caught flaw (2026-08-25): testing MFE against zero in
        # isolation is the wrong question - growing MFE/MAE with horizon
        # is exactly what ordinary volatility looks like too, signal or
        # no signal. The real test is ASYMMETRY: does the favorable
        # excursion exceed the adverse one? mae is already signed
        # negative, so mfe + mae is directly "how much MFE exceeds |MAE|"
        # - bootstrapped per-event below, not eyeballed from two separate
        # point estimates.
        result[f"asymmetry_{h}"] = mfe + mae if not (np.isnan(mfe) or np.isnan(mae)) else np.nan

    # favorable-vs-adverse race, over the LONGEST horizon's window
    if len(window_high) and len(window_low):
        mfe_level = window_high.max()
        mae_level = window_low.min()
        mfe_pos = int(np.argmax(window_high.to_numpy() >= mfe_level))
        mae_pos = int(np.argmax(window_low.to_numpy() <= mae_level))
        result["favorable_reached_first"] = bool(mfe_pos <= mae_pos)
        result["bars_to_mfe"] = mfe_pos + 1
        result["bars_to_mae"] = mae_pos + 1
    else:
        result["favorable_reached_first"] = None
        result["bars_to_mfe"] = None
        result["bars_to_mae"] = None

    return result


def block_bootstrap_mean(by_date: pd.DataFrame, n_iterations: int = N_BOOTSTRAP_ITERATIONS, seed: int = SEED) -> dict:
    sums, counts = by_date["sum"].to_numpy(), by_date["count"].to_numpy()
    n_blocks = len(by_date)
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


def benjamini_hochberg(pvals: list, alpha: float = 0.05) -> list:
    pvals = np.asarray(pvals)
    m = len(pvals)
    order = np.argsort(pvals)
    ranked = pvals[order]
    thresholds = (np.arange(1, m + 1) / m) * alpha
    passed = ranked <= thresholds
    significant = np.zeros(m, dtype=bool)
    if passed.any():
        cutoff = np.max(np.where(passed)[0])
        significant[order[: cutoff + 1]] = True
    return significant.tolist()


def classify(point: float, excludes_zero_after_fdr: bool, cost_bps: float) -> str:
    """First-cut classification rule, disclosed: ALPHA needs the point
    estimate to clear the real round-trip cost by a comfortable margin
    (>50% of cost, an arbitrary-but-stated bar - not optimized);
    EXECUTION-SCALE means real (survives FDR) but well under the cost
    threshold, i.e. too small to justify a trade on its own but large
    enough to matter as a timing nudge on a trade already happening;
    NO-INFO means it doesn't survive FDR at all."""
    point_bps = point * 10_000
    if not excludes_zero_after_fdr:
        return "NO_DEMONSTRATED_INFORMATION"
    if point_bps > 0.5 * cost_bps:
        return "CANDIDATE_ALPHA (survives FDR AND clears >50% of round-trip cost)"
    return "CANDIDATE_EXECUTION_SCALE (survives FDR but well under round-trip cost)"


# ---------------------------------------------------------------------------
# ORB decomposition - event = breakout confirmation, 1-min bars, native
# exit (stop/target/EOD) stripped entirely.
# ---------------------------------------------------------------------------
def orb_events_for_symbol(symbol: str) -> list[dict]:
    df = load_symbol_1min(symbol)
    df["session_date"] = df["timestamp"].dt.normalize()
    horizons = [15, 30, 60, 120]  # minutes post-breakout, native to ORB's own intraday character
    events = []
    for date, day_bars in df.groupby("session_date"):
        if date.date() in KNOWN_BAD_DATES:
            continue
        day_bars = day_bars.reset_index(drop=True)
        if len(day_bars) < ORB_MINUTES + max(horizons) + 2:
            continue
        orb_window = day_bars.iloc[:ORB_MINUTES]
        orb_high, orb_low = float(orb_window["high"].max()), float(orb_window["low"].min())
        if orb_low <= 0 or orb_high <= orb_low:
            continue
        breakout_level = orb_high * (1 + BREAKOUT_BUFFER_BPS / 10_000)
        rest = day_bars.iloc[ORB_MINUTES:]
        pos = rest.index[rest["high"] >= breakout_level]
        if len(pos) == 0:
            continue
        breakout_idx = int(pos[0])
        if breakout_idx + 1 >= len(day_bars):
            continue
        entry_price = float(day_bars.loc[breakout_idx + 1, "open"])
        outcome = decompose_event(day_bars, breakout_idx + 1, entry_price, horizons, same_session_only=True)
        if outcome is None:
            continue
        outcome.update({"symbol": symbol, "session_date": date, "entry_price": entry_price})
        events.append(outcome)
    return events


# ---------------------------------------------------------------------------
# Map+Context decomposition - event = reaction ENTRY (test+reclaim),
# 5-min bars, native exit (stop/target/EOD) stripped entirely.
# ---------------------------------------------------------------------------
def map_context_events_for_symbol(symbol: str, index_daily_full: pd.DataFrame) -> list[dict]:
    df_1min = load_symbol_1min(symbol)
    bars5 = aggregate_1min_to_5min_with_quality(df_1min)
    bars5 = bars5.sort_values("timestamp").reset_index(drop=True)
    bars5 = bars5[~bars5["timestamp"].dt.normalize().dt.date.isin(KNOWN_BAD_DATES)].reset_index(drop=True)
    horizons = [1, 2, 3, 6, 12]  # 5/10/15/30/60 min - same convention as the state-vector forward study

    reaction = ReactionState()
    current_day, daily_map, index_regime = None, None, None
    events = []

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
        pending_tested_level = reaction.tested_level
        result = reaction.update(float(row["low"]), close, support)
        if result != "ENTRY":
            continue

        # NOTE: sizing_guidance's PASS filter is deliberately NOT applied
        # here - this decomposition asks "what does price do after every
        # reclaim event", the raw entry signal, independent of the
        # context/sizing gate too, so the gate's own contribution can
        # later be assessed separately if wanted.
        outcome = decompose_event(bars5, i, close, horizons, same_session_only=False)
        if outcome is None:
            continue
        outcome.update({"symbol": symbol, "session_date": day, "entry_price": close,
                         "tested_level": pending_tested_level})
        events.append(outcome)

    return events


def analyze(events: list[dict], horizons: list[int], label: str, cost_bps: float) -> dict:
    df = pd.DataFrame(events)
    n = len(df)
    print(f"\n{'=' * 100}\n{label} - {n} events\n{'=' * 100}")
    if n == 0:
        return {"label": label, "n_events": 0}

    # DIRECTIONAL family (the only metrics that actually test for
    # exploitable edge) - subject to FDR and the ALPHA/EXECUTION/NO-INFO
    # classifier. fwd_return alone would miss asymmetric-but-flat-net
    # cases; asymmetry_h = mfe_h + mae_h directly tests whether the
    # favorable excursion exceeds the adverse one - the corrected
    # question, replacing the flawed "is MFE alone nonzero" test.
    directional_metrics = [f"fwd_return_{h}" for h in horizons] + [f"asymmetry_{h}" for h in horizons]
    # DESCRIPTIVE ONLY - large and "significant" under pure noise/
    # volatility too (a bigger MFE/MAE at a longer horizon says nothing
    # about direction by itself). Reported for diagnostic/risk-sizing
    # context (e.g. stop-distance calibration), explicitly NOT run
    # through the alpha classifier and NOT part of the FDR family.
    descriptive_metrics = [f"mfe_{h}" for h in horizons] + [f"mae_{h}" for h in horizons]

    results = {}
    pvals, keys = [], []
    for metric in directional_metrics:
        sub = df.dropna(subset=[metric])
        if len(sub) == 0:
            continue
        by_date = sub.groupby("session_date")[metric].agg(["sum", "count"]).reset_index()
        if len(by_date) < 5:
            continue
        boot = block_bootstrap_mean(by_date)
        results[metric] = boot
        pvals.append(boot["p_value"])
        keys.append(metric)

    bh_sig = benjamini_hochberg(pvals) if pvals else []
    print("  --- DIRECTIONAL (FDR-corrected family, {} tests) ---".format(len(keys)))
    for key, sig in zip(keys, bh_sig):
        results[key]["bh_fdr_significant"] = sig
        results[key]["classification"] = classify(results[key]["point"], sig, cost_bps)
        pt_bps = results[key]["point"] * 10_000
        print(f"  {key:16s} point={pt_bps:+7.3f}bp  CI=[{results[key]['ci_low']*10000:+7.3f}, "
              f"{results[key]['ci_high']*10000:+7.3f}]bp  p={results[key]['p_value']:.4f}  "
              f"FDR_sig={sig}  -> {results[key]['classification']}")

    print("  --- DESCRIPTIVE ONLY (movement magnitude, NOT a directional test, NOT FDR-corrected) ---")
    for metric in descriptive_metrics:
        sub = df.dropna(subset=[metric])
        if len(sub) == 0:
            continue
        by_date = sub.groupby("session_date")[metric].agg(["sum", "count"]).reset_index()
        if len(by_date) < 5:
            continue
        boot = block_bootstrap_mean(by_date)
        results[metric] = boot
        pt_bps = boot["point"] * 10_000
        print(f"  {metric:16s} point={pt_bps:+7.3f}bp  CI=[{boot['ci_low']*10000:+7.3f}, {boot['ci_high']*10000:+7.3f}]bp")

    fav_first_rate = float(df["favorable_reached_first"].dropna().mean()) if "favorable_reached_first" in df else None
    print(f"  P(favorable excursion reached before adverse): {fav_first_rate:.1%}  "
          f"(50% = coin flip = no directional edge)" if fav_first_rate is not None else "")

    return {"label": label, "n_events": n, "metrics": results, "favorable_reached_first_rate": fav_first_rate}


def main():
    cost_bps = 20.58  # same total round-trip figure used throughout today (statutory + slippage stress)

    print("Extracting ORB breakout events (native exit stripped)...")
    orb_events = []
    for s in SAMPLE_SYMBOLS:
        orb_events.extend(orb_events_for_symbol(s))
    orb_result = analyze(orb_events, [15, 30, 60, 120], "ORB - path decomposition (native exit stripped)", cost_bps)

    print("\nExtracting Map+Context reaction-ENTRY events (native exit stripped)...")
    index_daily_full = _load_index_daily()
    map_events = []
    for s in SAMPLE_SYMBOLS:
        map_events.extend(map_context_events_for_symbol(s, index_daily_full))
    map_result = analyze(map_events, [1, 2, 3, 6, 12], "Map+Context - path decomposition (native exit stripped)", cost_bps)

    manifest = {
        "experiment_id": "PILLAR_INFORMATION_DECOMPOSITION_V1_20260825", "frozen_2026_08_25": True,
        "cost_bps_reference": cost_bps,
        "symbols": list(SAMPLE_SYMBOLS),
        "orb": orb_result, "map_context": map_result,
    }
    OUT_PATH.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    h = hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()
    OUT_PATH.with_suffix(".json.sha256").write_text(f"{h}  {OUT_PATH.name}\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH.name} (sha256 {h[:16]}...)")


if __name__ == "__main__":
    main()
