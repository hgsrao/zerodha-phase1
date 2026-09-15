"""FROZEN 48-symbol replication of the Momentum DeltaE finding
(2026-08-25, owner-authorized as the closing scientific record for this
research thread).

PURPOSE (owner's own words, verbatim boundary): "Determine whether the
small momentum DeltaE phenomenon is a general market characteristic or
peculiar to the eight-symbol development sample... not an attempt to
rescue the strategy... If you run it now hoping perhaps the other 40
stocks turn 0.2 bp into 25 bp, then we've begun rescuing a failed
hypothesis. I would stop." This script does not search for anything -
it replicates exactly one already-frozen result on a larger sample.

FROZEN, UNCHANGED FROM THE 8-SYMBOL VERSION:
  - same state definitions (study_layer_v2_state_vector.py, untouched)
  - same momentum contrast: M>0, dM<=0 (decelerating) vs M>0, dM>0
    (accelerating)
  - same horizons: +1, +2, +3 bars ONLY (the 3 that survived the
    8-symbol bootstrap+FDR - +6/+12 are NOT retested here, since this is
    replication of an established finding, not a new search)
  - same joint-date block bootstrap (blocks = trading DATE, pooling
    ALL symbols for that date - imported unchanged from
    study_layer_v2_bootstrap_significance.py)
  - same Benjamini-Hochberg FDR method
  - NO threshold search, NO symbol exclusions beyond the same gap-aware
    eligibility already applied uniformly, NO subgroup hunting.

The only change from the 8-symbol version: SAMPLE_SYMBOLS is now the
full 48-symbol V10-C universe instead of the 8-symbol development
sample. Everything else is imported, not reimplemented, to guarantee
"no changes" is actually true rather than merely claimed.

This result does NOT, by itself, change the NO-GO verdict already
reached on the 8-symbol sample (see STUDY_LAYER_V2_RESEARCH_THREAD_
CLOSING_RECORD_20260825.md) - the economic-edge conclusion (0.07-0.20bp
gross vs ~20.58bp round-trip cost) does not depend on whether the
statistical phenomenon is general or sample-specific. This is archived
for the research record, not as a basis for reopening the trading
decision.
"""
from __future__ import annotations

import hashlib
import json

import pandas as pd

from P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825.acquire_v10_history import SYMBOLS
from study_layer_v2_bootstrap_significance import _block_table, benjamini_hochberg, block_bootstrap, report
from study_layer_v2_forward_information_study import ROOT, build_symbol_frame

HORIZONS_TO_REPLICATE = [1, 2, 3]  # the 3 that survived on the 8-symbol sample - not retesting +6/+12
OUT_PATH = ROOT / "STUDY_LAYER_V2_MOMENTUM_REPLICATION_48SYMBOL_FROZEN_20260825.json"


def main():
    print(f"FROZEN replication - {len(SYMBOLS)} symbols (full V10-C universe), "
          f"horizons {HORIZONS_TO_REPLICATE} only, no changes to method.")
    frames = [build_symbol_frame(s) for s in SYMBOLS]
    pooled = pd.concat(frames, ignore_index=True)
    n_dates = pooled["session_id"].nunique()
    print(f"Pooled bar-observations: {len(pooled):,}  |  distinct trading dates (bootstrap blocks): {n_dates:,}")

    m_pos = pooled["momentum"] > 0
    decelerating = m_pos & (pooled["momentum_delta"] <= 0)
    accelerating = m_pos & (pooled["momentum_delta"] > 0)

    results = []
    for h in HORIZONS_TO_REPLICATE:
        eligible = pooled["state_clean"] & pooled[f"label_eligible_{h}"] & m_pos
        blocks = _block_table(pooled, decelerating, accelerating, f"fwd_return_{h}", eligible)
        sum_a, n_a = blocks["sum_a"].sum(), blocks["n_a"].sum()
        sum_b, n_b = blocks["sum_b"].sum(), blocks["n_b"].sum()
        point = float(sum_a / n_a - sum_b / n_b)
        boot = block_bootstrap(blocks)
        results.append(report(f"[48-symbol replication] Momentum DeltaE, horizon +{h} bars", point, boot))

    pvals = [r["p_value"] for r in results]
    bh_significant = benjamini_hochberg(pvals)

    print("\n" + "=" * 100)
    print("REPLICATION SUMMARY (48 symbols) vs. ORIGINAL (8-symbol development sample)")
    print("=" * 100)
    original_8symbol = {1: 0.00334, 2: 0.00370, 3: 0.00374}  # from bootstrap_significance_output_v2.txt, in %
    for r, sig, h in zip(results, bh_significant, HORIZONS_TO_REPLICATE):
        verdict = "REPLICATES (survives FDR)" if sig else "DOES NOT REPLICATE (fails FDR)"
        print(f"  +{h} bars: 8-symbol point={original_8symbol[h]:+.5f}%  |  "
              f"48-symbol point={r['point_estimate']:+.5%}  |  {verdict}  (p={r['p_value']:.4f})")

    manifest = {
        "experiment_id": "STUDY_LAYER_V2_MOMENTUM_REPLICATION_48SYMBOL",
        "frozen_2026_08_25": True,
        "purpose": "Determine whether the momentum DeltaE phenomenon is a general market characteristic or "
                   "peculiar to the 8-symbol development sample - NOT an attempt to rescue the trading-brain "
                   "decision, which was already closed NO-GO on economic-edge grounds independent of this result.",
        "symbols": list(SYMBOLS),
        "n_symbols": len(SYMBOLS),
        "n_bar_observations": int(len(pooled)),
        "n_bootstrap_blocks_trading_dates": int(n_dates),
        "horizons_tested": HORIZONS_TO_REPLICATE,
        "method": "identical to study_layer_v2_bootstrap_significance.py - joint-date block bootstrap "
                  "(blocks = trading date, pooling all symbols), Benjamini-Hochberg FDR across the tested "
                  "horizons, no threshold search, no symbol exclusions, no subgroup hunting.",
        "results": [
            {**{k: v for k, v in r.items() if k != "label"}, "horizon": h, "bh_fdr_significant": bool(sig)}
            for r, sig, h in zip(results, bh_significant, HORIZONS_TO_REPLICATE)
        ],
        "original_8symbol_point_estimates_pct": original_8symbol,
        "conclusion": (
            "Archived for the research record. This result does not reopen the NO-GO verdict on building a "
            "5-minute trading brain from this state vector - that verdict rests on the economic-edge finding "
            "(0.07-0.20bp gross vs ~20.58bp round-trip cost on the 8-symbol sample), which is unaffected by "
            "whether the underlying statistical phenomenon generalizes across the full 48-symbol universe."
        ),
    }
    OUT_PATH.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    manifest_hash = hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()
    (OUT_PATH.with_suffix(".json.sha256")).write_text(f"{manifest_hash}  {OUT_PATH.name}\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH.name} (sha256 {manifest_hash[:16]}...)")


if __name__ == "__main__":
    main()
