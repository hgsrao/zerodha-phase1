"""Session-block bootstrap significance test (2026-08-25, owner-specified
Step 1 of the post-forward-study order: bootstrap -> cost overlay -> only
then 48-symbol replication).

Answers: do the three findings from study_layer_v2_forward_information_
study.py survive once we stop pretending 465,831 pooled bars are
independent observations? Reuses that script's own build_symbol_frame()
(same 8-symbol sample, same gap-aware eligibility, same state vector) -
does not recompute or redefine anything about how the state or forward
labels are built. NOT a new discovery pass - testing exactly the three
contrasts already found, nothing else, per the owner's explicit
instruction not to let a larger dataset make a tiny irrelevant effect
look impressive.

METHOD (CORRECTED, 2026-08-25, round 2 - owner caught a real flaw in the
first version): block bootstrap by TRADING DATE, JOINTLY across all 8
symbols - each block is one calendar date's entire cross-section (all 8
symbols' full sessions that day, ~600 bars), resampled AS ONE WHOLE UNIT
with replacement. The FIRST version of this script blocked by (symbol,
date) instead - resampling each symbol-day independently - which still
overstates effective sample size, since all 8 symbols can respond to the
same NIFTY/macro session on a given date; that shared-day dependence
only gets preserved if a resampled unit pulls in every symbol's data for
that date together, never one symbol's day in isolation from the others.
Confirmed and fixed here: blocks are now keyed by session_id ALONE.

MULTIPLE COMPARISONS (added round 2): 7 contrasts were examined (5
momentum horizons + Structure + Bollinger) - ordinary per-test 95% CIs
do not give the family a 5% overall false-positive rate. A two-sided
bootstrap p-value is computed for each contrast (p = 2*min(P(boot<=0),
P(boot>=0)), floored at 1/n_iterations to avoid a literal zero), then
Benjamini-Hochberg FDR is applied across all 7 - reported alongside,
not instead of, the raw per-test CIs.

For efficiency (thousands of iterations over hundreds of blocks, not
raw bars), each block is pre-reduced to the sums/counts a given
statistic needs BEFORE bootstrapping - so a bootstrap iteration
resamples small per-block summary rows, not full bar-level data.

Targets tested (the three findings from the forward study, chosen
because THOSE are what was found - not a new fishing expedition):
  1. Momentum: DeltaE = E[R | M>0, dM<=0] - E[R | M>0, dM>0], at every
     horizon - the owner's specifically requested statistic.
  2. Structure: extreme-bucket difference at +12 bars (the one horizon
     that showed a directional pattern; short horizons showed none
     worth testing).
  3. VWAP-conditional-Bollinger: within the most-bullish VWAP bucket,
     E[R | L_bb>0] - E[R | L_bb<=0] at +6 bars (where the largest gap
     appeared in the original study).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from study_layer_v2_forward_information_study import HORIZONS, SAMPLE_SYMBOLS, build_symbol_frame

N_ITERATIONS = 2000
SEED = 20260825


def _block_table(pooled: pd.DataFrame, group_a_mask: pd.Series, group_b_mask: pd.Series, value_col: str,
                  eligible_mask: pd.Series) -> pd.DataFrame:
    """Reduces the pooled bar-level frame to one row per TRADING DATE
    (session_id ALONE - not (symbol, session_id)), pooling every
    symbol's bars for that date into the same block, with the sum/count
    needed for group A and group B of a mean-difference statistic. This
    is the corrected joint-date grouping - see the module docstring for
    why (symbol, session_id) blocking (the first version of this
    script) understated cross-symbol same-day dependence."""
    df = pooled[eligible_mask].copy()
    df["_a"] = group_a_mask[eligible_mask]
    df["_b"] = group_b_mask[eligible_mask]
    df["_val_a"] = np.where(df["_a"], df[value_col], np.nan)
    df["_val_b"] = np.where(df["_b"], df[value_col], np.nan)

    blocks = df.groupby("session_id").agg(
        sum_a=("_val_a", "sum"), n_a=("_a", "sum"),
        sum_b=("_val_b", "sum"), n_b=("_b", "sum"),
    ).reset_index()
    return blocks


def _point_estimate(blocks: pd.DataFrame) -> float:
    sum_a, n_a = blocks["sum_a"].sum(), blocks["n_a"].sum()
    sum_b, n_b = blocks["sum_b"].sum(), blocks["n_b"].sum()
    if n_a == 0 or n_b == 0:
        return float("nan")
    return float(sum_a / n_a - sum_b / n_b)


def block_bootstrap(blocks: pd.DataFrame, n_iterations: int = N_ITERATIONS, seed: int = SEED) -> np.ndarray:
    rng = np.random.RandomState(seed)
    n_blocks = len(blocks)
    sum_a, n_a, sum_b, n_b = (blocks["sum_a"].to_numpy(), blocks["n_a"].to_numpy(),
                               blocks["sum_b"].to_numpy(), blocks["n_b"].to_numpy())
    stats = np.empty(n_iterations)
    for i in range(n_iterations):
        idx = rng.randint(0, n_blocks, size=n_blocks)
        rs_sum_a, rs_n_a = sum_a[idx].sum(), n_a[idx].sum()
        rs_sum_b, rs_n_b = sum_b[idx].sum(), n_b[idx].sum()
        stats[i] = (rs_sum_a / rs_n_a - rs_sum_b / rs_n_b) if rs_n_a > 0 and rs_n_b > 0 else np.nan
    return stats


def bootstrap_p_value(boot_stats: np.ndarray) -> float:
    """Two-sided bootstrap p-value for H0: true effect = 0, from the
    empirical bootstrap distribution: p = 2 * min(P(boot<=0), P(boot>=0)).
    Floored at 1/n_iterations - a percentile bootstrap cannot honestly
    report a literal p=0 with a finite number of draws."""
    boot_stats = boot_stats[~np.isnan(boot_stats)]
    n = len(boot_stats)
    p_le = float((boot_stats <= 0).mean())
    p_ge = float((boot_stats >= 0).mean())
    p = 2 * min(p_le, p_ge)
    return max(min(p, 1.0), 1.0 / n)


def benjamini_hochberg(pvals: list) -> list:
    """Standard BH step-up FDR procedure. Returns a boolean list, same
    order as `pvals`, of which tests remain significant at alpha=0.05
    after correcting for testing this whole family together (not each
    contrast in isolation)."""
    alpha = 0.05
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


def report(label: str, point_estimate: float, boot_stats: np.ndarray) -> dict:
    boot_stats_clean = boot_stats[~np.isnan(boot_stats)]
    ci_low, ci_high = np.percentile(boot_stats_clean, [2.5, 97.5])
    frac_same_sign_as_point = float((np.sign(boot_stats_clean) == np.sign(point_estimate)).mean()) if point_estimate != 0 else float("nan")
    excludes_zero = (ci_low > 0) or (ci_high < 0)
    p_value = bootstrap_p_value(boot_stats_clean)
    print(f"\n--- {label} ---")
    print(f"  point estimate: {point_estimate:+.5%}")
    print(f"  95% block-bootstrap CI: [{ci_low:+.5%}, {ci_high:+.5%}]  (n_iterations={len(boot_stats_clean)})")
    print(f"  fraction of bootstrap draws with same sign as point estimate: {frac_same_sign_as_point:.1%}")
    print(f"  95% CI excludes zero: {excludes_zero}")
    print(f"  two-sided bootstrap p-value: {p_value:.4f}")
    return {
        "label": label, "point_estimate": point_estimate, "ci_low": float(ci_low), "ci_high": float(ci_high),
        "frac_same_sign": frac_same_sign_as_point, "ci_excludes_zero": bool(excludes_zero), "p_value": p_value,
    }


def main():
    print("Building per-symbol frames (same 8-symbol sample as the forward-information study)...")
    frames = [build_symbol_frame(s) for s in SAMPLE_SYMBOLS]
    pooled = pd.concat(frames, ignore_index=True)
    n_dates = pooled["session_id"].nunique()
    print(f"Pooled bar-observations: {len(pooled):,}  |  distinct TRADING DATES (bootstrap blocks): {n_dates:,}  "
          f"|  each block pools all {len(SAMPLE_SYMBOLS)} symbols' bars for that date")

    results = []

    # --- 1. Momentum DeltaE, every horizon (owner's specifically requested statistic) ---
    print("\n" + "=" * 100)
    print("1. MOMENTUM: DeltaE = E[R | M>0, dM<=0] - E[R | M>0, dM>0]")
    print("=" * 100)
    m_pos = pooled["momentum"] > 0
    decelerating = m_pos & (pooled["momentum_delta"] <= 0)
    accelerating = m_pos & (pooled["momentum_delta"] > 0)
    for h in HORIZONS:
        eligible = pooled["state_clean"] & pooled[f"label_eligible_{h}"] & m_pos
        blocks = _block_table(pooled, decelerating, accelerating, f"fwd_return_{h}", eligible)
        point = _point_estimate(blocks)
        boot = block_bootstrap(blocks)
        results.append(report(f"Momentum DeltaE, horizon +{h} bars", point, boot))

    # --- 2. Structure extreme-bucket difference at +12 bars ---
    print("\n" + "=" * 100)
    print("2. STRUCTURE: E[R | S in [+0.6,1.0]] - E[R | S in [-1.0,-0.6)], horizon +12 bars")
    print("=" * 100)
    h = 12
    bullish_extreme = pooled["structure"] >= 0.6
    bearish_extreme = pooled["structure"] < -0.6
    eligible = pooled["state_clean"] & pooled[f"label_eligible_{h}"]
    blocks = _block_table(pooled, bullish_extreme, bearish_extreme, f"fwd_return_{h}", eligible)
    point = _point_estimate(blocks)
    boot = block_bootstrap(blocks)
    results.append(report(f"Structure extreme-bucket difference, horizon +{h} bars", point, boot))

    # --- 3. VWAP-conditional-Bollinger, most-bullish VWAP bucket, +6 bars ---
    print("\n" + "=" * 100)
    print("3. VWAP+BOLLINGER: within most-bullish L_vwap bucket, E[R|L_bb>0] - E[R|L_bb<=0], horizon +6 bars")
    print("=" * 100)
    h = 6
    vwap_bullish = pooled["location_vwap"] >= 0.6
    bb_positive = vwap_bullish & (pooled["location_bb"] > 0)
    bb_nonpositive = vwap_bullish & (pooled["location_bb"] <= 0)
    eligible = pooled["state_clean"] & pooled[f"label_eligible_{h}"] & vwap_bullish
    blocks = _block_table(pooled, bb_positive, bb_nonpositive, f"fwd_return_{h}", eligible)
    point = _point_estimate(blocks)
    boot = block_bootstrap(blocks)
    results.append(report(f"VWAP-conditional Bollinger split, horizon +{h} bars", point, boot))

    print("\n" + "=" * 100)
    print("SUMMARY - raw per-test 95% CI vs. Benjamini-Hochberg FDR across all 7 contrasts")
    print("=" * 100)
    pvals = [r["p_value"] for r in results]
    bh_significant = benjamini_hochberg(pvals)
    for r, sig in zip(results, bh_significant):
        raw_verdict = "SURVIVES" if r["ci_excludes_zero"] else "DOES NOT SURVIVE"
        bh_verdict = "SURVIVES FDR" if sig else "FAILS FDR"
        print(f"  [{raw_verdict:17s} | {bh_verdict:12s}] {r['label']}: {r['point_estimate']:+.5%}  "
              f"CI [{r['ci_low']:+.5%}, {r['ci_high']:+.5%}]  p={r['p_value']:.4f}")


if __name__ == "__main__":
    main()
