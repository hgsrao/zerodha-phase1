"""External-library calibration engine: the same shared-portfolio pipeline
shape as revision2/portfolio_orchestrator.py, with 8 of the 10 boxes
replaced by real external libraries:

  1. StartupCapabilityLock -> Pydantic            (revision2_external.startup_validation)
  2. DataIngestion         -> unchanged (in-house allow/deny filter; ArcticDB
                               is the loader upstream of this, not this box)
  3. L2DataCertifier       -> Pandera              (revision2_external.data_certification_pandera)
  4. PredictiveAnalytics   -> TA-Lib               (revision2_external.indicators_talib)
  5. IntelligentDiscrim.   -> Gaussian HMM          (revision2_external.regime_id_box;
                               hmmlearn itself is blocked by numba/Python 3.14,
                               see regime_hmm.py's own docstring)
  6. ModelPredictiveControl -> simple-pid           (revision2_external.pid_controller)
  7. SafetyGates (18-gate)  -> unchanged, in-house   (gates_framework.py)
  8. PositionManager       -> PyPortfolioOpt        (revision2_external.position_sizing_pyportfolioopt)
  9. P01D                  -> unchanged, in-house    (revision2.boxes.P01DBox; stdlib hmac
                               already covers signing where it's used)
  10. UnifiedExecution      -> kiteconnect+tenacity  (revision2_external.broker_adapter_kite;
                               NOT used during calibration -- offline/historical only)

This is deliberately a SEPARATE, additive engine -- it does not modify or
replace revision2/portfolio_orchestrator.py. Both exist so a real candidate
can eventually be scored on both and compared, the same cross-checking
principle as the Backtrader parity work.
"""

from __future__ import annotations
from revision2_external.dynamic_parameter_controller import DynamicParameterController, MarketEnvironmentState

import itertools
import math
from dataclasses import replace, asdict
from collections import Counter, deque
from dataclasses import dataclass
from types import SimpleNamespace
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from gates_framework import EntryDecisionEngine, EntrySignal, SafetyGateConfig, SystemState
from revision2.boxes import P01DBox, SafetyGatesTargetBox
from revision2.contracts import EffectiveConfig, MarketSnapshot, SafetyContract, StartupCertificate, StartupNotCertifiedError
from revision2.portfolio_orchestrator import SECTOR_MAP, _ClockEvent
from revision2_external.composite_study_signal import CompositeStudySignal, MAX_WEIGHT, MIN_WEIGHT
from revision2_external.closed_loop_control import ClosedLoopSupervisor, HMMRiskHysteresis, regime_risk_derate
from revision2_external.continuous_exit_controller import ContinuousExitController, ExitControllerState
from revision2_external.data_certification_pandera import certify_bars, certify_session_completeness
from revision2_external.bb05_bb06_parameters import require
from revision2_external.dynamic_target_setpoint import FrozenTargetSetpointProvider
from revision2_external.entry_expectancy_evidence import CausalEntryExpectancyLedger
from revision2_external.entry_candidate_observations import EntryCandidateObservationLedger
from revision2_external.final_execution_controller import FinalExecutionController
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.indicators_talib import TALibPredictiveAnalyticsBox
from revision2_external.pid_controller import SimplePIDModelPredictiveControlBox
from revision2_external.position_sizing_pyportfolioopt import PyPortfolioOptPositionManagerBox, compute_portfolio_weights
from revision2_external.regime_id_box import HMMIntelligentDiscriminationBox
from revision2_external.startup_validation import validate_runtime_parameters, validate_safety_contract
from runtime.operating_mode import ExecutionGate
from revision2_external.paper_execution import CostedPaperBrokerAdapter, ReplayIntentLedger
from revision2.transaction_costs import leg_cost, paper_fill_price
from revision5.ccpp_protection_cubicles import nifty_intertie_measurements
from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor
from revision5.governor_position_policy import absolute_conviction, update_conviction
from revision5.governor_authority import (
    PARAMETER_NAMES as GOVERNOR_AUTHORITY_PARAMETERS, BarTelemetry, GovernorAuthorityConfig,
    GovernorInputError, bar_telemetry, causal_percentile_rank, entry_decision as governor_entry_decision,
    exhaust_spread,
    position_decision as governor_position_decision, side_aligned_conviction,
)
from revision5.plant_control import (
    BayStatus, PlantControlChain, PlantControlError, PlantControlSnapshot, PlantControlMode,
)
from revision5.dynamic_parameters import R5EnvironmentState
from revision5.controller_parameters import (
    CONTROLLER_PARAMETER_NAMES, CONTROLLER_PARAMETERS, R5ControllerParameters,
)
from revision5.engine_state import ENGINE_A, VALID_ENGINES
from revision5.hrsg import GAS_TURBINE_BAYS
from revision5.protection_snapshot import build_plant_protection_snapshot, derive_bay_status_and_master_block
from revision5.supervisory_bridge import Revision5SupervisoryBridge, SupervisorySnapshotError
from revision5.topology import (
    BAY_IDS as _R5_BAY_IDS, SYMBOL_TO_BAY as _R5_SYMBOL_TO_BAY, bay_for_symbol as _r5_bay_for_symbol,
)

SNAPSHOT_LOOKBACK_BARS = 300
# F17: fixed PyPortfolioOpt maintenance policy, deliberately NOT calibratable.
# Refit every 500 unique portfolio clock ticks using the trailing 2,000
# one-minute bars.  The former rebalance_frequency_minutes registry entry
# was removed because it never controlled this real refit path.

# F11: fixed machine-bay protection policy, deliberately NOT calibratable.
# A net losing exit blocks new entries in that symbol until bar index
# exit_bar + symbol_loss_cooldown_bars (registry FIXED_SAFETY).  Session rollover clears the
# latch.  BAY_LOSS_COOLDOWN_BARS is the registry-default view kept for legacy imports.
BAY_LOSS_COOLDOWN_BARS = int(CONTROLLER_PARAMETERS["symbol_loss_cooldown_bars"].default)

GOVERNOR_POSITION_MODES = ("legacy", "v3")
ENTRY_FILL_ANCHOR_MODES = ("reanchor_distances", "box6_levels")
UNIT_PROTECTION_MODES = ("pre_synchronization", "pre_sync_and_in_flight")

# Native plant EXIT reasons that are protection trips (actuated at the next open as
# ``plant_trip:<reason>``).  A plant-governor EXIT is advisory: the orchestrator's own
# governor step owns discretionary exits.
_PLANT_TRIP_PREFIXES = (
    "SUBSTATION_TRIP", "SEL300G_TRIP", "AVR_TRIP", "SESSION_SQUAREOFF", "HRSG_PRESSURE_COLLAPSE",
    "UNMAPPED_SYMBOL", "SYMBOL_NOT_IN_BAY", "PLANT_INPUT_INVALID", "ENGINE_A_FILL_LATCH_REJECTED",
)

# Float-quantity tolerance for the broker/ledger position-reconciliation check below.
_POSITION_RECONCILIATION_EPS = 1e-6


class ExternalEngineStartupNotCertifiedError(StartupNotCertifiedError):
    pass


class PositionReconciliationError(RuntimeError):
    """Raised when an authoritative exit would close a broker position that does not actually
    match this orchestrator's own ``open_trades`` ledger (missing, short, or insufficient
    quantity).  Never caught/swallowed by replay code: a mismatch here means the exit order would
    open or flip a position instead of closing the one the ledger believes exists, which must stop
    the run rather than silently corrupt the SHADOW ledger/P&L and the realized-R close feedback
    that depends on it."""


