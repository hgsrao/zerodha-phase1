"""Write the deterministic three-loop dry-run trace as JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from revision2_external.closed_loop_dry_run import build_closed_loop_cascade_dry_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=Path("diagnostic_output/closed_loop_cascade_dry_run.json"),
    )
    args = parser.parse_args()
    report = build_closed_loop_cascade_dry_run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": report["status"], "events": len(report["events"])}, indent=2))


if __name__ == "__main__":
    main()
