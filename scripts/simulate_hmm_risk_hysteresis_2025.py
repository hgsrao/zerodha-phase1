#!/usr/bin/env python3
"""Causal HMM-posterior/hysteresis replay for one symbol, Jan-Mar 2025.

January is the only calibration period.  The HMM is frozen after January;
February-March are replayed one completed bar at a time with a streaming
filter.  This is a controller diagnostic, not a trade backtest: it does not
create orders or claim P&L improvement.
"""
from __future__ import annotations

import argparse
import json
import zlib
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.closed_loop_control import HMMRiskHysteresis
from revision2_external.regime_hmm import GaussianHMM

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def _features(closes: pd.Series) -> pd.DataFrame:
    returns = closes.pct_change() * 100.0
    rolling_vol = returns.rolling(10, min_periods=3).std().bfill()
    return pd.DataFrame({"return": returns, "rolling_vol": rolling_vol}).dropna()


def _halt_intervals(frame: pd.DataFrame) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    intervals = []
    start = None
    for timestamp, halted in zip(frame.timestamp, frame.stressed_latched):
        if halted and start is None:
            start = timestamp
        elif not halted and start is not None:
            intervals.append((start, timestamp))
            start = None
    if start is not None:
        intervals.append((start, frame.timestamp.iloc[-1]))
    return intervals


