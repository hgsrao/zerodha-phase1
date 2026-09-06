#!/usr/bin/env python3
"""Diagnostic, not a fix: WHY did saturation_exit_pa/saturation_exit_studies
fire zero times on the real INFY 6-month backtest (commit f119e0e)? Traces
every real ContinuousExitController.update() call during that same real run
and reports, per real completed trade, the MAX streak each track actually
reached before the trade closed (by whatever reason actually closed it) --
so the real bottleneck is diagnosed from real data, not guessed.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import determinism_guard  # noqa: F401 -- must import before pandas/numpy; see its module docstring
import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.continuous_exit_controller import ContinuousExitController
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output_external_engine"

records = []
_orig_update = ContinuousExitController.update


def _traced_update(self, symbol, state, current_confidence, current_chart_studies_confidence, current_close, current_atr):
    new_state = _orig_update(self, symbol, state, current_confidence, current_chart_studies_confidence, current_close, current_atr)
    records.append({
        "bars_held": new_state.bars_held,
        "pa_confidence": current_confidence,
        "studies_confidence": current_chart_studies_confidence,
        "streak_pa": new_state.consecutive_bars_at_low_confidence_extreme,
        "streak_studies": new_state.consecutive_bars_at_low_studies_extreme,
    })
    return new_state


def main():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv("INFY")
    start = frame["timestamp"].iloc[0]
    cutoff = start + pd.DateOffset(months=6)
    frame = frame[frame["timestamp"] < cutoff].reset_index(drop=True)

    registry = CanonicalParameterRegistry()
    orch = Revision2ExternalEngineOrchestrator(["INFY"], registry, starting_equity=1_000_000.0)
    real_saturation_exit_bars = orch.exit_controller.saturation_exit_bars
    print(f"saturation_exit_bars (the real, actual gate this run used) = {real_saturation_exit_bars}")

    ContinuousExitController.update = _traced_update
    try:
        report = orch.run({"INFY": frame}, warmup=60)
    finally:
        ContinuousExitController.update = _orig_update

    trades = report["trades"]

    # Split the flat per-bar trace into per-trade segments: a new trade's
    # first update() call always has bars_held == 1 (ExitControllerState
    # starts every new open_position() at bars_held=0, incremented before
    # anything else in update()). Single symbol (INFY only), one open
    # position at a time -> the Nth segment IS the Nth completed trade, in
    # the same chronological order the real ledger already reports.
    segments = []
    current = []
    for r in records:
        if r["bars_held"] == 1 and current:
            segments.append(current)
            current = []
        current.append(r)
    if current:
        segments.append(current)

    assert len(segments) == len(trades), (
        f"segment count {len(segments)} != real completed_trades count {len(trades)} -- "
        "tracing assumption (bars_held==1 marks a new trade) broke; do not trust the numbers below"
    )

    rows = []
    for trade, seg in zip(trades, segments):
        max_streak_pa = max(r["streak_pa"] for r in seg)
        max_streak_studies = max(r["streak_studies"] for r in seg)
        rows.append({
            "reason": trade["reason"], "bars_held_total": seg[-1]["bars_held"],
            "max_streak_pa": max_streak_pa, "max_streak_studies": max_streak_studies,
        })

    df = pd.DataFrame(rows)
    print(f"\nreal completed trades: {len(df)}")
    print("\nexit reason counts:")
    print(df["reason"].value_counts().to_string())
    print("\nbars_held_total distribution:")
    print(df["bars_held_total"].describe().to_string())
    print("\nmax_streak_pa distribution (real, per real trade, gate is saturation_exit_bars="
          f"{real_saturation_exit_bars}):")
    print(df["max_streak_pa"].value_counts().sort_index().to_string())
    print("\nmax_streak_studies distribution:")
    print(df["max_streak_studies"].value_counts().sort_index().to_string())
    print(f"\ntrades that reached max_streak_pa >= {real_saturation_exit_bars - 1} "
          f"(one bar short of the real gate): {(df['max_streak_pa'] >= real_saturation_exit_bars - 1).sum()}")
    print(f"trades that reached max_streak_studies >= {real_saturation_exit_bars - 1}: "
          f"{(df['max_streak_studies'] >= real_saturation_exit_bars - 1).sum()}")
    print(f"\ntrades closed via 'stop' or 'stop_gap' with bars_held_total <= {real_saturation_exit_bars}: "
          f"{((df['reason'].isin(['stop', 'stop_gap'])) & (df['bars_held_total'] <= real_saturation_exit_bars)).sum()} "
          f"/ {(df['reason'].isin(['stop', 'stop_gap'])).sum()} stop/stop_gap trades")

    out = OUTPUT_DIR / "saturation_streak_diagnosis_infy_6month.json"
    df.to_json(out, orient="records", indent=2)
    print(f"\nsaved per-trade breakdown to {out}")


if __name__ == "__main__":
    main()
