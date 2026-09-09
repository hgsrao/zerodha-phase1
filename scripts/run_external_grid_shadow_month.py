#!/usr/bin/env python3
"""Run one sealed external-engine month with passive Nifty/VIX Grid telemetry.

This runner is intentionally not a Grid-gated backtest.  It proves that the
real 15-minute context feeds can be sealed, causally aligned, and observed
alongside the existing external 10-box pipeline.  Order selection, quantity,
stops, targets, Gate16 policy, and every other safety control remain exactly
as they are without Grid context.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2.exogenous_context_manifest import (
    ExogenousContextManifest,
    verify_exogenous_context_manifest,
)
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STOCK_MANIFEST = PROJECT_ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"
DEFAULT_CONTEXT_MANIFEST = PROJECT_ROOT / "revision2" / "EXOGENOUS_CONTEXT_MANIFEST_15MIN.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _to_utc(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("Asia/Kolkata")
    return timestamp.tz_convert("UTC")


def _json_default(value: Any) -> str:
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def _load_context(manifest: ExogenousContextManifest) -> tuple[pd.DataFrame, pd.DataFrame]:
    records = {record.name: record for record in manifest.files}
    required = {"NIFTY_50_15MIN", "INDIA_VIX_15MIN"}
    if set(records) != required:
        raise ValueError(f"unexpected context manifest names: expected {sorted(required)}, got {sorted(records)}")
    nifty = pd.read_csv(records["NIFTY_50_15MIN"].path)
    vix = pd.read_csv(records["INDIA_VIX_15MIN"].path)
    return nifty, vix


def run_shadow_month(args: argparse.Namespace) -> dict:
    stock_manifest = DatasetManifest.load(args.stock_manifest)
    symbol_record = next((record for record in stock_manifest.files if record.symbol == args.symbol), None)
    if symbol_record is None:
        raise ValueError(f"{args.symbol} is not declared by {args.stock_manifest}")

    loader = MarketDataLoader(stock_manifest.data_dir, synthetic_if_missing=False)
    symbol_path = loader._resolve_csv(args.symbol)
    if symbol_path is None or _sha256(symbol_path) != symbol_record.sha256:
        raise RuntimeError(f"stock feed seal failed for {args.symbol}")

    context_manifest = ExogenousContextManifest.load(args.context_manifest)
    context_verification = verify_exogenous_context_manifest(context_manifest)
    if not context_verification.valid:
        raise RuntimeError(context_verification.message)
    nifty, vix = _load_context(context_manifest)

    all_bars = loader._load_symbol_csv(args.symbol)
    timestamps = pd.to_datetime(all_bars["timestamp"], utc=True, errors="raise")
    start = _to_utc(args.start)
    end_exclusive = _to_utc(args.end) + pd.Timedelta("1D")
    target = all_bars.loc[(timestamps >= start) & (timestamps < end_exclusive)].copy()
    if target.empty:
        raise ValueError(f"no {args.symbol} bars within {args.start} through {args.end}")
    prefix = all_bars.loc[timestamps < start].tail(args.context_warmup_bars).copy()
    if len(prefix) < args.min_engine_warmup_bars:
        raise ValueError(f"only {len(prefix)} warmup bars available; need {args.min_engine_warmup_bars}")
    run_bars = pd.concat([prefix, target], ignore_index=True)

    provider = SealedGridContextProvider(nifty, vix)
    engine = Revision2ExternalEngineOrchestrator(
        [args.symbol],
        registry=CanonicalParameterRegistry(),
        starting_equity=args.starting_equity,
        grid_context_provider=provider,
    )
    result = engine.run({args.symbol: run_bars}, warmup=len(prefix))
    shadow = result["grid_shadow"]
    completed = int(result["completed_trades"])
    report = {
        "kind": "EXTERNAL_GRID_SHADOW_MONTH",
        "status": "SHADOW_COMPLETE" if completed else "NO_EXECUTION",
        "shadow_only": True,
        "assertion": "Grid observations did not alter orders, sizing, stops, targets, or safety policy.",
        "scope": {
            "symbol": args.symbol,
            "start": args.start,
            "end": args.end,
            "engine_warmup_bars": len(prefix),
            "target_bars": len(target),
        },
        "identity": {
            "stock_manifest_hash": stock_manifest.manifest_hash,
            "stock_file_sha256": symbol_record.sha256,
            "context_manifest_hash": context_manifest.manifest_hash,
            "context_files_verified": context_verification.checked_files,
            "config_hash": result["config_hash"],
            "safety_contract_hash": result["safety_contract_hash"],
        },
        "execution": {
            key: result[key]
            for key in ("orders_submitted", "fills", "completed_trades", "gross_pnl", "net_pnl", "ending_equity")
        },
        "grid_shadow": shadow,
        "trades": result["trades"],
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SUNPHARMA")
    parser.add_argument("--start", default="2023-09-01")
    parser.add_argument("--end", default="2023-09-29")
    parser.add_argument("--stock-manifest", default=str(DEFAULT_STOCK_MANIFEST))
    parser.add_argument("--context-manifest", default=str(DEFAULT_CONTEXT_MANIFEST))
    parser.add_argument("--context-warmup-bars", type=int, default=1000)
    parser.add_argument("--min-engine-warmup-bars", type=int, default=60)
    parser.add_argument("--starting-equity", type=float, default=100_000.0)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    report = run_shadow_month(args)
    output = Path(args.output) if args.output else PROJECT_ROOT / "diagnostic_output" / (
        f"external_grid_shadow_{args.symbol}_{args.start.replace('-', '')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=_json_default) + "\n")
    console_report = {
        key: report[key] for key in ("kind", "status", "scope", "identity", "execution")
    }
    console_report["grid_shadow"] = {
        key: report["grid_shadow"][key] for key in ("enabled", "available", "synchronized")
    }
    print(json.dumps(console_report, indent=2, default=_json_default))
    print(f"\nSaved shadow report: {output}")


if __name__ == "__main__":
    main()
