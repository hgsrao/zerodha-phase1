"""
Revision 5 plant-control chain (SHADOW-first)
=============================================

    Nifty / VIX grid (causal, as-of)
            |
    PlantGridSynchronizer        -> immutable PlantGridState
            |
    ECSPlantSupervisor           -> ECSPlantOutput (bounded plant demand 0..1)
            |
    SectorDispatchController     -> five bay dispatch references
            |
    GovernorDispatchReference    -> typed, information-only hand-off
            |
    GTG1 / GTG2 / CSTG1 / CSTG2 / BPSTG governors  (final local ENTRY/HOLD/EXIT authority)

Authority boundaries (enforced by tests):

* The synchronizer reads market data only.  It selects no stock and places no order.
* ECSPlantSupervisor is strictly ONE-WAY: it may HOLD, reduce plant demand, and restore
  demand only within configured bounds and at a bounded rate.  It never widens risk, never
  overrides protection, chooses no stock and places no order.
* SectorDispatchController allocates the ECS demand across the five bays.  The merit
  weights come from the existing ``DynamicBayLoadDispatcher`` (reused, not replaced).
* The governors keep final local authority.  In SHADOW mode nothing here reaches them.
* BB07 (safety), BB08 (sizing), BB09 (order construction) and BB10 (execution) are downstream
  and untouched.
* ``ClosedLoopSupervisor`` is a supporting feedback subsystem.  It is NOT the ECS supervisor;
  its portfolio derate may only be passed in as an additional one-way input.

This module has no broker, order or execution imports.  ``PlantControlMode`` has no live
value, and ``PAPER_APPLY`` is defined only as the future consumption boundary: constructing
a chain in that mode is refused until it is explicitly approved.

Every operational value is registry-owned (``PlantControl`` black box, ENGINEERING_INITIAL_VALUE,
NOT_CALIBRATED).  The [0, 1] demand range and the sum/ceiling invariants are structural.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Any, Dict, Iterable, Mapping, Optional, Protocol, Tuple

import numpy as np
import pandas as pd

from revision2_external.bb05_bb06_parameters import require
from revision2_external.grid_context import SealedGridContextProvider
from revision3.macro_grid_synchronizer import MacroGridSynchronizer
from revision5.topology import BAY_IDS


class PlantControlError(ValueError):
    """Invalid plant-control input/configuration (never used to hide a data fault)."""


class PlantControlMode(str, Enum):
    SHADOW = "SHADOW"
    PAPER_APPLY = "PAPER_APPLY"   # future consumption boundary; refused until approved


class PlantGridStateName(str, Enum):
    SYNCHRONIZED = "SYNCHRONIZED"
    DERATED = "DERATED"
    UNSYNCHRONIZED = "UNSYNCHRONIZED"
    ISLANDED_SAFE = "ISLANDED_SAFE"


class ECSOperatingMode(str, Enum):
    NORMAL = "NORMAL"
    DERATED = "DERATED"
    HOLD = "HOLD"


_EPS = 1e-12


# --------------------------------------------------------------------------- grid

@dataclass(frozen=True)
class PlantGridState:
    """One plant-wide grid state (not 48 per-stock states)."""

    state: str
    available: bool
    reason: str
    decision_timestamp: str
    source_timestamp: Optional[str] = None
    source_age_seconds: Optional[float] = None
    nifty_close: Optional[float] = None
    vix_close: Optional[float] = None
    vix_slope: Optional[float] = None
    nifty_deviation: Optional[float] = None
    authority: str = "INFORMATION_ONLY"


class PlantGridSynchronizer:
    """Plant-level market-grid synchronizer.  COMPOSES the existing grid implementations:

    * causal / as-of data contract (strictly-earlier selection, alignment, staleness, warm-up):
      ``SealedGridContextProvider.causal_context`` -- the single owner of that contract;
    * frequency (VIX operating band): ``MacroGridSynchronizer.check_frequency`` -- the single owner
      of the band test, constructed here with the registry-owned band;
    * NOT used: per-stock phase alignment and direction-aligned trend (they need a stock and a
      trade direction); the per-stock synchronizer is untouched and remains a local diagnostic.

    What this class adds is only plant-level *aggregation semantics*: one state from Nifty/VIX with
    the derate thresholds (VIX level / rate-of-rise / index deviation from its EMA), which exist nowhere
    else.  The only time-zone rule it owns is the naive-replay-timestamp convention (below).
    """

    _NAMES = (
        "grid_vix_operating_min", "grid_vix_operating_max", "grid_vix_derate_start",
        "grid_vix_slope_bars", "grid_vix_slope_derate_fraction", "grid_nifty_ema_period",
        "grid_nifty_deviation_derate_fraction", "grid_max_staleness_seconds", "grid_min_aligned_bars",
    )

    def __init__(self, config, provider: SealedGridContextProvider) -> None:
        self.provider = provider
        self._cache: Dict[Any, Any] = {}
        self.configure(config)

    def configure(self, config) -> None:
        values = {name: require(config, name) for name in self._NAMES}
        if not (values["grid_vix_operating_min"] < values["grid_vix_derate_start"]
                < values["grid_vix_operating_max"]):
            raise PlantControlError("VIX bounds require operating_min < derate_start < operating_max")
        self.frequency = MacroGridSynchronizer(
            vix_operating_band=(float(values["grid_vix_operating_min"]), float(values["grid_vix_operating_max"])))
        self.vix_derate_start = float(values["grid_vix_derate_start"])
        self.vix_slope_bars = int(values["grid_vix_slope_bars"])
        self.vix_slope_derate = float(values["grid_vix_slope_derate_fraction"])
        self.ema_period = int(values["grid_nifty_ema_period"])
        self.deviation_derate = float(values["grid_nifty_deviation_derate_fraction"])
        self.max_staleness_seconds = float(values["grid_max_staleness_seconds"])
        self.min_aligned_bars = max(int(values["grid_min_aligned_bars"]), self.ema_period + 1,
                                    self.vix_slope_bars + 1)
        self._cache = {}

    @staticmethod
    def _islanded(decision: pd.Timestamp, reason: str, **extra) -> PlantGridState:
        return PlantGridState(PlantGridStateName.ISLANDED_SAFE.value, False, reason, decision.isoformat(), **extra)

    @staticmethod
    def decision_utc(decision_timestamp: object) -> pd.Timestamp:
        """RULE: a tz-aware decision time keeps its instant (converted to UTC); a tz-naive replay
        time is read as exchange-local Asia/Kolkata (the engine convention, as in UnifiedExecutionBox).
        Asia/Kolkata has no DST, so the localization is never ambiguous or non-existent."""
        decision = pd.Timestamp(decision_timestamp)
        if decision.tzinfo is None:
            decision = decision.tz_localize("Asia/Kolkata")
        return decision.tz_convert("UTC")

    def evaluate(self, decision_timestamp: object) -> PlantGridState:
        """Grid state as known strictly BEFORE ``decision_timestamp``; fails closed (ISLANDED_SAFE)."""
        decision = self.decision_utc(decision_timestamp)
        grid = self.provider.causal_context(
            decision, max_staleness_seconds=self.max_staleness_seconds, minimum_aligned_bars=self.min_aligned_bars)
        extra = {}
        if grid.source_timestamp is not None:
            extra = dict(source_timestamp=grid.source_timestamp.isoformat(), source_age_seconds=grid.age_seconds)
        if not grid.available:
            return self._islanded(decision, grid.reason, **extra)

        cached = self._cache.get(grid.source_timestamp)
        if cached is None:
            cached = self._measure(grid.aligned)
            self._cache = {grid.source_timestamp: cached}
        state, reason, measurements = cached
        return PlantGridState(state.value, True, reason, decision.isoformat(), **extra, **measurements)

    def _measure(self, aligned: pd.DataFrame):
        nifty_close = float(aligned["close_nifty"].iloc[-1])
        ema = float(aligned["close_nifty"].ewm(span=self.ema_period, adjust=False).mean().iloc[-1])
        deviation = nifty_close / ema - 1.0
        vix_close = float(aligned["close_vix"].iloc[-1])
        vix_before = float(aligned["close_vix"].iloc[-1 - self.vix_slope_bars])
        vix_slope = vix_close / vix_before - 1.0
        measurements = dict(nifty_close=nifty_close, vix_close=vix_close, vix_slope=vix_slope,
                            nifty_deviation=deviation)
        if not self.frequency.check_frequency(vix_close):
            state, reason = PlantGridStateName.UNSYNCHRONIZED, "FREQUENCY_TRIP_VIX_OUTSIDE_OPERATING_BAND"
        elif (vix_close >= self.vix_derate_start or vix_slope >= self.vix_slope_derate
              or abs(deviation) >= self.deviation_derate):
            state, reason = PlantGridStateName.DERATED, "GRID_STRESS_DERATE"
        else:
            state, reason = PlantGridStateName.SYNCHRONIZED, "GRID_SYNCHRONIZED"
        return (state, reason, measurements)


# ---------------------------------------------------------------------------- ECS

@dataclass(frozen=True)
class BayStatus:
    """Availability/protection input for one bay (supplied by the caller)."""

    available: bool = True
    tripped: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ECSPlantOutput:
    operating_mode: str
    plant_demand_reference_pu: float           # bounded 0.0 .. 1.0
    plant_derate: float                        # 1 - demand reference (fraction withheld)
    bay_availability_mask: Tuple[Tuple[str, bool], ...]
    reasons: Tuple[str, ...]
    grid_state: str
    # False in SHADOW replay: plant-level protection (CentralPlantMasterDCS / MiCOM / SEL) is NOT
    # connected, bay availability comes from symbol trips only.  This output therefore makes no
    # claim of full physical-plant protection integration.
    plant_protection_connected: bool = False
    bay_availability_source: str = "SYMBOL_TRIPS_ONLY"
    authority: str = "PLANT_DEMAND_ONE_WAY"


class ECSPlantSupervisor:
    """Dedicated ECS plant supervisory controller (distinct from ClosedLoopSupervisor).

    One-way authority: hold, reduce, or restore within bounds.  It has no reference to any
    broker/order API, chooses no stock, and its maximum demand is the structural 1.0.
    """

    def __init__(self, config) -> None:
        # Startup state is HOLD (demand 0): the plant always ramps up from zero, never jumps to
        # full demand from an unknown state.
        self._previous_demand: float = 0.0
        self.configure(config)

    def configure(self, config) -> None:
        derate = require(config, "ecs_derate_demand_pu")
        step = require(config, "ecs_demand_restore_step_pu")
        if not 0.0 < derate < 1.0 or not 0.0 < step <= 1.0:
            raise PlantControlError("ECS derate demand must be in (0, 1) and restore step in (0, 1]")
        self.derate_demand_pu = float(derate)
        self.restore_step_pu = float(step)

    def evaluate(
        self,
        grid: PlantGridState,
        bay_status: Mapping[str, BayStatus],
        *,
        gross_exposure_fraction: float,
        gross_exposure_limit_fraction: float,
        supporting_derate: float = 1.0,
        plant_protection_tripped: Optional[bool] = None,
    ) -> ECSPlantOutput:
        """``plant_protection_tripped``: True/False when a real plant protection state is connected;
        ``None`` (default) means NOT connected -- recorded in the output, never assumed healthy."""
        for name, value in (("gross_exposure_fraction", gross_exposure_fraction),
                            ("gross_exposure_limit_fraction", gross_exposure_limit_fraction),
                            ("supporting_derate", supporting_derate)):
            if not isfinite(value):
                raise PlantControlError(f"{name} must be finite")
        if gross_exposure_limit_fraction <= 0.0 or gross_exposure_fraction < 0.0:
            raise PlantControlError("exposure limit must be positive and exposure non-negative")
        if set(bay_status) != set(BAY_IDS):
            raise PlantControlError("bay_status must cover exactly the five R5 bays")

        mask = tuple((bay, bool(bay_status[bay].available and not bay_status[bay].tripped)) for bay in BAY_IDS)
        reasons = [f"GRID_{grid.state}:{grid.reason}"]

        if grid.state == PlantGridStateName.SYNCHRONIZED.value:
            target = 1.0
        elif grid.state == PlantGridStateName.DERATED.value:
            target = self.derate_demand_pu
        else:                                   # UNSYNCHRONIZED / ISLANDED_SAFE: fail closed
            target = 0.0
        protection_connected = plant_protection_tripped is not None
        if not protection_connected:
            reasons.append("PLANT_PROTECTION_NOT_CONNECTED")
        if plant_protection_tripped:
            target = 0.0
            reasons.append("PLANT_PROTECTION_TRIPPED")
        if not any(ok for _, ok in mask):
            target = 0.0
            reasons.append("NO_BAY_AVAILABLE")
        # one-way reductions only: a supporting derate above 1 can never raise demand
        target = min(target, max(0.0, min(1.0, float(supporting_derate))))
        headroom = max(0.0, 1.0 - gross_exposure_fraction / gross_exposure_limit_fraction)
        if headroom < target:
            reasons.append("EXPOSURE_HEADROOM_LIMIT")
        target = min(target, headroom)

        previous = self._previous_demand
        if target > previous:
            demand = min(target, previous + self.restore_step_pu)      # bounded restoration
            if demand < target:
                reasons.append("RESTORE_RATE_LIMITED")
        else:
            demand = target                                             # reductions are immediate
        demand = max(0.0, min(1.0, demand))
        self._previous_demand = demand

        if demand <= _EPS:
            mode = ECSOperatingMode.HOLD
        elif demand < 1.0 - _EPS:
            mode = ECSOperatingMode.DERATED
        else:
            mode = ECSOperatingMode.NORMAL
        return ECSPlantOutput(mode.value, demand, 1.0 - demand, mask, tuple(reasons), grid.state,
                              plant_protection_connected=protection_connected)


# ------------------------------------------------------------------------ dispatch

@dataclass(frozen=True)
class DispatchResult:
    references_pu: Tuple[Tuple[str, float], ...]      # one entry per bay, BAY_IDS order
    plant_demand_reference_pu: float
    allocated_pu: float
    unallocated_pu: float
    feasible: bool
    reasons: Tuple[str, ...]
    authority: str = "INFORMATION_ONLY"

    def as_dict(self) -> Dict[str, float]:
        return dict(self.references_pu)


class SectorDispatchController:
    """Allocate ECS plant demand over the five bays.

    demand x merit weights (existing DynamicBayLoadDispatcher) x availability mask, bounded by
    a per-bay ceiling, by deterministic water-filling.  Unavailable bays receive exactly 0.
    If the available ceilings cannot absorb the demand the shortfall is REPORTED
    (``feasible=False``, ``unallocated_pu``); a ceiling is never silently exceeded.
    """

    def __init__(self, merit_source: Any) -> None:
        # merit_source: a DynamicBayLoadDispatcher (or anything exposing .weights and .max_ceiling)
        self.merit_source = merit_source

    def dispatch(self, ecs: ECSPlantOutput) -> DispatchResult:
        weights = {bay: float(self.merit_source.weights[bay]) for bay in BAY_IDS}
        ceiling = float(self.merit_source.max_ceiling)
        if not isfinite(ceiling) or not 0.0 < ceiling <= 1.0:
            raise PlantControlError("bay ceiling must be in (0, 1]")
        if any((not isfinite(w)) or w < 0.0 for w in weights.values()):
            raise PlantControlError("merit weights must be finite and non-negative")
        available = {bay: ok for bay, ok in ecs.bay_availability_mask}
        demand = float(ecs.plant_demand_reference_pu)
        if not isfinite(demand) or not 0.0 <= demand <= 1.0:
            raise PlantControlError("plant demand reference must be within [0, 1]")

        refs = {bay: 0.0 for bay in BAY_IDS}
        active = [bay for bay in BAY_IDS if available[bay]]
        remaining = demand
        reasons = []
        while remaining > _EPS and active:
            total_weight = sum(weights[bay] for bay in active)
            if total_weight <= _EPS:
                shares = {bay: remaining / len(active) for bay in active}   # no merit signal: equal split
            else:
                shares = {bay: remaining * weights[bay] / total_weight for bay in active}
            over = [bay for bay in active if refs[bay] + shares[bay] > ceiling + _EPS]
            if not over:
                for bay in active:
                    refs[bay] += shares[bay]
                remaining = 0.0
                break
            for bay in over:                      # cap, remove, redistribute the excess next pass
                remaining -= ceiling - refs[bay]
                refs[bay] = ceiling
            active = [bay for bay in active if bay not in over]
        unallocated = max(0.0, remaining) if remaining > _EPS else 0.0
        feasible = unallocated <= _EPS
        if not feasible:
            reasons.append("INFEASIBLE_CAPACITY_DEMAND_EXCEEDS_AVAILABLE_BAY_CEILINGS")
        for bay in BAY_IDS:
            if not available[bay]:
                reasons.append(f"{bay}:UNAVAILABLE_ZERO_DISPATCH")
        allocated = sum(refs.values())
        return DispatchResult(tuple((bay, refs[bay]) for bay in BAY_IDS), demand, allocated,
                              unallocated, feasible, tuple(reasons))


# ------------------------------------------------------------ governor-facing hand-off

@dataclass(frozen=True)
class GovernorDispatchReference:
    """Typed, governor-facing reference.  INFORMATION_ONLY in SHADOW; never an order or entry."""

    bay_id: str
    dispatch_reference_pu: float
    plant_demand_reference_pu: float
    mode: str
    applied: bool = False
    authority: str = "INFORMATION_ONLY"


class DispatchReferenceConsumer(Protocol):
    """Future consumption boundary (PAPER_APPLY).  A governor would receive the reference and
    remain the final local ENTRY/HOLD/EXIT authority; no consumer is wired in SHADOW."""

    def accept_dispatch_reference(self, reference: GovernorDispatchReference) -> None: ...


def build_governor_references(
    dispatch: DispatchResult, mode: PlantControlMode = PlantControlMode.SHADOW
) -> Tuple[GovernorDispatchReference, ...]:
    return tuple(
        GovernorDispatchReference(bay, ref, dispatch.plant_demand_reference_pu, mode.value, applied=False)
        for bay, ref in dispatch.references_pu
    )


# ------------------------------------------------------------------------- chain

@dataclass(frozen=True)
class PlantControlSnapshot:
    mode: str
    grid: PlantGridState
    ecs: ECSPlantOutput
    dispatch: DispatchResult
    governor_references: Tuple[GovernorDispatchReference, ...]
    applied: bool = False
    authority: str = "INFORMATION_ONLY"


class PlantControlChain:
    """Grid synchronizer -> ECS supervisor -> sector dispatch -> governor references.

    Default and only implemented mode is SHADOW: everything is calculated and recorded and
    nothing is applied.  PAPER_APPLY is refused until separately approved; there is no live mode.
    """

    def __init__(self, config, grid_provider: SealedGridContextProvider, merit_source,
                 mode: PlantControlMode | str = PlantControlMode.SHADOW) -> None:
        try:
            self.mode = PlantControlMode(mode)
        except ValueError as exc:
            raise PlantControlError(f"unknown plant control mode {mode!r}") from exc
        if self.mode is not PlantControlMode.SHADOW:
            raise PlantControlError("only SHADOW plant control is enabled; PAPER_APPLY requires explicit approval")
        self.synchronizer = PlantGridSynchronizer(config, grid_provider)
        self.ecs = ECSPlantSupervisor(config)
        self.dispatch_controller = SectorDispatchController(merit_source)

    def configure(self, config) -> None:
        self.synchronizer.configure(config)
        self.ecs.configure(config)

    def evaluate(
        self, decision_timestamp: object, bay_status: Mapping[str, BayStatus], *,
        gross_exposure_fraction: float, gross_exposure_limit_fraction: float,
        supporting_derate: float = 1.0, plant_protection_tripped: Optional[bool] = None,
    ) -> PlantControlSnapshot:
        grid = self.synchronizer.evaluate(decision_timestamp)
        ecs = self.ecs.evaluate(
            grid, bay_status, gross_exposure_fraction=gross_exposure_fraction,
            gross_exposure_limit_fraction=gross_exposure_limit_fraction,
            supporting_derate=supporting_derate, plant_protection_tripped=plant_protection_tripped)
        dispatch = self.dispatch_controller.dispatch(ecs)
        return PlantControlSnapshot(self.mode.value, grid, ecs, dispatch,
                                    build_governor_references(dispatch, self.mode))
