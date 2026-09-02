#!/usr/bin/env python3
"""Parameter-to-black-box bindings derived from the canonical registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from canonical_parameter_registry import CanonicalParameterRegistry


@dataclass
class BlackBoxParameterBinding:
    black_box: str
    parameters: List[str]
    calibratable: bool = True
    notes: str = ""


class ParameterBlackBoxBinder:
    def __init__(self):
        registry = CanonicalParameterRegistry()
        self.registry = registry
        self.bindings = {
            "StartupCapabilityLock": BlackBoxParameterBinding("StartupCapabilityLock", ["kill_switch_enabled"], False, "Operational readiness"),
            "DataIngestion": BlackBoxParameterBinding("DataIngestion", ["symbols_to_trade", "exclude_symbols", "data_validation_mode"], True, "Data quality and universe"),
            "L2DataCertifier": BlackBoxParameterBinding("L2DataCertifier", ["data_validation_mode"], True, "Data certifier"),
            "PA": BlackBoxParameterBinding("PA", [
                "base_dp_dt_multiplier", "base_dv_dt_multiplier", "entry_confidence_threshold",
                "vwap_weight", "confirmation_2bar_weight", "momentum_weight", "volatility_weight",
                "green_threshold", "amber_threshold_lower", "red_threshold"
            ], True, "Predictive model layer"),
            "ID": BlackBoxParameterBinding("ID", [
                "exit_confidence_threshold", "min_risk_reward_ratio", "slippage_guard_threshold",
                "volatility_regime_multiplier", "low_vol_regime_multiplier",
                "medium_vol_regime_multiplier", "high_vol_regime_multiplier"
            ], True, "Identification / judging layer"),
            "MPC": BlackBoxParameterBinding("MPC", [
                "profit_target_margin_buffer", "profit_target_atr_mult", "stop_loss_atr_mult",
                "min_hold_bars", "max_hold_bars", "signal_persistence_requirement",
                "pid_kp_entry", "pid_ki_entry", "pid_kd_entry",
                "pid_kp_exit", "pid_ki_exit", "pid_kd_exit"
            ], True, "MPC layer"),
            "SafetyGates": BlackBoxParameterBinding("SafetyGates", [
                "drawdown_halt_threshold", "max_daily_loss_rupees", "max_concurrent_positions",
                "max_gross_exposure_fraction", "max_exposure_per_symbol_fraction", "max_market_data_age_seconds",
                "order_timeout_seconds_execution", "max_slippage_fraction", "no_entry_cutoff_time"
            ], False, "Hard safety layer"),
            "PositionManager": BlackBoxParameterBinding("PositionManager", [
                "max_positions_live", "max_positions_per_symbol", "capital_per_trade_fraction",
                "min_capital_buffer_fraction", "capital_allocation_mode", "rebalance_frequency_minutes",
                "max_loss_per_trade_rupees", "max_sector_exposure_fraction", "max_symbol_concentration"
            ], True, "Sizing and exposure"),
            "P01D": BlackBoxParameterBinding("P01D", [
                "order_type", "limit_order_offset_percent", "order_timeout_seconds",
                "max_retry_attempts", "retry_delay_seconds", "slippage_tolerance_percent"
            ], True, "Execution layer"),
            "UnifiedExecution": BlackBoxParameterBinding("UnifiedExecution", [
                "trading_hours_start", "trading_hours_end", "data_validation_mode"
            ], True, "Execution orchestration"),
        }
        # derive the canonical owner set from the registry to prevent drift
        derived = {}
        for box, binding in self.bindings.items():
            names = sorted(name for name, spec in registry.params.items() if spec.black_box == box)
            derived[box] = BlackBoxParameterBinding(box, names, binding.calibratable, binding.notes)
        self.bindings = derived

    def get_all(self) -> Dict[str, BlackBoxParameterBinding]:
        return self.bindings

    def get_calibratable_names(self) -> List[str]:
        return self.registry.calibratable_names()

    def get_hardcoded_names(self) -> List[str]:
        return self.registry.hardcoded_names()


if __name__ == "__main__":
    binder = ParameterBlackBoxBinder()
    print(len(binder.get_calibratable_names()), len(binder.get_hardcoded_names()))
    print(binder.get_all()['SafetyGates'])
