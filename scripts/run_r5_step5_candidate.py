#!/usr/bin/env python3
"""R5 Step-5 sealed candidate worker.

Offline historical PAPER_APPLY only.
No live broker or Kite network access.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from collections import deque
from pathlib import Path

import pandas as pd


EXCHANGE_TZ = "Asia/Kolkata"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_hash(value) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def exchange_ts(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)

    if ts.tzinfo is None:
        return ts.tz_localize(EXCHANGE_TZ)

    return ts.tz_convert(EXCHANGE_TZ)


def utc_iso(value) -> str:
    return exchange_ts(value).tz_convert("UTC").isoformat()


def git_output(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
    ).strip()


def verify_engine_identity(root: Path, protocol: dict) -> dict:
    parent = protocol["distributed_execution"]["engine_parent_commit"]

    subprocess.check_call(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", parent, "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    changed_raw = git_output(
        root,
        "diff",
        "--name-only",
        parent,
        "--",
    )

    changed = {
        line.strip()
        for line in changed_raw.splitlines()
        if line.strip()
    }

    allowed = {
        "revision5/step5_sealed_calibration_protocol.json",
    }

    unexpected = {
        name
        for name in changed
        if (
            name not in allowed
            and not name.startswith("scripts/run_r5_step5_")
        )
    }

    if unexpected:
        raise SystemExit(
            "ENGINE_PARENT_DRIFT: "
            + ", ".join(sorted(unexpected))
        )

    return {
        "engine_parent_commit": parent,
        "git_head": git_output(root, "rev-parse", "HEAD"),
        "changed_from_parent": sorted(changed),
    }


def convert_stock_rows(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)

    if "timestamp" not in frame.columns:
        if "date" not in frame.columns:
            raise ValueError("stock rows require timestamp/date")
        frame = frame.rename(columns={"date": "timestamp"})

    frame["timestamp"] = pd.to_datetime(
        frame["timestamp"],
        errors="raise",
    )

    for name in ("open", "high", "low", "close", "volume"):
        if name not in frame.columns:
            raise ValueError(f"stock rows missing {name}")

        frame[name] = pd.to_numeric(
            frame[name],
            errors="raise",
        )

    return frame.reset_index(drop=True)


def load_stock_block(
    csv_path: Path,
    sessions: list[str],
    warmup_bars: int,
) -> tuple[pd.DataFrame, dict]:
    target_dates = [
        pd.Timestamp(x).date()
        for x in sessions
    ]

    target_set = set(target_dates)

    first_date = target_dates[0]
    last_date = target_dates[-1]

    warmup = deque(maxlen=warmup_bars)
    target: list[dict] = []
    sentinel = None

    seen_dates = set()

    with csv_path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        reader = csv.DictReader(handle)

        ts_column = (
            "timestamp"
            if "timestamp" in (reader.fieldnames or [])
            else "date"
            if "date" in (reader.fieldnames or [])
            else None
        )

        if ts_column is None:
            raise ValueError(
                f"{csv_path}: no timestamp/date column"
            )

        target_started = False

        for row in reader:
            ts = exchange_ts(row[ts_column])
            day = ts.date()

            if not target_started:
                if day < first_date:
                    warmup.append(row)
                    continue

                if day > first_date:
                    raise ValueError(
                        f"{csv_path.name}: first target session missing "
                        f"{first_date}"
                    )

                target_started = True

            if day <= last_date:
                if day not in target_set:
                    raise ValueError(
                        f"{csv_path.name}: unexpected real session "
                        f"{day} inside declared block"
                    )

                target.append(row)
                seen_dates.add(day)
                continue

            sentinel = row
            break

    if len(warmup) != warmup_bars:
        raise ValueError(
            f"{csv_path.name}: expected {warmup_bars} warmup bars, "
            f"got {len(warmup)}"
        )

    if seen_dates != target_set:
        missing = sorted(target_set - seen_dates)

        raise ValueError(
            f"{csv_path.name}: missing target sessions {missing}"
        )

    if not target:
        raise ValueError(
            f"{csv_path.name}: zero target rows"
        )

    if sentinel is None:
        raise ValueError(
            f"{csv_path.name}: boundary sentinel missing"
        )

    rows = list(warmup) + target + [sentinel]

    frame = convert_stock_rows(rows)

    warm_frame = frame.iloc[:warmup_bars]
    target_frame = frame.iloc[
        warmup_bars:-1
    ]
    sentinel_frame = frame.iloc[-1:]

    target_local = [
        exchange_ts(x)
        for x in target_frame["timestamp"]
    ]

    counts = {}

    for ts in target_local:
        key = str(ts.date())
        counts[key] = counts.get(key, 0) + 1

    audit = {
        "warmup_rows": len(warm_frame),
        "warmup_first_utc": utc_iso(
            warm_frame["timestamp"].iloc[0]
        ),
        "warmup_last_utc": utc_iso(
            warm_frame["timestamp"].iloc[-1]
        ),
        "target_rows": len(target_frame),
        "target_first_utc": utc_iso(
            target_frame["timestamp"].iloc[0]
        ),
        "target_last_utc": utc_iso(
            target_frame["timestamp"].iloc[-1]
        ),
        "target_session_counts": counts,
        "sentinel_rows": 1,
        "sentinel_utc": utc_iso(
            sentinel_frame["timestamp"].iloc[0]
        ),
    }

    if exchange_ts(
        sentinel_frame["timestamp"].iloc[0]
    ) <= exchange_ts(
        target_frame["timestamp"].iloc[-1]
    ):
        raise ValueError(
            f"{csv_path.name}: sentinel is not after target"
        )

    return frame, audit


def load_grid_prefix(
    grid_manifest: Path,
    protocol: dict,
    cutoff_utc: pd.Timestamp,
) -> tuple[dict[str, pd.DataFrame], dict]:
    manifest = json.loads(
        grid_manifest.read_text()
    )

    expected = protocol["grid_dataset"]["feeds"]
    delay = int(
        protocol["grid_dataset"][
            "availability_delay_minutes"
        ]
    )

    feeds = {}
    audit = {}

    records = {
        r["name"]: r
        for r in manifest["files"]
    }

    if set(records) != set(expected):
        raise ValueError(
            "grid feed set differs from sealed protocol"
        )

    for name in sorted(expected):
        record = records[name]
        sealed = expected[name]

        path = Path(record["path"])

        if record["sha256"] != sealed["sha256"]:
            raise ValueError(
                f"{name}: local manifest SHA mismatch"
            )

        actual_sha = sha256_file(path)

        if actual_sha != sealed["sha256"]:
            raise ValueError(
                f"{name}: physical SHA mismatch"
            )

        ts_column = sealed["timestamp_column"]

        rows = []

        with path.open(
            "r",
            newline="",
            encoding="utf-8-sig",
        ) as handle:
            reader = csv.DictReader(handle)

            if ts_column not in (reader.fieldnames or []):
                raise ValueError(
                    f"{name}: missing timestamp column "
                    f"{ts_column}"
                )

            if "close" not in (reader.fieldnames or []):
                raise ValueError(
                    f"{name}: missing close"
                )

            for row in reader:
                availability = (
                    pd.to_datetime(
                        row[ts_column],
                        utc=True,
                    )
                    + pd.Timedelta(
                        minutes=delay
                    )
                )

                if availability > cutoff_utc:
                    break

                rows.append({
                    "timestamp": availability,
                    "close": row["close"],
                })

        if not rows:
            raise ValueError(
                f"{name}: no causal grid prefix"
            )

        frame = pd.DataFrame(rows)

        frame["timestamp"] = pd.to_datetime(
            frame["timestamp"],
            utc=True,
        )

        frame["close"] = pd.to_numeric(
            frame["close"],
            errors="raise",
        )

        if (
            frame["timestamp"] > cutoff_utc
        ).any():
            raise ValueError(
                f"{name}: post-cutoff row retained"
            )

        feeds[name] = frame

        audit[name] = {
            "sha256": actual_sha,
            "rows": len(frame),
            "first_available_utc":
                frame["timestamp"].iloc[0].isoformat(),
            "last_available_utc":
                frame["timestamp"].iloc[-1].isoformat(),
            "cutoff_utc": cutoff_utc.isoformat(),
            "rows_after_cutoff_retained": 0,
        }

    return feeds, audit


def prepare_block(
    root: Path,
    protocol: dict,
    block: dict,
) -> tuple[
    dict[str, pd.DataFrame],
    dict[str, pd.DataFrame],
    dict,
]:
    from market_data_loader import MarketDataLoader
    from revision2.dataset_manifest import (
        DatasetManifest,
        verify_manifest,
    )
    from revision2_external.orchestrator import (
        Revision2ExternalEngineOrchestrator,
    )

    manifest_path = (
        root
        / protocol["stock_dataset"]["manifest"]
    )

    manifest = DatasetManifest.load(
        str(manifest_path)
    )

    if (
        manifest.manifest_hash
        != protocol["stock_dataset"]["manifest_hash"]
    ):
        raise ValueError(
            "stock manifest identity mismatch"
        )

    verification = verify_manifest(manifest)

    if not verification.valid:
        raise ValueError(
            verification.message
        )

    loader = MarketDataLoader(
        manifest.data_dir,
        synthetic_if_missing=False,
    )

    sessions = list(block["sessions"])

    warmup_bars = int(
        protocol["block_execution_contract"][
            "stock_warmup_bars_per_symbol"
        ]
    )

    frames = {}
    stock_audit = {}

    for record in sorted(
        manifest.files,
        key=lambda r: r.symbol,
    ):
        path = loader._resolve_csv(
            record.symbol
        )

        if path is None:
            raise FileNotFoundError(
                record.symbol
            )

        frame, audit = load_stock_block(
            path,
            sessions,
            warmup_bars,
        )

        frames[record.symbol] = frame
        stock_audit[record.symbol] = audit

    certified = (
        Revision2ExternalEngineOrchestrator
        .prepare_market_data(frames)
    )

    clock = (
        Revision2ExternalEngineOrchestrator
        .build_clock(
            certified,
            warmup_bars,
        )
    )

    expected_events = sum(
        item["target_rows"]
        for item in stock_audit.values()
    )

    if len(clock) != expected_events:
        raise ValueError(
            f"clock event mismatch: "
            f"{len(clock)} != {expected_events}"
        )

    target_dates = {
        pd.Timestamp(x).date()
        for x in sessions
    }

    for event in clock:
        day = exchange_ts(
            event.timestamp
        ).date()

        if day not in target_dates:
            raise ValueError(
                f"clock escaped target sessions: {day}"
            )

        if (
            event.bar_idx
            >= len(certified[event.symbol]) - 1
        ):
            raise ValueError(
                "boundary sentinel entered trading clock"
            )

    max_target_utc = max(
        exchange_ts(
            frame["timestamp"].iloc[-2]
        ).tz_convert("UTC")
        for frame in certified.values()
    )

    grid_manifest = (
        root
        / protocol["grid_dataset"][
            "local_manifest"
        ]
    )

    feeds, grid_audit = load_grid_prefix(
        grid_manifest,
        protocol,
        max_target_utc,
    )

    canonical = {
        "block": int(block["block"]),
        "sessions": sessions,
        "warmup_bars_per_symbol": warmup_bars,
        "stock": stock_audit,
        "clock_events": len(clock),
        "grid": grid_audit,
        "max_target_utc":
            max_target_utc.isoformat(),
    }

    canonical["slice_sha256"] = (
        canonical_json_hash(canonical)
    )

    return certified, feeds, canonical


def metrics(report: dict) -> dict:
    trades = report.get("trades", [])

    pnls = [
        float(
            trade.get(
                "net_pnl",
                trade.get("pnl", 0.0),
            )
        )
        for trade in trades
    ]

    gains = sum(
        x for x in pnls
        if x > 0.0
    )

    losses = -sum(
        x for x in pnls
        if x < 0.0
    )

    return {
        "fills": int(
            report.get("fills", 0)
        ),
        "completed_trades": int(
            report.get(
                "completed_trades",
                len(trades),
            )
        ),
        "net_pnl": float(
            report.get("net_pnl", 0.0)
        ),
        "gross_pnl": float(
            report.get("gross_pnl", 0.0)
        ),
        "profit_factor": (
            gains / losses
            if losses
            else (
                math.inf
                if gains
                else 0.0
            )
        ),
        "mtm_max_drawdown_fraction":
            float(
                report.get(
                    "mtm_max_drawdown_fraction",
                    0.0,
                )
            ),
        "safety_violations": int(
            report.get(
                "safety_violations",
                0,
            )
        ),
    }


def execute_block(
    root: Path,
    protocol: dict,
    block: dict,
    params: dict,
) -> dict:
    from canonical_parameter_registry import (
        CanonicalParameterRegistry,
    )
    from revision2_external.grid_context import (
        SealedGridContextProvider,
    )
    from revision2_external.orchestrator import (
        Revision2ExternalEngineOrchestrator,
    )
    from revision5.ccpp_unified_plant import (
        CentralPlantMasterDCS,
    )

    frames, feeds, audit = prepare_block(
        root,
        protocol,
        block,
    )

    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()

    errors = registry.validate_calibration_payload(
        params,
        engine="EXTERNAL",
    )

    if errors:
        raise ValueError(
            "invalid calibration payload: "
            + "; ".join(errors)
        )

    provider = SealedGridContextProvider(
        feeds["NIFTY_50_15MIN"],
        feeds["INDIA_VIX_15MIN"],
    )

    equity = float(
        protocol["block_execution_contract"][
            "starting_equity_per_block"
        ]
    )

    plant = CentralPlantMasterDCS(
        total_capital=equity,
        db_path=":memory:",
    )

    symbols = sorted(frames)

    orch = Revision2ExternalEngineOrchestrator(
        symbols,
        registry,
        calibration_overrides=params,
        starting_equity=equity,
        grid_context_provider=provider,
        real_plant_dcs=plant,
        plant_control_mode="PAPER_APPLY",
        closed_loop_mode="active_paper",
        telemetry_mode="compact",
    )

    warmup = int(
        protocol["block_execution_contract"][
            "stock_warmup_bars_per_symbol"
        ]
    )

    report = orch.run(
        frames,
        warmup=warmup,
    )

    m = metrics(report)

    canonical = {
        "block": int(block["block"]),
        "sessions": block["sessions"],
        "params": params,
        "metrics": m,
        "plant_control":
            report["plant_control"],
        "trades":
            report.get("trades", []),
        "slice_sha256":
            audit["slice_sha256"],
    }

    return {
        "audit": audit,
        "metrics": m,
        "plant_control":
            report["plant_control"],
        "trades":
            report.get("trades", []),
        "block_fingerprint":
            canonical_json_hash(canonical),
    }


def aggregate(
    block_results: list[dict],
    protocol: dict,
    stage_key: str,
) -> dict:
    completed = sum(
        r["metrics"]["completed_trades"]
        for r in block_results
    )

    fills = sum(
        r["metrics"]["fills"]
        for r in block_results
    )

    net = sum(
        r["metrics"]["net_pnl"]
        for r in block_results
    )

    gross = sum(
        r["metrics"]["gross_pnl"]
        for r in block_results
    )

    max_dd = max(
        (
            r["metrics"][
                "mtm_max_drawdown_fraction"
            ]
            for r in block_results
        ),
        default=0.0,
    )

    safety = sum(
        r["metrics"]["safety_violations"]
        for r in block_results
    )

    all_trades = [
        trade
        for result in block_results
        for trade in result["trades"]
    ]

    pnls = [
        float(
            t.get(
                "net_pnl",
                t.get("pnl", 0.0),
            )
        )
        for t in all_trades
    ]

    gains = sum(
        x for x in pnls
        if x > 0
    )

    losses = -sum(
        x for x in pnls
        if x < 0
    )

    profit_factor = (
        gains / losses
        if losses
        else (
            math.inf
            if gains
            else 0.0
        )
    )

    minimum = int(
        protocol["objective"][
            "minimum_completed_trades"
        ]
    )

    rankable = safety == 0

    if not rankable:
        score = None
        status = "SAFETY_REJECTED"
    elif completed < minimum:
        score = float(
            protocol[stage_key]["aggregation"][
                "insufficient_trade_score"
            ]
        )
        status = "INSUFFICIENT_TRADES"
    else:
        score = (
            net
            - 1_000_000.0 * max_dd
        )
        status = "RANKABLE"

    return {
        "fills": fills,
        "completed_trades": completed,
        "net_pnl": net,
        "gross_pnl": gross,
        "profit_factor": profit_factor,
        "mtm_max_drawdown_fraction": max_dd,
        "safety_violations": safety,
        "rankable": rankable,
        "score": score,
        "status": status,
    }


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--root",
        required=True,
    )

    parser.add_argument(
        "--protocol",
        required=True,
    )

    parser.add_argument(
        "--expected-protocol-sha",
        required=True,
    )

    parser.add_argument(
        "--stage",
        choices=["A", "B"],
        required=True,
    )

    parser.add_argument(
        "--block",
        type=int,
    )

    parser.add_argument(
        "--params",
    )

    parser.add_argument(
        "--audit-only",
        action="store_true",
    )

    parser.add_argument(
        "--output",
        required=True,
    )

    args = parser.parse_args()

    root = Path(args.root).resolve()
    protocol_path = Path(
        args.protocol
    ).resolve()

    sys.path.insert(
        0,
        str(root),
    )

    protocol_sha = sha256_file(
        protocol_path
    )

    if (
        protocol_sha
        != args.expected_protocol_sha
    ):
        raise SystemExit(
            "PROTOCOL_SHA_MISMATCH: "
            f"{protocol_sha}"
        )

    protocol = json.loads(
        protocol_path.read_text()
    )

    identity = verify_engine_identity(
        root,
        protocol,
    )

    worker_sha = sha256_file(
        Path(__file__).resolve()
    )

    key = (
        "stage_a"
        if args.stage == "A"
        else "stage_b"
    )

    blocks = list(
        protocol["sampling_plan"][key]
    )

    if args.block is not None:
        blocks = [
            b
            for b in blocks
            if int(b["block"]) == args.block
        ]

        if len(blocks) != 1:
            raise SystemExit(
                "BLOCK_NOT_FOUND"
            )

    if args.audit_only:
        audits = []

        for block in blocks:
            _, _, audit = prepare_block(
                root,
                protocol,
                block,
            )

            audits.append(audit)

        canonical = {
            "protocol_sha256":
                protocol_sha,
            "engine_parent_commit":
                identity[
                    "engine_parent_commit"
                ],
            "stage": args.stage,
            "blocks": audits,
        }

        result = {
            "mode": "AUDIT_ONLY",
            "host": subprocess.check_output(
                ["hostname"],
                text=True,
            ).strip(),
            "git_head":
                identity["git_head"],
            "worker_sha256":
                worker_sha,
            **canonical,
            "audit_sha256":
                canonical_json_hash(
                    canonical
                ),
        }

    else:
        if not args.params:
            raise SystemExit(
                "--params required unless --audit-only"
            )

        params = json.loads(
            Path(args.params).read_text()
        )

        expected_keys = set(
            protocol["search_surface"]
        )

        if set(params) != expected_keys:
            raise SystemExit(
                "PARAMETER_SURFACE_MISMATCH"
            )

        block_results = [
            execute_block(
                root,
                protocol,
                block,
                params,
            )
            for block in blocks
        ]

        agg = aggregate(
            block_results,
            protocol,
            key,
        )

        canonical = {
            "protocol_sha256":
                protocol_sha,
            "engine_parent_commit":
                identity[
                    "engine_parent_commit"
                ],
            "stage": args.stage,
            "params": params,
            "aggregate": agg,
            "blocks": [
                {
                    "block":
                        int(blocks[i]["block"]),
                    "sessions":
                        blocks[i]["sessions"],
                    "metrics":
                        block_results[i][
                            "metrics"
                        ],
                    "plant_control":
                        block_results[i][
                            "plant_control"
                        ],
                    "block_fingerprint":
                        block_results[i][
                            "block_fingerprint"
                        ],
                    "slice_sha256":
                        block_results[i][
                            "audit"
                        ][
                            "slice_sha256"
                        ],
                }
                for i in range(
                    len(block_results)
                )
            ],
        }

        result = {
            "mode":
                "CANDIDATE_EVALUATION",
            "host":
                subprocess.check_output(
                    ["hostname"],
                    text=True,
                ).strip(),
            "git_head":
                identity["git_head"],
            "worker_sha256":
                worker_sha,
            **canonical,
            "candidate_sha256":
                canonical_json_hash(
                    canonical
                ),
        }

    output = Path(args.output)

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
            default=str,
        ) + "\n"
    )

    summary = {
        "mode": result["mode"],
        "host": result["host"],
        "protocol_sha256":
            protocol_sha,
        "worker_sha256":
            worker_sha,
    }

    if args.audit_only:
        summary["audit_sha256"] = (
            result["audit_sha256"]
        )
        summary["blocks"] = [
            {
                "block": b["block"],
                "clock_events":
                    b["clock_events"],
                "slice_sha256":
                    b["slice_sha256"],
            }
            for b in result["blocks"]
        ]
    else:
        summary["candidate_sha256"] = (
            result["candidate_sha256"]
        )
        summary["aggregate"] = (
            result["aggregate"]
        )

    print(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
