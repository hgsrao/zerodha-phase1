"""Launch a small sealed intraday calibration without touching the final test days."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from revision4.v3_intraday_calibration import V3IntradayCalibrator


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate V3 intraday economics on a sealed train/validation split")
    parser.add_argument("--train-start", default="2023-09-01")
    parser.add_argument("--train-end", default="2023-09-15")
    parser.add_argument("--validation-start", default="2023-09-18")
    parser.add_argument("--validation-end", default="2023-09-22")
    parser.add_argument("--phase1-trials", type=int, default=6)
    parser.add_argument("--phase2-generations", type=int, default=1)
    parser.add_argument("--phase3-iterations", type=int, default=1)
    parser.add_argument("--validation-finalists", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260909)
    args = parser.parse_args()

    calibrator = V3IntradayCalibrator(
        args.train_start, args.train_end,
        args.validation_start, args.validation_end,
        seed=args.seed,
    )
    result = calibrator.run(
        phase1_trials=args.phase1_trials,
        phase2_generations=args.phase2_generations,
        phase3_iterations=args.phase3_iterations,
        validation_finalists=args.validation_finalists,
    )
    payload = {
        "kind": "sealed_v3_intraday_calibration",
        "calibration_period": f"{args.train_start} to {args.train_end}",
        "validation_period": f"{args.validation_start} to {args.validation_end}",
        "untouched_test_period": "2023-09-25 to 2023-09-29",
        "selected_params": result.selected_params,
        "selected_validation_report": result.selected_validation_report,
        "training_trials": [asdict(trial) for trial in result.training_trials],
        "validation_trials": [asdict(trial) for trial in result.validation_trials],
    }
    output = Path("diagnostic_output/v3_intraday_calibration_202309.json")
    output.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(json.dumps({
        "selected": result.selected_params is not None,
        "training_trials": len(result.training_trials),
        "validation_trials": len(result.validation_trials),
        "report": str(output),
    }, indent=2))


if __name__ == "__main__":
    main()
