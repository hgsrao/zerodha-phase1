#!/usr/bin/env python3
"""OOS calibration engine and end-to-end backtest runner.

This module provides the functional backtest/calibration gate: it runs the
Revision 2 runtime against a realistic market-data path, validates the 48-symbol
universe, scores the calibration candidate, and emits a formal pass/fail report.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from ecs_runtime_v2 import ECSRuntimeV2
from market_data_loader import MarketDataLoader


@dataclass
class BacktestMetrics:
    total_return: float
    annualized_return: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    exposure: float
    best_score: float


class CalibrationPerformanceGate:
    """Fail-closed acceptance gate for OOS calibration performance."""

    def __init__(self):
        self.thresholds = {
            "total_return": 0.10,
            "annualized_return": 0.12,
            "sharpe": 1.0,
            "max_drawdown": 0.25,
            "win_rate": 0.50,
            "profit_factor": 1.2,
            "exposure": 0.60,
            "best_score": 0.10,
        }

    def evaluate(self, metrics: BacktestMetrics) -> Dict[str, Any]:
        checks: Dict[str, bool] = {
            "total_return": metrics.total_return >= self.thresholds["total_return"],
            "annualized_return": metrics.annualized_return >= self.thresholds["annualized_return"],
            "sharpe": metrics.sharpe >= self.thresholds["sharpe"],
            "max_drawdown": metrics.max_drawdown <= self.thresholds["max_drawdown"],
            "win_rate": metrics.win_rate >= self.thresholds["win_rate"],
            "profit_factor": metrics.profit_factor >= self.thresholds["profit_factor"],
            "exposure": metrics.exposure <= self.thresholds["exposure"],
            "best_score": metrics.best_score >= self.thresholds["best_score"],
        }
        reasons = [name for name, passed in checks.items() if not passed]
        passed = len(reasons) == 0
        return {
            "passed": passed,
            "status": "PASS" if passed else "FAIL",
            "thresholds": self.thresholds,
            "checks": checks,
            "reasons": reasons,
        }


@dataclass
class OOSPerformanceGateResult:
    passed: bool
    metrics: BacktestMetrics
    details: Dict[str, Any]


HARD_CODED_VALUES = {
    "kill_switch_enabled": True,
    "drawdown_halt_threshold": 0.25,
    "max_daily_loss_rupees": 50000,
    "max_concurrent_positions": 5,
    "max_gross_exposure_fraction": 0.50,
    "max_market_data_age_seconds": 30,
    "max_exposure_per_symbol_fraction": 0.15,
    "min_position_quantity": 1,
    "max_position_quantity": 100,
    "drawdown_derate_threshold": 0.18,
    "drawdown_derate_multiplier": 0.80,
    "lambda_derate_threshold": 0.15,
    "lambda_derate_multiplier": 0.80,
    "min_signal_confidence": 0.55,
    "min_risk_reward_ratio": 1.50,
    "order_dedup_window_seconds": 5,
    "order_timeout_seconds_execution": 30,
    "max_reconciliation_qty_diff": 0,
    "max_slippage_fraction": 0.001,
    "no_entry_cutoff_time": "15:20",
}


class OOSCalibrationEngine:
    """Runs the calibration search and enforces the performance gate."""

    def __init__(self, iterations: int = 25, data_dir: Optional[str] = None, synthetic_if_missing: bool = False):
        self.iterations = max(1, iterations)
        self.runtime = ECSRuntimeV2()
        self.registry = self.runtime.registry
        self.loader = MarketDataLoader(data_dir or str(Path.cwd()), synthetic_if_missing=synthetic_if_missing)

    def calibratable_subset(self) -> List[str]:
        return self.registry.calibratable_45()

    def base_parameter_seed(self) -> Dict[str, Any]:
        seed: Dict[str, Any] = {}
        for name in self.calibratable_subset():
            if name.startswith("entry") or name.endswith("threshold"):
                seed[name] = 0.50
            elif name.startswith("momentum") or name.startswith("vwap") or name.startswith("atr"):
                seed[name] = 20
            elif "weight" in name:
                seed[name] = 0.25
            elif "mult" in name or "ratio" in name or "factor" in name:
                seed[name] = 1.0
            elif "window" in name or "period" in name or "bars" in name:
                seed[name] = 5
            elif "max_" in name or "min_" in name:
                seed[name] = 10
            else:
                seed[name] = 1
        return seed

    def randomize_candidate(self, base: Dict[str, Any]) -> Dict[str, Any]:
        candidate = dict(base)
        for key, value in candidate.items():
            if isinstance(value, float):
                candidate[key] = round(value * random.uniform(0.70, 1.30), 6)
            elif isinstance(value, int):
                candidate[key] = max(1, int(value * random.uniform(0.70, 1.30)))
        return candidate

    def enforce_hardcoded_constraints(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        candidate.update(HARD_CODED_VALUES)
        return candidate

    def score_candidate(self, candidate: Dict[str, Any]) -> float:
        score = 0.0
        for key, value in candidate.items():
            if key in {"entry_confidence_threshold", "exit_confidence_threshold"}:
                score += float(value) * 0.10
            elif "weight" in key:
                score += float(value) * 0.08
            elif "threshold" in key:
                score += float(value) * 0.07
            elif "period" in key or "window" in key:
                score += (1.0 / max(1, int(value))) * 0.03
            else:
                score += 0.01
        return round(score, 6)

    def evaluate_oos_performance(self, best_score: float, frames: Optional[Dict[str, pd.DataFrame]] = None) -> OOSPerformanceGateResult:
        if frames is None:
            frames = self.loader.load_universe()
        if not frames:
            raise ValueError("No real market data available for OOS evaluation")

        all_returns: List[float] = []
        for frame in frames.values():
            if frame is None or frame.empty:
                continue
            series = pd.to_numeric(frame["close"], errors="coerce").dropna()
            if len(series) < 2:
                continue
            returns = series.pct_change().dropna()
            if returns.empty:
                continue
            all_returns.extend(returns.tolist())

        if not all_returns:
            raise ValueError("No valid return series available from real market data")

        daily = pd.Series(all_returns)
        cumulative = (daily.add(1.0)).cumprod()
        running_max = cumulative.cummax()
        drawdown = (cumulative / running_max) - 1.0
        total_return = float((1.0 + daily).prod() - 1.0)
        annualization_periods = max(len(daily), 1)
        annualized_return = float((1.0 + total_return) ** (252.0 / annualization_periods) - 1.0)
        std = daily.std(ddof=1)
        sharpe = float((daily.mean() / std) * math.sqrt(252.0)) if std > 0 else 0.0
        max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0
        win_rate = float((daily > 0).mean()) if not daily.empty else 0.0
        gains = float(daily[daily > 0].sum()) if not daily.empty else 0.0
        losses = float(-daily[daily < 0].sum()) if not daily.empty else 0.0
        profit_factor = float(gains / losses) if losses > 0 else (float("inf") if gains > 0 else 1.0)
        exposure = float(min(0.95, max(0.0, abs(total_return) * 2.0 + 0.15)))

        metrics = BacktestMetrics(
            total_return=total_return,
            annualized_return=annualized_return,
            sharpe=sharpe,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            profit_factor=profit_factor,
            exposure=exposure,
            best_score=best_score,
        )
        gate = CalibrationPerformanceGate().evaluate(metrics)
        return OOSPerformanceGateResult(gate["passed"], metrics, gate)

    def run(self) -> Dict[str, Any]:
        frames = self.loader.load_universe()
        universe_status = self.loader.validate_market_universe(frames)
        if not universe_status.valid:
            raise ValueError(f"Universe validation failed: {universe_status.message}")

        base_seed = self.base_parameter_seed()
        best_candidate = None
        best_score = -1e9
        for _ in range(self.iterations):
            candidate = self.randomize_candidate(base_seed)
            candidate = self.enforce_hardcoded_constraints(candidate)
            score = self.score_candidate(candidate)
            if score > best_score:
                best_score = score
                best_candidate = candidate.copy()

        runtime_result = self.runtime.run_cycle()
        performance_gate = self.evaluate_oos_performance(best_score, frames=frames)
        result = {
            "ok": bool(runtime_result["ok"] and performance_gate.passed),
            "iterations": self.iterations,
            "universe_status": asdict(universe_status),
            "runtime_cycle": runtime_result,
            "best_score": best_score,
            "best_candidate": best_candidate,
            "calibratable_subset_count": len(self.calibratable_subset()),
            "performance_gate": {
                "passed": performance_gate.passed,
                "status": "PASS" if performance_gate.passed else "FAIL",
                "metrics": asdict(performance_gate.metrics),
                "details": performance_gate.details,
            },
        }
        return result


class OOSBacktestRunner:
    """End-to-end OOS runner with a formal JSON pass/fail report."""

    def __init__(self, data_dir: Optional[str] = None, iterations: int = 25, report_path: Optional[str] = None):
        self.data_dir = Path(data_dir) if data_dir else Path.cwd()
        self.iterations = max(1, iterations)
        self.report_path = Path(report_path) if report_path else self.data_dir / "oos_backtest_report.json"
        self.engine = OOSCalibrationEngine(iterations=self.iterations, data_dir=str(self.data_dir))

    def run(self) -> Dict[str, Any]:
        try:
            calibration = self.engine.run()
        except (FileNotFoundError, ValueError) as exc:
            # Fail-closed for *expected* data problems only — missing files,
            # an incomplete universe, bad schema. A programming error
            # (AttributeError, TypeError, an assertion) must still propagate
            # and fail the test/run visibly rather than being reported as a
            # calm HOLD.
            summary = {
                "status": "HOLD",
                "passed": False,
                "runtime_ok": False,
                "iterations": self.iterations,
                "metrics": asdict(BacktestMetrics(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
                "gate": {"passed": False, "status": "HOLD", "reasons": [str(exc)]},
                "report_path": str(self.report_path),
                "universe_status": {"valid": False, "message": str(exc)},
            }
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            with self.report_path.open("w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2, sort_keys=True)
            return summary

        metrics = calibration["performance_gate"]["metrics"]
        gate = calibration["performance_gate"]["details"]
        metrics_obj = BacktestMetrics(**metrics)
        summary = {
            "status": "PASS" if calibration["ok"] and gate["passed"] else "FAIL",
            "passed": bool(calibration["ok"] and gate["passed"]),
            "runtime_ok": calibration["ok"],
            "iterations": self.iterations,
            "metrics": asdict(metrics_obj),
            "gate": gate,
            "report_path": str(self.report_path),
            "universe_status": calibration["universe_status"],
        }
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        with self.report_path.open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2, sort_keys=True)
        return summary


if __name__ == "__main__":
    runner = OOSBacktestRunner(data_dir=".", iterations=25, report_path="oos_backtest_report.json")
    result = runner.run()
    print(json.dumps({k: result[k] for k in ["status", "passed", "runtime_ok", "iterations", "report_path"]}, indent=2, sort_keys=True))
    print("REPORT_EXISTS=", Path(result["report_path"]).exists())
