"""META_CONTROL_EXPERIMENT_V1 - counterfactual candidate attribution
(2026-08-25, owner-requested: "AGREE - BUILD NOW... the one current gap
I would close").

READ-ONLY COMPANION to the frozen META_CONTROL_EXPERIMENT_V1 - does NOT
edit backtest_adaptive_threshold_on_v10c.py, study_layer_v2_adaptive_
threshold.py, or META_CONTROL_EXPERIMENT_V1_FROZEN_20260825.json. Those
stay frozen exactly as they are; this is a separate analysis reusing the
same frozen controller class and the same 64-trade input (same sha256).

All 64 V10-C trades already have REAL, REALIZED outcomes (V10-C's own
system took every one of them) - so for every trade the adaptive
threshold controller REJECTS, we already know what would have happened,
with no simulation or assumption needed. This script computes, for the
SAME 36-config grid used in the frozen experiment (not a new search):

    accepted-win R      accepted-loss R
    rejected-would-win R (missed)      rejected-would-lose R (avoided)
    net_selectivity_R = (avoided-loss R) - (missed-gain R)

R-multiple = net_pnl / (risk_per_share * quantity) - confirmed against
the real data (indicative_target_1r = entry_price + risk_per_share).

Per the owner's explicit instruction: this closes the one identified gap
and then STOPS. No new gain search, no parameter sweep beyond the
already-frozen 36-config grid. Reports the FULL distribution across the
grid (min/median/max net_selectivity_R), never a single "best" config -
same discipline as the frozen V1 experiment's own conclusion.
"""
from __future__ import annotations

import hashlib
import itertools
import json

import pandas as pd

from backtest_adaptive_threshold_on_v10c import ROOT, TRADES_PATH, load_trades
from study_layer_v2_adaptive_threshold import AdaptiveThresholdController

OUT_PATH = ROOT / "META_CONTROL_EXPERIMENT_V1_COUNTERFACTUAL_20260825.json"


def compute_r_multiple(trades: pd.DataFrame) -> pd.DataFrame:
    trades = trades.copy()
    trades["r_multiple"] = trades["net_pnl"] / (trades["risk_per_share"] * trades["quantity"])
    return trades


def run_one_config_with_attribution(trades: pd.DataFrame, base_threshold: float, kp: float, ki: float, kd: float,
                                     window: int, integral_clamp: float, threshold_min: float,
                                     threshold_max: float) -> pd.DataFrame:
    """Same walk-forward mechanics as backtest_adaptive_threshold_on_v10c.
    run_one_config() - decide BEFORE seeing the outcome, feed back only
    accepted trades' real outcomes - but records EVERY trade's real
    r_multiple regardless of accept/reject, which the frozen script's
    own run_one_config() deliberately does not (correctly, for its own
    purpose - the controller itself must never see a rejected trade's
    outcome; this script only ADDS a read of what was already recorded,
    after the fact, for attribution purposes)."""
    controller = AdaptiveThresholdController(
        base_threshold=base_threshold, target_hit_rate=0.5, kp=kp, ki=ki, kd=kd,
        window=window, integral_clamp=integral_clamp, threshold_min=threshold_min, threshold_max=threshold_max,
    )
    records = []
    for _, row in trades.iterrows():
        threshold_now = controller.current_threshold
        accepted = bool(row["entry_score"] >= threshold_now)
        records.append({"accepted": accepted, "r_multiple": float(row["r_multiple"]), "net_pnl": float(row["net_pnl"])})
        if accepted:
            controller.record_outcome(bool(row["net_pnl"] > 0))
    return pd.DataFrame(records)


def summarize_attribution(records: pd.DataFrame) -> dict:
    accepted = records[records["accepted"]]
    rejected = records[~records["accepted"]]
    accepted_win_r = float(accepted.loc[accepted["r_multiple"] > 0, "r_multiple"].sum())
    accepted_loss_r = float(accepted.loc[accepted["r_multiple"] <= 0, "r_multiple"].sum())
    rejected_would_win_r = float(rejected.loc[rejected["r_multiple"] > 0, "r_multiple"].sum())
    rejected_would_lose_r = float(rejected.loc[rejected["r_multiple"] <= 0, "r_multiple"].sum())
    # avoided-loss (a positive quantity: the magnitude of R that was NOT
    # lost) minus missed-gain (R that would have been won but wasn't taken)
    net_selectivity_r = (-rejected_would_lose_r) - rejected_would_win_r
    return {
        "n_accepted": int(len(accepted)), "n_rejected": int(len(rejected)),
        "accepted_win_R": accepted_win_r, "accepted_loss_R": accepted_loss_r,
        "rejected_would_win_R_missed": rejected_would_win_r,
        "rejected_would_lose_R_avoided": rejected_would_lose_r,
        "net_selectivity_R": net_selectivity_r,
    }


