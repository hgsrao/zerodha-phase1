#!/usr/bin/env python3
"""Canonical registry for the frozen Revision 2 engine target surface."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from calibration_config import Revision2ParameterManifest


@dataclass
class ParameterSpec:
    name: str
    black_box: str
    param_type: str
    default: Any
    minimum: Any
    maximum: Any
    calibratable: bool = True
    notes: str = ""


class CanonicalParameterRegistry:
    CONTRACT_ID = "ECS_REVISION_2_PARAMETER_SURFACE_V1"
    # Updated deliberately, three times now:
    # 1. minimum_absolute_profit_rupees (a fixed per-share rupee constant,
    #    checked before quantity existed) was replaced with
    #    minimum_profit_margin_over_cost (a scale-invariant cost-margin
    #    fraction, checked post-sizing against the real round-trip cost).
    # 2. rebalance_frequency_minutes was replaced with
    #    trailing_stop_atr_mult. rebalance_frequency_minutes was confirmed
    #    dead in BOTH engines (read via req() for coverage tracking only --
    #    the real PyPortfolioOpt refit cadence is a hardcoded constant,
    #    PORTFOLIO_WEIGHT_REFIT_EVERY_BARS -- and it was already in
    #    FIXED_TARGET_NAMES, non-calibratable, so removing it changes no
    #    calibratable-parameter count anywhere). trailing_stop_atr_mult is
    #    the ATR multiplier for continuous_exit_controller.py's real,
    #    per-bar-recomputed trailing stop -- previously borrowed
    #    stop_loss_atr_mult (tuned for a one-shot entry-time stop) for a
    #    continuously re-measured droop, which real data showed was far
    #    too tight (INFY's real median single-bar range is ~0.95x its own
    #    median ATR -- a 1x-ATR-wide continuous stop barely survives ONE
    #    bar, let alone a multi-bar hold). This is a genuinely new,
    #    independently-calibratable control, not a rename.
    # 3. saturation_exit_bars was added (not swapped) to expose the
    #    ContinuousExitController's PID saturation-exit streak threshold
    #    (default 5) to the automated optimizer. This lets the calibration
    #    engine tune the joint space of (trailing_stop_atr_mult ×
    #    saturation_exit_bars) to find the combination that actually lets
    #    saturation_exit fire on real data, rather than being perpetually
    #    starved by a stop that closes trades in 1-2 bars. This is a
    #    deliberate expansion, net +1 calibratable (68→69 total).
    # Parameter count changed twice: first 68/20 (both), then 69/20 with
    # saturation_exit_bars. These changes are exactly what this hash tracks.
    FROZEN_IDENTITY_SHA256 = "963b6cb434e892b0ffb4ed608e66e8f9793bc7c46bfae895505605e023a2ff26"
    SAFETY_ALIASES = {
        "drawdown_halt_threshold": "safety_drawdown_halt_threshold",
        "min_risk_reward_ratio": "safety_min_risk_reward_ratio",
    }
    CORE_SAFETY_KEYS = {
        "kill_switch_enabled",
        "safety_drawdown_halt_threshold",
        "max_daily_loss_rupees",
        "max_concurrent_positions",
    }

    LEGACY_SAFETY_ALIASES = {v: k for k, v in SAFETY_ALIASES.items()}

    FIXED_TARGET_NAMES = {
        "data_validation_mode",
        "drawdown_derated_threshold",
        "drawdown_halt_threshold",
        "drawdown_normal_threshold",
        "exclude_symbols",
        "limit_order_offset_percent",
        "lot_size_by_symbol",
        "max_loss_per_day_rupees",
        "max_loss_per_trade_rupees",
        "max_retry_attempts",
        "max_sector_exposure_fraction",
        "max_symbol_concentration",
        "order_timeout_seconds",
        "order_type",
        "phase1_exploration_intensity",
        "phase2_optimization_intensity",
        "portfolio_lambda_risk_limit",
        "retry_delay_seconds",
        "slippage_tolerance_percent",
        "symbols_to_trade",
        "trading_hours_end",
        "trading_hours_start",
    }
    APPROVED_CALIBRATABLE = set(Revision2ParameterManifest.all_68()) - FIXED_TARGET_NAMES

    def __init__(self):
        self.params: Dict[str, ParameterSpec] = {}
        self.safety_params: Dict[str, ParameterSpec] = {}
        self._build_registry()

    def _build_registry(self):
        entries = [
            ParameterSpec("base_dp_dt_multiplier", "PA", "float", 1.0, 0.5, 2.0, True, "Base price momentum multiplier"),
            ParameterSpec("base_dv_dt_multiplier", "PA", "float", 1.0, 0.5, 2.0, True, "Base volume momentum multiplier"),
            ParameterSpec("entry_confidence_threshold", "PA", "float", 0.50, 0.3, 0.8, True, "Minimum signal confidence"),
            ParameterSpec("exit_confidence_threshold", "ID", "float", 0.60, 0.4, 0.9, True, "Minimum exit confidence"),
            ParameterSpec("min_risk_reward_ratio", "MPC", "float", 1.50, 1.0, 3.0, True, "Minimum risk/reward"),
            ParameterSpec("profit_target_margin_buffer", "MPC", "float", 0.10, 0.0, 0.5, True, "Buffer above target"),
            ParameterSpec("vwap_weight", "PA", "float", 0.25, 0.1, 0.4, True, "VWAP weight"),
            ParameterSpec("confirmation_2bar_weight", "PA", "float", 0.25, 0.1, 0.4, True, "2-bar confirmation"),
            ParameterSpec("momentum_weight", "PA", "float", 0.25, 0.1, 0.4, True, "Momentum weight"),
            ParameterSpec("volatility_weight", "PA", "float", 0.25, 0.05, 0.4, True, "Volatility weight"),
            ParameterSpec("green_threshold", "PA", "float", 0.75, 0.6, 0.95, True, "Green signal threshold"),
            ParameterSpec("amber_threshold_lower", "PA", "float", 0.50, 0.3, 0.7, True, "Amber threshold lower bound"),
            ParameterSpec("red_threshold", "PA", "float", 0.30, 0.1, 0.5, True, "Red threshold"),
            ParameterSpec("slippage_guard_threshold", "ID", "float", 0.05, 0.01, 0.15, True, "Max slippage"),
            ParameterSpec("volatility_regime_multiplier", "PA", "float", 1.00, 0.7, 1.5, True, "Volatility regime multiplier"),
            ParameterSpec("low_vol_regime_multiplier", "PA", "float", 1.00, 0.8, 1.5, True, "Low vol multiplier"),
            ParameterSpec("medium_vol_regime_multiplier", "PA", "float", 1.00, 0.7, 1.5, True, "Medium vol multiplier"),
            ParameterSpec("high_vol_regime_multiplier", "PA", "float", 1.00, 0.8, 1.5, True, "High vol multiplier"),
            ParameterSpec("profit_target_atr_mult", "MPC", "float", 1.50, 0.8, 2.5, True, "ATR profit target multiplier"),
            ParameterSpec("stop_loss_atr_mult", "MPC", "float", 0.75, 0.3, 1.2, True, "ATR stop multiplier"),
            ParameterSpec("atr_calculation_period", "PA", "int", 20, 10, 30, True, "ATR period"),
            ParameterSpec("entry_signal_smoothing_window", "PA", "int", 3, 1, 8, True, "Entry smoothing window"),
            ParameterSpec("exit_signal_smoothing_window", "PA", "int", 2, 1, 4, True, "Exit smoothing window"),
            ParameterSpec("slippage_cost_multiplier", "MPC", "float", 1.00, 0.8, 1.5, True, "Cost multiplier"),
            # Replaced minimum_absolute_profit_rupees (a fixed per-share rupee
            # proxy checked before quantity existed -- structurally unable to
            # represent whether a trade was actually worth its real cost,
            # since real round-trip cost scales with price x quantity, not a
            # fixed constant). Checked post-sizing now (SafetyGatesTargetBox.
            # evaluate_post_sizing), against the real round-trip cost for the
            # actual quantity -- see that method for the full rationale.
            ParameterSpec("minimum_profit_margin_over_cost", "SafetyGates", "float", 0.5, 0.0, 2.0, True,
                          "Required fraction by which projected total trade profit must exceed real round-trip cost"),
            ParameterSpec("momentum_calculation_period", "PA", "int", 20, 10, 30, True, "Momentum period"),
            ParameterSpec("vwap_calculation_period", "PA", "int", 20, 10, 30, True, "VWAP period"),
            ParameterSpec("signal_persistence_requirement", "PA", "float", 1.50, 1.0, 2.5, True, "Persistence requirement"),
            ParameterSpec("min_hold_bars", "MPC", "int", 2, 1, 5, True, "Minimum hold bars"),
            ParameterSpec("max_hold_bars", "MPC", "int", 60, 20, 120, True, "Maximum hold bars"),
            ParameterSpec("phase1_exploration_intensity", "UnifiedExecution", "int", 50, 30, 100, True, "Optimizer phase 1 exploration"),
            ParameterSpec("phase2_optimization_intensity", "UnifiedExecution", "int", 250, 100, 500, True, "Optimizer phase 2 intensity"),
            ParameterSpec("learning_rate_exploration_factor", "UnifiedExecution", "float", 0.05, 0.01, 0.10, True, "Meta learning rate"),
            ParameterSpec("lot_size_by_symbol", "PositionManager", "dict", {}, 0, 0, True, "Per-symbol lot sizing"),
            ParameterSpec("max_positions_live", "PositionManager", "int", 5, 1, 12, True, "Max live positions"),
            ParameterSpec("max_positions_per_symbol", "PositionManager", "int", 1, 1, 3, True, "Max per symbol"),
            ParameterSpec("capital_per_trade_fraction", "PositionManager", "float", 0.02, 0.005, 0.10, True, "Capital per trade fraction"),
            ParameterSpec("min_capital_buffer_fraction", "PositionManager", "float", 0.10, 0.05, 0.30, True, "Cash reserve fraction"),
            ParameterSpec("capital_allocation_mode", "PositionManager", "str", "equal", 0, 0, True, "Allocation mode"),
            ParameterSpec("trailing_stop_atr_mult", "MPC", "float", 3.0, 1.0, 8.0, True,
                           "Continuous exit-controller ATR trail multiplier (independent of the one-shot entry stop's stop_loss_atr_mult)"),
            ParameterSpec("drawdown_normal_threshold", "SafetyGates", "float", 0.10, 0.05, 0.20, True, "Normal drawdown threshold"),
            ParameterSpec("drawdown_derated_threshold", "SafetyGates", "float", 0.18, 0.10, 0.25, True, "Derated threshold"),
            ParameterSpec("drawdown_halt_threshold", "SafetyGates", "float", 0.25, 0.15, 0.35, True, "Hard drawdown halt"),
            ParameterSpec("max_loss_per_trade_rupees", "SafetyGates", "float", 5000, 1000, 20000, True, "Max loss per trade"),
            ParameterSpec("max_loss_per_day_rupees", "SafetyGates", "float", 50000, 10000, 150000, True, "Daily loss limit"),
            ParameterSpec("portfolio_lambda_risk_limit", "SafetyGates", "float", 0.15, 0.05, 0.30, True, "Portfolio lambda risk limit"),
            ParameterSpec("max_sector_exposure_fraction", "PositionManager", "float", 0.30, 0.10, 0.60, True, "Sector max exposure"),
            ParameterSpec("max_symbol_concentration", "PositionManager", "float", 0.05, 0.01, 0.15, True, "Single symbol cap"),
            ParameterSpec("pid_kp_entry", "MPC", "float", 0.15, 0.05, 0.30, True, "Entry KP"),
            ParameterSpec("pid_ki_entry", "MPC", "float", 0.05, 0.01, 0.20, True, "Entry KI"),
            ParameterSpec("pid_kd_entry", "MPC", "float", 0.08, 0.01, 0.20, True, "Entry KD"),
            ParameterSpec("pid_kp_exit", "MPC", "float", 0.12, 0.05, 0.25, True, "Exit KP"),
            ParameterSpec("pid_ki_exit", "MPC", "float", 0.04, 0.01, 0.15, True, "Exit KI"),
            ParameterSpec("pid_kd_exit", "MPC", "float", 0.06, 0.01, 0.15, True, "Exit KD"),
            ParameterSpec("pid_integral_window_bars", "MPC", "int", 10, 5, 30, True, "Integral window"),
            ParameterSpec("pid_integral_max_clamp", "MPC", "float", 0.10, 0.02, 0.25, True, "Integral clamp"),
            ParameterSpec("saturation_exit_bars", "MPC", "int", 5, 2, 10, True,
                           "Consecutive bars at saturation extreme before exit (both PA and studies tracks independently)"),
            ParameterSpec("pid_derivative_smoothing", "MPC", "int", 3, 1, 10, True, "Derivative smoothing"),
            ParameterSpec("order_type", "P01D", "str", "MARKET", 0, 0, True, "Execution order type"),
            ParameterSpec("limit_order_offset_percent", "P01D", "float", 0.02, 0.00, 0.05, True, "Limit offset"),
            ParameterSpec("order_timeout_seconds", "P01D", "int", 30, 5, 120, True, "Order timeout"),
            ParameterSpec("max_retry_attempts", "P01D", "int", 2, 0, 5, True, "Retry attempts"),
            ParameterSpec("retry_delay_seconds", "P01D", "int", 5, 1, 20, True, "Retry delay"),
            ParameterSpec("slippage_tolerance_percent", "P01D", "float", 0.10, 0.02, 0.20, True, "Slippage tolerance"),
            ParameterSpec("trading_hours_start", "UnifiedExecution", "str", "09:15", 0, 0, True, "Trading start"),
            ParameterSpec("trading_hours_end", "UnifiedExecution", "str", "15:30", 0, 0, True, "Trading end"),
            ParameterSpec("symbols_to_trade", "DataIngestion", "list", [], 0, 0, True, "Universe to trade"),
            ParameterSpec("exclude_symbols", "DataIngestion", "list", [], 0, 0, True, "Symbols excluded"),
            ParameterSpec("data_validation_mode", "L2DataCertifier", "str", "strict", 0, 0, True, "Validation mode"),
        ]

        safety_entries = [
            ParameterSpec("kill_switch_enabled", "StartupCapabilityLock", "bool", True, 0, 0, False, "Kill switch invariant"),
            ParameterSpec("safety_drawdown_halt_threshold", "SafetyGates", "float", 0.25, 0, 0, False, "Hard drawdown halt"),
            ParameterSpec("max_daily_loss_rupees", "SafetyGates", "float", 50000, 0, 0, False, "Daily loss hard cap"),
            ParameterSpec("max_concurrent_positions", "SafetyGates", "int", 5, 0, 0, False, "Concurrent positions cap"),
            ParameterSpec("max_gross_exposure_fraction", "SafetyGates", "float", 0.50, 0, 0, False, "Gross exposure cap"),
            ParameterSpec("max_market_data_age_seconds", "SafetyGates", "int", 30, 0, 0, False, "Market data age max"),
            ParameterSpec("max_exposure_per_symbol_fraction", "SafetyGates", "float", 0.15, 0, 0, False, "Per-symbol cap"),
            ParameterSpec("min_position_quantity", "SafetyGates", "int", 1, 0, 0, False, "Min qty"),
            ParameterSpec("max_position_quantity", "SafetyGates", "int", 100, 0, 0, False, "Max qty"),
            ParameterSpec("drawdown_derate_threshold", "SafetyGates", "float", 0.18, 0, 0, False, "Drawdown derate trigger"),
            ParameterSpec("drawdown_derate_multiplier", "SafetyGates", "float", 0.80, 0, 0, False, "Drawdown derate multiplier"),
            ParameterSpec("lambda_derate_threshold", "SafetyGates", "float", 0.15, 0, 0, False, "Lambda risk trigger"),
            ParameterSpec("lambda_derate_multiplier", "SafetyGates", "float", 0.80, 0, 0, False, "Lambda reduction factor"),
            ParameterSpec("min_signal_confidence", "PA", "float", 0.55, 0, 0, False, "Minimum signal confidence"),
            ParameterSpec("safety_min_risk_reward_ratio", "ID", "float", 1.50, 0, 0, False, "Minimum reward/risk"),
            ParameterSpec("order_dedup_window_seconds", "P01D", "int", 5, 0, 0, False, "Order dedup window"),
            ParameterSpec("order_timeout_seconds_execution", "P01D", "int", 30, 0, 0, False, "Execution timeout"),
            ParameterSpec("max_reconciliation_qty_diff", "P01D", "int", 0, 0, 0, False, "Qty reconciliation diff"),
            ParameterSpec("max_slippage_fraction", "P01D", "float", 0.001, 0, 0, False, "Max slippage fraction"),
            ParameterSpec("no_entry_cutoff_time", "UnifiedExecution", "str", "15:20", 0, 0, False, "Cutoff time"),
        ]

        target_names = Revision2ParameterManifest.all_68()
        calibratable = set(self.APPROVED_CALIBRATABLE) & set(target_names)
        for item in entries:
            item.calibratable = item.name in calibratable
            self.params[item.name] = item
        for item in safety_entries:
            self.safety_params[item.name] = item
        self.validate_contract()

    def base_33(self) -> List[str]:
        return Revision2ParameterManifest.base_33()

    def revision2_35(self) -> List[str]:
        return Revision2ParameterManifest.revision2_35()

    def hardcoded_20(self) -> List[str]:
        return sorted(self.safety_params)

    def calibratable_names(self) -> List[str]:
        return sorted([name for name, spec in self.params.items() if spec.calibratable])

    def calibratable_45(self) -> List[str]:
        # Name kept for historical continuity (same reasoning as all_68()
        # keeping its name) -- the real optimizer surface is now 46, not
        # 45 (see FROZEN_IDENTITY_SHA256's comment). Callers that need the
        # true, current count should use calibratable_names() directly,
        # not this [:45] slice, which would silently drop whichever name
        # sorts last. The only caller of this specific method is
        # oos_calibration_engine.py, a discredited, unused scoring path
        # (see revision2/calibration_supervisor.py's own module docstring)
        # -- not part of any real calibration this project runs.
        return self.calibratable_names()[:45]

    def hardcoded_names(self) -> List[str]:
        return self.hardcoded_20()

    def fixed_target_names(self) -> List[str]:
        return sorted(name for name, spec in self.params.items() if not spec.calibratable)

    def total_target_surface(self) -> int:
        return len(self.params)

    def validate_contract(self) -> None:
        expected = Revision2ParameterManifest.all_68()
        # NOTE: Adding saturation_exit_bars (2025) expands from 68 → 69 total.
        # base_33() + revision2_35() now = 33 + 36 = 69 (was 68 before saturation_exit_bars).
        if len(expected) != 69 or len(set(expected)) != 69:
            raise ValueError("Revision 2 target names must contain 69 unique values")
        if set(expected) != set(self.params):
            raise ValueError("registry does not exactly match the Revision 2 manifest")
        if len(self.safety_params) != 20:
            raise ValueError("hardcoded safety layer must contain exactly 20 values")
        if set(self.params) & set(self.safety_params):
            overlap = sorted(set(self.params) & set(self.safety_params))
            raise ValueError(f"target and safety surfaces must not overlap: {overlap}")
        calibratable = set(self.calibratable_names())
        # 46, not 45: rebalance_frequency_minutes (FIXED, non-calibratable)
        # was replaced by trailing_stop_atr_mult (genuinely calibratable) --
        # see FROZEN_IDENTITY_SHA256's comment. A like-for-like swap (fixed
        # for fixed, or calibratable for calibratable) would have kept this
        # at 45; this one is a deliberate net expansion of the real,
        # tunable surface, not a bug.
        # Further expanded by saturation_exit_bars (2025) from 46 → 47, another
        # genuine calibratable addition to Box 6's exit control surface.
        if len(calibratable) != 47:
            raise ValueError(f"optimizer surface must contain exactly 47 values; got {len(calibratable)}")
        if set(self.APPROVED_CALIBRATABLE) != calibratable:
            missing = sorted(set(self.APPROVED_CALIBRATABLE) - calibratable)
            extra = sorted(calibratable - set(self.APPROVED_CALIBRATABLE))
            raise ValueError(f"approved calibration surface mismatch: missing={missing}, extra={extra}")

    def identity_payload(self) -> Dict[str, Any]:
        return {
            "contract": self.CONTRACT_ID,
            "base_33": self.base_33(),
            "revision2_35": self.revision2_35(),
            "target_parameters": [asdict(self.params[name]) for name in sorted(self.params)],
            "hardcoded_safety": [asdict(self.safety_params[name]) for name in sorted(self.safety_params)],
        }

    def identity_sha256(self) -> str:
        payload = json.dumps(self.identity_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def verify_frozen_identity(self) -> None:
        if self.identity_sha256() != self.FROZEN_IDENTITY_SHA256:
            raise ValueError("frozen identity mismatch")

    def get(self, name: str) -> ParameterSpec:
        return self.params[name]

    def validate_calibration_payload(self, payload: Dict[str, Any]) -> List[str]:
        if not isinstance(payload, dict):
            return ["calibration payload must be a dictionary"]

        reasons: List[str] = []
        allowed = set(self.params)
        calibratable = set(self.calibratable_names())

        unknown = sorted(set(payload.keys()) - allowed)
        if unknown:
            reasons.append(f"unknown parameter(s): {', '.join(unknown)}")

        for name, value in payload.items():
            if name not in self.params:
                continue

            spec = self.params[name]
            if not spec.calibratable:
                reasons.append(f"non-calibratable parameter {name} cannot be updated")
                continue

            if name not in calibratable:
                reasons.append(f"parameter {name} is not part of the approved calibration surface")
                continue

            expected_type = spec.param_type
            if expected_type == "int":
                if not isinstance(value, int) or isinstance(value, bool):
                    reasons.append(f"type mismatch for {name}: expected int")
                    continue
                if not (spec.minimum <= value <= spec.maximum):
                    reasons.append(f"range violation for {name}: {value} outside [{spec.minimum}, {spec.maximum}]")
            elif expected_type == "float":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    reasons.append(f"type mismatch for {name}: expected float")
                    continue
                numeric_value = float(value)
                if not (float(spec.minimum) <= numeric_value <= float(spec.maximum)):
                    reasons.append(f"range violation for {name}: {numeric_value} outside [{spec.minimum}, {spec.maximum}]")
            elif expected_type == "bool":
                if not isinstance(value, bool):
                    reasons.append(f"type mismatch for {name}: expected bool")
            elif expected_type == "str":
                if not isinstance(value, str):
                    reasons.append(f"type mismatch for {name}: expected str")
            elif expected_type == "list":
                if not isinstance(value, list):
                    reasons.append(f"type mismatch for {name}: expected list")
            elif expected_type == "dict":
                if not isinstance(value, dict):
                    reasons.append(f"type mismatch for {name}: expected dict")

        return reasons

    def validate_execution_payload(self, payload: Dict[str, Any]) -> List[str]:
        if not isinstance(payload, dict):
            return ["execution payload must be a dictionary"]

        normalized: Dict[str, Any] = {}
        reasons: List[str] = []
        for key, value in payload.items():
            canonical_key = self.SAFETY_ALIASES.get(key, key)
            if canonical_key in normalized and normalized[canonical_key] != value:
                reasons.append(
                    f"alias conflict for {canonical_key}: both {key} and {canonical_key} were provided with different values"
                )
                continue
            normalized[canonical_key] = value

        required = self.CORE_SAFETY_KEYS
        missing = sorted(required - set(normalized.keys()))
        if missing:
            reasons.append(f"missing required safety parameter(s): {', '.join(missing)}")

        unknown = sorted(set(normalized.keys()) - set(self.safety_params))
        if unknown:
            reasons.append(f"unknown parameter(s): {', '.join(unknown)}")

        for name, value in sorted(normalized.items()):
            if name not in self.safety_params:
                continue

            spec = self.safety_params[name]
            expected_type = spec.param_type

            if expected_type == "bool":
                if not isinstance(value, bool):
                    reasons.append(f"type mismatch for {name}: expected bool")
                    continue
                if name == "kill_switch_enabled":
                    if value is False:
                        reasons.append("kill switch cannot be disabled during execution")
                    elif value is not spec.default:
                        reasons.append(f"safety invariant mismatch for {name}: expected {spec.default}")
            elif expected_type == "int":
                if not isinstance(value, int) or isinstance(value, bool):
                    reasons.append(f"type mismatch for {name}: expected int")
                    continue
                if not math.isfinite(float(value)):
                    reasons.append(f"non-finite numeric value for {name}")
                    continue
                if value != spec.default:
                    reasons.append(f"safety invariant mismatch for {name}: expected {spec.default}")
            elif expected_type == "float":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    reasons.append(f"type mismatch for {name}: expected float")
                    continue
                numeric_value = float(value)
                if not math.isfinite(numeric_value):
                    reasons.append(f"non-finite numeric value for {name}")
                    continue
                if name == "safety_drawdown_halt_threshold" and numeric_value > float(spec.default):
                    reasons.append("execution exceeds drawdown halt threshold")
                if name == "max_daily_loss_rupees" and numeric_value > float(spec.default):
                    reasons.append("execution exceeds max daily loss rupees cap")
                if numeric_value != float(spec.default):
                    reasons.append(f"safety invariant mismatch for {name}: expected {spec.default}")

        return reasons

    def black_box_mapping(self) -> Dict[str, List[str]]:
        mapping: Dict[str, List[str]] = {}
        for spec in list(self.params.values()) + list(self.safety_params.values()):
            mapping.setdefault(spec.black_box, []).append(spec.name)
        return {k: sorted(set(v)) for k, v in mapping.items()}


if __name__ == "__main__":
    registry = CanonicalParameterRegistry()
    print('total=', registry.total_target_surface())
    print('calibratable=', len(registry.calibratable_names()))
    print('hardcoded=', len(registry.hardcoded_names()))
    print('identity_sha256=', registry.identity_sha256())
