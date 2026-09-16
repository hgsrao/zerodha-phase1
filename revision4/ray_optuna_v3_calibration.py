"""Ray Tune + Optuna search over the sealed Revision 4 training evaluator.

This module intentionally does *not* share a preloaded data dictionary through
Ray.  Each worker calls the ordinary sealed evaluator, which performs manifest
verification and creates an independent shared-portfolio replay.  That avoids
the invalid ``ray.get(dict)`` pattern in the retired external_v1.1 adapter and
makes every trial independently reproducible.

Only economic parameters explicitly supplied by the caller may be searched.
The canonical registry remains the authority for whether a parameter is
calibratable; immutable safety-contract values cannot enter this search space.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

import ray
from ray import tune
from ray.tune import RunConfig
from ray.tune.search.optuna import OptunaSearch

from canonical_parameter_registry import CanonicalParameterRegistry, ParameterSpec
from revision4.v3_intraday_calibration import (
    DEFAULT_ECONOMIC_PARAMETERS,
    REQUIRED_TEN_BOX_PATHS,
    V3IntradayCalibrator,
)
from revision4.validate_48symbol_sealed import DATA_DIR, MANIFEST_PATH, run_48symbol_validation


# Ray's result table needs a finite scalar.  The boolean ``eligible`` remains
# authoritative; this sentinel must never be treated as an economic score.
INELIGIBLE_SCORE = -1.0e18
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ABSOLUTE_MANIFEST_PATH = str((REPOSITORY_ROOT / MANIFEST_PATH).resolve())


def validated_parameter_specs(parameter_names: Iterable[str]) -> Dict[str, ParameterSpec]:
    """Return only numeric, canonically calibratable economic parameters."""
    names = tuple(parameter_names)
    if not names:
        raise ValueError("at least one economic parameter is required")
    registry = CanonicalParameterRegistry()
    specs: Dict[str, ParameterSpec] = {}
    failures: List[str] = []
    for name in names:
        try:
            spec = registry.get(name)
        except KeyError:
            failures.append(f"unknown parameter {name}")
            continue
        if not spec.calibratable or spec.param_type not in {"int", "float"}:
            failures.append(f"non-calibratable or non-numeric parameter {name}")
            continue
        specs[name] = spec
    if failures:
        raise ValueError("invalid calibration surface: " + "; ".join(failures))
    return specs


def search_space_for(parameter_names: Iterable[str]) -> Dict[str, Any]:
    """Build a Ray domain directly from frozen registry bounds."""
    space: Dict[str, Any] = {}
    for name, spec in validated_parameter_specs(parameter_names).items():
        if spec.param_type == "int":
            # ``randint`` has an exclusive upper bound.
            space[name] = tune.randint(int(spec.minimum), int(spec.maximum) + 1)
        else:
            space[name] = tune.uniform(float(spec.minimum), float(spec.maximum))
    return space


def normalize_trial_parameters(config: Mapping[str, Any], parameter_names: Iterable[str]) -> Dict[str, Any]:
    """Restore registry numeric types after Ray samples a configuration."""
    specs = validated_parameter_specs(parameter_names)
    return {
        name: int(config[name]) if spec.param_type == "int" else float(config[name])
        for name, spec in specs.items()
    }


def evaluate_sealed_trial(
    config: Mapping[str, Any],
    *,
    train_start: str,
    train_end: str,
    parameter_names: Tuple[str, ...],
    manifest_path: str = ABSOLUTE_MANIFEST_PATH,
    data_dir: str = DATA_DIR,
    evaluator: Callable[..., Dict[str, Any]] = run_48symbol_validation,
) -> None:
    """Ray worker function: execute exactly one independent sealed train replay."""
    params = normalize_trial_parameters(config, parameter_names)
    report = evaluator(
        manifest_path=manifest_path,
        data_dir=data_dir,
        month_start=train_start,
        month_end=train_end,
        calibration_overrides=params,
    )
    raw_score = V3IntradayCalibrator._score(report)
    eligible = math.isfinite(raw_score)
    intraday = report.get("intraday_audit", {})
    reconciliation = report.get("reconciliation", {})
    calls = report.get("ten_box_audit", {}).get("calls", {})
    complete_paths = not REQUIRED_TEN_BOX_PATHS.difference(
        name for name, count in calls.items() if count > 0
    )
    # Ray 2.58 requires one mapping here; keyword arguments are not accepted.
    tune.report({
        "score": float(raw_score) if eligible else INELIGIBLE_SCORE,
        "eligible": eligible,
        "net_pnl": float(report.get("financials", {}).get("realized_pnl", 0.0)),
        "completed_trades": int(intraday.get("completed_trade_count", 0)),
        "status": str(report.get("status", "UNKNOWN")),
        "reconciliation_exact": bool(reconciliation.get("exact")),
        "all_trades_same_session": bool(intraday.get("all_trades_same_session")),
        "ten_box_paths_complete": complete_paths,
    })


@dataclass(frozen=True)
class RayOptunaCalibrationResult:
    training_trials: List[Dict[str, Any]]
    validation_trials: List[Dict[str, Any]]
    selected_params: Optional[Dict[str, Any]]
    selected_validation_report: Optional[Dict[str, Any]]
    artifact_path: str


class RayOptunaV3Calibrator:
    """Parallel train-only search with sequential, non-feedback validation."""

    def __init__(
        self,
        train_start: str,
        train_end: str,
        validation_start: str,
        validation_end: str,
        parameter_names: Iterable[str] = DEFAULT_ECONOMIC_PARAMETERS,
        evaluator: Callable[..., Dict[str, Any]] = run_48symbol_validation,
        manifest_path: str = ABSOLUTE_MANIFEST_PATH,
        data_dir: str = DATA_DIR,
        seed: int = 20260909,
    ) -> None:
        self.train_start, self.train_end = train_start, train_end
        self.validation_start, self.validation_end = validation_start, validation_end
        self.parameter_names = tuple(parameter_names)
        validated_parameter_specs(self.parameter_names)
        self.evaluator = evaluator
        self.manifest_path = str(Path(manifest_path).resolve())
        self.data_dir = str(Path(data_dir).resolve())
        self.seed = seed

    def run(
        self,
        samples: int = 2,
        max_concurrent_trials: int = 2,
        validation_finalists: int = 1,
        storage_path: str | Path = "diagnostic_output/ray_optuna_results",
    ) -> RayOptunaCalibrationResult:
        """Run bounded parallel training, then validate eligible finalists once."""
        if samples < 1:
            raise ValueError("samples must be positive")
        if not 1 <= max_concurrent_trials <= 2:
            raise ValueError("max_concurrent_trials must be between 1 and 2")
        if validation_finalists < 1:
            raise ValueError("validation_finalists must be positive")

        root = Path(storage_path).resolve()
        root.mkdir(parents=True, exist_ok=True)
        trainable = tune.with_resources(
            tune.with_parameters(
                evaluate_sealed_trial,
                train_start=self.train_start,
                train_end=self.train_end,
                parameter_names=self.parameter_names,
                manifest_path=self.manifest_path,
                data_dir=self.data_dir,
                evaluator=self.evaluator,
            ),
            {"cpu": 1},
        )
        # No ray.put()/ray.get() data path: every worker seals and loads its own
        # manifest-defined train window.
        tuner = tune.Tuner(
            trainable,
            param_space=search_space_for(self.parameter_names),
            tune_config=tune.TuneConfig(
                metric="score",
                mode="max",
                num_samples=samples,
                max_concurrent_trials=max_concurrent_trials,
                search_alg=OptunaSearch(metric="score", mode="max", seed=self.seed),
            ),
            run_config=RunConfig(
                name="rev4_sealed_ray_optuna_train",
                storage_path=str(root),
                verbose=1,
            ),
        )
        results = tuner.fit()
        training_trials: List[Dict[str, Any]] = []
        for result in results:
            metrics = dict(result.metrics)
            params = normalize_trial_parameters(result.config, self.parameter_names)
            training_trials.append({"params": params, "metrics": metrics})

        finalists = sorted(
            (trial for trial in training_trials if trial["metrics"].get("eligible") is True),
            key=lambda trial: float(trial["metrics"]["score"]),
            reverse=True,
        )[:validation_finalists]
        validation_trials: List[Dict[str, Any]] = []
        for finalist in finalists:
            report = self.evaluator(
                manifest_path=self.manifest_path,
                data_dir=self.data_dir,
                month_start=self.validation_start,
                month_end=self.validation_end,
                calibration_overrides=finalist["params"],
            )
            score = V3IntradayCalibrator._score(report)
            validation_trials.append({
                "params": finalist["params"],
                "score": float(score) if math.isfinite(score) else INELIGIBLE_SCORE,
                "eligible": math.isfinite(score),
                "report": report,
            })

        # A calibration run may rank losing candidates for diagnostic purposes,
        # but no losing validation result can be promoted as a configuration.
        winners = [
            trial for trial in validation_trials
            if trial["eligible"] and trial["score"] > 0.0
        ]
        winner = max(winners, key=lambda trial: trial["score"]) if winners else None
        return RayOptunaCalibrationResult(
            training_trials=training_trials,
            validation_trials=validation_trials,
            selected_params=None if winner is None else winner["params"],
            selected_validation_report=None if winner is None else winner["report"],
            artifact_path=str(root),
        )