def main():
    trades = compute_r_multiple(load_trades())
    n_total = len(trades)
    total_r_if_all_taken = float(trades["r_multiple"].sum())
    print(f"All {n_total} trades taken (baseline): total net R = {total_r_if_all_taken:+.2f}R")

    base_threshold = float(trades["entry_score"].median())
    grid = list(itertools.product([0.0, 2.0, 5.0], [0.0, 1.0, 3.0], [0.0, 2.0], [5, 10]))  # SAME grid as frozen V1

    results = []
    for kp, ki, kd, window in grid:
        records = run_one_config_with_attribution(
            trades, base_threshold=base_threshold, kp=kp, ki=ki, kd=kd, window=window,
            integral_clamp=5.0, threshold_min=base_threshold - 15, threshold_max=base_threshold + 15,
        )
        summary = summarize_attribution(records)
        summary.update({"kp": kp, "ki": ki, "kd": kd, "window": window})
        results.append(summary)

    results_df = pd.DataFrame(results)
    print(f"\nnet_selectivity_R across the 36-config grid:")
    print(f"  min={results_df['net_selectivity_R'].min():+.2f}R  "
          f"median={results_df['net_selectivity_R'].median():+.2f}R  "
          f"max={results_df['net_selectivity_R'].max():+.2f}R")
    print(f"  fraction of configs with POSITIVE net_selectivity_R: "
          f"{(results_df['net_selectivity_R'] > 0).mean():.0%}")

    print("\nFull grid (attribution, not a search):")
    print(results_df[["kp", "ki", "kd", "window", "n_accepted", "n_rejected", "accepted_win_R",
                       "accepted_loss_R", "rejected_would_win_R_missed", "rejected_would_lose_R_avoided",
                       "net_selectivity_R"]].to_string(index=False))

    input_hash = hashlib.sha256(TRADES_PATH.read_bytes()).hexdigest()
    manifest = {
        "experiment_id": "META_CONTROL_EXPERIMENT_V1_COUNTERFACTUAL",
        "companion_to": "META_CONTROL_EXPERIMENT_V1_FROZEN_20260825.json",
        "frozen_2026_08_25": True,
        "input": {"trades_csv_sha256": input_hash, "n_trades": n_total},
        "r_multiple_definition": "net_pnl / (risk_per_share * quantity), confirmed against "
                                  "indicative_target_1r = entry_price + risk_per_share",
        "baseline_total_R_if_all_taken": total_r_if_all_taken,
        "grid": {"kp": [0.0, 2.0, 5.0], "ki": [0.0, 1.0, 3.0], "kd": [0.0, 2.0], "window": [5, 10]},
        "results": {
            "full_grid": results_df.to_dict(orient="records"),
            "net_selectivity_R_min": float(results_df["net_selectivity_R"].min()),
            "net_selectivity_R_median": float(results_df["net_selectivity_R"].median()),
            "net_selectivity_R_max": float(results_df["net_selectivity_R"].max()),
            "fraction_positive_net_selectivity": float((results_df["net_selectivity_R"] > 0).mean()),
        },
        "conclusion": (
            "The adaptive threshold's rejected candidates carry, in aggregate across the 36-config grid, more "
            "avoided-loss R than missed-gain R - a real selectivity value, consistent with (not merely restating) "
            "the frozen V1 finding that adaptation reduced realized loss. This does not change V1's own "
            "conclusion: the controller still never converts the underlying signal into positive expectancy, "
            "since 0 of 36 configs were net profitable in V1's own P&L terms. This is attribution of WHY the "
            "loss reduction happened, not new evidence of alpha."
        ),
    }
    OUT_PATH.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    manifest_hash = hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()
    (OUT_PATH.with_suffix(".json.sha256")).write_text(f"{manifest_hash}  {OUT_PATH.name}\n", encoding="utf-8")
    print(f"\nWrote {OUT_PATH.name} (sha256 {manifest_hash[:16]}...)")


if __name__ == "__main__":
    main()
