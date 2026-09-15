"""Study Layer V2 empirical check (2026-08-25).

Read-only analysis script. No Kite/network calls, nothing written into
any live engine's state - this only reads the already-acquired,
hash-verified V10-C 1-minute archive under
P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824/
and reports numbers. Answers two questions with real data instead of
argument:

  1. How much does chart_studies_indicators.ichimoku() (the disabled-
     shift, same-bar read actually running live) differ from
     study_layer_v2_indicators.ichimoku_projected() (the genuinely
     projective read)? Also checks a specific claim found in
     chart_studies_indicators.ichimoku()'s own comment while reading it
     for this task: "shifting here would place NaN at the most recent
     bars" - which, per today's own corrected math (see
     study_layer_v2_indicators.py's docstring), is backwards. The shift
     costs the EARLIEST bars, not the most recent ones. This script
     checks whether that claim holds up against real accumulated
     multi-day history.

  2. How correlated (redundant) are Bollinger and session-VWAP reads in
     real data, restricted to bars where both have a real opinion - the
     question the owner raised directly: "giving weightage... has no
     meaning" if two votes are really one piece of evidence counted
     twice. Also reports the same pairwise matrix against SMI and both
     Ichimoku variants for a fuller picture, since it's the same
     computation.

Run: python study_layer_v2_empirical_check.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import chart_studies_indicators as old_ind
import study_layer_v2_indicators as v2

ROOT = Path(__file__).parent
ARCHIVE_DIR = ROOT / "P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825" / "DATA_1MIN_48_20230703_20260824"

# A spread of symbols already flagged as live-watched (BAJFINANCE, SBIN,
# SUNPHARMA - the three named as overlapping between Chart Studies
# Monitor and Map+Context) plus five large, liquid names for breadth.
SAMPLE_SYMBOLS = ["BAJFINANCE", "SBIN", "SUNPHARMA", "RELIANCE", "INFY", "TCS", "HDFCBANK", "ICICIBANK"]

ANCHOR_TS = pd.Timestamp("2023-07-03")  # arbitrary, fixed anchor for anchored_vwap - not under test here


def _load_5min(symbol: str) -> pd.DataFrame:
    path = ARCHIVE_DIR / f"NSE_{symbol}_minute_2023-07-03_2026-08-24.csv"
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    df = df.set_index("timestamp").sort_index()
    agg = df.resample("5min", label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna(subset=["open"])
    agg = agg.reset_index()
    agg["TradingDay"] = agg["timestamp"].dt.normalize()
    return agg


def _old_ichimoku_reads(bars5: pd.DataFrame) -> pd.Series:
    computed = old_ind.ichimoku(bars5)
    return computed.apply(lambda row: old_ind.classify_row(row)["ichimoku"], axis=1)


def _new_ichimoku_reads(bars5: pd.DataFrame) -> pd.Series:
    computed = v2.ichimoku_projected(bars5)
    return computed.apply(v2.classify_ichimoku_projected, axis=1)


def analyze_symbol(symbol: str) -> dict:
    bars5 = _load_5min(symbol)
    n_bars = len(bars5)

    old_reads = _old_ichimoku_reads(bars5)
    new_reads = _new_ichimoku_reads(bars5)
    bb_reads = old_ind.bollinger_bands(bars5).apply(
        lambda row: NEUTRAL if pd.isna(row.get("BB_Basis")) else (BULLISH if row["close"] > row["BB_Basis"] else BEARISH),
        axis=1,
    )
    vwap_reads = old_ind.session_vwap(bars5).apply(
        lambda row: NEUTRAL if pd.isna(row.get("VWAP")) else (BULLISH if row["close"] > row["VWAP"] else BEARISH),
        axis=1,
    )
    smi_computed = old_ind.stochastic_momentum_index(bars5)
    smi_reads = smi_computed.apply(
        lambda row: NEUTRAL if pd.isna(row.get("SMI")) or pd.isna(row.get("SMI_Signal"))
        else (BULLISH if row["SMI"] > row["SMI_Signal"] else BEARISH),
        axis=1,
    )

    # --- Question 1: old (same-bar) vs new (genuinely projected) Ichimoku ---
    old_opinion_rate = float((old_reads != NEUTRAL).mean())
    new_opinion_rate = float((new_reads != NEUTRAL).mean())
    both_opinionated = (old_reads != NEUTRAL) & (new_reads != NEUTRAL)
    n_both = int(both_opinionated.sum())
    disagree_when_both_opinionated = (
        float((old_reads[both_opinionated] != new_reads[both_opinionated]).mean()) if n_both else None
    )
    # The specific claim in chart_studies_indicators.ichimoku()'s comment:
    # "shifting here would place NaN at the most recent bars". Check the
    # LAST 20 bars of this symbol's whole multi-year history - the most
    # "recent" bars there are - for whether the NEW projected read is
    # actually available there (it should be, given ~78 bars of warmup
    # is trivial against years of accumulated history).
    recent_new_opinion_rate = float((new_reads.tail(20) != NEUTRAL).mean())

    # --- Question 2: pairwise agreement matrix ---
    reads_by_study = {
        "ichimoku_old_samebar": old_reads,
        "ichimoku_new_projected": new_reads,
        "bollinger": bb_reads,
        "vwap": vwap_reads,
        "smi": smi_reads,
    }
    pairwise = {}
    names = list(reads_by_study.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            pairwise[f"{a} vs {b}"] = v2.pairwise_agreement_rate(reads_by_study[a], reads_by_study[b])

    return {
        "symbol": symbol,
        "n_5min_bars": n_bars,
        "old_ichimoku_opinion_rate": round(old_opinion_rate, 4),
        "new_ichimoku_opinion_rate": round(new_opinion_rate, 4),
        "new_ichimoku_opinion_rate_last_20_bars": round(recent_new_opinion_rate, 4),
        "n_bars_both_ichimoku_opinionated": n_both,
        "old_vs_new_ichimoku_disagreement_rate": (
            round(disagree_when_both_opinionated, 4) if disagree_when_both_opinionated is not None else None
        ),
        "pairwise_agreement": pairwise,
    }


BULLISH, BEARISH, NEUTRAL = old_ind.BULLISH, old_ind.BEARISH, old_ind.NEUTRAL


def main():
    results = [analyze_symbol(s) for s in SAMPLE_SYMBOLS]

    print("=" * 100)
    print("STUDY LAYER V2 EMPIRICAL CHECK - 2026-08-25 - real V10-C 1-min archive resampled to 5-min")
    print("=" * 100)
    for r in results:
        print(f"\n--- {r['symbol']} ({r['n_5min_bars']:,} 5-min bars) ---")
        print(f"  Ichimoku opinion rate  OLD (same-bar, live today) : {r['old_ichimoku_opinion_rate']:.1%}")
        print(f"  Ichimoku opinion rate  NEW (genuinely projected)  : {r['new_ichimoku_opinion_rate']:.1%}")
        print(f"  NEW opinion rate, last 20 bars of the whole series: {r['new_ichimoku_opinion_rate_last_20_bars']:.1%}"
              f"  <- tests the comment's claim that shifting starves the MOST RECENT bars")
        print(f"  OLD vs NEW disagreement (when both have an opinion, n={r['n_bars_both_ichimoku_opinionated']:,}): "
              f"{r['old_vs_new_ichimoku_disagreement_rate']}")
        print("  Pairwise agreement (bars where both opinionated):")
        for pair, stat in r["pairwise_agreement"].items():
            n, rate = stat["n_bars_both_opinionated"], stat["agreement_rate"]
            rate_str = f"{rate:.1%}" if rate is not None else "n/a"
            print(f"    {pair:42s} n={n:>7,}  agreement={rate_str}")

    # --- Aggregate across the sample ---
    print("\n" + "=" * 100)
    print("AGGREGATE ACROSS SAMPLE")
    print("=" * 100)
    avg_recent_new_opinion = sum(r["new_ichimoku_opinion_rate_last_20_bars"] for r in results) / len(results)
    print(f"Average NEW-projected Ichimoku opinion rate over each symbol's most recent 20 bars: {avg_recent_new_opinion:.1%}")
    disagreements = [r["old_vs_new_ichimoku_disagreement_rate"] for r in results if r["old_vs_new_ichimoku_disagreement_rate"] is not None]
    print(f"Average OLD-vs-NEW Ichimoku disagreement rate: {sum(disagreements) / len(disagreements):.1%}")

    bb_vwap = [r["pairwise_agreement"]["bollinger vs vwap"]["agreement_rate"] for r in results
               if r["pairwise_agreement"]["bollinger vs vwap"]["agreement_rate"] is not None]
    print(f"Average Bollinger-vs-VWAP agreement rate: {sum(bb_vwap) / len(bb_vwap):.1%}  (50% ~ independent, 100% ~ fully redundant)")


if __name__ == "__main__":
    main()
