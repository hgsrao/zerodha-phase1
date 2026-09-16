#!/usr/bin/env python3
"""Causal, manifest-verified mean-reversion candidate shadow study.

This replaces the contaminated prototype experiment.  It is intentionally a
research runner, not the production orchestrator and not a claim of alpha:

* indicators use only completed/past bars (never ``bfill``);
* an eligible signal at bar close enters at the *next* bar open with adverse
  slippage;
* stop checks are chronological and recovery exits are scheduled for a later
  bar open; and
* every assumption is stored beside the resulting trade ledger.

It does not fit parameters, inspect a future return before entry, or model a
shared portfolio.  A portfolio simulation is meaningful only after a
candidate survives a frozen chronological research protocol.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from revision2.dataset_manifest import DatasetManifest, verify_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"


def _last_percentile(values: pd.Series) -> float:
    return float(values.rank(pct=True).iloc[-1])


def causal_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Return indicators at each bar using no later bar from that session."""
    df = frame.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["session"] = df["timestamp"].dt.tz_convert("Asia/Kolkata").dt.date
    previous_close = df["close"].shift(1)
    true_range = pd.concat([
        df["high"] - df["low"], (df["high"] - previous_close).abs(),
        (df["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    df["atr_14"] = true_range.rolling(14, min_periods=14).mean()
    df["atr_baseline_100"] = df["atr_14"].rolling(100, min_periods=100).mean()
    df["atr_ratio"] = df["atr_14"] / df["atr_baseline_100"]
    delta = df["close"].diff()
    gains, losses = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain = gains.rolling(14, min_periods=14).mean()
    avg_loss = losses.rolling(14, min_periods=14).mean()
    df["rsi_14"] = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss.replace(0, np.nan)))
    df["rsi_percentile_100"] = df["rsi_14"].rolling(100, min_periods=100).apply(_last_percentile, raw=False)
    df["volume_median_50"] = df["volume"].rolling(50, min_periods=50).median()
    df["volume_flow_ratio"] = df["volume"] / df["volume_median_50"]
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    cumulative_volume = df["volume"].groupby(df["session"]).cumsum()
    cumulative_value = (typical * df["volume"]).groupby(df["session"]).cumsum()
    df["session_vwap"] = cumulative_value / cumulative_volume.replace(0, np.nan)
    # A rolling standard deviation is complete only on the current close;
    # it is therefore valid for a decision taken after this bar closes.
    session_std = typical.groupby(df["session"]).transform(
        lambda values: values.rolling(30, min_periods=30).std()
    )
    df["session_vwap_z"] = (df["close"] - df["session_vwap"]) / session_std.replace(0, np.nan)
    return df


def _adverse_entry(open_price: float, slippage_bps: float) -> float:
    return float(open_price * (1.0 + slippage_bps / 10_000.0))


def _adverse_long_exit(price: float, slippage_bps: float) -> float:
    return float(price * (1.0 - slippage_bps / 10_000.0))


def _evaluate_symbol(symbol: str, df: pd.DataFrame, *, z_entry: float, rsi_percentile_max: float,
                     volume_flow_min: float, stop_atr: float, max_hold_bars: int,
                     recovery_z: float, slippage_bps: float, fees_r: float) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    next_eligible = 0
    required = ["atr_14", "atr_ratio", "rsi_percentile_100", "volume_flow_ratio", "session_vwap_z"]
    for signal_index in range(len(df) - max_hold_bars - 2):
        if signal_index < next_eligible:
            continue
        row = df.iloc[signal_index]
        if row[required].isna().any():
            continue
        # This is a fixed candidate definition.  It contains no future-price
        # prediction or MPC return gate.
        if not (float(row["session_vwap_z"]) <= z_entry
                and float(row["rsi_percentile_100"]) <= rsi_percentile_max
                and float(row["volume_flow_ratio"]) >= volume_flow_min):
            continue
        entry_index = signal_index + 1
        entry_bar = df.iloc[entry_index]
        entry = _adverse_entry(float(entry_bar["open"]), slippage_bps)
        risk = float(stop_atr * row["atr_14"])
        if not math.isfinite(risk) or risk <= 0:
            continue
        stop = entry - risk
        exit_index: int | None = None
        exit_reason = "horizon"
        exit_price: float | None = None
        # The entry is at this bar's open, so its subsequent low may trigger
        # the stop.  A recovery signal from a completed bar exits no earlier
        # than the following open.
        for index in range(entry_index, min(entry_index + max_hold_bars, len(df) - 1)):
            bar = df.iloc[index]
            if float(bar["low"]) <= stop:
                fill_base = min(float(bar["open"]), stop)
                exit_price, exit_index, exit_reason = _adverse_long_exit(fill_base, slippage_bps), index, "stop"
                break
            if index > entry_index and pd.notna(bar["session_vwap_z"]) and float(bar["session_vwap_z"]) >= recovery_z:
                next_open = float(df.iloc[index + 1]["open"])
                exit_price, exit_index, exit_reason = _adverse_long_exit(next_open, slippage_bps), index + 1, "recovery"
                break
        if exit_price is None:
            exit_index = min(entry_index + max_hold_bars, len(df) - 1)
            exit_price = _adverse_long_exit(float(df.iloc[exit_index]["close"]), slippage_bps)
        gross_r = (exit_price - entry) / risk
        net_r = gross_r - fees_r
        trades.append({
            "symbol": symbol, "side": "BUY", "signal_timestamp": str(row["timestamp"]),
            "entry_timestamp": str(entry_bar["timestamp"]), "entry_price": entry,
            "entry_atr": float(row["atr_14"]), "entry_vwap_z": float(row["session_vwap_z"]),
            "entry_rsi_percentile": float(row["rsi_percentile_100"]),
            "entry_volume_flow_ratio": float(row["volume_flow_ratio"]), "stop_price": stop,
            "exit_timestamp": str(df.iloc[exit_index]["timestamp"]), "exit_price": exit_price,
            "exit_reason": exit_reason, "bars_held": int(exit_index - entry_index + 1),
            "gross_r": gross_r, "assumed_fees_r": fees_r, "net_r": net_r,
        })
        next_eligible = exit_index + 1
    return trades


def _slice(frame: pd.DataFrame, start: str, end_exclusive: str) -> pd.DataFrame:
    ts = pd.to_datetime(frame["timestamp"], utc=True)
    start_ts, end_ts = pd.Timestamp(start, tz="Asia/Kolkata").tz_convert("UTC"), pd.Timestamp(end_exclusive, tz="Asia/Kolkata").tz_convert("UTC")
    return frame[(ts >= start_ts) & (ts < end_ts)].copy()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end-exclusive", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--symbols", help="optional comma-separated manifest symbols")
    parser.add_argument("--z-entry", type=float, default=-2.5)
    parser.add_argument("--rsi-percentile-max", type=float, default=0.05)
    parser.add_argument("--volume-flow-min", type=float, default=1.05)
    parser.add_argument("--stop-atr", type=float, default=1.2)
    parser.add_argument("--max-hold-bars", type=int, default=40)
    parser.add_argument("--recovery-z", type=float, default=-0.3)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--fees-r", type=float, default=0.0, help="explicit non-slippage fee assumption in R")
    args = parser.parse_args()
    if pd.Timestamp(args.end_exclusive) <= pd.Timestamp(args.start):
        raise ValueError("end-exclusive must be after start")
    manifest = DatasetManifest.load(str(MANIFEST_PATH))
    print("[VERIFY] Re-hashing manifest-declared raw files...", flush=True)
    verification = verify_manifest(manifest)
    if not verification.valid:
        raise RuntimeError(verification.message)
    chosen = set(args.symbols.split(",")) if args.symbols else None
    trades: list[dict[str, Any]] = []
    for record in sorted(manifest.files, key=lambda row: row.symbol):
        if chosen and record.symbol not in chosen:
            continue
        path = Path(manifest.data_dir) / record.filename
        raw = _slice(pd.read_csv(path), args.start, args.end_exclusive)
        if len(raw) < 500:
            continue
        print(f"[LOAD] {record.symbol}: {len(raw):,} raw bars", flush=True)
        trades.extend(_evaluate_symbol(record.symbol, causal_features(raw), z_entry=args.z_entry,
            rsi_percentile_max=args.rsi_percentile_max, volume_flow_min=args.volume_flow_min,
            stop_atr=args.stop_atr, max_hold_bars=args.max_hold_bars, recovery_z=args.recovery_z,
            slippage_bps=args.slippage_bps, fees_r=args.fees_r))
    gross = [float(row["gross_r"]) for row in trades]
    net = [float(row["net_r"]) for row in trades]
    artifact = {
        "run_type": "causal_mean_reversion_candidate_shadow", "status": "RESEARCH_ONLY",
        "research_boundary": "No future-price entry filter, no parameter fitting, no shared-portfolio claim, no promotion.",
        "period": [args.start, args.end_exclusive], "dataset_manifest_hash": manifest.manifest_hash,
        "dataset_verification": verification.message,
        "assumptions": {key: getattr(args, key) for key in (
            "z_entry", "rsi_percentile_max", "volume_flow_min", "stop_atr", "max_hold_bars",
            "recovery_z", "slippage_bps", "fees_r",
        )},
        "metrics": {"trades": len(trades), "win_rate": (sum(value > 0 for value in net) / len(net)) if net else None,
                    "mean_gross_r": float(np.mean(gross)) if gross else None,
                    "mean_net_r_after_assumptions": float(np.mean(net)) if net else None},
        "exit_reasons": dict(Counter(row["exit_reason"] for row in trades)), "trades_ledger": trades,
    }
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), **artifact["metrics"], "exit_reasons": artifact["exit_reasons"]}, indent=2))


if __name__ == "__main__":
    main()
