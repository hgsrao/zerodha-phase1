"""Launch in-house sealed Ray + Optuna calibration; leaves final test untouched."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from inhouse_validation.ray_optuna_calibration import InHouseRayOptunaCalibrator


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--train-start", default="2023-09-01")
    parser.add_argument("--train-end", default="2023-09-29")
    parser.add_argument("--validation-start", default="2023-10-02")
    parser.add_argument("--validation-end", default="2023-10-06")
    parser.add_argument("--test-start", default="2023-10-09")
    parser.add_argument("--test-end", default="2023-10-13")
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--max-concurrent-trials", type=int, default=2)
    parser.add_argument("--validation-finalists", type=int, default=2)
    parser.add_argument("--output", default="diagnostic_output/inhouse_ray_optuna_202309.json")
    args = parser.parse_args()
    result = InHouseRayOptunaCalibrator(
        manifest_path=args.manifest, data_dir=args.data_dir,
        train_start=args.train_start, train_end=args.train_end,
        validation_start=args.validation_start, validation_end=args.validation_end,
    ).run(samples=args.samples, max_concurrent_trials=args.max_concurrent_trials,
          validation_finalists=args.validation_finalists)
    payload = {"kind": "inhouse_sealed_ray_optuna_calibration",
               "train_period": f"{args.train_start} to {args.train_end}",
               "validation_period": f"{args.validation_start} to {args.validation_end}",
               "untouched_test_period": f"{args.test_start} to {args.test_end}",
               **asdict(result)}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(json.dumps({"selected": result.selected_params is not None,
                      "training_trials": len(result.training_trials),
                      "validation_trials": len(result.validation_trials),
                      "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()
