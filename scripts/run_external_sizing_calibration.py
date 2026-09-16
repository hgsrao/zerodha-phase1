#!/usr/bin/env python3
"""Sealed, train/validation-only Optuna calibration for the external engine.

This runner is intentionally usable from either the current external-engine
checkout or a worktree containing a prior sizing implementation.  It never
loads the held-out test interval.  A caller therefore runs the identical
script once in each checkout, compares the sealed artifacts, and only then
decides whether either architecture merits a separate untouched-test replay.

It is not a broker-sandbox program and it does not send orders anywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import optuna
import pandas as pd

import determinism_guard  # noqa: F401  -- must precede numerical engine imports
from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest, verify_manifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json"

# Deliberately narrow.  These are the critical alpha/MPC economics previously
# agreed for research; PID gains, execution mechanics and every safety field
# are frozen for this experiment.
ECONOMIC_SEARCH_SURFACE = (
    "entry_confidence_threshold",
    "profit_target_atr_mult",
    "stop_loss_atr_mult",
    "minimum_profit_margin_over_cost",
    "max_hold_bars",
)


def validate_search_surface(registry: CanonicalParameterRegistry, names: tuple[str, ...] = ECONOMIC_SEARCH_SURFACE) -> None:
    """Reject any request that could weaken a safety or fixed contract."""
    forbidden = (
        set(registry.FIXED_TARGET_NAMES)
        | set(registry.CORE_SAFETY_KEYS)
        | set(registry.SAFETY_ALIASES)
        | set(registry.LEGACY_SAFETY_ALIASES)
    )
    invalid: list[str] = []
    for name in names:
        if name in forbidden:
            invalid.append(f"{name}: immutable safety/fixed parameter")
            continue
        try:
            spec = registry.get(name)
        except KeyError:
            invalid.append(f"{name}: unknown parameter")
            continue
        if not spec.calibratable or spec.param_type not in {"int", "float"}:
            invalid.append(f"{name}: not a numeric calibratable economic parameter")
    if invalid:
        raise ValueError("invalid calibration surface: " + "; ".join(invalid))


def _load_interval(
    manifest: DatasetManifest,
    start: str,
    end_exclusive: str,
    symbols: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    requested = set(symbols) if symbols else None
    loaded: dict[str, pd.DataFrame] = {}
    for record in sorted(manifest.files, key=lambda item: item.symbol):
        if requested is not None and record.symbol not in requested:
            continue
        frame = loader._load_symbol_csv(record.symbol)
        timezone = frame["timestamp"].dt.tz
        begin = pd.Timestamp(start)
        end = pd.Timestamp(end_exclusive)
        if timezone is not None:
            begin = begin.tz_localize(timezone) if begin.tzinfo is None else begin.tz_convert(timezone)
            end = end.tz_localize(timezone) if end.tzinfo is None else end.tz_convert(timezone)
        elif begin.tzinfo is not None:
            begin, end = begin.tz_localize(None), end.tz_localize(None)
        bounded = frame[(frame["timestamp"] >= begin) & (frame["timestamp"] < end)].reset_index(drop=True)
        if not bounded.empty:
            loaded[record.symbol] = bounded
    return loaded


def _metrics(report: dict[str, Any]) -> dict[str, Any]:
    trades = report.get("trades", [])
    pnls = [float(trade.get("net_pnl", trade.get("pnl", 0.0))) for trade in trades]
    gains = sum(value for value in pnls if value > 0.0)
    losses = -sum(value for value in pnls if value < 0.0)
    return {
        "net_pnl": float(report.get("net_pnl", 0.0)),
        "gross_pnl": float(report.get("gross_pnl", 0.0)),
        "completed_trades": int(report.get("completed_trades", len(trades))),
        "profit_factor": (gains / losses) if losses else (math.inf if gains else 0.0),
        "mtm_max_drawdown_fraction": float(report.get("mtm_max_drawdown_fraction", 0.0)),
        "symbols_traded": len({trade.get("symbol") for trade in trades if trade.get("symbol")}),
    }


def _score(metrics: dict[str, Any]) -> float:
    """A research ranking only; it does not promote a configuration."""
    if metrics["completed_trades"] < 20:
        return -1_000_000_000.0
    # Net P&L remains primary; the explicit drawdown penalty stops a small
    # P&L improvement from ranking above a materially riskier candidate.
    return metrics["net_pnl"] - 1_000_000.0 * metrics["mtm_max_drawdown_fraction"]


def _config_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _run_replay(
    registry: CanonicalParameterRegistry,
    symbols: list[str],
    bars: dict[str, pd.DataFrame],
    params: dict[str, Any],
) -> dict[str, Any]:
    errors = registry.validate_calibration_payload(params)
    if errors:
        raise ValueError("candidate violates registry calibration contract: " + "; ".join(errors))
    orchestrator = Revision2ExternalEngineOrchestrator(
        symbols, registry, calibration_overrides=params, starting_equity=1_000_000.0,
    )
    return orchestrator.run(bars, warmup=60)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-start", default="2023-07-03")
    parser.add_argument("--train-end-exclusive", default="2023-08-01")
    parser.add_argument("--validation-start", default="2023-08-01")
    parser.add_argument("--validation-end-exclusive", default="2023-09-01")
    parser.add_argument("--held-test-start", default="2023-09-01")
    parser.add_argument("--held-test-end-exclusive", default="2023-10-01")
    parser.add_argument("--trials", type=int, default=2, help="Full-month trials; keep small on a single host")
    parser.add_argument("--seed", type=int, default=20260910)
    parser.add_argument("--engine-label", default="external-current")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "diagnostic_output" / "external_sizing_calibration_train_validation.json"))
    args = parser.parse_args()
    if args.trials < 1:
        raise ValueError("--trials must be positive")
    if not (args.train_end_exclusive <= args.validation_start <= args.validation_end_exclusive <= args.held_test_start):
        raise ValueError("periods must be ordered and non-overlapping; held test must remain after validation")

    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    validate_search_surface(registry)
    manifest = DatasetManifest.load(str(MANIFEST_PATH))
    print("[VERIFY] Re-hashing manifest-declared data files...", flush=True)
    verification = verify_manifest(manifest)
    if not verification.valid:
        raise RuntimeError(f"manifest verification failed: {verification.message}")

    print("[LOAD] Training data only (held-out September is not read)...", flush=True)
    train_bars = _load_interval(manifest, args.train_start, args.train_end_exclusive)
    symbols = sorted(train_bars)
    if len(symbols) < 2:
        raise RuntimeError("training interval has fewer than two symbols")
    print(f"[TRAIN] {len(symbols)} locked symbols, {sum(len(x) for x in train_bars.values()):,} bars", flush=True)

    trials: list[dict[str, Any]] = []
    sampler = optuna.samplers.TPESampler(seed=args.seed, n_startup_trials=min(args.trials, 2))
    study = optuna.create_study(direction="maximize", sampler=sampler)

    def objective(trial: optuna.Trial) -> float:
        params: dict[str, Any] = {}
        for name in ECONOMIC_SEARCH_SURFACE:
            spec = registry.get(name)
            if spec.param_type == "int":
                params[name] = trial.suggest_int(name, int(spec.minimum), int(spec.maximum))
            else:
                params[name] = trial.suggest_float(name, float(spec.minimum), float(spec.maximum))
        print(f"[TRIAL {trial.number + 1}/{args.trials}] {params}", flush=True)
        report = _run_replay(registry, symbols, train_bars, params)
        metrics = _metrics(report)
        score = _score(metrics)
        trials.append({"number": trial.number, "params": params, "config_hash": _config_hash(params), "metrics": metrics, "score": score})
        print(f"[TRIAL {trial.number + 1}] score={score:.2f} net={metrics['net_pnl']:.2f} trades={metrics['completed_trades']}", flush=True)
        return score

    # Deliberately sequential: a full 47-symbol replay consumes the host's
    # memory bandwidth. Parallel full trials have already been terminated by
    # the OS and would undermine reproducibility.
    study.optimize(objective, n_trials=args.trials, n_jobs=1)
    selected = max(trials, key=lambda row: row["score"])

    print("[LOAD] Validation data for the training-selected candidate only...", flush=True)
    validation_bars = _load_interval(
        manifest, args.validation_start, args.validation_end_exclusive, symbols,
    )
    missing = sorted(set(symbols) - set(validation_bars))
    if missing:
        raise RuntimeError(f"validation is missing locked training symbols: {missing}")
    validation_report = _run_replay(registry, symbols, validation_bars, selected["params"])
    validation_metrics = _metrics(validation_report)

    # This is intentionally stricter than the train ranking but does not
    # authorize a held-out test or live deployment. A false result is normal
    # and is the expected outcome for a losing research candidate.
    selected_for_held_test = bool(
        validation_metrics["completed_trades"] >= 20
        and validation_metrics["net_pnl"] > 0.0
        and validation_metrics["profit_factor"] >= 1.05
        and validation_metrics["mtm_max_drawdown_fraction"] <= 0.15
    )
    artifact = {
        "run_type": "external_train_validation_economic_calibration",
        "engine_label": args.engine_label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "search_engine": "Optuna TPESampler (sequential full-replay protocol)",
        "economic_search_surface": list(ECONOMIC_SEARCH_SURFACE),
        "immutable_contracts": {
            "registry_identity": registry.FROZEN_IDENTITY_SHA256,
            "safety_contract_hash": validation_report["safety_contract_hash"],
            "fixed_target_names": sorted(registry.FIXED_TARGET_NAMES),
        },
        "dataset": {
            "manifest_hash": manifest.manifest_hash,
            "verification": {"checked_files": verification.checked_files, "message": verification.message},
            "locked_symbols": symbols,
            "train_input_bars": sum(len(x) for x in train_bars.values()),
            "validation_input_bars": sum(len(x) for x in validation_bars.values()),
        },
        "periods": {
            "train": [args.train_start, args.train_end_exclusive],
            "validation": [args.validation_start, args.validation_end_exclusive],
            "held_test_reserved_not_loaded": [args.held_test_start, args.held_test_end_exclusive],
        },
        "trials": trials,
        "training_selected": selected,
        "validation": {"params": selected["params"], "config_hash": _config_hash(selected["params"]), "metrics": validation_metrics},
        "selected_for_held_test": selected_for_held_test,
        "status": "READY_FOR_HELD_TEST_REVIEW" if selected_for_held_test else "NOT_SELECTED_FOR_HELD_TEST",
        "note": "No held-out September data was loaded or evaluated by this run. This artifact is research evidence, not a deployment approval.",
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, default=str))
    print(json.dumps({"output": str(output), "status": artifact["status"], "validation": validation_metrics}, indent=2), flush=True)


if __name__ == "__main__":
    main()