def _plot(frame: pd.DataFrame, output: Path, symbol: str, controller: HMMRiskHysteresis) -> None:
    """Write an inspectable HTML chart without adding a plotting dependency."""
    # Two months of one-minute bars are dense.  Keep every fifth bar in the
    # view; all events remain in the JSON ledger at full resolution.
    view = frame.iloc[::5].copy()
    alt.data_transformers.disable_max_rows()
    base = alt.Chart(view).encode(x=alt.X("timestamp:T", title="Time"))
    price = base.mark_line(color="#222", strokeWidth=1).encode(
        y=alt.Y("close:Q", title="Close"), tooltip=["timestamp:T", "close:Q", "stressed_latched:N"],
    ).properties(title=f"{symbol}: price with HMM hysteresis shadow state", height=260)
    halts = base.transform_filter(alt.datum.stressed_latched).mark_point(color="#c62828", opacity=0.35, size=10).encode(
        y="close:Q",
    )
    probability = base.transform_fold(
        ["raw_stress_probability", "filtered_stress_probability", "applied_derate"],
        as_=["series", "value"],
    ).mark_line().encode(
        y=alt.Y("value:Q", title="Probability / applied derate", scale=alt.Scale(domain=[0, 1])),
        color=alt.Color("series:N", title="Series"),
        tooltip=["timestamp:T", "series:N", alt.Tooltip("value:Q", format=".3f")],
    ).properties(title="Causal HMM posterior and step-buffered risk derate", height=220)
    rules = alt.Chart(pd.DataFrame({"level": [controller.enter_stress_probability, controller.exit_stress_probability],
                                    "label": ["Latch", "Release"]})).mark_rule(strokeDash=[5, 4]).encode(
        y="level:Q", color="label:N", tooltip=["label:N", alt.Tooltip("level:Q", format=".2f")],
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    ((price + halts) & (probability + rules)).save(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SUNPHARMA")
    parser.add_argument("--output", default=None)
    parser.add_argument("--plot", default=None)
    args = parser.parse_args()
    output = Path(args.output or ROOT / "diagnostic_output" / f"hmm_risk_hysteresis_{args.symbol}_2025_janmar.json")
    plot = Path(args.plot or ROOT / "diagnostic_output" / f"hmm_risk_hysteresis_{args.symbol}_2025_janmar.html")

    manifest = DatasetManifest.load(str(MANIFEST))
    verified = verify_manifest(manifest)
    if not verified.valid:
        raise RuntimeError(verified.message)
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    bars = loader._load_symbol_csv(args.symbol)
    timezone = bars.timestamp.dt.tz
    start, train_end, end = pd.Timestamp("2025-01-01"), pd.Timestamp("2025-02-01"), pd.Timestamp("2025-04-01")
    if timezone is not None:
        start, train_end, end = (value.tz_localize(timezone) for value in (start, train_end, end))
    bars = bars[(bars.timestamp >= start) & (bars.timestamp < end)].reset_index(drop=True)
    features = _features(bars.close)
    train = features.loc[bars.loc[features.index, "timestamp"] < train_end]
    replay = features.loc[bars.loc[features.index, "timestamp"] >= train_end]
    if len(train) < 60 or len(replay) < 1:
        raise RuntimeError("insufficient January calibration or February-March replay bars")

    seed = zlib.crc32(args.symbol.encode("utf-8")) % (2 ** 31)
    model = GaussianHMM(n_states=2, n_iter=20, random_state=seed).fit(train.to_numpy(float))
    variances = model.vars_.sum(axis=1)
    stressed_state, calm_state = int(np.argmax(variances)), int(np.argmin(variances))
    valid = bool(model.valid_state_mask_.all() and variances[calm_state] > 0 and variances[stressed_state] / variances[calm_state] >= 2.5)
    if not valid:
        raise RuntimeError("January HMM did not establish two supported, distinct regimes; no risk simulation is justified")

    # Seed the causal filter with January only, then advance one replay bar.
    prior = model.filter_proba(train.to_numpy(float))[-1]
    controller = HMMRiskHysteresis()
    rows = []
    prior_multiplier = controller.applied_derate
    for index, feature_row in replay.iterrows():
        prior = model.filter_step(feature_row.to_numpy(float), prior)
        observation = {"available": True, "stress_probability": float(prior[stressed_state])}
        control = controller.update(observation)
        bar = bars.loc[index]
        resize = not np.isclose(control["suggested_hysteresis_derate"], prior_multiplier)
        rows.append({
            "timestamp": str(bar.timestamp), "close": float(bar.close),
            "raw_stress_probability": control["raw_stress_probability"],
            "filtered_stress_probability": control["filtered_stress_probability"],
            "applied_derate": control["suggested_hysteresis_derate"],
            "stressed_latched": control["stressed_latched"], "resize_event": bool(resize),
            "reason": control["reason"],
        })
        prior_multiplier = control["suggested_hysteresis_derate"]
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame.timestamp)
    transitions = frame.stressed_latched.ne(frame.stressed_latched.shift()).fillna(False)
    starts = int((transitions & frame.stressed_latched).sum())
    halted = int(frame.stressed_latched.sum())
    durations = [int((frame.stressed_latched.iloc[a:b]).sum()) for a, b in []]  # intervals are summarized below
    intervals = _halt_intervals(frame)
    durations = [int(((frame.timestamp >= start) & (frame.timestamp < end)).sum()) for start, end in intervals]
    report = {
        "run_type": "causal_hmm_risk_hysteresis_shadow",
        "symbol": args.symbol,
        "calibration_window": "2025-01-01 through 2025-01-31",
        "replay_window": "2025-02-01 through 2025-03-31",
        "mode": "shadow_only_no_orders_no_position_changes",
        "hmm": {"states": 2, "stressed_state": stressed_state, "variances": [float(value) for value in variances],
                "occupancy": [float(value) for value in model.state_occupancy_]},
        "controller": {"enter_probability": controller.enter_stress_probability,
                       "exit_probability": controller.exit_stress_probability,
                       "confirmation_bars": controller.confirmation_bars,
                       "smoothing_alpha": controller.smoothing_alpha,
                       "minimum_derate_step": controller.minimum_derate_step},
        "diagnostics": {"bars": len(frame), "halt_starts": starts, "halted_bars": halted,
                        "halted_fraction": halted / len(frame),
                        "mean_halt_duration_bars": float(np.mean(durations)) if durations else 0.0,
                        "resize_events": int(frame.resize_event.sum())},
        "plot": str(plot), "events": [{**row, "timestamp": str(row["timestamp"])} for row in frame.to_dict("records")],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    _plot(frame, plot, args.symbol, controller)
    print(json.dumps({"output": str(output), "plot": str(plot), "diagnostics": report["diagnostics"]}, indent=2))


if __name__ == "__main__":
    main()
