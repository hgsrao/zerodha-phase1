"""Immutable read-only bridge from the real Revision-5 plant protection state to ECS.

    real R5 plant (CentralPlantMasterDCS)
            |
    build_plant_protection_snapshot()    -> immutable PlantProtectionSnapshot
            |
    derive_bay_status_and_master_block() -> (bay_status, plant_protection_tripped, source)
            |
    ECSPlantSupervisor.evaluate()        -> bay availability mask / plant demand

This module reports EXISTING plant state only.  It contains none of the protection logic
that decided that state (ANSI-86 lockout, SEL-300G, MiCOM, mechanical protection, startup
sequencing): source ownership of every one of those decisions stays in ``ccpp_unified_plant``,
``ccpp_protection_cubicles``, ``machine_dynamics`` and ``startup_synchronization``.  Nothing
here can BLOCK, TRIP, ISOLATE or clear a trip -- it can only read and translate.

Authority rule: protection may block; ECS may only consume the result.  A snapshot never
creates a trade and is never itself an admission decision.

Clock rule: cooldown/startup readiness is read using the real plant's OWN ``current_bar_index``
(defaulting to 0 before the plant's first bar).  No external replay timestamp or bar index is
ever accepted here, so a replay engine can never advance, expire or clear native R5 protection
or cooldown state through this module.

Fallback rule: when no real plant is supplied (the normal external-replay condition -- that
engine does not instantiate a ``CentralPlantMasterDCS``), this module returns an explicit,
clearly-labelled fallback snapshot (``connected=False``, ``source=FALLBACK_SOURCE``).  It never
fabricates or fakes ``CentralPlantMasterDCS`` state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Mapping, Optional, Tuple

from revision5.plant_control import BayStatus, PlantControlError
from revision5.topology import BAY_IDS

if TYPE_CHECKING:  # pragma: no cover - type checking only, no runtime import cycle
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS


CONNECTED_SOURCE = "CENTRAL_PLANT_MASTER_DCS"
FALLBACK_SOURCE = "SYMBOL_TRIPS_ONLY"


@dataclass(frozen=True)
class BayProtectionStatus:
    """Read-only per-bay protection state, copied from the real plant."""

    bay_id: str
    available: bool
    reason: str
    tripped_offline: bool
    lockout_86: bool
    breaker_closed: bool
    cooldown_bars_remaining: int
    # None means "unknown": no startup sequencer is installed on this bay, so neither
    # dispatch-readiness nor a mechanical-protection trip through the sequencer can be read.
    # Unknown never counts as a block; it is reported, never assumed healthy or tripped.
    dispatch_ready: Optional[bool]
    mechanical_tripped: Optional[bool]


@dataclass(frozen=True)
class PlantProtectionSnapshot:
    """Immutable snapshot of real R5 plant protection/availability state (or its explicit
    fallback).  Reports existing state only; contains no protection logic of its own."""

    connected: bool
    source: str
    master_block: bool
    master_block_reason: str
    bays: Tuple[BayProtectionStatus, ...]
    observed_bar_index: Optional[int]

    def bay(self, bay_id: str) -> BayProtectionStatus:
        for status in self.bays:
            if status.bay_id == bay_id:
                return status
        raise KeyError(f"no protection status recorded for bay {bay_id!r}")


def _bay_protection_status(plant: "CentralPlantMasterDCS", bay_id: str, bar_index: int) -> BayProtectionStatus:
    bay = plant.bays[bay_id]
    breaker = plant.electrical_network.unit_breaker(bay_id)
    tripped_offline = bool(bay.tripped_offline)
    lockout = bool(breaker.lockout_86)
    breaker_closed = bool(plant.electrical_network.unit_available(bay_id))
    cooldown_remaining = int(bay.cooldown_remaining(bar_index))

    sequencer = getattr(bay, "startup_sequencer", None)
    if sequencer is None:
        dispatch_ready: Optional[bool] = None
        mechanical_tripped: Optional[bool] = None
    else:
        dispatch_ready = sequencer.state.value == "DISPATCH_READY"
        mechanical_tripped = sequencer.state.value == "TRIPPED"

    if lockout:
        reason = "ANSI_86_LOCKOUT"
    elif tripped_offline:
        reason = "UNIT_TRIPPED_OFFLINE"
    elif not breaker_closed:
        reason = "GEN_BREAKER_OPEN"
    elif cooldown_remaining > 0:
        reason = "BAY_COOLDOWN"
    elif mechanical_tripped is True:
        reason = "MECHANICAL_PROTECTION_TRIP"
    elif dispatch_ready is False:
        reason = "UNIT_NOT_DISPATCH_READY"
    else:
        reason = ""

    return BayProtectionStatus(
        bay_id=bay_id, available=(reason == ""), reason=reason, tripped_offline=tripped_offline,
        lockout_86=lockout, breaker_closed=breaker_closed, cooldown_bars_remaining=cooldown_remaining,
        dispatch_ready=dispatch_ready, mechanical_tripped=mechanical_tripped,
    )


def build_plant_protection_snapshot(plant: Optional["CentralPlantMasterDCS"]) -> PlantProtectionSnapshot:
    """Read the real plant's existing protection state, or return the explicit fallback.

    ``plant is None`` -- the normal external-replay condition -- returns
    ``connected=False, source=FALLBACK_SOURCE``; nothing is faked.  When a real plant is
    supplied, only its already-decided state is read (no protection evaluation is re-run here).
    """
    if plant is None:
        return PlantProtectionSnapshot(
            connected=False, source=FALLBACK_SOURCE, master_block=False,
            master_block_reason="PLANT_PROTECTION_NOT_CONNECTED", bays=(), observed_bar_index=None,
        )

    bar_index = plant.current_bar_index if plant.current_bar_index is not None else 0
    grid_connected = bool(plant.electrical_network.grid_connected)
    master_open = bool(plant.grid_relay.master_breaker_open)
    master_block = master_open or not grid_connected

    if master_open:
        master_block_reason = "MICOM_MASTER_BREAKER_OPEN_ANSI67_FLEET_DRAWDOWN"
    elif not grid_connected:
        breaker_snapshot = plant.electrical_network.snapshot()["breakers"][plant.electrical_network.GRID_BREAKER]
        master_block_reason = str(breaker_snapshot.get("trip_reason") or "GRID_INTERTIE_OPEN")
    else:
        master_block_reason = ""

    bays = tuple(_bay_protection_status(plant, bay_id, bar_index) for bay_id in BAY_IDS)
    return PlantProtectionSnapshot(
        connected=True, source=CONNECTED_SOURCE, master_block=master_block,
        master_block_reason=master_block_reason, bays=bays, observed_bar_index=bar_index,
    )


def derive_bay_status_and_master_block(
    snapshot: PlantProtectionSnapshot, fallback_bay_status: Mapping[str, BayStatus],
) -> Tuple[Dict[str, BayStatus], Optional[bool], str]:
    """Translate a snapshot into the ``(bay_status, plant_protection_tripped, source)`` inputs
    ``ECSPlantSupervisor.evaluate`` already accepts.

    ``snapshot.connected`` is False -> the caller's own fallback bay status is used unchanged and
    ``plant_protection_tripped`` is ``None`` (not connected, never assumed healthy).  A healthy
    real plant can therefore never override an existing fallback signal, because the fallback
    path never reads real-plant state in the first place; and connected real-plant state is never
    mixed with -- or clearable by -- the fallback signal, because a connected snapshot ignores the
    fallback entirely.
    """
    if not snapshot.connected:
        if set(fallback_bay_status) != set(BAY_IDS):
            raise PlantControlError("fallback bay_status must cover exactly the five R5 bays")
        return dict(fallback_bay_status), None, snapshot.source

    bay_status = {
        status.bay_id: BayStatus(available=status.available, tripped=not status.available, reason=status.reason)
        for status in snapshot.bays
    }
    if set(bay_status) != set(BAY_IDS):
        raise PlantControlError("protection snapshot must cover exactly the five R5 bays")
    return bay_status, snapshot.master_block, snapshot.source
