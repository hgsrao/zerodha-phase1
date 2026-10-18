#!/usr/bin/env python3
"""Audit final-controller exit recommendations against realised paper exits."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from revision2_external.final_exit_authority_audit import build_final_exit_authority_audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", help="Full replay report or one-trade trace JSON")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    audit = build_final_exit_authority_audit(json.loads(Path(args.report).read_text()))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(audit, indent=2))
    print(json.dumps({key: audit[key] for key in (
        "trades_audited", "final_exit_decisions_observed", "verdict_counts",
        "unresolved_authority_blockers", "authority_promotion_allowed",
    )}, indent=2))


if __name__ == "__main__":
    main()
