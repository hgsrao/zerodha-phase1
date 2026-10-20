#!/usr/bin/env python3
"""Fail-closed export of Stage-C TRAINING trades for the R5 diagnostics.

The Stage-C output lives under the quarantined outputs/r5_step6_verification_state/.  The
diagnostic (r5_entry_edge_audit.py) keeps refusing that path.  This exporter is the single,
deliberate bridge: it reads exactly ONE named Stage-C result file and writes a training-only copy
outside the quarantine -- or writes nothing at all.

Before anything is written, every one of these must hold:
  * the source is a single JSON file in the worker's candidate-result layout
    (params / fixed_parameters, blocks[].sessions, blocks[].trades);
  * the effective parameters equal the frozen Stage-B winner exactly (--expected-params);
  * the sessions covered equal the TRAIN session list exactly (--train-sessions; 453 expected);
  * every session and every trade entry/exit timestamp lies in [2023-09-01, 2025-07-01);
  * no session or trade timestamp falls on a validation session (--validation-sessions);
Only block numbers, session lists and completed trades are exported (no metrics, no aggregates).
The export records the SHA-256 of the source, the session lists and the export itself.

    python scripts/diagnostics/export_stage_c_train.py \\
        --source <the one Stage-C training result JSON> \\
        --expected-params revision5/calibrated_parameters.json \\
        --train-sessions <453 train sessions JSON> --validation-sessions <126 validation sessions JSON> \\
        --label stage_c_train_trial_007
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPORT_ROOT = ROOT / "outputs/diagnostics/stage_c_train_export"
EXPORT_KIND = "r5_stage_c_train_v1"
TRAIN_START = date(2023, 9, 1)          # inclusive
TRAIN_END = date(2025, 7, 1)            # exclusive
EXPECTED_TRAIN_SESSIONS = 453


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _day(value) -> date:
    return date.fromisoformat(str(value)[:10])


def _session_list(path: Path) -> list:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        for key in ("sessions", "train_sessions", "validation_sessions", "dates"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list) or not data:
        raise SystemExit(f"SESSION_LIST_UNREADABLE: {path} (expected a JSON list of YYYY-MM-DD)")
    days = [str(d)[:10] for d in data]
    if len(set(days)) != len(days):
        raise SystemExit(f"SESSION_LIST_DUPLICATES: {path}")
    return days


def build_export(source: dict, expected_params: dict, train: list, validation: list) -> dict:
    """Validate and build the training-only export; raises SystemExit on any violation."""
    if not isinstance(source, dict) or not isinstance(source.get("blocks"), list):
        keys = sorted(source) if isinstance(source, dict) else type(source).__name__
        raise SystemExit(f"SCHEMA_UNSUPPORTED: expected the worker candidate-result layout; top-level keys {keys}")
    effective = {**(source.get("fixed_parameters") or {}), **(source.get("params") or {})}
    if effective != expected_params:
        diff = sorted(set(effective) ^ set(expected_params)
                      | {k for k in set(effective) & set(expected_params) if effective[k] != expected_params[k]})
        raise SystemExit(f"PARAMS_MISMATCH: keys differing from the frozen Stage-B winner: {diff}")

    train_set, validation_set = set(train), set(validation)
    if train_set & validation_set:
        raise SystemExit("SESSION_LISTS_OVERLAP: train and validation lists share sessions")
    for d in train:
        if not (TRAIN_START <= _day(d) < TRAIN_END):
            raise SystemExit(f"TRAIN_LIST_OUT_OF_WINDOW: {d}")

    seen, blocks = [], []
    for b in source["blocks"]:
        sessions = [str(s)[:10] for s in b.get("sessions", [])]
        if not sessions or not isinstance(b.get("trades"), list):
            raise SystemExit(f"SCHEMA_UNSUPPORTED: block {b.get('block')} lacks sessions or trades")
        for s in sessions:
            if s in validation_set:
                raise SystemExit(f"VALIDATION_SESSION_PRESENT: {s} (block {b.get('block')})")
            if not (TRAIN_START <= _day(s) < TRAIN_END):
                raise SystemExit(f"SESSION_OUT_OF_TRAIN_WINDOW: {s}")
            if s not in train_set:
                raise SystemExit(f"SESSION_NOT_IN_TRAIN_LIST: {s}")
        block_days = set(sessions)
        for t in b["trades"]:
            for field in ("entry_timestamp", "exit_timestamp"):
                d = _day(t[field])
                if d.isoformat() in validation_set:
                    raise SystemExit(f"VALIDATION_TRADE_PRESENT: {t.get('trade_id')} {field} {t[field]}")
                if not (TRAIN_START <= d < TRAIN_END):
                    raise SystemExit(f"TRADE_OUT_OF_TRAIN_WINDOW: {t.get('trade_id')} {field} {t[field]}")
                if d.isoformat() not in block_days:
                    raise SystemExit(f"TRADE_OUTSIDE_ITS_BLOCK: {t.get('trade_id')} {field} {t[field]}")
        seen.extend(sessions)
        blocks.append({"block": int(b["block"]), "sessions": sessions, "trades": b["trades"]})
    if len(seen) != len(set(seen)):
        raise SystemExit("SESSION_REPEATED_ACROSS_BLOCKS")
    if set(seen) != train_set:
        missing, extra = sorted(train_set - set(seen)), sorted(set(seen) - train_set)
        raise SystemExit(f"TRAIN_COVERAGE_MISMATCH: missing {missing[:5]}... ({len(missing)}), extra {extra[:5]} ({len(extra)})")
    if len(train_set) != EXPECTED_TRAIN_SESSIONS:
        raise SystemExit(f"TRAIN_SESSION_COUNT: {len(train_set)} != {EXPECTED_TRAIN_SESSIONS}")
    return {"export_kind": EXPORT_KIND, "params": expected_params, "window": [TRAIN_START.isoformat(),
            TRAIN_END.isoformat()], "train_sessions": len(train_set), "blocks": blocks}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", type=Path, required=True, help="the ONE Stage-C training result JSON")
    parser.add_argument("--expected-params", type=Path, required=True, help="frozen Stage-B winner parameters JSON")
    parser.add_argument("--train-sessions", type=Path, required=True)
    parser.add_argument("--validation-sessions", type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args(argv)

    if not args.source.is_file() or args.source.suffix != ".json":
        parser.error("--source must be a single existing .json file")
    out = (EXPORT_ROOT / f"{args.label}.json").resolve()
    if EXPORT_ROOT.resolve() not in out.parents or "r5_step6" in str(out):
        parser.error("export must stay under outputs/diagnostics/stage_c_train_export/")
    if out.exists():
        parser.error(f"refusing to overwrite an existing export: {out}")

    expected = json.loads(args.expected_params.read_text())
    train, validation = _session_list(args.train_sessions), _session_list(args.validation_sessions)
    document = build_export(json.loads(args.source.read_text()), expected, train, validation)
    document["provenance"] = {
        "source_sha256": _sha(args.source), "expected_params_sha256": _sha(args.expected_params),
        "train_sessions_sha256": _sha(args.train_sessions),
        "validation_sessions_sha256": _sha(args.validation_sessions)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n")
    export_sha = _sha(out)
    (out.parent / f"{args.label}.sha256").write_text(f"{export_sha}  {out.name}\n")
    trades = sum(len(b["trades"]) for b in document["blocks"])
    print(f"EXPORTED {out}\n  blocks {len(document['blocks'])}  sessions {document['train_sessions']}  trades {trades}\n"
          f"  source_sha256 {document['provenance']['source_sha256']}\n  export_sha256 {export_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