class Revision2ExternalEngineOrchestrator:
    """Shared-portfolio orchestrator using the external-library box set."""

    # These retain compatibility in the shared registry but have no external
    # replay actuator. They must not spend calibration budget.
    INACTIVE_CALIBRATION_PARAMETERS = frozenset({
        "exit_confidence_threshold", "pid_derivative_smoothing",
        "limit_order_offset_percent", "max_retry_attempts", "retry_delay_seconds",
        "max_positions_per_symbol", "max_symbol_concentration", "data_validation_mode",
    })

    def __init__(
        self,
        symbols: List[str],
        registry: Optional[CanonicalParameterRegistry] = None,
        calibration_overrides: Optional[Dict[str, Any]] = None,
        starting_equity: float = 1_000_000.0,
        sector_map: Optional[Dict[str, str]] = None,
        grid_context_provider: Optional[SealedGridContextProvider] = None,
        closed_loop_mode: str = "shadow",
        telemetry_mode: str = "full",
        pid_mode: str = "enabled",
        dynamic_target_setpoint_provider: Optional[FrozenTargetSetpointProvider] = None,
        dynamic_target_setpoint_mode: str = "shadow",
        risk_profile: str = "development",
        session_schedule: Optional[Dict[str, Tuple[str, str]]] = None,
        supervisory_bridge: Optional[Revision5SupervisoryBridge] = None,
        plant_control_mode: str = "SHADOW",
        real_plant_dcs: Optional[Any] = None,
        paper_journal: Optional[Any] = None,
        governor_authority: str = "advisory",
        final_exit_authority: bool = False,
        plant_engine_mode: str = ENGINE_A,
        governor_position_mode: str = "legacy",
        avr_voltage_mode: str = "legacy",
        dispatcher_allocation_mode: str = "legacy",
        hrsg_correlation_mode: str = "off",
        authority_telemetry: bool = False,
        entry_fill_anchor_mode: str = "reanchor_distances",
        unit_protection_mode: str = "pre_synchronization",
    ) -> None:
        # Opt-in controller modes (defaults keep the baseline decision-identical):
        #   governor_position_mode   "legacy" V2 inner loop | "v3" Protocol V3 inner loop (full
        #                            authority only; revision5.governor.PositionControlV3)
        #   avr_voltage_mode         "legacy" unmeasured AVR | "measured" causal terminal-voltage
        #                            regulation once per bar (PAPER_APPLY plant only)
        #   dispatcher_allocation_mode "legacy" | "bounded" (floor <= allocation <= ceiling always)
        #   hrsg_correlation_mode    "off" | "causal_bay_returns" (feed the HRSG economizer the
        #                            synchronous completed-bar bay returns)
        #   authority_telemetry      record parameter / AVR / dispatcher / HRSG telemetry events
        #                            (telemetry only; no decision effect)
        #   entry_fill_anchor_mode   venue-executed engines (B, C): "reanchor_distances" keeps the
        #                            planned stop/target DISTANCES at the fill (Box 6 entry PID has no
        #                            effect); "box6_levels" keeps the Box 6 PID-derived stop/target
        #                            LEVELS, so the entry PID reaches the position (anchor_plan_at_fill)
        #   unit_protection_mode     "pre_sync_and_in_flight" also runs SEL-300G ANSI 87G (fill vs Box 6
        #                            reference) and ANSI 32R (adverse drift) on every held bar (PAPER_APPLY)
        # ``real_plant_dcs``: an optional, already-constructed native R5 ``CentralPlantMasterDCS``.
        # Default None -- unchanged behaviour: this replay engine does not instantiate a real plant,
        # so protection state uses the explicit SYMBOL_TRIPS_ONLY fallback (see protection_snapshot.py).
        # When supplied, its EXISTING protection/availability state is read (never mutated, never
        # re-evaluated) for ECS bay availability on every plant-control step.
        self.real_plant_dcs = real_plant_dcs
        self.paper_journal = paper_journal
        self._native_timestamp = None
        self._native_bar_index = -1
        if plant_control_mode == PlantControlMode.PAPER_APPLY:
            from revision5.ccpp_unified_plant import CentralPlantMasterDCS
            if not isinstance(real_plant_dcs, CentralPlantMasterDCS) or grid_context_provider is None:
                raise PlantControlError("PAPER_APPLY requires an attached R5 plant and real grid context provider")
        if closed_loop_mode not in {"shadow", "active_paper"}:
            raise ValueError("closed_loop_mode must be 'shadow' or 'active_paper'")
        # Governor authority.  ``advisory`` records the bay governor's ENTRY/HOLD/EXIT decision
        # beside the existing controllers.  ``full`` makes the governor the only entry and
        # discretionary-exit authority (safety and protection still outrank it); it is a paper
        # actuation, so it needs active_paper and a real grid reference for the droop input.
        if governor_authority not in {"advisory", "full"}:
            raise ValueError("governor_authority must be 'advisory' or 'full'")
        if governor_authority == "full" and (closed_loop_mode != "active_paper" or grid_context_provider is None):
            raise ValueError("governor_authority='full' requires closed_loop_mode='active_paper' "
                             "and a real grid context provider")
        if governor_position_mode not in GOVERNOR_POSITION_MODES:
            raise ValueError(f"governor_position_mode must be one of {GOVERNOR_POSITION_MODES}")
        if governor_position_mode == "v3" and governor_authority != "full":
            raise ValueError("governor_position_mode='v3' requires governor_authority='full'")
        if avr_voltage_mode not in {"legacy", "measured"}:
            raise ValueError("avr_voltage_mode must be 'legacy' or 'measured'")
        if avr_voltage_mode == "measured" and plant_control_mode != PlantControlMode.PAPER_APPLY:
            raise ValueError("avr_voltage_mode='measured' requires the PAPER_APPLY native plant")
        if dispatcher_allocation_mode not in {"legacy", "bounded"}:
            raise ValueError("dispatcher_allocation_mode must be 'legacy' or 'bounded'")
        if hrsg_correlation_mode not in {"off", "causal_bay_returns"}:
            raise ValueError("hrsg_correlation_mode must be 'off' or 'causal_bay_returns'")
        if hrsg_correlation_mode != "off" and plant_control_mode != PlantControlMode.PAPER_APPLY:
            raise ValueError("hrsg_correlation_mode requires the PAPER_APPLY native plant")
        if entry_fill_anchor_mode not in ENTRY_FILL_ANCHOR_MODES:
            raise ValueError(f"entry_fill_anchor_mode must be one of {ENTRY_FILL_ANCHOR_MODES}")
        if unit_protection_mode not in UNIT_PROTECTION_MODES:
            raise ValueError(f"unit_protection_mode must be one of {UNIT_PROTECTION_MODES}")
        if unit_protection_mode != "pre_synchronization" and plant_control_mode != PlantControlMode.PAPER_APPLY:
            raise ValueError("unit_protection_mode='pre_sync_and_in_flight' requires the PAPER_APPLY native plant")
        if telemetry_mode not in {"full", "compact"}:
            raise ValueError("telemetry_mode must be 'full' or 'compact'")
        if pid_mode not in {"enabled", "disabled"}:
            raise ValueError("pid_mode must be 'enabled' or 'disabled'")
        if dynamic_target_setpoint_mode not in {"shadow", "paper_apply"}:
            raise ValueError("dynamic_target_setpoint_mode must be 'shadow' or 'paper_apply'")
        self.symbols = list(symbols)
        # Machine Bay & Symbol Closed-Loop Lockout Registers
        self.symbol_cooldown_until_bar = {}
        self.symbol_consecutive_losses = {}
        self.symbol_tripped = {}
        self.closed_loop_mode = closed_loop_mode
        self.telemetry_mode = telemetry_mode
        self.pid_mode = pid_mode
        self.dynamic_target_setpoint_provider = dynamic_target_setpoint_provider
        self.dynamic_target_setpoint_mode = dynamic_target_setpoint_mode
        if risk_profile not in {"development", "trial"}:
            raise ValueError("risk_profile must be development or trial")
        self.risk_profile = risk_profile
        self.session_schedule = dict(session_schedule or {})
        self.registry = registry or CanonicalParameterRegistry()
        if risk_profile == "trial":
            self.registry = self.registry.trial_profile()
        overrides = calibration_overrides or {}
        errors = self.registry.validate_calibration_payload(overrides, engine="EXTERNAL")
        if errors:
            raise ValueError(f"invalid calibration overrides: {errors}")

        values = {name: spec.default for name, spec in self.registry.params.items()}
        values.update(overrides)

        # Box 1: Pydantic-based validation, in place of StartupGate's
        # hand-written type/range checks.
        param_errors = validate_runtime_parameters(self.registry, values)
        safety_values = {name: spec.default for name, spec in self.registry.safety_params.items()}
        safety_errors = validate_safety_contract(self.registry, safety_values)
        if param_errors or safety_errors:
            raise ExternalEngineStartupNotCertifiedError(
                "Pydantic startup certification failed: " + "; ".join(param_errors + safety_errors)
            )
        self.startup_certificate_reasons: List[str] = []

        self.config = EffectiveConfig.build(values, registry_hash=self.registry.FROZEN_IDENTITY_SHA256)
        # R5 controller parameter authority: one validated image of every native R5 controller
        # value, read through EffectiveConfig.require (no fallback), passed explicitly to the
        # governors, dispatcher and the attached native plant below.
        self.controller_parameters = R5ControllerParameters.from_config(self.config)
        self.governor_position_mode = governor_position_mode
        self.avr_voltage_mode = avr_voltage_mode
        self.dispatcher_allocation_mode = dispatcher_allocation_mode
        self.hrsg_correlation_mode = hrsg_correlation_mode
        self.authority_telemetry = bool(authority_telemetry)
        self.entry_fill_anchor_mode = entry_fill_anchor_mode
        self.unit_protection_mode = unit_protection_mode
        self._position_control_v3 = self.controller_parameters.position_control_v3()
        self.safety_contract = SafetyContract.from_registry(self.registry)
        # BB01/BB02 remain upstream.  The bridge provides their single
        # tracked hand-off to R5; it does not call a plant or alter strategy.
        self.supervisory_bridge = (
            supervisory_bridge
            if supervisory_bridge is not None
            else Revision5SupervisoryBridge(self.registry)
        )
        if self.config.require("order_type") != "MARKET":
            raise ValueError("External replay supports MARKET orders only")
        self.sector_map = dict(sector_map) if sector_map is not None else dict(SECTOR_MAP)
        # The per-stock observation remains shadow telemetry. The plant-level
        # PAPER_APPLY consumer uses the same causal provider to cap new admissions.
        self.grid_context_provider = grid_context_provider
        self.grid_shadow_observations: List[Dict[str, Any]] = []
        # Audit ledger for controller comparators and, in ``active_paper``
        # mode, their bounded paper-only actuations.  It is never a safety
        # override and is not connected to a live broker.
        self.controller_telemetry: List[Dict[str, Any]] = []
        self._controller_event_counts: Counter[str] = Counter()
        self._position_sizing_events: List[Dict[str, Any]] = []
        # Entry research telemetry. It is a strictly causal ledger: a
        # pre-entry observation is paired only when that filled trade later
        # completes. No current execution decision reads this ledger.
        self.entry_expectancy_ledger = CausalEntryExpectancyLedger()
        self.entry_candidate_observations = EntryCandidateObservationLedger()
        self._controller_sequence = 0
        self._trade_sequence = 0
        self.bb03_bb04_supervisory_by_symbol: Dict[str, Any] = {}
        self.bb05_bb06_supervisory_by_symbol: Dict[str, Any] = {}
        self.bb09_bb10_supervisory_by_symbol: Dict[str, Any] = {}
        # Observer failures after an order was already placed.  Telemetry only:
        # they never veto, roll back or alter fill/position/ledger bookkeeping.
        self.bb09_bb10_observer_failures: List[Dict[str, Any]] = []

        self.pa = TALibPredictiveAnalyticsBox()
        # Box 4b, the Chart-Studies Confirmation Layer -- gains/clamp/
        # grading-horizon/hit-rate-window are real, disclosed, fixed
        # defaults, not yet exposed as registry-calibratable parameters
        # (same "disclosed first cut, not swept" discipline this
        # project's own prior chart-studies work used for its thresholds).
        self.chart_studies = CompositeStudySignal(config=self.config)
        self.final_execution_controller = FinalExecutionController()
        self.id_box = HMMIntelligentDiscriminationBox(config=self.config)
        self.mpc = SimplePIDModelPredictiveControlBox(pid_enabled=pid_mode == "enabled")
        self.safety_gates_target = SafetyGatesTargetBox()
        self.position_manager = PyPortfolioOptPositionManagerBox()
        self.p01d = P01DBox()
        self.broker = CostedPaperBrokerAdapter(
            account_id="PAPER-EXTERNAL-ENGINE",
            slippage_fraction=float(self.config.require("slippage_cost_multiplier")) * float(self.config.require("mpc_base_slippage_fraction")),
        )
        self.entry_decision_engine = EntryDecisionEngine(config=self._build_safety_gate_config())
        self._intent_ledger = ReplayIntentLedger(self.safety_contract.values["order_dedup_window_seconds"])
        self._execution_halted = False
        self._exit_orders_submitted = 0
        self._post_fill_checks = []

        self.starting_equity = starting_equity
        self.consumed_parameters: set = set()
        self.completed_trades: List[Dict[str, Any]] = []
        self.open_trades: Dict[str, Dict[str, Any]] = {}
        self._equity_curve: List[float] = [starting_equity]
        self._last_close: Dict[str, float] = {}
        self._mtm_equity_curve: List[Tuple[str, float]] = [("", starting_equity)]
        self._mtm_peak = starting_equity
        self._mtm_max_drawdown_fraction = 0.0
        self._active_trading_date = None
        self._day_start_equity = starting_equity
        self._portfolio_weights: Dict[str, float] = {s: 1.0 / len(symbols) for s in symbols}

        # Box 6's real feedback path -- see BOX6_CONTROL_LOOP_DIAGRAM_20260906.html
        # for why this exists: without it, a position's exit is governed
        # only by a frozen entry-time stop/target plus a bare elapsed-time
        # check, with maximum_hold_bars never enforced and no live PA
        # signal feeding back in after entry. Reuses the exit PID's own
        # registry gains (pid_kp_exit/pid_ki_exit/pid_kd_exit/
        # pid_integral_max_clamp/pid_integral_window_bars). The droop
        # magnitude is its OWN independently-calibratable parameter,
        # trailing_stop_atr_mult -- NOT stop_loss_atr_mult (the one-shot
        # entry stop's multiplier). Real data forced this split: a real
        # INFY 6-month run with the droop borrowing stop_loss_atr_mult
        # (0.75) produced a stop narrower than ordinary 1-minute noise
        # (verified: median single-bar range is ~0.95x median ATR, so a
        # sub-1x-ATR continuous stop barely survives one bar, let alone a
        # multi-bar hold) -- 100% of trades exited via stop/stop_gap, zero
        # via max_hold or saturation_exit, ever. See
        # continuous_exit_controller.py's module docstring for the full
        # reasoning (including why ATR, not a guessed percentage, is the
        # real physical constant this droop should be grounded in).
        self.exit_controller = ContinuousExitController(
            kp=float(self.config.require("pid_kp_exit")), ki=float(self.config.require("pid_ki_exit")),
            kd=float(self.config.require("pid_kd_exit")), clamp=float(self.config.require("pid_integral_max_clamp")),
            atr_droop_mult=float(self.config.require("trailing_stop_atr_mult")),
            baseline_window=int(self.config.require("pid_integral_window_bars")),
            saturation_exit_bars=int(self.config.require("saturation_exit_bars")),
            config=self.config,
        )
        self.consumed_parameters.update({
            "pid_kp_exit", "pid_ki_exit", "pid_kd_exit", "pid_integral_max_clamp",
            "pid_integral_window_bars", "trailing_stop_atr_mult", "saturation_exit_bars",
            "mpc_time_decay_gain", "mpc_shadow_r_gamma", "mpc_base_slippage_fraction",
            "studies_pid_kp", "studies_pid_ki", "studies_pid_kd", "studies_pid_output_clamp",
            "studies_grading_horizon_bars", "studies_hit_rate_window_bars",
        })
        self._exit_controller_states: Dict[str, ExitControllerState] = {}
        # Three-loop supervisory layer. ``shadow`` records comparators only;
        # ``active_paper`` permits bounded, one-way paper actuations only:
        # entry/portfolio loops can reduce size, and the path loop can only
        # tighten a stop. Neither mode can weaken a safety constraint.
        self.closed_loop = ClosedLoopSupervisor(config=self.config)
        self._hmm_risk_hysteresis: Dict[str, HMMRiskHysteresis] = {}
        self.consumed_parameters.update(
            name for name in self.registry.params if name.startswith("cl_"))

        # Plant-control chain (Nifty/VIX grid -> ECS supervisor -> sector dispatch -> governor
        # references). SHADOW computes and records; explicit PAPER_APPLY adds a bounded admission cap.
        # SHADOW never alters admission,
        # sizing, orders or the trade ledger.  ClosedLoopSupervisor above stays a supporting
        # subsystem; it is only a one-way derate INPUT to the ECS supervisor.
        from revision5.ccpp_unified_plant import DynamicBayLoadDispatcher
        grid_provider = (self.grid_context_provider if grid_context_provider is not None
                         else SealedGridContextProvider(pd.DataFrame({"timestamp": [], "close": []}),
                                                        pd.DataFrame({"timestamp": [], "close": []})))
        self.plant_control = PlantControlChain(
            self.config, grid_provider,
            DynamicBayLoadDispatcher(total_capital=float(starting_equity),
                                     parameters=self.controller_parameters.dispatcher,
                                     governor_specs=self.controller_parameters.governor_specs,
                                     allocation_mode=dispatcher_allocation_mode), plant_control_mode)
        self._paper_plant_snapshot = None
        self._paper_plant_timestamp = None
        self._paper_admission_evaluations = 0
        self._paper_admission_rejections = 0
        self._paper_admission_caps = 0
        self.plant_control_snapshots: List[PlantControlSnapshot] = []    # recorded on change only
        self.plant_control_observer_failures: List[Dict[str, Any]] = []
        self.plant_control_evaluations = 0
        self.plant_control_state_counts: Dict[str, int] = {}
        self._plant_control_last_key: Optional[Tuple[Any, ...]] = None
        # Updated on every plant-control step (not only on state change), so the final report
        # always reflects the most recent protection connection/source, not a stale default.
        self._plant_protection_connected = False
        self._plant_protection_source = "SYMBOL_TRIPS_ONLY"
        self.consumed_parameters.update(
            name for name in self.registry.params if name.startswith(("grid_", "ecs_")))

        # Realized-R trade-close feedback (BLOCKER 2).  Two distinct, separately-owned feedback
        # paths, both fed the same realized-R outcome exactly once per authoritative close:
        #   * self._bay_governors[bay_id].register_trade(r)              -- local governor feedback
        #   * self.plant_control.dispatch_controller.merit_source.register_trade(bay_id, r)
        #                                                                 -- plant-level dispatch feedback
        # Neither replaces the other; see _register_realized_r_close_feedback.
        self._bay_governors: Dict[str, BayTurbineClosedLoopGovernor] = {
            bay_id: BayTurbineClosedLoopGovernor(self.controller_parameters.governor_specs[bay_id])
            for bay_id in _R5_BAY_IDS
        }
        # Exactly-once (this process/replay run only) close-feedback receipts, keyed per trade.
        # See _register_realized_r_close_feedback for the state-machine contract.
        self._close_feedback_receipts: Dict[str, str] = {}

        if self.plant_control.mode == PlantControlMode.PAPER_APPLY:
            # The attached native plant receives this run's EffectiveConfig controller image
            # (state-preserving; identical values leave it unchanged) and the opt-in modes.
            self.real_plant_dcs.apply_controller_parameters(
                self.controller_parameters, dispatcher_allocation_mode=dispatcher_allocation_mode,
                avr_voltage_mode=avr_voltage_mode)
            # One owner for outcome feedback: dispatch and caps consume the native objects.
            self._bay_governors = {key: bay.governor for key, bay in self.real_plant_dcs.bays.items()}
            self.plant_control.dispatch_controller.merit_source = self.real_plant_dcs.dispatcher
        if paper_journal is not None and self.plant_control.mode != PlantControlMode.PAPER_APPLY:
            raise PlantControlError("Durable replay requires PAPER_APPLY")
        # Native plant execution wiring (PAPER_APPLY only; SHADOW never mutates the plant):
        # every bar steps the plant (dynamic gains + HRSG thermal cycle), every candidate is
        # gated and sized by evaluate_entry(), every fill is bound to its bay through
        # on_trade_filled(), and every open position is governed by evaluate_position_control().
        if plant_engine_mode not in VALID_ENGINES:
            raise ValueError(f"plant_engine_mode must be one of {VALID_ENGINES}")
        self.plant_engine_mode = plant_engine_mode
        self._plant_wired = self.plant_control.mode == PlantControlMode.PAPER_APPLY
        self.plant_call_counts: Counter[str] = Counter()
        self._plant_trip_counts: Counter[str] = Counter()
        self._plant_entry_rejections: Counter[str] = Counter()
        self._dynamic_gain_scale: Dict[str, Tuple[float, float, float]] = {}
        self._vol_baseline: Dict[str, float] = {}
        self._micom_measurements: Optional[Tuple[float, float]] = None
        self._micom_drawdown = 0.0
        self.plant_bar_trace: List[Dict[str, Any]] = []
        # Governor authority: registry-owned Mark V gate and causal market telemetry.  Telemetry
        # and bay exhaust spread are measured once per portfolio timestamp (one owner each).
        self.governor_authority = governor_authority
        # Explicit research opt-in.  The default preserves the sealed baseline: the
        # final execution controller remains telemetry-only unless enabled by a caller.
        self.final_exit_authority = bool(final_exit_authority)
        self.governor_config = GovernorAuthorityConfig.from_config(self.config)
        self.consumed_parameters.update(GOVERNOR_AUTHORITY_PARAMETERS)
        self.consumed_parameters.update(CONTROLLER_PARAMETER_NAMES)
        self._governor_telemetry: Dict[str, BarTelemetry] = {}
        self._bay_exhaust_spread: Dict[str, Optional[float]] = {}
        self._session_bar_index: Dict[str, Any] = {}
        self._governor_entry_counts: Counter[str] = Counter()
        self._governor_position_counts: Counter[str] = Counter()
        self.governor_position_policy = None
        # FSRN inputs: each conviction measure placed on its own causal distribution (percentile
        # rank over the governor's z window), updated on every evaluated bar including warm-up.
        self._conviction_history: Dict[str, Dict[str, deque]] = {}
        self._conviction_rank: Dict[str, Dict[str, Optional[float]]] = {}
        # MiCOM substation protection (PAPER_APPLY with the native plant): evaluated every
        # portfolio timestamp from the causal NIFTY grid and the daily fleet MTM drawdown.
        self.micom_vol_z_window = int(require(self.config, "micom_nifty_vol_z_window"))
        self.consumed_parameters.add("micom_nifty_vol_z_window")
        self._micom_trip: Optional[Dict[str, Any]] = None
        self._micom_opened_intertie = False
        self._micom_trip_counts: Counter[str] = Counter()
        self.startup_certificate = self._issue_startup_certificate()
        self._authority_summary: Dict[str, Dict[str, float]] = {}
        self._authority_session = None
        if self.authority_telemetry:
            self._record_controller_event("CONTROLLER_PARAMETER_STARTUP", "STARTUP", "PLANT",
                                          self.controller_parameter_startup_report())

    def controller_parameter_startup_report(self) -> Dict[str, Any]:
        """Startup report: every effective R5 controller parameter value, its hash, the values
        differing from the registry default, the registry identity and the opt-in modes."""
        return {
            **self.controller_parameters.startup_report(),
            "registry_identity_sha256": self.registry.FROZEN_IDENTITY_SHA256,
            "config_hash": self.config.config_hash,
            "modes": {"governor_authority": self.governor_authority,
                      "governor_position_mode": self.governor_position_mode,
                      "avr_voltage_mode": self.avr_voltage_mode,
                      "dispatcher_allocation_mode": self.dispatcher_allocation_mode,
                      "hrsg_correlation_mode": self.hrsg_correlation_mode,
                      "final_exit_authority": self.final_exit_authority,
                      "entry_fill_anchor_mode": self.entry_fill_anchor_mode,
                      "unit_protection_mode": self.unit_protection_mode,
                      "closed_loop_mode": self.closed_loop_mode,
                      "plant_control_mode": self.plant_control.mode.value},
        }

    def authority_report_section(self, report: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Compact controller-authority section for engine runner reports (A, B and C): the
        startup parameter report, modes, controller event counts, governor / plant / MiCOM
        decision counts and (with authority_telemetry) the applied-value summary."""
        report = report or {}
        wiring = dict(report.get("plant_dcs_wiring") or {})
        wiring.pop("bar_trace", None)
        return {
            "startup": self.controller_parameter_startup_report(),
            "controller_event_counts": dict(self._controller_event_counts),
            "governor_authority": report.get("governor_authority"),
            "plant_dcs_wiring": wiring,
            "plant_control": report.get("plant_control"),
            "micom": report.get("micom"),
            "applied_value_summary": self._authority_summary if self.authority_telemetry else None,
        }

    def anchor_plan_at_fill(self, planned, fill_price: float):
        """The one fill contract shared by the venue-executed engines (B: Nautilus, C: Backtrader).

        Sequence: signal -> Box 6 entry PID -> plan (reference, stop, target) -> order -> fill ->
        position.  Returns (plan booked at the fill, risk per share at the booked stop).

        ``reanchor_distances`` (default, baseline): the planned stop and target DISTANCES are kept and
        re-anchored at the fill price; the entry PID's reference shift is therefore discarded.
        ``box6_levels``: the Box 6 PID-derived stop and target LEVELS are kept (only the entry price
        becomes the fill), so the entry PID changes the booked stop / target / risk.  A fill beyond
        the PID-derived stop is still caught by the caller's fill-beyond-planned-stop protection."""
        sign = 1.0 if planned.side == "BUY" else -1.0
        price = float(fill_price)
        if self.entry_fill_anchor_mode == "box6_levels":
            plan = replace(planned, entry_price=price)
            return plan, abs(price - float(plan.stop_price))
        risk = abs(float(planned.entry_price) - float(planned.stop_price))
        reward = abs(float(planned.target_price) - float(planned.entry_price))
        plan = replace(planned, entry_price=price, stop_price=price - sign * risk, target_price=price + sign * reward)
        return plan, risk

    def _in_flight_protection(self, symbol, trade, bar) -> Optional[Dict[str, Any]]:
        """SEL-300G in-flight elements (unit_protection_mode='pre_sync_and_in_flight'), causal on
        the completed bar: ANSI 87G compares the booked fill with the Box 6 reference price the
        order was planned on; ANSI 32R counts consecutive completed closes on the adverse side of
        the fill (a SELL is evaluated on the price reflected about the fill).  A trip is a plant
        protection exit armed for the next open (it outranks the governor)."""
        bay_id = trade.get("plant_bay_id") or _R5_SYMBOL_TO_BAY.get(symbol)
        if bay_id is None:
            return {"action": "EXIT", "reason": "UNMAPPED_SYMBOL:in_flight"}
        relay = self.real_plant_dcs.bays[bay_id].relay
        entry = float(trade["entry_price"])
        close = float(bar["close"])
        current = close if trade["side"] == "BUY" else 2.0 * entry - close
        intended = float(trade.get("box6_reference_price", trade.get("planned_entry_price", entry)))
        trip = relay.check_in_flight_protection(symbol=symbol, entry_price=entry, current_price=current,
                                                exec_fill_price=entry, intended_price=intended)
        if not trip.tripped:
            return None
        return {"action": "EXIT", "reason": f"SEL300G_TRIP:{trip.ansi_code}:{trip.reason}"}

    def _applied_controller_values(self) -> Dict[str, float]:
        """The operating values the native plant controllers are applying right now."""
        plant = self.real_plant_dcs
        values: Dict[str, float] = {}
        for bay_id, bay in plant.bays.items():
            g, a = bay.governor, bay.avr
            for name in ("kp", "ki", "kd", "target_r", "outcome_window", "integral_clamp", "base_z",
                         "droop_r", "dynamic_offset_min", "dynamic_offset_max", "grid_droop_gain",
                         "grid_droop_max", "feedback_gain"):
                values[f"{bay_id}.governor.{name}"] = float(getattr(g, f"runtime_{name}"))
            for name in ("runtime_kp", "runtime_ki", "runtime_kd", "runtime_integral_clamp", "excitation_command",
                         "excitation_min", "excitation_max", "loading_fraction", "undervoltage_trip_pu",
                         "overvoltage_trip_pu", "oel_max", "uel_min"):
                values[f"{bay_id}.avr.{name}"] = float(getattr(a, name))
            values[f"{bay_id}.dispatcher.weight"] = float(plant.dispatcher.weights[bay_id])
            values[f"{bay_id}.effective_allocation"] = float(bay.effective_allocation)
            values[f"{bay_id}.loss_cooldown_bars"] = float(bay.loss_cooldown_bars)
            values[f"{bay_id}.target_cooldown_bars"] = float(bay.target_cooldown_bars)
            values[f"{bay_id}.relay.vol_surge_mult"] = float(bay.relay.vol_surge_mult)
        values["hrsg.steam_pressure_pu"] = float(plant.hrsg.steam_pressure_pu)
        values["hrsg.pressure_time_constant_bars"] = float(plant.hrsg.pressure_time_constant_bars)
        values["micom.max_dd_pct"] = float(plant.grid_relay.max_dd_pct)
        return values

    def _authority_bar_telemetry(self, timestamp) -> None:
        """Runtime telemetry of the applied controller values (authority_telemetry only): a running
        min/max/last per value every bar, and the full value set at each session's first bar."""
        values = self._applied_controller_values()
        for name, value in values.items():
            row = self._authority_summary.get(name)
            if row is None:
                self._authority_summary[name] = {"min": value, "max": value, "last": value, "bars": 1}
            else:
                row["min"], row["max"], row["last"] = min(row["min"], value), max(row["max"], value), value
                row["bars"] += 1
        session = pd.Timestamp(timestamp).date()
        if session != self._authority_session:
            self._authority_session = session
            self._record_controller_event("CONTROLLER_APPLIED_VALUES", timestamp, "PLANT", {"values": values})

    def _build_safety_gate_config(self) -> SafetyGateConfig:
        v = self.safety_contract.values
        return SafetyGateConfig(
            kill_switch_enabled=bool(v["kill_switch_enabled"]),
            drawdown_halt_threshold=float(v["safety_drawdown_halt_threshold"]),
            daily_loss_halt_threshold=float(v["max_daily_loss_rupees"]),
            lambda_derate_threshold=float(v["lambda_derate_threshold"]),
            lambda_derate_multiplier=float(v["lambda_derate_multiplier"]),
            min_signal_confidence=float(v["min_signal_confidence"]),
            min_risk_reward_ratio=float(v["safety_min_risk_reward_ratio"]),
            slippage_tolerance_percent=float(v["max_slippage_fraction"]),
            max_position_quantity=int(v["max_position_quantity"]),
            max_concurrent_positions=int(v["max_concurrent_positions"]),
            max_gross_exposure_fraction=float(v["max_gross_exposure_fraction"]),
            max_exposure_per_symbol_fraction=float(v["max_exposure_per_symbol_fraction"]),
            no_entry_cutoff_time=str(v["no_entry_cutoff_time"]),
            max_market_data_age_seconds=int(v["max_market_data_age_seconds"]),
            max_broker_offline_seconds=int(v["max_broker_offline_seconds"]),
            force_close_time=str(v["force_close_time"]),
            drawdown_derate_threshold=float(v["drawdown_derate_threshold"]),
            drawdown_derate_multiplier=float(v["drawdown_derate_multiplier"]),
            order_timeout_seconds=min(int(v["order_timeout_seconds_execution"]),
                                      int(self.config.require("order_timeout_seconds"))),
            max_reconciliation_qty_diff=int(v["max_reconciliation_qty_diff"]),
        )

    def _issue_startup_certificate(self) -> StartupCertificate:
        # Pydantic already certified the parameter surface above (Box 1);
        # this issues the same StartupCertificate record type so downstream
        # code (reports, hashes) has the identical shape as the in-house
        # engine, without re-running StartupGate's own (now superseded)
        # validation.
        return StartupCertificate.issue(
            {"passed": True, "operating_mode": "paper", "broker_environment": self.broker.environment, "reasons": []},
            self.config.config_hash, self.safety_contract.contract_hash,
        )

    def _record(self, trace) -> None:
        for use in trace:
            self.consumed_parameters.add(use.parameter)

    def _equity(self) -> float:
        return self.starting_equity + self.broker.realized_pnl - self.broker.booked_costs

    def _current_drawdown(self) -> float:
        peak = max(self._mtm_peak, max(self._equity_curve, default=self.starting_equity))
        current = self._mark_to_market_equity()
        return (peak - current) / peak if peak > 0 else 0.0

    def _mark_to_market_equity(self) -> float:
        unrealized = 0.0
        for symbol, trade in self.open_trades.items():
            mark = self._last_close.get(symbol, trade["entry_price"])
            unrealized += (
                (mark - trade["entry_price"]) * trade["quantity"] if trade["side"] == "BUY"
                else (trade["entry_price"] - mark) * trade["quantity"]
            )
        return self._equity() + unrealized

    def _plant_bay_status(self) -> Dict[str, BayStatus]:
        """A bay is tripped only when it has universe symbols and every one is tripped."""
        status = {}
        for bay in _R5_BAY_IDS:
            members = [s for s in self.symbols if _R5_SYMBOL_TO_BAY.get(s) == bay]
            tripped = bool(members) and all(self.symbol_tripped.get(s, False) for s in members)
            status[bay] = BayStatus(available=not tripped, tripped=tripped,
                                    reason="ALL_SYMBOLS_TRIPPED" if tripped else "")
        return status

    def _plant_control_shadow_step(self, timestamp: object, max_gross_fraction: float) -> None:
        """Evaluate plant control once per timestamp and publish a current reference.

        SHADOW only observes; PAPER_APPLY consumes this reference in the admission cap.
        An expected input error clears the current reference and is recorded; unexpected
        defects propagate. The historical method name is retained for existing callers.

        Bay availability/protection: a real ``self.real_plant_dcs`` (None by default) is read
        through an immutable ``PlantProtectionSnapshot`` -- its EXISTING state only, never
        re-evaluated or faked here.  When it is not attached, the existing symbol-trip fallback
        (``_plant_bay_status``) is used unchanged, and that fallback path never sees or is
        overridden by real-plant state (see ``derive_bay_status_and_master_block``)."""
        if self.plant_control.mode == PlantControlMode.PAPER_APPLY:
            ts = pd.Timestamp(timestamp)
            if self._native_timestamp is not None and ts < self._native_timestamp:
                raise PlantControlError("Native plant clock moved backwards")
            if ts != self._native_timestamp:
                self._native_bar_index += 1
                self.real_plant_dcs.begin_bar(ts.to_pydatetime(), self._native_bar_index)
                self._native_timestamp = ts
            self._micom_step(ts)
            self._record_controller_event("NATIVE_PLANT_STATE", timestamp, "PLANT", {
                "protection": asdict(build_plant_protection_snapshot(self.real_plant_dcs)),
                "clock_basis": "unique_portfolio_timestamp",
            })
        self._paper_plant_snapshot = None  # a failed evaluation must never reuse an earlier reference
        self._paper_plant_timestamp = None
        equity = self._equity()
        gross = self._gross_exposure_notional()
        protection = build_plant_protection_snapshot(self.real_plant_dcs)
        bay_status, plant_protection_tripped, bay_availability_source = derive_bay_status_and_master_block(
            protection, self._plant_bay_status())
        try:
            supporting = self.closed_loop.observe_portfolio_risk(
                gross, equity, max_gross_fraction,
                soft_budget_fraction=self.closed_loop.soft_budget_fraction)["suggested_new_risk_derate"]
            snapshot = self.plant_control.evaluate(
                timestamp, bay_status,
                gross_exposure_fraction=gross / max(equity, 1.0),
                gross_exposure_limit_fraction=max_gross_fraction,
                supporting_derate=supporting, plant_protection_tripped=plant_protection_tripped,
                bay_availability_source=bay_availability_source)
        except PlantControlError as exc:
            self.plant_control_observer_failures.append({
                "timestamp": str(timestamp), "error_type": type(exc).__name__, "error": str(exc)})
            return
        self._paper_plant_snapshot = snapshot
        self._paper_plant_timestamp = timestamp
        self.plant_control_evaluations += 1
        self._plant_protection_connected = snapshot.ecs.plant_protection_connected
        self._plant_protection_source = snapshot.ecs.bay_availability_source
        self.plant_control_state_counts[snapshot.grid.state] = (
            self.plant_control_state_counts.get(snapshot.grid.state, 0) + 1)
        key = (snapshot.grid.state, snapshot.ecs.operating_mode,
               round(snapshot.ecs.plant_demand_reference_pu, 9), snapshot.dispatch.feasible,
               tuple(round(ref, 9) for _, ref in snapshot.dispatch.references_pu))
        if key != self._plant_control_last_key:
            self._plant_control_last_key = key
            self.plant_control_snapshots.append(snapshot)

    def _micom_step(self, ts: pd.Timestamp) -> None:
        """Evaluate the native plant's MiCOM grid relay for this timestamp (PAPER_APPLY only).

        ANSI 67  daily fleet mark-to-market drawdown (latched for the session by the relay)
        ANSI 81U/81O  latest completed NIFTY 15-minute return
        ANSI 21  that return in units of the preceding returns' dispersion
        A trip opens the grid intertie (blocking admissions through the protection snapshot)
        and forces the protective exit of open positions; a healthy evaluation recloses an
        intertie that MiCOM itself opened.  Unavailable grid inputs fail closed as a trip."""
        relay = self.real_plant_dcs.grid_relay
        day_start = float(self._day_start_equity)
        drawdown = max(0.0, (day_start - self._mark_to_market_equity()) / day_start) if day_start > 0 else math.inf
        sync = self.plant_control.synchronizer
        grid = self.grid_context_provider.causal_context(
            sync.decision_utc(ts), max_staleness_seconds=sync.max_staleness_seconds,
            minimum_aligned_bars=sync.min_aligned_bars)
        measured = (nifty_intertie_measurements(grid.aligned["close_nifty"].to_numpy(), self.micom_vol_z_window)
                    if grid.available else None)
        self._micom_measurements, self._micom_drawdown = measured, drawdown
        if measured is None:
            trip = {"ansi_code": "MICOM_INPUT_UNAVAILABLE",
                    "reason": grid.reason if not grid.available else "NIFTY_HISTORY_INSUFFICIENT",
                    "fleet_drawdown": drawdown}
            if drawdown >= relay.max_dd_pct:
                relay.master_breaker_open = True        # ANSI 67 needs no grid input
                trip["ansi_code"] = "ANSI 67"
        else:
            nifty_return, vol_z = measured
            result = relay.evaluate_grid_intertie(
                nifty_15m_return=nifty_return, nifty_vol_z=vol_z, fleet_equity_drawdown_pct=drawdown)
            trip = None
            if result.tripped or relay.master_breaker_open:
                trip = {"ansi_code": result.ansi_code if result.tripped else "ANSI 67",
                        "reason": result.reason if result.tripped else "ANSI 67 session lockout",
                        "fleet_drawdown": drawdown, "nifty_15m_return": nifty_return, "nifty_vol_z": vol_z}
        network = self.real_plant_dcs.electrical_network
        if trip is not None:
            self._micom_trip_counts[trip["ansi_code"]] += 1
            if network.grid_connected:
                self.real_plant_dcs.open_grid_intertie(
                    reason=f"{trip['ansi_code']}:{trip['reason']}", source="MICOM")
                self._micom_opened_intertie = True
            if self._micom_trip is None or self._micom_trip["ansi_code"] != trip["ansi_code"]:
                self._record_controller_event("MICOM_GRID_TRIP", ts, "PLANT", trip)
        elif self._micom_opened_intertie:
            self.real_plant_dcs.close_grid_intertie()
            self._micom_opened_intertie = False
            self._record_controller_event("MICOM_GRID_RECLOSE", ts, "PLANT", {
                "fleet_drawdown": drawdown, "nifty_15m_return": measured[0], "nifty_vol_z": measured[1]})
        self._micom_trip = trip

    def _assert_paper_plant_broker(self):
        if self.plant_control.mode is PlantControlMode.PAPER_APPLY:
            if type(self.broker) is not CostedPaperBrokerAdapter or self.broker.environment != "paper":
                raise PlantControlError("PAPER_APPLY requires the offline CostedPaperBrokerAdapter")

    def _paper_plant_entry_limit(self, symbol, quantity, entry_price, timestamp):
        """Fresh, fail-closed paper-only cap. Re-read protection and used budgets per candidate."""
        if self.plant_control.mode is PlantControlMode.SHADOW:
            return quantity
        self._assert_paper_plant_broker()
        self._paper_admission_evaluations += 1
        snapshot = self._paper_plant_snapshot
        result = {"quantity": 0, "reason": "PAPER_PLANT_REFERENCE_UNAVAILABLE"}
        if (snapshot is not None and self._paper_plant_timestamp == timestamp
                and snapshot.ecs.plant_protection_connected):
            protection = build_plant_protection_snapshot(self.real_plant_dcs)
            bay_id = _R5_SYMBOL_TO_BAY.get(symbol)
            if protection.connected and not protection.master_block and bay_id is not None:
                bay = protection.bay(bay_id)
                if bay.available and not self.symbol_tripped.get(symbol, False):
                    reference = next(ref for ref in snapshot.governor_references if ref.bay_id == bay_id)
                    bay_notional = sum(
                        t["quantity"] * self._last_close.get(s, t["entry_price"])
                        for s, t in self.open_trades.items() if _R5_SYMBOL_TO_BAY.get(s) == bay_id)
                    result = self._bay_governors[bay_id].cap_dispatch_entry(
                        reference, requested_quantity=quantity, entry_price=entry_price,
                        equity=self._equity(), gross_limit_fraction=float(self.safety_contract.values["max_gross_exposure_fraction"]),
                        bay_notional=bay_notional, gross_notional=self._gross_exposure_notional())
                else:
                    result["reason"] = "PAPER_PLANT_BAY_BLOCKED"
            else:
                result["reason"] = "PAPER_PLANT_PROTECTION_UNAVAILABLE_OR_BLOCKED"
        allowed = min(quantity, result["quantity"])
        self._paper_admission_rejections += int(allowed <= 0)
        self._paper_admission_caps += int(0 < allowed < quantity)
        self._record_controller_event("PLANT_CONTROL_PAPER_ADMISSION", timestamp, symbol,
                                      {"requested_quantity": quantity, **result})
        return allowed

    # ------------------------------------------------------------------
    # Native R5 plant execution wiring (PAPER_APPLY only)
    # ------------------------------------------------------------------
    def _gain_scale_for(self, symbol: str) -> Tuple[float, float, float]:
        """(Kp, Ki, Kd) multipliers for this symbol's bay from the current R5 snapshot."""
        return self._dynamic_gain_scale.get(_R5_SYMBOL_TO_BAY.get(symbol), (1.0, 1.0, 1.0))

    def _plant_environment(self, tick_events) -> R5EnvironmentState:
        """Causal R5 supervisory environment for this timestamp: volatility against each
        symbol's own EMA baseline, HMM stress posterior, trend share and fleet drawdown."""
        alpha = 2.0 / (self.governor_config.z_window_bars + 1.0)
        ratios, stress, trending, measured = [], [], 0, 0
        for event in tick_events:
            telemetry = self._governor_telemetry.get(event.symbol)
            if telemetry is None or not telemetry.available or not telemetry.atr_fraction:
                continue
            measured += 1
            fraction = float(telemetry.atr_fraction)
            baseline = self._vol_baseline.get(event.symbol)
            if baseline is not None and baseline > 0.0:
                ratios.append(fraction / baseline)
            self._vol_baseline[event.symbol] = (
                fraction if baseline is None else baseline + alpha * (fraction - baseline))
            trending += int(abs(float(telemetry.z_score)) >= 1.0)
            observation = self.id_box.latest_regime_observation(event.symbol)
            if observation.get("available") and observation.get("stress_probability") is not None:
                stress.append(float(observation["stress_probability"]))
        turbulent = min(1.0, max(0.0, sum(stress) / len(stress))) if stress else 1.0 / 3.0
        trend_share = trending / measured if measured else 0.5
        ratios.sort()
        governors = [bay.governor for bay in self.real_plant_dcs.bays.values()]
        return R5EnvironmentState(
            normalized_vol=ratios[len(ratios) // 2] if ratios else 1.0,
            trend_probability=(1.0 - turbulent) * trend_share,
            chop_probability=(1.0 - turbulent) * (1.0 - trend_share),
            turbulent_probability=turbulent,
            fleet_drawdown_fraction=self._current_drawdown(),
            control_error_delta=sum(g.last_error for g in governors) / len(governors),
        )

    def _gtg_measurements(self, tick_events, symbol_bars) -> Dict[str, Dict[str, float]]:
        """GTG mass flow (bar volume over its trailing mean) and exhaust temperature (bar
        range over ATR), the median across each gas-turbine bay's members at this instant."""
        window = self.governor_config.z_window_bars
        flows: Dict[str, List[float]] = {}
        temps: Dict[str, List[float]] = {}
        for event in tick_events:
            bay_id = _R5_SYMBOL_TO_BAY.get(event.symbol)
            telemetry = self._governor_telemetry.get(event.symbol)
            frame = symbol_bars[event.symbol]
            if (bay_id not in GAS_TURBINE_BAYS or telemetry is None or not telemetry.available
                    or not telemetry.atr or "volume" not in frame.columns or event.bar_idx < 1):
                continue
            row = frame.iloc[event.bar_idx]
            trailing = frame["volume"].iloc[max(0, event.bar_idx - window):event.bar_idx]
            mean_volume = float(trailing.mean())
            if not math.isfinite(mean_volume) or mean_volume <= 0.0:
                continue
            flows.setdefault(bay_id, []).append(float(row["volume"]) / mean_volume)
            temps.setdefault(bay_id, []).append((float(row["high"]) - float(row["low"])) / float(telemetry.atr))
        median = lambda values: sorted(values)[len(values) // 2]
        return {bay_id: {"mass_flow_pu": median(flows[bay_id]), "exhaust_temp_pu": median(temps[bay_id])}
                for bay_id in flows}

    def _plant_bar_step(self, timestamp, tick_events, symbol_bars) -> None:
        """Every bar: dynamic operating snapshot, HRSG thermal cycle and capital balance on
        the native plant, then the snapshot's gains onto the execution PIDs."""
        if not self._plant_wired:
            return
        plant = self.real_plant_dcs
        self.plant_call_counts["step_bar"] += 1
        result = plant.step_bar(
            current_dt=self._replay_event_time(timestamp), bar_index=self._native_bar_index,
            environment=self._plant_environment(tick_events),
            gtg_measurements=self._gtg_measurements(tick_events, symbol_bars))
        snapshot = result["dynamic_snapshot"]
        if self.avr_voltage_mode == "measured":
            # Bus voltage: the grid synchronizer's causal Nifty-deviation measurement for THIS
            # timestamp (None when the plant reference is unavailable -> AVR fails closed).
            deviation = self._governor_grid_return(timestamp)
            regulation = plant.regulate_bay_voltages(
                bar_index=self._native_bar_index,
                bus_voltage_pu=None if deviation is None else 1.0 + float(deviation))
            self._record_controller_event("AVR_VOLTAGE_REGULATION", timestamp, "PLANT", {"bays": regulation})
        if self.hrsg_correlation_mode == "causal_bay_returns":
            returns: Dict[str, List[float]] = {}
            for event in tick_events:
                telemetry = self._governor_telemetry.get(event.symbol)
                bay_id = _R5_SYMBOL_TO_BAY.get(event.symbol)
                if bay_id is not None and telemetry is not None and telemetry.available:
                    returns.setdefault(bay_id, []).append(float(telemetry.one_bar_return))
            bay_returns = {bay_id: sum(v) / len(v) for bay_id, v in returns.items()}
            if bay_returns:
                plant.record_hrsg_return_snapshot(bay_returns)
                if self.authority_telemetry:
                    self._record_controller_event("HRSG_RETURN_SNAPSHOT", timestamp, "PLANT", {
                        "bay_returns": bay_returns,
                        "correlation_penalties": plant.hrsg.compute_economizer_penalties()})
        if self.authority_telemetry:
            self._authority_bar_telemetry(timestamp)
        for bay_id, profile in snapshot.governor_by_bay.items():
            spec = self.controller_parameters.governor_specs[bay_id]
            self._dynamic_gain_scale[bay_id] = tuple(
                (float(getattr(profile, name)) / float(getattr(spec, name))) if getattr(spec, name) else 1.0
                for name in ("kp", "ki", "kd"))
        thermal = result["thermal"]
        row = {
            "timestamp": str(timestamp), "bar_index": self._native_bar_index,
            "steam_pressure_pu": thermal.steam_pressure_pu, "heat_input_pu": thermal.heat_input_pu,
            "steam_capacity_factor": thermal.steam_capacity_factor,
            "pressure_collapsed": thermal.pressure_collapsed, "gtg_inputs": thermal.gtg_inputs,
            "reserve_cash": plant.hrsg_reserve_cash,
            "dynamic_version": snapshot.version, "stress_index": snapshot.stress_index,
            "bays": {bay_id: {
                "effective_allocation": bay.effective_allocation,
                "tripped_offline": bay.tripped_offline,
                "cooldown_remaining": bay.cooldown_remaining(self._native_bar_index),
                "unit_available": plant.electrical_network.unit_available(bay_id),
                "governor_kp": bay.governor.runtime_kp, "governor_ki": bay.governor.runtime_ki,
                "governor_kd": bay.governor.runtime_kd, "base_z": bay.governor.runtime_base_z,
                "gain_scale": self._dynamic_gain_scale[bay_id],
            } for bay_id, bay in plant.bays.items()},
        }
        if self.telemetry_mode == "full":
            self.plant_bar_trace.append(row)
        self._record_controller_event("PLANT_BAR_STEP", timestamp, "PLANT", {
            key: row[key] for key in ("steam_pressure_pu", "heat_input_pu", "steam_capacity_factor",
                                      "pressure_collapsed", "reserve_cash", "stress_index")})

    def _plant_quote(self, price: float) -> Tuple[float, float]:
        """Replay has no quotes: bid/ask are the paper broker's own adverse-fill band."""
        slip = float(self.broker.slippage_fraction)
        return price * (1.0 - slip), price * (1.0 + slip)

    def _plant_grid_inputs(self) -> Dict[str, float]:
        nifty_return, vol_z = self._micom_measurements or (0.0, 0.0)
        return {"nifty_15m_ret": float(nifty_return), "nifty_vol_z": float(vol_z),
                "fleet_equity_dd_pct": float(self._micom_drawdown)}

    def _plant_entry_gate(self, symbol, timestamp, side, quantity, price, bar, candidate_id,
                          governor_entry) -> int:
        """Native plant entry authority.  Returns the plant-approved quantity: the bay AVR
        lot, capped by the bay's remaining HRSG-effective capital headroom, times the
        governor's selected FSR.  Zero means the plant (or any of its bays) rejected."""
        plant = self.real_plant_dcs
        telemetry = self._governor_telemetry.get(symbol)
        bay_id = _R5_SYMBOL_TO_BAY.get(symbol)
        result: Dict[str, Any] = {"admitted": False}
        if self._micom_trip is not None:
            result["reason"] = f"PLANT_MICOM_TRIP_ACTIVE:{self._micom_trip['ansi_code']}"
        elif (telemetry is None or not telemetry.available or not telemetry.atr
              or telemetry.z_score is None):
            result["reason"] = "PLANT_TELEMETRY_UNAVAILABLE"
        else:
            self.plant_call_counts["evaluate_entry"] += 1
            bid, ask = self._plant_quote(price)
            result = plant.evaluate_entry(
                engine_mode=self.plant_engine_mode, symbol=symbol,
                bar_dt=self._replay_event_time(timestamp), bar_index=self._native_bar_index,
                z_score=float(telemetry.z_score), price=float(price), atr=float(telemetry.atr),
                bid=bid, ask=ask, tick_age_s=0.0, bar_range=float(bar["high"]) - float(bar["low"]),
                side=side, entry_mode="trend_overspeed", **self._plant_grid_inputs())
        allowed = 0
        detail: Dict[str, Any] = {}
        if result.get("admitted"):
            bay = plant.bays[result["bay_id"]]
            bay_notional = sum(t["quantity"] * self._last_close.get(s, t["entry_price"])
                               for s, t in self.open_trades.items() if _R5_SYMBOL_TO_BAY.get(s) == bay.bay_id)
            headroom = max(0.0, float(bay.effective_allocation) - bay_notional)
            fsr = float(governor_entry.get("size_multiplier", 1.0)) if self._governor_full else 1.0
            plant_quantity = int(math.floor(min(int(result["quantity"]), math.floor(headroom / price)) * fsr))
            allowed = max(0, plant_quantity)
            detail = {"avr_quantity": int(result["quantity"]), "bay_headroom_notional": headroom,
                      "bay_open_notional": bay_notional, "fsr": fsr, "plant_quantity": allowed}
            if self.authority_telemetry:
                detail.update({
                    "dispatcher_weight": float(plant.dispatcher.weights[bay.bay_id]),
                    "dispatcher_allocation": float(plant.dispatcher.get_allocation(bay.bay_id)),
                    "bay_effective_allocation": float(bay.effective_allocation),
                    "avr_excitation_command": float(bay.avr.excitation_command),
                    "avr_state": result.get("avr_state"), "avr_control": result.get("avr_control")})
            if allowed <= 0:
                result["reason"] = "PLANT_BAY_HEADROOM_EXHAUSTED"
        if allowed <= 0:
            self._plant_entry_rejections[str(result.get("reason", "UNKNOWN")).split(":")[0]] += 1
        self._record_controller_event("PLANT_DCS_ENTRY", timestamp, symbol, {
            "candidate_id": candidate_id, "bay_id": bay_id, "requested_quantity": int(quantity),
            "admitted": bool(result.get("admitted")) and allowed > 0, "reason": result.get("reason"),
            "dynamic_z": result.get("dynamic_z"),
            "hrsg_effective_allocation": result.get("hrsg_effective_allocation"), **detail})
        return allowed

    def _plant_register_fill(self, symbol, fill_timestamp, trade) -> None:
        """Bind a confirmed fill to its turbine bay on the native plant.  A fill the plant's
        Engine-A latch refuses is unwound at the next open as a plant trip."""
        self.plant_call_counts["on_trade_filled"] += 1
        accepted = self.real_plant_dcs.on_trade_filled(
            engine_mode=self.plant_engine_mode, symbol=symbol,
            filled_at=self._replay_event_time(fill_timestamp), position_id=trade["trade_id"],
            side=trade["side"], quantity=int(trade["quantity"]), fill_price=float(trade["entry_price"]))
        binding = self.real_plant_dcs.bound_positions[trade["trade_id"]]
        trade["plant_bay_id"] = binding["bay_id"]
        if self.unit_protection_mode == "pre_sync_and_in_flight":
            # ANSI 32R counts adverse closes of THIS position only.
            self.real_plant_dcs.bays[binding["bay_id"]].relay.adverse_drift_counters[symbol] = 0
        self._record_controller_event("PLANT_FILL_BOUND", fill_timestamp, symbol, {
            "trade_id": trade["trade_id"], "bay_id": binding["bay_id"], "latch_accepted": accepted,
            "quantity": int(trade["quantity"]), "fill_price": float(trade["entry_price"])})
        if not accepted:
            self._arm_plant_trip(symbol, fill_timestamp, trade, "ENGINE_A_FILL_LATCH_REJECTED")

    def _arm_plant_trip(self, symbol, timestamp, trade, reason: str) -> None:
        trade["controller_exit_pending"] = {
            "armed_timestamp": str(timestamp), "reason": reason,
            "exit_reason": f"plant_trip:{reason}", "event": "PLANT_TRIP_EXIT",
        }
        self._plant_trip_counts[reason.split(":")[0]] += 1
        self._record_controller_event("PLANT_TRIP_ARMED", timestamp, symbol, {
            "trade_id": trade.get("trade_id"), **trade["controller_exit_pending"]})

    def _plant_position_control(self, symbol, timestamp, trade, bar, held_bars, path_observation) -> bool:
        """Native plant control of one open position from this completed bar.  Returns True
        when a plant trip was armed for the next bar's open."""
        sign = 1.0 if trade["side"] == "BUY" else -1.0
        entry = float(trade["entry_price"])
        risk = abs(entry - float(trade["stop_price"]))
        if not (math.isfinite(risk) and risk > 0.0):
            result = {"action": "EXIT", "reason": "PLANT_INPUT_INVALID:zero_initial_risk"}
        else:
            favorable = float(bar["high"]) if sign > 0 else float(bar["low"])
            mfe_r = max(float(trade.get("plant_mfe_r", 0.0)), sign * (favorable - entry) / risk)
            trade["plant_mfe_r"] = mfe_r
            reference_r = (float(path_observation.expected_r)
                           if path_observation is not None and math.isfinite(float(path_observation.expected_r))
                           else 0.0)
            telemetry = self._governor_telemetry.get(symbol)
            atr = (float(telemetry.atr) if telemetry is not None and telemetry.available and telemetry.atr
                   else float(trade.get("entry_atr") or 0.0))
            bid, ask = self._plant_quote(float(bar["close"]))
            self.plant_call_counts["evaluate_position_control"] += 1
            try:
                result = self.real_plant_dcs.evaluate_position_control(
                    engine_mode=self.plant_engine_mode, symbol=symbol,
                    bar_dt=self._replay_event_time(timestamp), bar_index=self._native_bar_index,
                    current_r=sign * (float(bar["close"]) - entry) / risk, reference_r=reference_r,
                    max_favorable_r=mfe_r, elapsed_bars=int(held_bars),
                    min_hold_bars=int(trade["minimum_hold_bars"]),
                    max_hold_bars=max(1, int(trade["maximum_hold_bars"]), int(trade["minimum_hold_bars"])),
                    hard_stop_r=-1.0, trade_target_r=abs(float(trade["target_price"]) - entry) / risk,
                    bid=bid, ask=ask, tick_age_s=0.0, atr=atr,
                    bar_range=float(bar["high"]) - float(bar["low"]),
                    position_id=trade["trade_id"], **self._plant_grid_inputs())
            except ValueError as exc:
                result = {"action": "EXIT", "reason": f"PLANT_INPUT_INVALID:{exc}"}
            if (self.unit_protection_mode == "pre_sync_and_in_flight"
                    and not (result.get("action") == "EXIT"
                             and str(result.get("reason", "")).startswith(_PLANT_TRIP_PREFIXES))):
                in_flight = self._in_flight_protection(symbol, trade, bar)
                if in_flight is not None:
                    result = in_flight
        reason = str(result.get("reason", ""))
        trip = result.get("action") == "EXIT" and reason.startswith(_PLANT_TRIP_PREFIXES)
        self._record_controller_event("PLANT_POSITION_CONTROL", timestamp, symbol, {
            "trade_id": trade.get("trade_id"), "bay_id": trade.get("plant_bay_id"),
            "action": result.get("action"), "reason": reason, "trip": trip,
            "steam_pressure_pu": result.get("steam_pressure_pu")})
        if trip:
            self._arm_plant_trip(symbol, timestamp, trade, reason)
        return trip

    @property
    def _governor_full(self) -> bool:
        return self.governor_authority == "full"

    def _governor_for(self, symbol: str):
        bay_id = _R5_SYMBOL_TO_BAY.get(symbol)
        return bay_id, (self._bay_governors.get(bay_id) if bay_id is not None else None)

    def _governor_session_bar(self, symbol: str, bar_idx: Optional[int]) -> Optional[int]:
        """Causal session-bar index (FSRS input); None outside a replay run (fails closed)."""
        index = self._session_bar_index.get(symbol)
        if index is None or bar_idx is None or not 0 <= bar_idx < len(index):
            return None
        return int(index[bar_idx])

    def _governor_tick_telemetry(self, tick_events, symbol_bars) -> None:
        """Measure every symbol at this timestamp from its completed-bar prefix, then each
        bay's exhaust spread from the members measured at the same instant."""
        self._governor_telemetry = {
            event.symbol: bar_telemetry(symbol_bars[event.symbol].iloc[:event.bar_idx + 1], self.governor_config)
            for event in tick_events
        }
        members: Dict[str, List[BarTelemetry]] = {}
        for symbol, telemetry in self._governor_telemetry.items():
            bay_id = _R5_SYMBOL_TO_BAY.get(symbol)
            if bay_id is not None:
                members.setdefault(bay_id, []).append(telemetry)
        self._bay_exhaust_spread = {bay_id: exhaust_spread(rows) for bay_id, rows in members.items()}

    def _governor_grid_return(self, timestamp) -> Optional[float]:
        """NIFTY deviation from its EMA at this timestamp (the governor droop input), or None
        when the plant reference for this exact timestamp is unavailable."""
        snapshot = self._paper_plant_snapshot
        if snapshot is None or self._paper_plant_timestamp != timestamp:
            return None
        deviation = snapshot.grid.nifty_deviation
        return None if deviation is None or not math.isfinite(float(deviation)) else float(deviation)

    def _observe_conviction(self, symbol: str, signal, composite_result) -> None:
        """Rank this bar's PA entry, PA exit and chart-studies confidences against the values seen
        on the preceding ``gov_z_window_bars`` bars (causal), then record them."""
        window = self.governor_config.z_window_bars
        history = self._conviction_history.setdefault(
            symbol, {key: deque(maxlen=window) for key in ("pa", "pa_exit", "studies")})
        current = {"pa": float(signal.confidence), "pa_exit": float(signal.exit_confidence),
                   "studies": float(composite_result["confidence"])}
        self._conviction_rank[symbol] = {
            key: causal_percentile_rank(history[key], value, window) for key, value in current.items()}
        for key, value in current.items():
            history[key].append(value)

    def _governor_conviction(self, symbol: str, side: str, signal, composite_result, pa_key: str) -> float:
        ranks = self._conviction_rank.get(symbol) or {}
        if ranks.get(pa_key) is None or ranks.get("studies") is None:
            raise GovernorInputError("conviction history warming up")
        return side_aligned_conviction(
            side, pa_direction=int(signal.direction), pa_confidence=ranks[pa_key],
            studies_direction=(composite_result or {}).get("direction"), studies_confidence=ranks["studies"])

    def _governor_entry(self, symbol, timestamp, bar_idx, side, signal, decision, composite_result,
                        chart_studies_confidence) -> Dict[str, Any]:
        bay_id, governor = self._governor_for(symbol)
        if governor is None:
            return {"bay_id": None, "action": "NO_ACTION", "reason": "GOVERNOR_UNMAPPED_SYMBOL", "size_multiplier": 0.0}
        grid_return = self._governor_grid_return(timestamp)
        if grid_return is None:
            return {"bay_id": bay_id, "action": "NO_ACTION", "reason": "GRID_REFERENCE_UNAVAILABLE",
                    "size_multiplier": 0.0}
        try:
            conviction = self._governor_conviction(symbol, side, signal, composite_result, "pa")
        except (GovernorInputError, TypeError, ValueError) as exc:
            return {"bay_id": bay_id, "action": "NO_ACTION", "reason": f"INVALID_GOVERNOR_INPUT:{exc}",
                    "size_multiplier": 0.0}
        telemetry = self._governor_telemetry.get(symbol, BarTelemetry(False, "TELEMETRY_NOT_MEASURED"))
        result = governor_entry_decision(
            governor, self.governor_config, side=side, telemetry=telemetry, conviction=conviction,
            drawdown=self._current_drawdown(), session_bar=self._governor_session_bar(symbol, bar_idx),
            grid_return_fraction=grid_return, bay_exhaust_spread=self._bay_exhaust_spread.get(bay_id),
            # PA candidates follow momentum: the governor limits overspeed rather than
            # demanding a mean-reversion dip (which vetoed every momentum candidate).
            entry_mode="trend_overspeed")
        entry_measurement = None
        if self.governor_position_policy is not None:
            try:
                entry_measurement = absolute_conviction(side, signal, composite_result)
            except (KeyError, TypeError, ValueError) as exc:
                return {"action": "NO_ACTION", "reason": f"INVALID_ENTRY_CONVICTION:{exc}", "size_multiplier": 0.0}
        return {"bay_id": bay_id, "grid_return_fraction": grid_return, "conviction": conviction,
                "entry_absolute_conviction": entry_measurement, **result}

    def _governor_position_step(self, symbol, timestamp, trade, bar, signal, held_bars, composite_result,
                                chart_studies_confidence, path_observation) -> Dict[str, Any]:
        """The governor's per-bar HOLD/EXIT decision for one open position, from the completed bar.
        In ``full`` authority an EXIT is armed for the next bar's open and a HOLD moves the
        protective stop to the governor's ratcheted R floor (one way only)."""
        bay_id, governor = self._governor_for(symbol)
        if governor is None:
            result = {"action": "EXIT", "reason": "GOVERNOR_UNMAPPED_SYMBOL", "load_shed": False}
        else:
            sign = 1.0 if trade["side"] == "BUY" else -1.0
            entry = float(trade["entry_price"])
            risk = abs(entry - float(trade["stop_price"]))       # 1R: fill to the plan's hard stop
            valid_risk = math.isfinite(risk) and risk > 0.0
            favorable = float(bar["high"]) if sign > 0 else float(bar["low"])
            measured_r = sign * (float(bar["close"]) - entry) / risk if valid_risk else math.nan
            mfe_r = (max(float(trade.get("governor_mfe_r", 0.0)), sign * (favorable - entry) / risk)
                     if valid_risk else math.nan)
            if valid_risk:
                trade["governor_mfe_r"] = mfe_r
            conviction_detail = None
            fuel_cut_confirmed = None
            try:
                if self.governor_position_policy is not None:
                    current = absolute_conviction(trade["side"], signal, composite_result)
                    conviction_detail = update_conviction(trade, current, timestamp, self.governor_position_policy)
                    conviction = conviction_detail["conviction"]
                    fuel_cut_confirmed = conviction_detail["fuel_cut_confirmed"]
                else:
                    conviction = self._governor_conviction(symbol, trade["side"], signal, composite_result, "pa_exit")
            except (KeyError, GovernorInputError, TypeError, ValueError):
                conviction = math.nan              # rejected by the gate below: fail closed to EXIT
            telemetry = self._governor_telemetry.get(symbol)
            result = governor_position_decision(
                governor, self.governor_config, position_id=trade.get("trade_id"), measured_r=measured_r,
                reference_r=path_observation.expected_r if path_observation is not None else math.nan,
                max_favorable_r=mfe_r, elapsed_bars=int(held_bars),
                min_hold_bars=int(trade["minimum_hold_bars"]), max_hold_bars=int(trade["maximum_hold_bars"]),
                trade_target_r=abs(float(trade["target_price"]) - entry) / risk if valid_risk else math.nan,
                conviction=conviction, drawdown=self._current_drawdown(),
                fuel_cut_confirmed=fuel_cut_confirmed,
                velocity=telemetry.velocity if telemetry is not None and telemetry.available else None,
                session_bar=self._governor_session_bar(symbol, getattr(self, "_current_bar_idx", None)),
                bay_exhaust_spread=self._bay_exhaust_spread.get(bay_id), hard_stop_r=-1.0,
                path_noise_r=(telemetry.atr / risk
                              if telemetry is not None and telemetry.available and valid_risk else None),
                position_control=(self._position_control_v3 if self.governor_position_mode == "v3" else None))
            result = {"measured_r": measured_r, "max_favorable_r": mfe_r,
                      "conviction_detail": conviction_detail, **result}
        self._governor_position_counts[f"{result['action']}:{result['reason']}"] += 1
        self._record_controller_event("GOVERNOR_POSITION_DECISION", timestamp, symbol, {
            "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
            "authority": self.governor_authority, "bay_id": bay_id,
            **{key: result.get(key) for key in (
                "action", "reason", "load_shed", "measured_r", "max_favorable_r", "protected_r_floor",
                "fsr_selected", "fsr_effective", "controlling_limiter", "limiters", "bay_exhaust_spread",
                "conviction_detail", "inner", "conviction_exit_deferred")},
        })
        if not self._governor_full:
            return result
        if result["action"] == "EXIT":
            trade["controller_exit_pending"] = {
                "armed_timestamp": str(timestamp), "reason": result["reason"],
                "exit_reason": f"governor_exit:{result['reason']}", "event": "GOVERNOR_EXIT",
            }
            self._record_controller_event("GOVERNOR_EXIT_ARMED", timestamp, symbol, {
                "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                **trade["controller_exit_pending"]})
        elif result.get("protected_r_floor") is not None:
            entry = float(trade["entry_price"])
            risk = abs(entry - float(trade["stop_price"]))
            proposed = (entry + float(result["protected_r_floor"]) * risk if trade["side"] == "BUY"
                        else entry - float(result["protected_r_floor"]) * risk)
            before = float(trade.get("governor_stop_price", trade["stop_price"]))
            trade["governor_stop_price"] = max(before, proposed) if trade["side"] == "BUY" else min(before, proposed)
            self._record_controller_event("GOVERNOR_STOP_UPDATE", timestamp, symbol, {
                "trade_id": trade.get("trade_id"), "stop_before": before,
                "stop_after": trade["governor_stop_price"], "proposed_stop": proposed,
                "effective_from": "NEXT_BAR", "side": trade["side"],
                "initial_risk": risk, "inner": result.get("inner"),
            })
        return result

    def _gross_exposure_notional(self) -> float:
        return sum(t["quantity"] * self._last_close.get(s, t["entry_price"])
                   for s, t in self.open_trades.items())

    def _record_mtm(self, timestamp: object) -> None:
        """Update drawdown online; retain the full curve only for full telemetry."""
        equity = self._mark_to_market_equity()
        self._mtm_peak = max(self._mtm_peak, equity)
        if self._mtm_peak > 0.0:
            self._mtm_max_drawdown_fraction = max(
                self._mtm_max_drawdown_fraction,
                (self._mtm_peak - equity) / self._mtm_peak,
            )
        if self.telemetry_mode == "full":
            self._mtm_equity_curve.append((str(timestamp), equity))

    _leg_cost = staticmethod(leg_cost)
    _paper_fill_price = staticmethod(paper_fill_price)

    def _system_state(self) -> SystemState:
        equity = self._equity()
        unrealized = self._mark_to_market_equity() - equity
        return SystemState(
            portfolio_value=equity, current_dd_percent=self._current_drawdown(),
            current_lambda=self._gross_exposure_notional() / max(equity, 1.0),
            daily_realized_loss=max(0.0, self._day_start_equity - equity),
            daily_unrealized_loss=max(0.0, -unrealized),
            open_positions_count=len(self.open_trades),
            open_positions=[type("P", (), {"position_notional":
                t["quantity"] * self._last_close.get(s, t["entry_price"])})()
                for s, t in self.open_trades.items()],
            market_data_age_seconds=0, broker_connected=True, broker_offline_seconds=0,
            kill_switch_active=not bool(self.safety_contract.values["kill_switch_enabled"]),
            circuit_breaker_triggered=False,
        )

    def _verify_broker_position_reconciles(self, symbol: str, trade: Dict[str, Any]) -> None:
        """Fail closed: an authoritative close may only ever close the position this orchestrator's
        own ledger believes is open.  A missing/short/insufficient broker position means the exit
        order would open or flip a position instead of closing one -- never silently execute that.
        This is a precondition for the realized-R close feedback below, never weakened to make a
        badly-seeded caller pass."""
        position = self._broker_position(symbol)
        held = float(position.get("quantity", 0.0))
        expected_sign = 1.0 if trade["side"] == "BUY" else -1.0
        required = float(trade["quantity"])
        if held * expected_sign < required - _POSITION_RECONCILIATION_EPS:
            raise PositionReconciliationError(
                f"{symbol}: ledger open_trades expects a {trade['side']} position of at least "
                f"{required} to close, but the broker holds {held}; refusing to execute an exit "
                f"that would open or flip a position instead of closing the existing one"
            )

    @staticmethod
    def _close_feedback_key(symbol: str, trade: Dict[str, Any]) -> str:
        """Idempotency key for one authoritative close.  A real trade always carries a
        ``trade_id`` (unique per entry, see the real entry flow); when one is absent (a minimal
        test fixture) the identity of the ``trade`` record itself is the key, since a genuine
        retry of the SAME close would be invoked with that SAME ``open_trades[symbol]`` record,
        while two distinct real trades on the same symbol are always distinct dict objects."""
        trade_id = trade.get("trade_id")
        if trade_id is not None:
            return f"trade_id:{trade_id}"
        return f"symbol_object:{symbol}:{id(trade)}"

    def _register_realized_r_close_feedback(
        self, *, symbol: str, trade: Dict[str, Any], bay_id: str, realized_r: float, reason: str = "CLOSE",
    ) -> None:
        """Feed one authoritative realized-R close to both separately-owned feedback paths,
        exactly once per close, within this process/replay run only (no persistent cross-restart
        receipt is implemented -- that remains future work).

        State machine per receipt key: absent -> PENDING -> DONE.  A retried/duplicate call for a
        key that is already PENDING or DONE is a silent no-op (never fed twice).  If either
        ``register_trade`` call raises after the receipt is marked PENDING, the receipt is left at
        PENDING (never advanced to DONE, never removed), so a retry is still blocked from feeding
        the same close again -- and the exception still propagates unmodified; this never swallows
        an unexpected error."""
        key = self._close_feedback_key(symbol, trade)
        if key in self._close_feedback_receipts:
            return
        self._close_feedback_receipts[key] = "PENDING"
        self.plant_control.dispatch_controller.merit_source.register_trade(bay_id, realized_r)
        if self.authority_telemetry:
            self._record_controller_event("DISPATCHER_UPDATE", getattr(self, "_native_timestamp", None), symbol, {
                "trade_id": trade.get("trade_id"), "affected_order_quantity": trade.get("quantity"),
                **(self.plant_control.dispatch_controller.merit_source.last_update or {})})
        if self.plant_control.mode == PlantControlMode.PAPER_APPLY:
            self.real_plant_dcs.bays[bay_id].register_outcome(
                realized_r=realized_r, reason=reason, bar_index=max(0, self._native_bar_index))
        else:
            self._bay_governors[bay_id].register_trade(realized_r)
        self._close_feedback_receipts[key] = "DONE"

    # ---- Execution-port extension points ------------------------------------------------------
    # Engine A (this class) keeps the synchronous offline paper broker and the next-bar replay
    # price exactly as before.  A derived event-driven engine overrides only these seams; every
    # strategy, safety, plant and governor decision stays in the shared code.
    def _next_bar_timestamp(self, bars: pd.DataFrame, bar_idx: int) -> pd.Timestamp:
        return pd.Timestamp(bars.iloc[bar_idx + 1]["timestamp"])

    def _entry_reference_price(self, bars: pd.DataFrame, bar_idx: int) -> float:
        return float(bars.iloc[bar_idx + 1]["open"])

    def _entries_suspended(self, symbol: str) -> bool:
        return False

    def _broker_position(self, symbol: str) -> Dict[str, Any]:
        return self.broker.get_position(symbol)

    def _broker_realized_pnl(self) -> float:
        return self.broker.realized_pnl

    def _execute_exit(self, symbol: str, timestamp, trade: Dict[str, Any], exit_price: float, reason: str) -> None:
        self._assert_paper_plant_broker()
        self._verify_broker_position_reconciles(symbol, trade)
        close_side = "SELL" if trade["side"] == "BUY" else "BUY"
        self._exit_orders_submitted += 1
        result = self.broker.place_order(
            symbol=symbol, side=close_side, quantity=trade["quantity"], order_type="MARKET",
            market_price=exit_price, config=self.safety_contract.as_dict(), parameter_registry=self.registry,
        )
        self._on_exit_order_result(symbol, timestamp, trade, result, reason)

    def _on_exit_order_result(self, symbol: str, timestamp, trade: Dict[str, Any], result: Dict[str, Any],
                              reason: str) -> None:
        """Authoritative close bookkeeping for one exit order result (fill or rejection)."""
        close_side = "SELL" if trade["side"] == "BUY" else "BUY"
        if result["passed"]:
            state = self._exit_controller_states.get(symbol)
            pnl = (
                (result["filled_price"] - trade["entry_price"]) * trade["quantity"]
                if trade["side"] == "BUY" else (trade["entry_price"] - result["filled_price"]) * trade["quantity"]
            )
            trade_costs = self._leg_cost(trade["entry_price"], trade["quantity"], trade["side"]) + self._leg_cost(
                result["filled_price"], trade["quantity"], close_side
            )
            completed = {
                "symbol": symbol, "side": trade["side"], "entry_price": trade["entry_price"],
                "exit_price": result["filled_price"], "quantity": trade["quantity"],
                "entry_timestamp": trade["entry_timestamp"], "exit_timestamp": str(timestamp),
                "reason": reason, "pnl": pnl, "costs": trade_costs, "net_pnl": pnl - trade_costs,
                "trade_id": trade.get("trade_id"), "candidate_id": trade.get("candidate_id"),
                "bars_held": int(state.bars_held) if state is not None else None,
                "entry_atr": trade.get("entry_atr"),
                "planned_entry_price": trade.get("planned_entry_price"),
                "planned_stop_price": trade.get("planned_stop_price"),
                "planned_target_price": trade.get("planned_target_price"),
            }
            if state is not None:
                risk = abs(float(trade["entry_price"]) - float(state.initial_stop_price))
                if risk > 0:
                    favorable = state.mfe_price - trade["entry_price"] if trade["side"] == "BUY" else trade["entry_price"] - state.mfe_price
                    adverse = state.mae_price - trade["entry_price"] if trade["side"] == "BUY" else trade["entry_price"] - state.mae_price
                    completed.update({"mfe_price": state.mfe_price, "mae_price": state.mae_price,
                                      "mfe_r": favorable / risk, "mae_r": adverse / risk,
                                      "terminal_bar_excursion": "intrabar_order_unknown"})
                    terminal = trade.get("_terminal_bar")
                    inclusive_mfe, inclusive_mae = state.mfe_price, state.mae_price
                    if terminal is not None:
                        if trade["side"] == "BUY":
                            inclusive_mfe = max(inclusive_mfe, float(terminal["high"]))
                            inclusive_mae = min(inclusive_mae, float(terminal["low"]))
                        else:
                            inclusive_mfe = min(inclusive_mfe, float(terminal["low"]))
                            inclusive_mae = max(inclusive_mae, float(terminal["high"]))
                    sign = 1 if trade["side"] == "BUY" else -1
                    completed.update({
                        "mfe_pre_exit_bar_r": favorable / risk,
                        "mae_pre_exit_bar_r": adverse / risk,
                        "mfe_terminal_inclusive_r": sign * (inclusive_mfe - trade["entry_price"]) / risk,
                        "mae_terminal_inclusive_r": sign * (inclusive_mae - trade["entry_price"]) / risk,
                        "terminal_inclusive_is_ohlc_bound": True,
                    })
            shadow = None
            if state is not None and state.shadow_exit_price is not None:
                shadow_close_side = "SELL" if trade["side"] == "BUY" else "BUY"
                # Compare like with like: the shadow stop identifies the
                # counterfactual *market* trigger from causal OHLC, then it
                # receives the same deterministic paper-broker adverse fill
                # adjustment as the live exit.  Comparing its raw stop with
                # the live broker fill would fabricate a P&L difference even
                # when both trigger on the same bar.
                shadow_market_exit_price = float(state.shadow_exit_price)
                shadow_filled_price = self._paper_fill_price(
                    shadow_market_exit_price, shadow_close_side, self.broker.slippage_fraction
                )
                shadow_pnl = (
                    (shadow_filled_price - trade["entry_price"]) * trade["quantity"]
                    if trade["side"] == "BUY" else (trade["entry_price"] - shadow_filled_price) * trade["quantity"]
                )
                shadow_costs = self._leg_cost(trade["entry_price"], trade["quantity"], trade["side"]) + self._leg_cost(
                    shadow_filled_price, trade["quantity"], shadow_close_side
                )
                shadow = {
                    "shadow_exit_timestamp": state.shadow_exit_timestamp,
                    "shadow_market_exit_price": shadow_market_exit_price,
                    "shadow_exit_price": shadow_filled_price,
                    "shadow_exit_reason": state.shadow_exit_reason,
                    "shadow_exit_bars_held": state.shadow_exit_bars_held,
                    "shadow_pnl": shadow_pnl,
                    "shadow_costs": shadow_costs,
                    "shadow_net_pnl": shadow_pnl - shadow_costs,
                }
                completed["shadow_r_trajectory"] = shadow

            # ---- 1. Authoritative close bookkeeping --------------------------------------
            # The broker position is already flat.  Everything that keeps this engine's ledger,
            # P&L and protection state consistent with the broker happens first, before any
            # research telemetry can raise.  A later defect still propagates, but can no longer
            # leave a flat broker position recorded as open.
            self.completed_trades.append(completed)
            _pnl = float(completed.get("net_pnl", 0.0))
            if _pnl < 0:
                _c_losses = self.symbol_consecutive_losses.get(symbol, 0) + 1
                self.symbol_consecutive_losses[symbol] = _c_losses
                self.symbol_cooldown_until_bar[symbol] = (
                    getattr(self, "_current_bar_idx", 0) + self.controller_parameters.symbol_loss_cooldown_bars
                )
                if _c_losses >= self.controller_parameters.symbol_trip_consecutive_losses:
                    self.symbol_tripped[symbol] = True
            elif _pnl > 0:
                # Any profitable exit breaks consecutive loss streak
                self.symbol_consecutive_losses[symbol] = 0
            self._equity_curve.append(self._equity())
            del self.open_trades[symbol]
            self._exit_controller_states.pop(symbol, None)
            _, governor = self._governor_for(symbol)
            if governor is not None:
                governor.confirm_position_closed(trade.get("trade_id"))
            if self._plant_wired:
                self.real_plant_dcs.release_position(trade.get("trade_id"))
            self._record_mtm(timestamp)
            self._record_controller_event("CONTROLLER_OUTCOME", timestamp, symbol, {
                "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                "exit_reason": reason, "net_pnl": completed["net_pnl"], "pnl": pnl, "costs": trade_costs,
                "entry_atr": completed["entry_atr"], "planned_entry_price": completed["planned_entry_price"],
                "planned_stop_price": completed["planned_stop_price"], "planned_target_price": completed["planned_target_price"],
                "mfe_r": completed.get("mfe_r"), "mae_r": completed.get("mae_r"),
                "terminal_bar_excursion": completed.get("terminal_bar_excursion"),
                "shadow_r_trajectory": shadow,
            })

            # ---- 2. Authoritative realized-R close feedback -------------------------------
            # Plant-level dispatch feedback (BLOCKER 2) and local governor feedback: after the
            # authoritative exit fill, after position/ledger reconciliation and after the engine
            # ledger above already records the trade as closed.  Exactly-once receipts still apply;
            # an exception here propagates with the ledger already consistent with the broker.
            if state is not None:
                risk = abs(float(trade["entry_price"]) - float(state.initial_stop_price))
                if risk > 0.0:
                    try:
                        bay_id = _r5_bay_for_symbol(symbol)
                    except KeyError:
                        # Symbol outside the certified 48-symbol R5 topology (e.g. a synthetic
                        # test-only symbol): there is no R5 bay to feed, exactly like the native
                        # plant's own UNMAPPED_SYMBOL admission path.  Never invent a bay mapping.
                        bay_id = None
                    if bay_id is not None:
                        realized_r = self.exit_controller._r_multiple(state, float(result["filled_price"]))
                        self._register_realized_r_close_feedback(
                            symbol=symbol, trade=trade, bay_id=bay_id, realized_r=realized_r, reason=reason)

            # ---- 3. Research telemetry (never part of the authoritative books) -------------
            if completed["bars_held"] is None:
                # Without exit-controller state the holding period is unknown.  It is never
                # fabricated: the pre-entry evidence stays pending instead of being paired
                # with an invented outcome.
                self._record_controller_event("ENTRY_EXPECTANCY_OUTCOME_UNAVAILABLE", timestamp, symbol, {
                    "candidate_id": completed["candidate_id"], "trade_id": completed["trade_id"],
                    "reason": "bars_held_unknown_without_exit_controller_state",
                })
            else:
                entry_evidence = self.entry_expectancy_ledger.record_outcome({
                    "candidate_id": completed["candidate_id"], "trade_id": completed["trade_id"],
                    "exit_timestamp": completed["exit_timestamp"], "exit_reason": reason,
                    "bars_held": completed["bars_held"], "pnl": completed["pnl"],
                    "costs": completed["costs"], "net_pnl": completed["net_pnl"],
                    "mfe_r": completed.get("mfe_r"), "mae_r": completed.get("mae_r"),
                    "terminal_bar_excursion": completed.get("terminal_bar_excursion"),
                })
                if entry_evidence is not None:
                    self._record_controller_event("ENTRY_EXPECTANCY_OUTCOME", timestamp, symbol, entry_evidence)
            closed_loop_profile = self.closed_loop.record_outcome(
                completed, regime=trade.get("closed_loop", {}).get("regime", "unknown"),
            )
            self._record_controller_event("OUTCOME_LEDGER_UPDATE", timestamp, symbol, {
                "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                "net_pnl": completed["net_pnl"], "entry_quality_profile": closed_loop_profile,
            })

    def _record_controller_event(self, event_type: str, timestamp: object, symbol: str, payload: Dict[str, Any]) -> None:
        """Record controller state and any bounded paper-only actuation."""
        event = {"event_type": event_type, "timestamp": str(timestamp), "symbol": symbol, **payload}
        self._controller_event_counts[event_type] += 1
        if event_type == "POSITION_SIZING":
            # Compact mode retains only the small, decision-level sizing
            # stream. Per-bar controller observations remain aggregate-only.
            self._position_sizing_events.append(event)
        if self.telemetry_mode == "full":
            self.controller_telemetry.append(event)

    def _maybe_exit(
        self, symbol: str, timestamp, bar, signal, held_bars: int, session_last_bar: bool,
        chart_studies_confidence: float, chart_studies_audit: Optional[Dict[str, Any]] = None,
    ) -> None:
        trade = self.open_trades.get(symbol)
        if trade is None:
            return
        # Completed fill bar is elapsed bar 0; the clock owns this counter.
        state = self._exit_controller_states.get(symbol)
        if state is not None:
            state.bars_held = int(held_bars)
        trade["_terminal_bar"] = dict(bar)
        studies_direction = (chart_studies_audit or {}).get("direction")
        if studies_direction is not None and studies_direction != (1 if trade["side"] == "BUY" else -1):
            chart_studies_confidence = 0.0
        if pd.Timestamp(timestamp).strftime("%H:%M") >= self.entry_decision_engine.config.force_close_time:
            self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "force_close_time")
            return
        halt_dd = min(float(self.config.require("drawdown_halt_threshold")),
                      float(self.safety_contract.values["safety_drawdown_halt_threshold"]))
        if self._current_drawdown() >= halt_dd:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "forced_close_drawdown_halt")
            return
        if self._micom_trip is not None and self._micom_trip["ansi_code"] != "MICOM_INPUT_UNAVAILABLE":
            # Substation protection outranks the governor: a measured MiCOM trip unwinds the fleet.
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]),
                               f"micom_trip:{self._micom_trip['ansi_code']}")
            return

        # The real feedback path: re-run the exit controller on THIS bar's
        # freshly-evaluated PA signal (exit_confidence -- the field PA
        # computes specifically for exit timing, distinct from entry
        # confidence) AND the chart-studies composite confidence (Box 4b,
        # computed above, never blended into `signal`) as two fully
        # separate PID tracks, before checking anything else. current_stop
        # is a ratcheted stop that only ever tightens.  Critically, an update
        # calculated from THIS bar's close is armed for the NEXT bar.  The
        # OHLC order inside the current bar is unknown, so testing a freshly
        # calculated stop against this bar's high/low would be look-ahead.
        state = self._exit_controller_states.get(symbol)
        # In shadow mode the controller is strictly observational.  In
        # active-paper mode a stop ratchet computed from a completed prior
        # bar becomes the protective stop for this bar.
        if self._governor_full:
            # Full governor authority: the protective stop is the plan's hard stop ratcheted by
            # the governor's own R floor (armed from a completed earlier bar).  The exit PID's
            # ratchet and the path loop are advisory.
            current_stop = float(trade.get("governor_stop_price", trade["stop_price"]))
        else:
            current_stop = (
                float(state.current_stop_price)
                if state is not None and self.closed_loop_mode == "active_paper"
                else float(trade["stop_price"])
            )
        pending_exit = trade.get("controller_exit_pending")
        if pending_exit is not None:
            # The exit was armed using only the preceding completed bar.
            # Preserve a hard-stop or target gap's precedence at this open;
            # otherwise the controller has direct paper-only exit authority.
            if trade["side"] == "BUY" and float(bar["open"]) <= current_stop:
                self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "stop_gap")
            elif trade["side"] == "SELL" and float(bar["open"]) >= current_stop:
                self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "stop_gap")
            elif trade["side"] == "BUY" and float(bar["open"]) >= float(trade["target_price"]):
                self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "target_gap")
            elif trade["side"] == "SELL" and float(bar["open"]) <= float(trade["target_price"]):
                self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "target_gap")
            else:
                self._record_controller_event(pending_exit.get("event", "CONTROLLER_PATH_EXIT"), timestamp, symbol, {
                    "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                    **pending_exit,
                })
                self._execute_exit(symbol, timestamp, trade, float(bar["open"]),
                                   pending_exit.get("exit_reason", "controller_path_exit"))
            return
        path_observation = None
        if state is not None:
            shadow_exit = self.exit_controller.check_shadow_stop(state, bar, timestamp)
            if shadow_exit is not None:
                self._record_controller_event("SHADOW_R_TRAJECTORY_EXIT", timestamp, symbol, {
                    "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"), **shadow_exit,
                })
            # Droop input: CURRENT ATR, re-measured this bar -- not the
            # frozen entry-time ATR -- matches how atr is computed
            # everywhere else in this file (signal.volatility * close).
            current_atr = float(signal.volatility) * float(bar["close"])
            self.exit_controller.configure(self.config)
            self.exit_controller.gain_scale = self._gain_scale_for(symbol)
            state = self.exit_controller.update(
                symbol, state, float(signal.exit_confidence), chart_studies_confidence,
                float(bar["close"]), current_atr, held_bars=held_bars,
            )
            self._exit_controller_states[symbol] = state
            closed_loop_snapshot = trade.get("closed_loop")
            if closed_loop_snapshot is not None:
                path_observation = self.closed_loop.observe_trade_path(
                    closed_loop_snapshot, float(bar["close"]), state.bars_held,
                )
                self._record_controller_event("TRADE_PATH_COMPARATOR", timestamp, symbol, {
                    "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                    "bars_held": state.bars_held, **path_observation.to_dict(),
                })
                if (self.closed_loop_mode == "active_paper" and not self._governor_full
                        and path_observation.behind_path):
                    # Path control is one-way and never crosses the current
                    # price (guaranteed by the supervisor). It can only make
                    # the already-ratcheted stop stricter.
                    risk = float(closed_loop_snapshot["reference_path"]["initial_risk"])
                    entry = float(trade["entry_price"])
                    proposed_stop = (
                        entry + path_observation.suggested_protection_r * risk
                        if trade["side"] == "BUY" else entry - path_observation.suggested_protection_r * risk
                    )
                    before_path_actuation = state.current_stop_price
                    if trade["side"] == "BUY":
                        state.current_stop_price = max(state.current_stop_price, proposed_stop)
                    else:
                        state.current_stop_price = min(state.current_stop_price, proposed_stop)
                    self._record_controller_event("TRADE_PATH_STOP_ACTUATION", timestamp, symbol, {
                        "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                        "stop_before": before_path_actuation, "proposed_stop": proposed_stop,
                        "stop_after": state.current_stop_price,
                    })
                    if held_bars >= trade["minimum_hold_bars"]:
                        # Do not retroactively execute on this bar: its
                        # OHLC sequence is unknown.  Arm a market exit for
                        # the next bar's open, shortening (never extending)
                        # the holding period if the path remains breached.
                        trade["controller_exit_pending"] = {
                            "armed_timestamp": str(timestamp),
                            "actual_r": path_observation.actual_r,
                            "expected_r": path_observation.expected_r,
                            "error_r": path_observation.error_r,
                            "reason": "behind_reference_path",
                        }
                        self._record_controller_event("CONTROLLER_PATH_EXIT_ARMED", timestamp, symbol, {
                            "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                            **trade["controller_exit_pending"],
                        })
            entry_atr = trade.get("entry_atr")
            atr_drift_fraction = None
            if entry_atr is not None and float(entry_atr) > 0.0:
                atr_drift_fraction = (current_atr - float(entry_atr)) / float(entry_atr)
            study_weights = dict((chart_studies_audit or {}).get("weights", {}))
            study_hit_rates = dict((chart_studies_audit or {}).get("hit_rates", {}))
            # These bounds are owned by CompositeStudySignal.  This is a
            # passive audit label only; it cannot influence the controller.
            study_weights_clamped = any(
                weight <= MIN_WEIGHT + 1e-12 or weight >= MAX_WEIGHT - 1e-12
                for weight in study_weights.values()
            )
            self._record_controller_event("EXIT_PROTECTION_UPDATE", timestamp, symbol, {
                "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"),
                "baseline_window_bars": self.exit_controller.baseline_window,
                "entry_atr": entry_atr,
                "atr_drift_fraction": atr_drift_fraction,
                "study_weights": study_weights,
                "study_hit_rates": study_hit_rates,
                "study_votes": dict((chart_studies_audit or {}).get("votes", {})),
                # Raw causal study measurements and the four independent
                # study-weight PID outputs.  Audit only: neither field is an
                # additional execution input at this layer.
                "study_indicator_inputs": dict((chart_studies_audit or {}).get("indicator_inputs", {})),
                "study_weight_pid_audit": dict((chart_studies_audit or {}).get("weight_pid_audit", {})),
                "study_weights_clamped": study_weights_clamped,
                **state.last_telemetry,
            })
            final_exit = self.final_execution_controller.exit_decision(
                path=path_observation.to_dict() if closed_loop_snapshot is not None else None,
                exit_pid=state.last_telemetry, held_bars=held_bars,
                minimum_hold_bars=int(trade["minimum_hold_bars"]),
                maximum_hold_bars=int(trade["maximum_hold_bars"]), side=trade["side"],
            )
            self._record_controller_event("FINAL_EXECUTION_EXIT_DECISION", timestamp, symbol, {
                "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"), **final_exit,
            })
            if self.final_exit_authority and final_exit.get("action") in {"EXIT", "EXIT_NEXT_BAR"}:
                # The decision is based on a completed bar and becomes effective at the
                # next bar's open. Hard stop/target gaps retain precedence in the pending
                # exit handler; HOLD/PROTECT never loosen a one-way stop.  Processing of THIS
                # bar continues below (no early return): its intrabar stop/target, the session
                # close, the maximum hold, plant protection trips (which re-arm the pending exit
                # with their own reason) and the governor all keep their precedence.
                trade["controller_exit_pending"] = {
                    "armed_timestamp": str(timestamp),
                    "reason": final_exit.get("reason", "FINAL_EXECUTION_EXIT"),
                    "exit_reason": f"final_controller_exit:{final_exit.get('reason', 'unknown')}",
                    "event": "FINAL_EXECUTION_EXIT_ARMED",
                }
                self._record_controller_event("FINAL_EXECUTION_EXIT_ARMED", timestamp, symbol, {
                    "candidate_id": trade.get("candidate_id"),
                    "trade_id": trade.get("trade_id"),
                    **trade["controller_exit_pending"],
                })

        exit_price, reason = None, None
        if trade["side"] == "BUY":
            if bar["open"] <= current_stop:
                exit_price, reason = float(bar["open"]), "stop_gap"
            elif bar["open"] >= trade["target_price"]:
                exit_price, reason = float(bar["open"]), "target_gap"
            elif bar["low"] <= current_stop:
                exit_price, reason = current_stop, "stop"
            elif bar["high"] >= trade["target_price"]:
                exit_price, reason = trade["target_price"], "target"
        else:
            if bar["open"] >= current_stop:
                exit_price, reason = float(bar["open"]), "stop_gap"
            elif bar["open"] <= trade["target_price"]:
                exit_price, reason = float(bar["open"]), "target_gap"
            elif bar["high"] >= current_stop:
                exit_price, reason = current_stop, "stop"
            elif bar["low"] <= trade["target_price"]:
                exit_price, reason = trade["target_price"], "target"

        if exit_price is not None:
            self._execute_exit(symbol, timestamp, trade, exit_price, reason)
            return
        if session_last_bar:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "mis_session_close")
            return
        # maximum_hold_bars is now a REAL, independent hard ceiling -- it
        # used to be stored on every trade and never once read anywhere.
        if held_bars >= trade["maximum_hold_bars"]:
            self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "max_hold")
            return
        # Native plant position control: a protection trip, Engine-A square-off or HRSG
        # pressure collapse arms an exit at the next bar's open and outranks the governor.
        if self._plant_wired and self._plant_position_control(
                symbol, timestamp, trade, bar, held_bars, path_observation):
            if state is not None:
                self.exit_controller.observe_completed_bar_excursion(state, bar)
            return
        # Bay governor HOLD/EXIT from this completed bar.  In full authority it is the only
        # discretionary exit: an EXIT is executed at the next bar's open (above).
        governor_position = self._governor_position_step(
            symbol, timestamp, trade, bar, signal, held_bars, chart_studies_audit,
            chart_studies_confidence, path_observation)
        if self._governor_full and governor_position["action"] == "EXIT":
            if state is not None:
                self.exit_controller.observe_completed_bar_excursion(state, bar)
            return
        # The old code exited unconditionally the instant minimum_hold_bars
        # was satisfied, labeled "max_hold_or_signal" despite checking
        # neither -- see BOX6_CONTROL_LOOP_DIAGRAM_20260906.html. Replaced
        # with the controller's own real, live-signal-driven condition:
        # sustained saturation on EITHER independent track (PA or chart
        # studies) is enough on its own to trigger the exit -- OR logic,
        # not a merge -- only actionable once the minimum hold is met.
        # saturation_exit_reason() names which track actually fired
        # ("saturation_exit_pa" / "saturation_exit_studies"), so the trade
        # record's own reason field now tells the two apart instead of a
        # single opaque "saturation_exit" label.
        if state is not None and held_bars >= trade["minimum_hold_bars"]:
            saturation_reason = self.exit_controller.saturation_exit_reason(state)
            if saturation_reason is not None:
                if self._governor_full:
                    self._record_controller_event("ADVISORY_EXIT_NOT_ACTUATED", timestamp, symbol, {
                        "trade_id": trade.get("trade_id"), "advisor": "exit_pid", "reason": saturation_reason})
                else:
                    self._execute_exit(symbol, timestamp, trade, float(bar["close"]), saturation_reason)
                    return
        # Observation advances before eligibility checks in run(). Exit and
        # entry read the same completed-bar posterior without re-appending it.
        regime = self.id_box.latest_regime(symbol)
        if regime == "stressed" and held_bars >= trade["minimum_hold_bars"]:
            if self._governor_full:
                self._record_controller_event("ADVISORY_EXIT_NOT_ACTUATED", timestamp, symbol, {
                    "trade_id": trade.get("trade_id"), "advisor": "hmm_regime", "reason": "regime_stressed_exit"})
            else:
                self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "regime_stressed_exit")
                return
        if state is not None:
            self.exit_controller.observe_completed_bar_excursion(state, bar)
        if state is not None and state.shadow_exit_price is None:
            shadow_update = self.exit_controller.update_shadow_r_trajectory(state, float(bar["close"]))
            self._record_controller_event("SHADOW_R_TRAJECTORY_UPDATE", timestamp, symbol, {
                "candidate_id": trade.get("candidate_id"), "trade_id": trade.get("trade_id"), **shadow_update,
            })

    @staticmethod
    def prepare_market_data(symbol_bars):
        """Canonical frames must precede any clock construction."""
        return {symbol: certify_bars(frame)[0] for symbol, frame in symbol_bars.items()}

    @staticmethod
    def build_clock(symbol_bars: Dict[str, pd.DataFrame], warmup: int) -> List[_ClockEvent]:
        events: List[_ClockEvent] = []
        for symbol, bars in symbol_bars.items():
            ts = pd.to_datetime(bars["timestamp"])
            for bar_idx in range(warmup, len(bars) - 1):
                events.append(_ClockEvent(ts.iloc[bar_idx], symbol, bar_idx))
        events.sort(key=lambda e: (e.timestamp, e.symbol))
        return events

    @staticmethod
    def rank_simultaneous_entry_candidates(candidates):
        """Deterministic arbitration for candidates created at one timestamp.

        This does not create alpha or tune a weighted score. It uses existing
        authoritative entry evidence lexicographically:

        1. higher ID confidence,
        2. higher actual planned bracket reward:risk,
        3. symbol only as the deterministic final tie-break.

        Input order must never affect the returned ordering.
        """
        def key(candidate):
            entry = float(candidate["plan"].entry_price)
            stop = float(candidate["plan"].stop_price)
            target = float(candidate["plan"].target_price)

            risk = abs(entry - stop)
            reward = abs(target - entry)
            rr = reward / risk if risk > 0.0 else 0.0

            return (
                -float(candidate["decision"].confidence),
                -float(rr),
                str(candidate["symbol"]),
            )

        return sorted(candidates, key=key)

    def run(
        self, symbol_bars: Dict[str, pd.DataFrame], warmup: int = 60,
        precomputed_clock: Optional[List[_ClockEvent]] = None,
    ) -> Dict[str, Any]:
        symbol_bars = self._prepare_run(symbol_bars, warmup, precomputed_clock)
        # Calibration driving many candidates against the SAME symbol_bars
        # can build this once and pass it via precomputed_clock= -- mirrors
        # revision2/portfolio_orchestrator.py's identical optimization, so
        # the two engines' calibration supervisors can share one code path.
        clock = precomputed_clock if precomputed_clock is not None else self.build_clock(symbol_bars, warmup)
        for timestamp, tick_events in itertools.groupby(clock, key=lambda e: e.timestamp):
            self._process_tick(timestamp, list(tick_events))

        for symbol in list(self.open_trades.keys()):
            bars = symbol_bars[symbol]
            final_close = float(bars.iloc[len(bars) - 1]["close"])
            self._execute_exit(symbol, bars.iloc[len(bars) - 1].get("timestamp", ""), self.open_trades[symbol], final_close, "end_of_run_reconciliation")

        return self._finalize_run()

    def _prepare_run(
        self, symbol_bars: Dict[str, pd.DataFrame], warmup: int,
        precomputed_clock: Optional[List[_ClockEvent]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Certification, warm-up calibration and run-scoped state, before the first decision tick."""
        self._assert_paper_plant_broker()
        if self.paper_journal is not None:
            self.paper_journal.bind(self, symbol_bars, warmup)
        # Box 3: Pandera certification, once per symbol, before the loop --
        # bars don't change during a backtest, so re-validating them every
        # iteration (the per-bar design the in-house box uses) is pure
        # waste at this scale; certifying once up front is the more
        # faithful "certify before trading on it" reading of the box's job.
        certified: Dict[str, pd.DataFrame] = {}
        certification_audit: Dict[str, Dict[str, int]] = {}
        for symbol, bars in symbol_bars.items():
            frame, audit = certify_bars(
                bars,
                validation_mode=str(
                    self.config.require("data_validation_mode")
                ),
            )
            certified[symbol] = frame
            certification_audit[symbol] = audit
        symbol_bars = certified
        # Completed bars of the current exchange-local session before each row (FSRS input).
        for symbol, frame in symbol_bars.items():
            local = pd.to_datetime(frame["timestamp"])
            local = (local.dt.tz_localize("Asia/Kolkata") if local.dt.tz is None
                     else local.dt.tz_convert("Asia/Kolkata"))
            self._session_bar_index[symbol] = frame.groupby(local.dt.date.to_numpy()).cumcount().to_numpy()
        if precomputed_clock is not None:
            expected_clock = self.build_clock(symbol_bars, warmup)
            if precomputed_clock != expected_clock:
                raise ValueError("precomputed clock does not match certified frames")

        for symbol, bars in symbol_bars.items():
            self.pa.calibrate(symbol, bars.iloc[:warmup], self.config)
            self.id_box.calibrate(symbol, bars.iloc[:0], self.config)
            # Replay warmup through sensor histories and eligible planner PIDs,
            # with no order construction, portfolio allocation, or submission.
            for idx in range(min(warmup, len(bars))):
                self.id_box.observe_bar(symbol, float(bars.iloc[idx]["close"]),
                                        timestamp=str(bars.iloc[idx]["timestamp"]))
                snapshot = MarketSnapshot(symbol, str(bars.iloc[idx]["timestamp"]), bars.iloc[:idx + 1])
                warm_signal, _ = self.pa.evaluate(snapshot, self.config)
                self.chart_studies.configure(self.config)
                self._observe_conviction(symbol, warm_signal, self.chart_studies.evaluate(symbol, snapshot.bars))
                if warm_signal.direction == 0:
                    continue
                warm_decision, _ = self.id_box.evaluate(
                    warm_signal, self.config, latest_close=None)
                if warm_decision.approved:
                    close = float(bars.iloc[idx]["close"])
                    self.mpc.build_plan(warm_signal, warm_decision, close,
                                        max(float(warm_signal.volatility) * close, 1e-6), self.config)

        funnel = {
            "plant_entry_rejections": 0,
            "pa_evaluations": 0, "directional_signals": 0, "confidence_qualified": 0,
            "pa_green": 0, "pa_amber": 0, "pa_red": 0, "pa_neutral": 0,
            "bay_trip_rejections": 0, "bay_cooldown_rejections": 0,
            "bars_processed": 0, "pa_signals": 0, "id_approvals": 0, "id_rejections": 0,
            "mpc_plans": 0, "safety_approvals": 0, "safety_rejections": 0,
            "gates_evaluated": 0, "gates_passed": 0, "gates_rejected": 0,
            "orders_submitted": 0, "exit_orders_submitted": 0, "fills": 0,
            "portfolio_cap_rejections": 0,
        }
        max_concurrent = int(self.safety_contract.values["max_concurrent_positions"])
        max_gross_fraction = float(self.safety_contract.values["max_gross_exposure_fraction"])
        # Was self.registry.get(...).default -- silently ignored calibration
        # overrides (always read the registry's frozen default, never the
        # candidate's actual value) and, since it bypassed self.config,
        # never registered as consumed either, which is why this parameter
        # was incorrectly in test_orchestrator_end_to_end.py's
        # expected_missing set under a "replaced by PyPortfolioOpt weights"
        # rationale that isn't true -- it's read and it enforces a real
        # sector cap, just never the calibrated one. Found during external
        # review, verified directly against this line before fixing.
        sector_cap_fraction = float(self.config.require("max_sector_exposure_fraction"))
        self.consumed_parameters.add("max_sector_exposure_fraction")

        entry_bar_index: Dict[str, int] = {}
        ticks_since_reweight = 0

        # BB08 operational controls.  These are explicit runtime parameters,
        # but remain FIXED / NOT_CALIBRATED until the parameter surface is
        # validated independently.
        portfolio_refit_bars = int(self.config.require("portfolio_weight_refit_bars"))
        portfolio_lookback_bars = int(self.config.require("portfolio_weight_lookback_minute_bars"))
        portfolio_min_observations = int(self.config.require("portfolio_min_15min_observations"))
        portfolio_risk_free_rate = float(self.config.require("portfolio_optimizer_risk_free_rate"))
        self.consumed_parameters.update({
            "portfolio_weight_refit_bars",
            "portfolio_weight_lookback_minute_bars",
            "portfolio_min_15min_observations",
            "portfolio_optimizer_risk_free_rate",
        })

        self._run = SimpleNamespace(
            symbol_bars=symbol_bars, certification_audit=certification_audit, funnel=funnel,
            max_concurrent=max_concurrent, max_gross_fraction=max_gross_fraction,
            sector_cap_fraction=sector_cap_fraction, entry_bar_index=entry_bar_index,
            ticks_since_reweight=ticks_since_reweight, portfolio_refit_bars=portfolio_refit_bars,
            portfolio_lookback_bars=portfolio_lookback_bars,
            portfolio_min_observations=portfolio_min_observations,
            portfolio_risk_free_rate=portfolio_risk_free_rate,
        )
        return symbol_bars

    def _process_tick(self, timestamp, tick_events: List[_ClockEvent]) -> None:
        """One clock timestamp: plant/governor steps, exits, then the S4 entry candidate batch."""
        run = self._run
        symbol_bars, funnel, certification_audit = run.symbol_bars, run.funnel, run.certification_audit
        max_gross_fraction, entry_bar_index = run.max_gross_fraction, run.entry_bar_index
        ticks_since_reweight = run.ticks_since_reweight
        portfolio_refit_bars, portfolio_lookback_bars = run.portfolio_refit_bars, run.portfolio_lookback_bars
        portfolio_min_observations = run.portfolio_min_observations
        portfolio_risk_free_rate = run.portfolio_risk_free_rate
        event_ts = pd.Timestamp(timestamp)
        if self._active_trading_date != event_ts.date():
            self._active_trading_date = event_ts.date()
            self._day_start_equity = self._equity()
            # Session-scoped machine bay lockout reset
            self.symbol_cooldown_until_bar.clear()
            self.symbol_consecutive_losses.clear()
            self.symbol_tripped.clear()

        for event in tick_events:
            self._last_close[event.symbol] = float(symbol_bars[event.symbol].iloc[event.bar_idx]["close"])
        self._record_mtm(timestamp)
        if self.paper_journal is not None:
            self.paper_journal.before_tick(self, timestamp)
        self._plant_control_shadow_step(timestamp, max_gross_fraction)
        self._governor_tick_telemetry(tick_events, symbol_bars)
        self._plant_bar_step(timestamp, tick_events, symbol_bars)

        # Box 8: refit PyPortfolioOpt weights periodically from real
        # trailing prices across the universe -- not every tick (that
        # would dominate runtime for no real benefit at 1-minute
        # granularity).
        ticks_since_reweight += 1
        if ticks_since_reweight >= portfolio_refit_bars:
            ticks_since_reweight = 0
            price_history = {}
            for symbol in self.symbols:
                bars = symbol_bars[symbol]
                idx = min(event_ts, bars["timestamp"].max())
                window = bars[bars["timestamp"] <= idx].tail(portfolio_lookback_bars)
                if len(window) >= portfolio_lookback_bars:
                    price_history[symbol] = window.set_index("timestamp")["close"]
            if len(price_history) >= 2:
                self._portfolio_weights = compute_portfolio_weights(
                    price_history,
                    min_observations=portfolio_min_observations,
                    risk_free_rate=portfolio_risk_free_rate,
                )

        pending_entry_candidates = []

        for event in tick_events:
            funnel["bars_processed"] += 1
            symbol, bar_idx = event.symbol, event.bar_idx
            self._current_bar_idx = bar_idx
            bars = symbol_bars[symbol]
            self.id_box.configure(self.config)
            self.id_box.observe_bar(symbol, float(bars.iloc[bar_idx]["close"]),
                                    timestamp=str(timestamp))
            next_ts = self._next_bar_timestamp(bars, bar_idx)
            end_time = str(self.config.require("trading_hours_end"))
            session_last_bar = next_ts.date() != event_ts.date() or event_ts.strftime("%H:%M") >= end_time

            upstream = self.supervisory_bridge.evaluate_upstream_admission(
                symbol=symbol,
                runtime_parameters=self.config.as_dict(),
                safety_parameters=self.safety_contract.as_dict(),
            )
            self.consumed_parameters.update(
                upstream.consumed_parameters
            )
            if not upstream.admitted:
                continue
            in_window = self._in_trading_window(str(timestamp))

            snapshot = MarketSnapshot(
                symbol=symbol, timestamp=str(timestamp),
                bars=bars.iloc[max(0, bar_idx - SNAPSHOT_LOOKBACK_BARS + 1):bar_idx + 1],
            )
            signal, trace = self.pa.evaluate(snapshot, self.config)
            self._record(trace)
            self.bb03_bb04_supervisory_by_symbol[symbol] = (
                self.supervisory_bridge.snapshot_bb03_bb04(
                    certification_audit=(
                        certification_audit[symbol]
                    ),
                    analytics_signal=signal,
                )
            )
            funnel["pa_signals"] += 1  # deprecated compatibility alias for pa_evaluations
            funnel["pa_evaluations"] += 1
            funnel["directional_signals"] += int(signal.direction != 0)
            funnel["confidence_qualified"] += int(signal.confidence >= float(self.config.require("entry_confidence_threshold")))
            funnel["pa_" + signal.quality_band] += 1

            # Box 4b, the Chart-Studies Confirmation Layer -- a SECOND,
            # fully independent confidence reading (Ichimoku/Bollinger/
            # Stochastic/session VWAP, PID-weighted -- see
            # composite_study_signal.py), deliberately kept SEPARATE
            # from PA's own confidence rather than blended into it.
            # An earlier version of this wiring blended the two
            # ("agree -> average, disagree -> floor to the lower",
            # chart_studies_confirmation.py) directly into `signal`
            # before ID/MPC ever saw it -- a real INFY 6-month backtest
            # showed this let a weak PA signal get boosted past ID's
            # threshold purely by an unrelated composite agreeing with
            # it (id_approvals 455->2,949, win rate 9.3%->5.9%, net
            # P&L -37,907.57->-81,964.77), which is structurally
            # unsound: two independent opinions averaged together can
            # manufacture confidence neither one earned alone. Per
            # explicit user direction, this signal instead goes
            # straight into the exit controller (Box 6's feedback
            # path, below) as its own separate PID track -- never
            # merged with PA's confidence, never touching entry
            # (ID/MPC) at all. See continuous_exit_controller.py's
            # module docstring for the two-track design.
            self.chart_studies.configure(self.config)
            self.closed_loop.configure(self.config)
            composite_result = self.chart_studies.evaluate(symbol, snapshot.bars)
            chart_studies_confidence = float(composite_result["confidence"])
            self._observe_conviction(symbol, signal, composite_result)

            held = bar_idx - entry_bar_index.get(symbol, bar_idx)
            self._maybe_exit(
                symbol, timestamp, bars.iloc[bar_idx], signal, held, session_last_bar,
                chart_studies_confidence, composite_result,
            )

            if symbol in self.open_trades or not in_window or self._execution_halted or self._entries_suspended(symbol):
                continue
            if next_ts.date() != event_ts.date() or next_ts.strftime("%H:%M") >= str(self.safety_contract.values["no_entry_cutoff_time"]):
                continue

            decision, trace = self.id_box.evaluate(signal, self.config, latest_close=None)
            self._record(trace)
            self.bb05_bb06_supervisory_by_symbol[symbol] = (
                self.supervisory_bridge.snapshot_bb05_bb06(symbol=symbol, decision=decision)
            )
            # HMM posterior -> portfolio-risk input is deliberately
            # shadow-only.  The posterior is causal (filtering, not a
            # future-smoothed state estimate) and the adapter can only
            # recommend a derate.  It cannot change this candidate's
            # order, size, safety state, or existing HMM ID veto until
            # its own sealed out-of-sample study earns that authority.
            regime_observation = self.id_box.latest_regime_observation(symbol)
            hysteresis = self._hmm_risk_hysteresis.setdefault(symbol, HMMRiskHysteresis())
            hysteresis.configure(self.config)  # controls refresh; latch/filter state preserved
            self._record_controller_event("HMM_REGIME_RISK_SHADOW", timestamp, symbol, {
                "candidate_id": f"candidate-preview-{self._controller_sequence + 1}",
                "id_approved": decision.approved, "id_reason": decision.reason,
                "hmm_observation": regime_observation,
                **regime_risk_derate(regime_observation),
                "hysteresis": hysteresis.update(regime_observation),
            })
            if not decision.approved:
                funnel["id_rejections"] += 1
                continue
            funnel["id_approvals"] += 1

            if self.grid_context_provider is not None:
                observation = self.grid_context_provider.observe(
                    symbol,
                    bars.iloc[:bar_idx + 1],
                    timestamp,
                    signal.direction,
                )
                self.grid_shadow_observations.append(observation.to_dict())

            _close_price = float(bars.iloc[bar_idx]["close"])
            env_state = MarketEnvironmentState.from_bars(symbol, bars, bar_idx, regime=decision.regime if hasattr(decision, "regime") else "trend", config=self.config)
            t1_params = DynamicParameterController.get_tier1_vol_parameters(env_state, config=self.config)
            self.consumed_parameters.update({
                'mpc_environment_lookback', 'mpc_range_atr_period',
                'mpc_range_fallback_fraction', 'mpc_atr_floor_gain', 'mpc_slippage_vol_gain',
            })
            _min_atr_floor = t1_params["min_atr_floor"]
            atr = max(float(signal.volatility * _close_price), _min_atr_floor)
            next_open = self._entry_reference_price(bars, bar_idx)
            self.mpc.gain_scale = self._gain_scale_for(symbol)
            plan, pid_info, trace = self.mpc.build_plan(signal, decision, next_open, atr, self.config)
            self._record(trace)
            self.bb05_bb06_supervisory_by_symbol[symbol] = (
                self.supervisory_bridge.snapshot_bb05_bb06(symbol=symbol, decision=decision, plan=plan)
            )
            if plan is None:
                continue
            # Inter-box Closed Loop Lockout Checks
            if self.symbol_tripped.get(symbol, False):
                self._controller_sequence += 1
                candidate_id = f"candidate-{self._controller_sequence}"
                self.entry_candidate_observations.observe({
                    "candidate_id": candidate_id, "symbol": symbol, "side": plan.side,
                    "timestamp": str(timestamp), "planned_fill_timestamp": str(next_ts),
                    "pa_confidence": float(signal.confidence), "id_confidence": float(decision.confidence),
                })
                self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "bay_ansi_flameout_tripped")
                funnel["bay_trip_rejections"] += 1
                continue

            if bar_idx < self.symbol_cooldown_until_bar.get(symbol, -1):
                self._controller_sequence += 1
                candidate_id = f"candidate-{self._controller_sequence}"
                self.entry_candidate_observations.observe({
                    "candidate_id": candidate_id, "symbol": symbol, "side": plan.side,
                    "timestamp": str(timestamp), "planned_fill_timestamp": str(next_ts),
                    "pa_confidence": float(signal.confidence), "id_confidence": float(decision.confidence),
                })
                self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "bay_cooldown_active")
                funnel["bay_cooldown_rejections"] += 1
                continue

            funnel["mpc_plans"] += 1
            self._controller_sequence += 1
            candidate_id = f"candidate-{self._controller_sequence}"
            self.entry_candidate_observations.observe({
                "candidate_id": candidate_id, "symbol": symbol, "side": plan.side,
                "timestamp": str(timestamp), "planned_fill_timestamp": str(next_ts),
                "pa_confidence": float(signal.confidence), "id_confidence": float(decision.confidence),
                "studies_confidence": float(chart_studies_confidence),
                "planned_entry_price": float(plan.entry_price), "planned_stop_price": float(plan.stop_price),
                "planned_target_price": float(plan.target_price), "planned_maximum_hold_bars": int(plan.maximum_hold_bars),
            })
            entry_quality = self.closed_loop.outcomes.profile(symbol, plan.side)
            self._record_controller_event("ENTRY_QUALITY_COMPARATOR", timestamp, symbol, {
                "candidate_id": candidate_id, "id_confidence": float(decision.confidence),
                **entry_quality,
            })
            self._record_controller_event("ENTRY_CONFIDENCE_THROTTLE", timestamp, symbol, {
                "candidate_id": candidate_id,
                # Passive input/geometry telemetry. These values were
                # fixed by PA, ID and MPC above; recording them cannot
                # alter admission, sizing, stops, targets or execution.
                "pa_confidence": float(signal.confidence),
                "id_confidence": float(decision.confidence),
                "id_timing_quality": float(decision.timing_quality),
                "id_risk_reward_ratio": float(decision.risk_reward_ratio),
                "entry_atr": float(atr),
                "planned_entry_price": float(plan.entry_price),
                "planned_stop_price": float(plan.stop_price),
                "planned_target_price": float(plan.target_price),
                "planned_stop_distance": abs(float(plan.entry_price) - float(plan.stop_price)),
                "planned_target_distance": abs(float(plan.target_price) - float(plan.entry_price)),
                "planned_minimum_hold_bars": int(plan.minimum_hold_bars),
                "planned_maximum_hold_bars": int(plan.maximum_hold_bars),
                **pid_info,
            })
            if self.dynamic_target_setpoint_provider is not None:
                proposal = self.dynamic_target_setpoint_provider.propose(
                    entry_price=float(plan.entry_price), stop_price=float(plan.stop_price),
                    target_price=float(plan.target_price), maximum_hold_bars=int(plan.maximum_hold_bars),
                )
                self._record_controller_event("DYNAMIC_TARGET_SETPOINT_SHADOW", timestamp, symbol, {
                    "candidate_id": candidate_id, **proposal,
                })
                if self.dynamic_target_setpoint_mode == "paper_apply" and proposal["available"]:
                    risk = abs(float(plan.entry_price) - float(plan.stop_price))
                    signed_target = risk * float(proposal["proposed_target_r"])
                    target_price = (
                        float(plan.entry_price) + signed_target if plan.side == "BUY"
                        else float(plan.entry_price) - signed_target
                    )
                    plan = replace(
                        plan, target_price=target_price,
                        maximum_hold_bars=int(proposal["proposed_maximum_hold_bars"]),
                    )
                    self._record_controller_event("DYNAMIC_TARGET_SETPOINT_PAPER_APPLIED", timestamp, symbol, {
                        "candidate_id": candidate_id, "target_price": target_price,
                        "maximum_hold_bars": plan.maximum_hold_bars, **proposal,
                    })

            # S4: candidate construction is complete.  No portfolio
            # capacity, sizing, gate submission, or broker mutation is
            # allowed until every symbol at this timestamp has reached
            # this same boundary.
            pending_entry_candidates.append({
                "symbol": symbol,
                "bar_idx": bar_idx,
                "bars": bars,
                "next_ts": next_ts,
                "signal": signal,
                "decision": decision,
                "plan": plan,
                "pid_info": pid_info,
                "candidate_id": candidate_id,
                "entry_quality": entry_quality,
                "composite_result": composite_result,
                "chart_studies_confidence": chart_studies_confidence,
                "atr": atr,
            })
            continue

        # S4_PHASE_B_SIMULTANEOUS_ARBITRATION
        # Every eligible candidate for this exact timestamp now exists.
        # Rank the immutable candidate batch first; only then allow any
        # candidate to consume portfolio capacity or create a fill.
        run.ticks_since_reweight = ticks_since_reweight
        for pending in self.rank_simultaneous_entry_candidates(
            pending_entry_candidates
        ):
            self._admit_entry_candidate(pending, timestamp)

        if self.paper_journal is not None:
            self.paper_journal.checkpoint(self, timestamp)

    def _admit_entry_candidate(self, pending: Dict[str, Any], timestamp) -> None:
        """Phase B for one ranked candidate: safety, governor, sizing, plant and gates, then submit."""
        run = self._run
        funnel, max_concurrent = run.funnel, run.max_concurrent
        max_gross_fraction, sector_cap_fraction = run.max_gross_fraction, run.sector_cap_fraction
        symbol = pending["symbol"]
        bar_idx = pending["bar_idx"]
        bars = pending["bars"]
        next_ts = pending["next_ts"]
        signal = pending["signal"]
        decision = pending["decision"]
        plan = pending["plan"]
        pid_info = pending["pid_info"]
        candidate_id = pending["candidate_id"]
        entry_quality = pending["entry_quality"]
        composite_result = pending["composite_result"]
        chart_studies_confidence = pending["chart_studies_confidence"]
        atr = pending["atr"]

        # Some lifecycle/audit code uses this authoritative bar index.
        self._current_bar_idx = bar_idx

        approved, _, size_mult, trace = self.safety_gates_target.evaluate_pre_sizing(
            self._equity_curve, self.config, current_lambda=self._system_state().current_lambda,
        )
        self._record(trace)
        if not approved:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "pre_sizing_safety")
            funnel["safety_rejections"] += 1
            return
        governor_entry = self._governor_entry(
            symbol, timestamp, bar_idx, plan.side, signal, decision, composite_result,
            chart_studies_confidence)
        self._governor_entry_counts[f"{governor_entry['action']}:{governor_entry['reason']}"] += 1
        self._record_controller_event("GOVERNOR_ENTRY_DECISION", timestamp, symbol, {
            "candidate_id": candidate_id, "authority": self.governor_authority,
            **{key: governor_entry.get(key) for key in (
                "bay_id", "action", "reason", "size_multiplier", "conviction", "grid_return_fraction",
                "fsr_selected", "controlling_limiter", "limiters", "entry_hurdle", "vibration",
                "velocity", "z_score", "bay_exhaust_spread", "comparator")},
        })
        if self._governor_full and governor_entry["action"] != "ENTRY":
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "governor_no_action")
            return
        symbol_dynamics = self.closed_loop.dynamics_profiler.estimate(bars.iloc[:bar_idx + 1])
        final_entry = self.final_execution_controller.entry_decision(
            side=plan.side, pa_confidence=float(signal.confidence), id_approved=bool(decision.approved),
            studies_direction=int(composite_result["direction"]), studies_confidence=chart_studies_confidence,
            entry_price=float(plan.entry_price), stop_price=float(plan.stop_price), target_price=float(plan.target_price),
            maximum_hold_bars=int(plan.maximum_hold_bars), entry_quality=entry_quality,
            dynamics=symbol_dynamics,
        )
        self._record_controller_event("FINAL_EXECUTION_ENTRY_DECISION", timestamp, symbol, {
            "candidate_id": candidate_id, **final_entry,
        })
        # The unified decision is an actual paper-only entry veto in
        # active mode. Shadow mode records the identical decision
        # but leaves the existing baseline execution untouched.
        if self.closed_loop_mode == "active_paper" and not self._governor_full and final_entry["action"] != "ADMIT":
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "final_execution_entry_veto")
            self._record_controller_event("FINAL_EXECUTION_ENTRY_VETO", timestamp, symbol, {
                "candidate_id": candidate_id, **final_entry,
            })
            return
        size_mult *= pid_info["entry_timing_multiplier"]
        if self._governor_full:
            # The governor's selected FSR (<= 1) can only derate the approved size.
            size_mult *= float(governor_entry["size_multiplier"])
        if (
            self.closed_loop_mode == "active_paper" and not self._governor_full
            and entry_quality["symbol_regime_samples"] >= self.closed_loop.outcomes.minimum_history
            and float(decision.confidence) < float(self.config.require("entry_confidence_threshold"))
            + float(entry_quality["suggested_confidence_offset"])
        ):
            self.entry_candidate_observations.dispose(candidate_id, "DEFERRED", "entry_quality_hold")
            self._record_controller_event("ENTRY_QUALITY_HOLD", timestamp, symbol, {
                "candidate_id": candidate_id,
                "effective_confidence_floor": float(self.config.require("entry_confidence_threshold"))
                + float(entry_quality["suggested_confidence_offset"]),
                "id_confidence": float(decision.confidence),
            })
            return

        if len(self.open_trades) >= max_concurrent:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "max_concurrent_positions")
            funnel["portfolio_cap_rejections"] += 1
            return
        equity_now = self._equity()
        portfolio_observation = self.closed_loop.observe_portfolio_risk(
            self._gross_exposure_notional(), equity_now, max_gross_fraction,
            soft_budget_fraction=self.closed_loop.soft_budget_fraction,
        )
        self._record_controller_event("PORTFOLIO_RISK_COMPARATOR", timestamp, symbol, {
            "candidate_id": candidate_id, **portfolio_observation,
        })
        if self.closed_loop_mode == "active_paper":
            size_mult *= float(entry_quality["suggested_entry_derate"])
            size_mult *= float(portfolio_observation["suggested_new_risk_derate"])
            self._record_controller_event("DYNAMIC_SIZE_ACTUATION", timestamp, symbol, {
                "candidate_id": candidate_id,
                "entry_quality_derate": entry_quality["suggested_entry_derate"],
                "portfolio_risk_derate": portfolio_observation["suggested_new_risk_derate"],
                "resulting_size_multiplier": size_mult,
            })
        sector = self.sector_map.get(symbol, "Unclassified")

        quantity, trace = self.position_manager.size(
            plan, equity_now, size_mult, self.config, symbol=symbol,
            portfolio_weights=self._portfolio_weights,
            max_exposure_per_symbol_fraction=float(self.safety_contract.values["max_exposure_per_symbol_fraction"]),
            open_positions_count=len(self.open_trades),
            symbol_positions_count=1 if symbol in self.open_trades else 0,
        )
        self._record(trace)
        self._record_controller_event("POSITION_SIZING", timestamp, symbol, {
            "candidate_id": candidate_id,
            **self.position_manager.last_sizing_telemetry,
        })
        if quantity <= 0:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "position_sizing_zero")
            return

        if self.plant_control.mode is PlantControlMode.PAPER_APPLY:
            # Use the greater of planned price and the actual costed replay fill price.
            cap_price = max(float(plan.entry_price), paper_fill_price(
                float(pid_info["execution_market_price"]), plan.side, self.broker.slippage_fraction))
            quantity = self._paper_plant_entry_limit(symbol, quantity, cap_price, timestamp)
            if quantity <= 0:
                self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "plant_control_paper_cap")
                funnel["portfolio_cap_rejections"] += 1
                return
        plant_quantity = None
        if self._plant_wired:
            # Native five-bay plant: final entry authority and bay-headroom x FSR sizing.
            plant_quantity = self._plant_entry_gate(
                symbol, timestamp, plan.side, quantity, cap_price, bars.iloc[bar_idx],
                candidate_id, governor_entry)
            quantity = min(quantity, plant_quantity)
            if quantity <= 0:
                self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "plant_dcs_entry_rejected")
                funnel["plant_entry_rejections"] += 1
                return

        real_notional = plan.entry_price * quantity
        if self._gross_exposure_notional() + real_notional > equity_now * max_gross_fraction:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "gross_exposure_cap")
            funnel["portfolio_cap_rejections"] += 1
            return
        sector_notional = sum(
            t["quantity"] * self._last_close.get(s, t["entry_price"]) for s, t in self.open_trades.items()
            if self.sector_map.get(s, "Unclassified") == sector
        )
        if sector_notional + real_notional > equity_now * sector_cap_fraction:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "sector_exposure_cap")
            funnel["portfolio_cap_rejections"] += 1
            return

        post_ok, _, trace = self.safety_gates_target.evaluate_post_sizing(
            self._equity_curve, plan, quantity, self.config,
            daily_loss=self._system_state().daily_realized_loss + self._system_state().daily_unrealized_loss,
        )
        self._record(trace)
        if not post_ok:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "post_sizing_safety")
            funnel["safety_rejections"] += 1
            return
        funnel["safety_approvals"] += 1

        order, trace = self.p01d.create_order(symbol, plan, quantity, self.config)
        self._record(trace)
        if order is None:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "order_construction")
            return

        state = self._system_state()
        entry_signal = EntrySignal(
            symbol=symbol, entry_price=plan.entry_price, stop_loss_price=plan.stop_price,
            profit_target_price=plan.target_price, confidence=decision.confidence,
            suggested_quantity=quantity, position_notional=real_notional,
            risk_reward_ratio=abs(plan.target_price - plan.entry_price) / max(abs(plan.entry_price - plan.stop_price), 1e-12),
        )
        # Replay event time only.  A wall-clock fallback would make every time-of-day
        # and de-duplication gate depend on when the replay happens to run.
        current_time = self._replay_event_time(next_ts)
        gate_result = self.entry_decision_engine.evaluate_pre_submit(
            state, signal=entry_signal, current_time=current_time, proposed_quantity=quantity,
            target_price=plan.entry_price, fill_price=plan.entry_price, expected_qty=quantity,
            actual_qty=quantity, symbol=symbol,
            seen_recent=self._intent_ledger.seen_recent(symbol, order.side, current_time),
            proposed_notional=real_notional,
        )
        funnel["gates_evaluated"] += 1
        if not gate_result["passed"]:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "entry_decision_gate",
                details={**gate_result, "decisions": [asdict(d) for d in gate_result["decisions"]]})
            funnel["gates_rejected"] += 1
            return
        funnel["gates_passed"] += 1
        quantity = min(quantity, max(0, int(gate_result["adjusted_quantity"])))
        if quantity <= 0:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "entry_decision_quantity_zero")
            return

        gate2 = ExecutionGate().validate_pre_submit(
            self.safety_contract.as_dict(),
            {"symbol": symbol, "side": order.side, "quantity": quantity, "order_type": order.order_type},
            parameter_registry=self.registry,
        )
        if not gate2["passed"]:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "execution_gate")
            funnel["safety_rejections"] += 1
            return

        if self.plant_control.mode is PlantControlMode.PAPER_APPLY:
            # Recheck protection immediately before submission; safety gates may only reduce.
            quantity = self._paper_plant_entry_limit(symbol, quantity, cap_price, timestamp)
        if plant_quantity is not None:
            quantity = min(quantity, plant_quantity)

        # Derating/clamping can only round DOWN to a valid symbol lot.
        lot_size = max(1, int(self.config.require("lot_size_by_symbol").get(symbol, 1)))
        quantity = (quantity // lot_size) * lot_size
        if quantity <= 0:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "lot_rounding_zero")
            return
        self._intent_ledger.record(symbol, order.side, current_time)
        self._submit_entry_order({
            "symbol": symbol, "order": order, "quantity": quantity, "timestamp": timestamp,
            "next_ts": next_ts, "bar_idx": bar_idx, "pid_info": pid_info, "candidate_id": candidate_id,
            "plan": plan, "signal": signal, "decision": decision, "atr": atr,
            "composite_result": composite_result, "chart_studies_confidence": chart_studies_confidence,
            "symbol_dynamics": symbol_dynamics, "governor_entry": governor_entry,
        })

    def _submit_entry_order(self, intent: Dict[str, Any]) -> None:
        """Engine A execution port: synchronous offline paper broker at the next-bar replay price."""
        fill = self.broker.place_order(
            symbol=intent["symbol"], side=intent["order"].side, quantity=intent["quantity"],
            order_type=intent["order"].order_type,
            market_price=float(intent["pid_info"]["execution_market_price"]),
            config=self.safety_contract.as_dict(), parameter_registry=self.registry,
        )
        self._run.funnel["orders_submitted"] += 1
        self._on_entry_order_result(intent, fill)

    def _on_entry_order_result(self, intent: Dict[str, Any], fill: Dict[str, Any]) -> None:
        """Authoritative entry fill (or rejection) bookkeeping for one submitted entry order."""
        funnel, entry_bar_index = self._run.funnel, self._run.entry_bar_index
        symbol, order, quantity, timestamp = intent["symbol"], intent["order"], intent["quantity"], intent["timestamp"]
        next_ts, bar_idx, pid_info = intent["next_ts"], intent["bar_idx"], intent["pid_info"]
        candidate_id, plan, signal, decision = intent["candidate_id"], intent["plan"], intent["signal"], intent["decision"]
        atr, composite_result = intent["atr"], intent["composite_result"]
        chart_studies_confidence = intent["chart_studies_confidence"]
        symbol_dynamics, governor_entry = intent["symbol_dynamics"], intent["governor_entry"]
        # BB09/BB10 supervisory hand-off: a read-only record of what
        # P01D constructed and what UnifiedExecution did with it.
        # The order already exists, so this observer has NO veto, NO
        # rollback and NO authority: only the bridge's own data-validation
        # error is contained (recorded); any other exception is a real
        # defect and still propagates.  Bookkeeping below always runs.
        try:
            self.bb09_bb10_supervisory_by_symbol[symbol] = (
                self.supervisory_bridge.snapshot_bb09_bb10(
                    proposed_order=replace(order, quantity=quantity),
                    fill_result=fill,
                )
            )
        except SupervisorySnapshotError as exc:
            self.bb09_bb10_observer_failures.append({
                "symbol": symbol, "timestamp": str(timestamp),
                "side": order.side, "quantity": int(quantity),
                "fill_passed": bool(fill.get("passed")),
                "error_type": type(exc).__name__, "error": str(exc),
            })
        if fill["passed"]:
            actual_quantity = int(fill["filled_quantity"])
            post_fill = self.entry_decision_engine.evaluate_post_fill(
                target_price=float(pid_info["execution_market_price"]), fill_price=float(fill["filled_price"]),
                expected_qty=quantity, actual_qty=actual_quantity,
                elapsed_seconds=float(fill["ack_elapsed_seconds"]),
                expected_position=actual_quantity * (1 if order.side == "BUY" else -1),
                actual_position=self._broker_position(symbol)["quantity"],
            )
            self._post_fill_checks.append({
                "candidate_id": candidate_id, **post_fill,
                "decisions": [asdict(d) for d in post_fill["decisions"]],
            })
            if not post_fill["passed"]:
                # A fill already happened: retain it in the ledger and halt NEW entries.
                # Existing protective exits remain enabled.
                self._execution_halted = True
            if actual_quantity <= 0:
                raise RuntimeError("Paper fill has no reconcilable positive quantity")
            quantity = actual_quantity
            self.entry_candidate_observations.dispose(candidate_id, "FILLED", "paper_fill")
            funnel["fills"] += 1
            self._trade_sequence += 1
            risk = abs(float(plan.entry_price) - float(plan.stop_price))
            target_r = abs(float(plan.target_price) - float(plan.entry_price)) / risk if risk > 0.0 else 0.0
            entry_evidence = self.entry_expectancy_ledger.observe_fill({
                "candidate_id": candidate_id,
                "symbol": symbol,
                "side": plan.side,
                "timestamp": str(timestamp),
                "fill_timestamp": str(next_ts),
                "pa_confidence": float(signal.confidence),
                "id_confidence": float(decision.confidence),
                "studies_confidence": chart_studies_confidence,
                "studies_direction": int(composite_result["direction"]),
                "atr_fraction": float(atr) / max(abs(float(plan.entry_price)), 1e-12),
                "target_r": target_r,
                "maximum_hold_bars": int(plan.maximum_hold_bars),
                "session_minute": int(next_ts.hour * 60 + next_ts.minute),
            })
            self._record_controller_event("ENTRY_EXPECTANCY_CANDIDATE", timestamp, symbol, entry_evidence)
            # Causal plant/dynamics estimate: this slice ends at
            # the decision bar. It cannot see the fill bar or any
            # subsequent held-position price action.
            closed_loop_snapshot = self.closed_loop.entry_snapshot(
                symbol=symbol, side=plan.side, entry_price=float(fill["filled_price"]),
                stop_price=float(plan.stop_price), target_price=float(plan.target_price),
                max_hold_bars=int(plan.maximum_hold_bars), regime="unknown", dynamics=symbol_dynamics,
            )
            self._record_controller_event("CLOSED_LOOP_ENTRY_SNAPSHOT", next_ts, symbol, {
                "candidate_id": candidate_id, "trade_id": f"trade-{self._trade_sequence}",
                **closed_loop_snapshot,
            })
            self.open_trades[symbol] = {
                "side": plan.side, "entry_price": fill["filled_price"], "stop_price": plan.stop_price,
                "target_price": plan.target_price, "quantity": quantity,
                "minimum_hold_bars": plan.minimum_hold_bars, "maximum_hold_bars": plan.maximum_hold_bars,
                "exit_confidence_threshold": decision.timing_quality, "entry_timestamp": str(next_ts),
                "entry_atr": float(atr), "planned_entry_price": float(plan.entry_price),
                "planned_stop_price": float(plan.stop_price), "planned_target_price": float(plan.target_price),
                "candidate_id": candidate_id, "trade_id": f"trade-{self._trade_sequence}",
                "controller_exit_pending": None,
                "closed_loop": closed_loop_snapshot,
            }
            self.open_trades[symbol].update({
                "governor_stop_price": float(plan.stop_price), "governor_mfe_r": 0.0,
                "governor_entry_conviction": governor_entry.get("entry_absolute_conviction"),
            })
            _, governor = self._governor_for(symbol)
            if governor is not None:
                governor.begin_position(hard_stop_r=-1.0, position_id=f"trade-{self._trade_sequence}")
            entry_bar_index[symbol] = bar_idx + 1
            self._exit_controller_states[symbol] = self.exit_controller.open_position(
                plan.side, fill["filled_price"], plan.stop_price, plan.target_price, plan.maximum_hold_bars,
            )
            if self._plant_wired:
                self._plant_register_fill(symbol, next_ts, self.open_trades[symbol])
        else:
            self.entry_candidate_observations.dispose(candidate_id, "REJECTED", "paper_fill_rejected")

    def _finalize_run(self) -> Dict[str, Any]:
        run = self._run
        symbol_bars, funnel, certification_audit = run.symbol_bars, run.funnel, run.certification_audit
        if self.paper_journal is not None:
            self.paper_journal.checkpoint(self, "FINAL")
        self.entry_candidate_observations.finalize_pending()
        gross_pnl = self._broker_realized_pnl()
        assert abs(gross_pnl - sum(t["pnl"] for t in self.completed_trades)) < 1e-6

        target_names = set(self.registry.applicable_names("EXTERNAL"))
        safety_names = set(self.registry.safety_params)
        coverage_target = sorted(target_names & self.consumed_parameters)

        funnel["exit_orders_submitted"] = self._exit_orders_submitted
        return {
            "risk_profile": self.risk_profile,
            "operating_mode": "paper",
            "post_fill_checks": self._post_fill_checks,
            "execution_halted": self._execution_halted,
            "safety_violations": sum(not row["passed"] for row in self._post_fill_checks),
            **funnel, "symbols": self.symbols, "completed_trades": len(self.completed_trades),
            "gross_pnl": gross_pnl, "net_pnl": sum(t["net_pnl"] for t in self.completed_trades),
            "ending_equity": self.starting_equity + sum(t["net_pnl"] for t in self.completed_trades),
            "config_hash": self.config.config_hash, "safety_contract_hash": self.safety_contract.contract_hash,
            "governor_authority": {
                "mode": self.governor_authority,
                "entry_decisions": dict(self._governor_entry_counts),
                "position_decisions": dict(self._governor_position_counts),
            },
            "micom": {"trip_bar_counts": dict(self._micom_trip_counts), "vol_z_window": self.micom_vol_z_window},
            "plant_dcs_wiring": {
                "wired": self._plant_wired, "engine_mode": self.plant_engine_mode,
                "call_counts": dict(self.plant_call_counts),
                "entry_rejection_reasons": dict(self._plant_entry_rejections),
                "trip_exits_armed": dict(self._plant_trip_counts),
                "bound_positions_open": (len(self.real_plant_dcs.bound_positions)
                                         if self._plant_wired else 0),
                "bar_trace": self.plant_bar_trace if self.telemetry_mode == "full" else [],
            },
            "plant_control": {
                "mode": self.plant_control.mode.value, "applied": self._paper_admission_evaluations > 0,
                "plant_protection_connected": self._plant_protection_connected,
                "bay_availability_source": self._plant_protection_source,
                "evaluations": self.plant_control_evaluations,
                "paper_admission_evaluations": self._paper_admission_evaluations,
                "paper_admission_rejections": self._paper_admission_rejections,
                "paper_admission_caps": self._paper_admission_caps,
                "grid_state_counts": dict(self.plant_control_state_counts),
                "transitions": len(self.plant_control_snapshots),
                "observer_failures": len(self.plant_control_observer_failures),
            },
            "plant_control_shadow": {
                "mode": self.plant_control.mode.value, "applied": self._paper_admission_evaluations > 0,
                "plant_protection_connected": self._plant_protection_connected,
                "bay_availability_source": self._plant_protection_source,
                "evaluations": self.plant_control_evaluations,
                "grid_state_counts": dict(self.plant_control_state_counts),
                "transitions": len(self.plant_control_snapshots),
                "observer_failures": len(self.plant_control_observer_failures),
            },
            "closed_loop_mode": self.closed_loop_mode,
            "pid_mode": self.pid_mode,
            **({"controller_authority": {
                "startup": self.controller_parameter_startup_report(),
                "applied_value_summary": self._authority_summary,
            }} if self.authority_telemetry else {}),
            "certification_audit": certification_audit,
            "session_completeness": {
                s: certify_session_completeness(frame, self.session_schedule)
                for s, frame in symbol_bars.items()
            },
            "final_portfolio_weights": self._portfolio_weights,
            "parameter_coverage": {
                "target_total": len(target_names), "target_consumed": len(coverage_target),
                "target_missing": sorted(target_names - self.consumed_parameters),
                "safety_total": len(safety_names),
            },
            "trades": self.completed_trades,
            "mtm_equity_curve": self._mtm_equity_curve if self.telemetry_mode == "full" else [],
            "mtm_max_drawdown_fraction": self._mtm_max_drawdown_fraction,
            "controller_telemetry": self.controller_telemetry,
            "controller_telemetry_summary": {
                "events": sum(self._controller_event_counts.values()),
                "entry_throttle_updates": self._controller_event_counts["ENTRY_CONFIDENCE_THROTTLE"],
                "exit_protection_updates": self._controller_event_counts["EXIT_PROTECTION_UPDATE"],
                "shadow_r_trajectory_updates": self._controller_event_counts["SHADOW_R_TRAJECTORY_UPDATE"],
                "shadow_r_trajectory_exits": self._controller_event_counts["SHADOW_R_TRAJECTORY_EXIT"],
                "outcomes": self._controller_event_counts["CONTROLLER_OUTCOME"],
                "closed_loop_entry_snapshots": self._controller_event_counts["CLOSED_LOOP_ENTRY_SNAPSHOT"],
                "trade_path_comparisons": self._controller_event_counts["TRADE_PATH_COMPARATOR"],
                "portfolio_risk_comparisons": self._controller_event_counts["PORTFOLIO_RISK_COMPARATOR"],
                "outcome_ledger_updates": self._controller_event_counts["OUTCOME_LEDGER_UPDATE"],
                "entry_quality_comparisons": self._controller_event_counts["ENTRY_QUALITY_COMPARATOR"],
                "dynamic_size_actuations": self._controller_event_counts["DYNAMIC_SIZE_ACTUATION"],
                "trade_path_stop_actuations": self._controller_event_counts["TRADE_PATH_STOP_ACTUATION"],
                "hmm_regime_risk_shadow_observations": self._controller_event_counts["HMM_REGIME_RISK_SHADOW"],
            },
            "position_sizing_events": self._position_sizing_events,
            "entry_expectancy_evidence": {
                **self.entry_expectancy_ledger.summary(),
                # Resolved entry/outcome rows are trade-level evidence, not
                # per-bar telemetry.  Retain them in compact mode so a
                # research replay can be small without losing the causal
                # pairs needed for a later, frozen entry-quality study.
                "resolved": self.entry_expectancy_ledger.resolved,
            },
            "entry_candidate_observations": self.entry_candidate_observations.report(),
            "grid_shadow": {
                "enabled": self.grid_context_provider is not None,
                "observations": self.grid_shadow_observations,
                "available": sum(1 for row in self.grid_shadow_observations if row["available"]),
                "synchronized": sum(1 for row in self.grid_shadow_observations if row["synchronized"] is True),
            },
        }
    def _in_trading_window(self, timestamp: str) -> bool:
        start = str(self.config.require("trading_hours_start"))
        end = str(self.config.require("trading_hours_end"))
        # These two really are consumed and really do gate control flow
        # below -- tracking them here (not just calling config.require()
        # directly) matches every box's own req()-wrapper pattern, so the
        # parameter_coverage report doesn't undercount a parameter that
        # genuinely does something. Missing this made trading_hours_start/
        # end show up as "unconsumed" despite being load-bearing.
        self.consumed_parameters.add("trading_hours_start")
        self.consumed_parameters.add("trading_hours_end")
        # Fail closed: a timestamp that cannot be placed on the exchange clock is never inside
        # the trading window.  The fault is recorded, never silently treated as tradeable.
        try:
            time_part = self._exchange_local_time(timestamp).strftime("%H:%M")
        except (TypeError, ValueError) as exc:
            self._record_controller_event("TRADING_WINDOW_FAULT", timestamp, "PLANT", {
                "error_type": type(exc).__name__, "error": str(exc), "in_window": False})
            return False
        return start <= time_part <= end

    @staticmethod
    def _exchange_local_time(timestamp: object) -> pd.Timestamp:
        """Exchange-local (Asia/Kolkata) time.  A tz-aware value keeps its instant; a tz-naive
        replay value is read as exchange-local, the same rule as PlantGridSynchronizer."""
        ts = pd.Timestamp(timestamp)
        if pd.isna(ts):
            raise ValueError(f"timestamp is not a valid instant: {timestamp!r}")
        return ts.tz_localize("Asia/Kolkata") if ts.tzinfo is None else ts.tz_convert("Asia/Kolkata")

    @staticmethod
    def _replay_event_time(timestamp: object) -> datetime:
        """Deterministic replay event time for gate evaluation.  Never the wall clock: an
        unparseable replay timestamp is a data defect and stops the run."""
        ts = pd.Timestamp(timestamp)
        if pd.isna(ts):
            raise ValueError(f"replay event timestamp is not a valid instant: {timestamp!r}")
        return datetime.fromisoformat(ts.isoformat())
