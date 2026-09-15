"""Walk-forward backtest: would an adaptive PID-style entry-threshold
controller have helped the frozen V10-C historical replay? (2026-08-25)

Reuses P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825's own completed,
frozen output (v10c_history_trades.csv - 64 trades, real 3-year replay,
already hash-manifested) - no new data acquisition, no live risk, no
edits to that sandbox. Read-only.

Method (strict no-lookahead, mirroring the walk-forward discipline this
session has used throughout):
  1. Sort the 64 trades chronologically by entry_ts (they already are,
     but don't assume it).
  2. Walk through them one at a time. Before each trade, ask the
     controller (built from ONLY the outcomes of trades already closed
     as of this point) whether this trade's entry_score clears the
     CURRENT adaptive threshold.
  3. If yes: the trade is "controller-accepted" - tally its real net_pnl
     (the actual historical outcome, unchanged), then feed win/loss back
     into the controller via record_outcome().
  4. If no: the trade is "controller-rejected" - excluded from the
     controller-gated P&L, and (correctly) NOT fed back into the
     controller, since we don't know what would have happened to a trade
     that was never taken - only real, realized outcomes are allowed to
     inform the loop.

Compares against the ORIGINAL baseline (every trade taken, exactly as
V10-C's replay already reported: -Rs.5,292.97 net, 12.50% win rate).

HONESTY CONSTRAINT: 64 trades is a thin sample. This runs a small GRID of
gain combinations (not one hand-picked "best" set) and reports the
DISTRIBUTION of outcomes across the grid, explicitly flagging that a
single winning gain combination out of many tried would be cherry-
picking, not evidence. This script draws a directional conclusion
(does the mechanism plausibly help, and how sensitive is it to gains),
not a final tuned system.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import pandas as pd

from study_layer_v2_adaptive_threshold import AdaptiveThresholdController

ROOT = Path(__file__).parent
TRADES_PATH = (
    ROOT / "P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825" / "outputs"
    / "V10C_POST_HOC_HISTORY_20230814_20260824_RUN1" / "v10c_history_trades.csv"
)


def load_trades() -> pd.DataFrame:
    df = pd.read_csv(TRADES_PATH)
    df["entry_ts"] = pd.to_datetime(df["entry_ts"])
    df = df.sort_values("entry_ts").reset_index(drop=True)
    df["won"] = df["net_pnl"] > 0
    return df


def run_one_config(trades: pd.DataFrame, base_threshold: float, kp: float, ki: float, kd: float,
                    window: int, integral_clamp: float, threshold_min: float, threshold_max: float) -> dict:
    controller = AdaptiveThresholdController(
        base_threshold=base_threshold, target_hit_rate=0.5, kp=kp, ki=ki, kd=kd,
        window=window, integral_clamp=integral_clamp, threshold_min=threshold_min, threshold_max=threshold_max,
    )
    accepted_rows = []
    threshold_trace = []
    for _, row in trades.iterrows():
        threshold_now = controller.current_threshold  # decided BEFORE seeing this trade's outcome
        threshold_trace.append(threshold_now)
        if row["entry_score"] >= threshold_now:
            accepted_rows.append(row)
            controller.record_outcome(bool(row["won"]))
        # rejected trades: no feedback, no P&L counted - never happened.

    accepted = pd.DataFrame(accepted_rows)
    n_accepted = len(accepted)
    if n_accepted == 0:
        return {"n_accepted": 0, "net_pnl": 0.0, "win_rate": None, "avg_threshold": None}
    return {
        "n_accepted": n_accepted,
        "net_pnl": float(accepted["net_pnl"].sum()),
        "win_rate": float(accepted["won"].mean()),
        "avg_threshold": float(sum(threshold_trace) / len(threshold_trace)),
    }


def main():
    trades = load_trades()
    n_total = len(trades)
    baseline_net_pnl = float(trades["net_pnl"].sum())
    baseline_win_rate = float(trades["won"].mean())
    print("=" * 100)
    print(f"BASELINE (V10-C replay as originally run, all {n_total} trades taken):")
    print(f"  net P&L = Rs.{baseline_net_pnl:,.2f}   win rate = {baseline_win_rate:.1%}")
    print("=" * 100)

    entry_scores = trades["entry_score"]
    print(f"entry_score range in this sample: {entry_scores.min():.1f} - {entry_scores.max():.1f}, "
          f"median {entry_scores.median():.1f}")

    base_threshold = float(entry_scores.median())  # start where half of history's signals would already pass

    # Small grid - NOT a search for the best result, a sensitivity check.
    grid = list(itertools.product(
        [0.0, 2.0, 5.0],       # kp
        [0.0, 1.0, 3.0],       # ki
        [0.0, 2.0],            # kd
        [5, 10],               # window
    ))

    print(f"\nRunning {len(grid)} gain combinations, window in {{5,10}}, integral_clamp=5.0, "
          f"threshold in [{base_threshold - 15:.1f}, {base_threshold + 15:.1f}] ...\n")

    results = []
    for kp, ki, kd, window in grid:
        r = run_one_config(
            trades, base_threshold=base_threshold, kp=kp, ki=ki, kd=kd, window=window,
            integral_clamp=5.0, threshold_min=base_threshold - 15, threshold_max=base_threshold + 15,
        )
        r.update({"kp": kp, "ki": ki, "kd": kd, "window": window})
        results.append(r)

    results_df = pd.DataFrame(results)
    beat_baseline = results_df[results_df["net_pnl"] > baseline_net_pnl]
    profitable = results_df[results_df["net_pnl"] > 0]

    print(f"Configs that beat baseline net P&L (Rs.{baseline_net_pnl:,.2f}): "
          f"{len(beat_baseline)} / {len(results_df)} ({len(beat_baseline)/len(results_df):.0%})")
    print(f"Configs that were net PROFITABLE outright: {len(profitable)} / {len(results_df)} "
          f"({len(profitable)/len(results_df):.0%})")
    print(f"\nnet_pnl across the grid: min=Rs.{results_df['net_pnl'].min():,.2f}  "
          f"median=Rs.{results_df['net_pnl'].median():,.2f}  max=Rs.{results_df['net_pnl'].max():,.2f}")
    print(f"n_accepted across the grid: min={results_df['n_accepted'].min()}  "
          f"median={results_df['n_accepted'].median():.0f}  max={results_df['n_accepted'].max()}  "
          f"(of {n_total} total trades)")

    print("\nTop 5 by net_pnl (for inspection only - NOT a recommendation, see honesty constraint in docstring):")
    top5 = results_df.sort_values("net_pnl", ascending=False).head(5)
    print(top5.to_string(index=False))

    print("\nBottom 5 by net_pnl:")
    bottom5 = results_df.sort_values("net_pnl", ascending=True).head(5)
    print(bottom5.to_string(index=False))

    # The all-zero-gains config is the no-op control: base_threshold fixed,
    # never adjusts - shows what pure static-threshold filtering (no PID
    # at all) would have done, isolating the controller's OWN contribution.
    noop = results_df[(results_df["kp"] == 0) & (results_df["ki"] == 0) & (results_df["kd"] == 0)]
    print("\nStatic-threshold no-op control (kp=ki=kd=0, threshold frozen at base_threshold):")
    print(noop.drop_duplicates(subset=["net_pnl", "n_accepted"]).to_string(index=False))

    _write_frozen_manifest(trades, baseline_net_pnl, baseline_win_rate, base_threshold, grid, results_df)


def _write_frozen_manifest(trades, baseline_net_pnl, baseline_win_rate, base_threshold, grid, results_df) -> None:
    """META_CONTROL_EXPERIMENT_V1 freeze (owner's round-2 request): freeze
    the WHOLE EXPERIMENT, not just the controller source file - input
    hash, controller design (anti-windup rule, threshold bounds, window
    definition), the full response-surface grid, and the summary
    statistics, all in one durable artifact with a sha256 sidecar,
    matching this project's own established manifest convention (see
    P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825's own
    *_MANIFEST_*.json / .json.sha256 pattern). The scientifically
    important artifact is the EXPERIMENT, not merely the controller
    source - so a later attempt to "reproduce META_CONTROL_EXPERIMENT_V1"
    with subtly different data or parameters is detectable, not silently
    assumed equivalent."""
    input_hash = hashlib.sha256(TRADES_PATH.read_bytes()).hexdigest()

    beat_baseline_frac = float((results_df["net_pnl"] > baseline_net_pnl).mean())
    profitable_frac = float((results_df["net_pnl"] > 0).mean())

    manifest = {
        "experiment_id": "META_CONTROL_EXPERIMENT_V1",
        "frozen_2026_08_25": True,
        "input": {
            "trades_csv_path": str(TRADES_PATH.relative_to(ROOT)),
            "trades_csv_sha256": input_hash,
            "n_trades": int(len(trades)),
        },
        "controller_design": {
            "process_variable": "trailing hit-rate over the last `window` CLOSED, ACTUALLY-TAKEN trades",
            "setpoint": "target_hit_rate = 0.5",
            "actuator": "entry_score threshold - candidate trades below it are rejected, not fed back",
            "anti_windup_rule": "integral hard-clamped to +-integral_clamp (5.0 in this grid) each update",
            "threshold_bounds": [base_threshold - 15, base_threshold + 15],
            "base_threshold": base_threshold,
            "no_lookahead_rule": "rejected trades are never fed back into the controller - only realized, "
                                  "actually-taken-trade outcomes ever update state",
        },
        "baseline": {"net_pnl": baseline_net_pnl, "win_rate": baseline_win_rate},
        "grid": {
            "kp_values": sorted(set(g[0] for g in grid)),
            "ki_values": sorted(set(g[1] for g in grid)),
            "kd_values": sorted(set(g[2] for g in grid)),
            "window_values": sorted(set(g[3] for g in grid)),
            "n_configs": len(grid),
        },
        "results": {
            "full_grid": results_df.to_dict(orient="records"),
            "net_pnl_min": float(results_df["net_pnl"].min()),
            "net_pnl_median": float(results_df["net_pnl"].median()),
            "net_pnl_max": float(results_df["net_pnl"].max()),
            "fraction_beating_baseline": beat_baseline_frac,
            "fraction_net_profitable": profitable_frac,
        },
        # Deliberately narrow, per the owner's explicit instruction not
        # to promote the single best result as "the winning PID" - with
        # 36 variants tried, any one config is vulnerable to selection
        # bias. The response-surface SHAPE (all 36 improved, none
        # profitable) is the credible finding, not any single optimum.
        "conclusion": (
            "Every tested adaptive configuration improved the V10-C loss relative to the stated static "
            "baseline, but none established positive net expectancy. The broad, unanimous direction of "
            "improvement across the entire tested parameter region is the credible finding - not any single "
            "best-performing gain combination, which remains vulnerable to selection bias with 36 configs tried."
        ),
    }

    out_path = ROOT / "META_CONTROL_EXPERIMENT_V1_FROZEN_20260825.json"
    out_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    manifest_hash = hashlib.sha256(out_path.read_bytes()).hexdigest()
    (ROOT / "META_CONTROL_EXPERIMENT_V1_FROZEN_20260825.json.sha256").write_text(
        f"{manifest_hash}  {out_path.name}\n", encoding="utf-8"
    )
    print(f"\nFroze experiment manifest: {out_path.name} (sha256 {manifest_hash[:16]}...)")


if __name__ == "__main__":
    main()
