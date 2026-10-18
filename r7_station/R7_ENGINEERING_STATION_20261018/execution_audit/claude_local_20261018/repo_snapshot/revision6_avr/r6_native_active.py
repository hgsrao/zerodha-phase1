"""R6 active-paper Unit Master authority inside the REAL Engine B / Engine C paths.

Opt-in only: the classes below subclass the unmodified native orchestrators; no existing
source file is edited, Engine A and default R5/R6 runs never import this module.

Where R6 acts (one seam each, all delegating to the native implementation first):

* ``_plant_entry_gate``  -- after the native plant (MiCOM, ANSI 86, cooldown, SEL-300G,
  bay comparator, legacy AVR lot + UEL, HRSG headroom, x FSR) has produced its approved
  quantity, the Unit Master (governor vote + ExposureAVR bay vote + safety permit) decides
  ENTRY / BLOCK.  R6 can only keep or LOWER the native quantity, never raise it.
* ``_register``          -- binds a same-bar R6 bay reservation to the real native order id.
* ``_admit_entry_candidate`` -- releases a reservation whose candidate never produced an
  order (a later native gate rejected it), so no capacity is held for a non-existent order.

Held positions: R6 does NOT intervene.  Mark V / plant / protection exits stay native and
unilateral; R6 REDUCE is DISABLED (the native engines have no partial-reduce order path).

Commercial minimum: still enforced ONCE, by the legacy BayExcitationAVR UEL inside the native
plant (retained veto, see report); the Unit Master is therefore called with minimum 0.
"""
from __future__ import annotations

from collections import Counter
import os
import sys
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from r6_avr_core import Action, CausalID, SafetyPermit          # noqa: E402  (supplied, unchanged)
from r6_active_local_gateway import ActiveBayGate, R6GateError  # noqa: E402
from r6_broker_exposure_probe import LedgerProbeError, snapshot  # noqa: E402
from r6_turbine_governor import TurbineGovernorUnit, repository_defaults  # noqa: E402

from revision5.topology import SYMBOL_TO_BAY                     # noqa: E402

# Order events that prove the venue has acknowledged or terminated an order.
_NOT_YET_ACKNOWLEDGED = {"SUBMIT", "SUBMITTED"}


class R6ActivePaperMixin:
    """Mixin placed BEFORE EngineBOrchestrator / EngineCOrchestrator in the MRO."""

    r6_variant: str = ""

    def __init__(self, *args, r6_max_utilization: float, r6_target_utilization: float, **kwargs):
        super().__init__(*args, **kwargs)
        if getattr(self, "full_adaptation", False):
            raise ValueError("R6 active paper requires global adaptation OFF")
        if self.governor_authority != "full":
            raise ValueError("R6 active paper requires governor_authority='full' (native FSR authority)")
        self.r6_max_utilization = float(r6_max_utilization)
        self.r6_target_utilization = float(r6_target_utilization)
        bays = sorted(set(SYMBOL_TO_BAY.values()))
        self._r6_gates = {b: ActiveBayGate(b, self.r6_max_utilization, self.r6_target_utilization) for b in bays}
        self._r6_bar: Dict[str, Optional[tuple]] = {b: None for b in bays}      # (bar_index, ts) stepped
        self._r6_blocked: Dict[str, Optional[str]] = {b: None for b in bays}    # fail-closed reason this bar
        self._r6_bindings: Dict[str, Dict[str, Any]] = {}                        # event_id -> binding
        self._r6_current: Optional[Dict[str, Any]] = None                        # candidate in flight
        self.r6_decisions: list = []
        self.r6_counts: Counter = Counter()
        # Physical-equivalent governor state is owned by the active R6 engine.
        # It is not driven by market bars or used to delay financial protections.
        self.r6_governors = {bay: TurbineGovernorUnit(config, p_mech_pu=config.power_min_pu)
                             for bay, config in repository_defaults().items()}

    def r6_physical_governor_step(self, bay_id: str, *, dt_s: float, p_ref_pu: float,
                                  p_electrical_pu: float) -> dict:
        """Advance one explicitly supplied physical-equivalent governor input.

        This opt-in interface is intentionally separate from ``_plant_entry_gate``:
        trading bars are not physical integration timesteps and native emergency or
        financial protections must remain immediate.
        """
        try:
            governor = self.r6_governors[bay_id]
        except KeyError as exc:
            raise ValueError(f"unknown R6 governor bay: {bay_id}") from exc
        return governor.step(dt_s=dt_s, p_ref_pu=p_ref_pu, p_electrical_pu=p_electrical_pu)

    # ---------------------------------------------------------------- bar / bay feedback
    def _r6_ts(self, timestamp) -> str:
        import pandas as pd
        ts = pd.Timestamp(timestamp)
        if ts.tzinfo is None:
            raise R6GateError("NAIVE_TIMESTAMP")
        return ts.isoformat()

    def _r6_order_ids(self) -> Dict[str, list]:
        key = "client_order_id" if self.r6_variant == "B" else "bt_ref"
        events: Dict[str, list] = {}
        for e in self.order_audit:
            events.setdefault(str(e.get(key)), []).append(str(e.get("event")).upper())
        return events

    def _r6_settle(self, bay_id: str) -> Optional[str]:
        """Release reservations whose real orders are now acknowledged or terminal (their
        exposure is then visible to the ledger probe).  Returns a blocking reason or None."""
        gate = self._r6_gates[bay_id]
        events = self._r6_order_ids()
        for event_id in list(gate.reserved):
            binding = self._r6_bindings.get(event_id)
            if binding is None or binding.get("order_id") is None:
                return "R6_RESERVATION_WITHOUT_NATIVE_ORDER"
            seen = set(events.get(str(binding["order_id"]), []))
            if not seen or seen <= _NOT_YET_ACKNOWLEDGED:
                return "R6_NATIVE_ORDER_NOT_YET_ACKNOWLEDGED"
            gate.settle(event_id, native_ack_or_terminal=True)
            binding["settled_by"] = sorted(seen - _NOT_YET_ACKNOWLEDGED)
            self.r6_counts["reservations_settled"] += 1
        return None

    def _r6_begin(self, bay_id: str, timestamp) -> None:
        key = (int(self._native_bar_index), self._r6_ts(timestamp))
        if self._r6_bar[bay_id] == key:
            return                                          # already stepped for this bar
        if self._r6_bar[bay_id] is not None and key <= self._r6_bar[bay_id]:
            raise R6GateError("R6_BAR_REPLAYED_OR_NON_MONOTONIC")
        self._r6_bar[bay_id] = key
        self._r6_blocked[bay_id] = None
        reason = self._r6_settle(bay_id)
        if reason:
            self._r6_blocked[bay_id] = reason
            return
        try:
            # Only symbols this engine actually trades: the venue has no feed (C: KeyError) for others.
            topology = {s: b for s, b in SYMBOL_TO_BAY.items() if s in set(self.symbols)}
            snap = snapshot(engine=self, variant=self.r6_variant, bay_id=bay_id, symbol_to_bay=topology,
                            completed_bar_marks=dict(self._last_close), bar_timestamp=key[1])
            self._r6_gates[bay_id].begin_bar(snap, key[0])
            self.r6_counts["bay_feedback_steps"] += 1
        except (LedgerProbeError, R6GateError, ValueError) as exc:
            self._r6_blocked[bay_id] = f"R6_FEEDBACK_UNAVAILABLE:{exc}"

    # ---------------------------------------------------------------- checkpoint / restore
    def r6_snapshot(self) -> dict:
        """Deterministic R6 state: per-bay ExposureAVR PI state, reservations, last stepped bar,
        fail-closed flag, bindings and counters.  Native state is checkpointed by the native journal."""
        import copy
        return {"schema": 2, "variant": self.r6_variant,
                "utilization": [self.r6_max_utilization, self.r6_target_utilization],
                "bays": {b: {"exposure_avr": g.avr.snapshot(), "allowed": g.allowed,
                             "reserved": dict(g.reserved), "seen": sorted(g.seen),
                             "bar": list(self._r6_bar[b]) if self._r6_bar[b] else None,
                             "blocked": self._r6_blocked[b]} for b, g in self._r6_gates.items()},
                "bindings": copy.deepcopy(self._r6_bindings), "counts": dict(self.r6_counts),
                "governors": {b: g.snapshot() for b, g in self.r6_governors.items()}}

    def r6_restore(self, state: dict) -> None:
        if state.get("schema") != 2 or state.get("variant") != self.r6_variant \
                or state.get("utilization") != [self.r6_max_utilization, self.r6_target_utilization] \
                or set(state.get("bays", {})) != set(self._r6_gates):
            raise R6GateError("INCOMPATIBLE_R6_CHECKPOINT")
        for b, st in state["bays"].items():
            g = self._r6_gates[b]
            g.avr.restore(st["exposure_avr"])
            g.allowed, g.reserved, g.seen = float(st["allowed"]), dict(st["reserved"]), set(st["seen"])
            g.current = None            # a fresh causal ledger snapshot is required after restore
            self._r6_bar[b] = tuple(st["bar"]) if st["bar"] else None
            self._r6_blocked[b] = st["blocked"]
        import copy
        self._r6_bindings = copy.deepcopy(state["bindings"])
        self.r6_counts = Counter(state["counts"])
        if set(state.get("governors", {})) != set(self.r6_governors):
            raise R6GateError("INCOMPATIBLE_R6_GOVERNOR_CHECKPOINT")
        for bay, governor_state in state["governors"].items():
            self.r6_governors[bay].restore(governor_state)

    # ---------------------------------------------------------------- native seams
    def _admit_entry_candidate(self, pending, timestamp) -> None:
        self._r6_current = {"candidate_id": pending["candidate_id"], "symbol": pending["symbol"]}
        try:
            return super()._admit_entry_candidate(pending, timestamp)
        finally:
            current, self._r6_current = self._r6_current, None
            event_id = current.get("event_id") if current else None
            if event_id and not current.get("order_id"):
                gate = self._r6_gates[current["bay_id"]]
                if event_id in gate.reserved:      # no native order was created: nothing to hold
                    del gate.reserved[event_id]
                    self._r6_bindings[event_id]["released"] = "NO_NATIVE_ORDER"
                    self.r6_counts["reservations_released_no_order"] += 1

    def _register(self, order, record):
        super()._register(order, record)
        current = self._r6_current
        if record.get("kind") == "entry" and current and current.get("event_id") \
                and record.get("symbol") == current["symbol"] and not current.get("order_id"):
            order_id = str(order.client_order_id) if self.r6_variant == "B" else str(int(order.ref))
            current["order_id"] = order_id
            binding = self._r6_bindings[current["event_id"]]
            binding.update(order_id=order_id, submitted_quantity=int(record["requested"]))
            gate = self._r6_gates[current["bay_id"]]
            if current["event_id"] in gate.reserved:   # reserve what was actually submitted
                gate.reserved[current["event_id"]] = int(record["requested"]) * float(binding["price"])

    def _plant_entry_gate(self, symbol, timestamp, side, quantity, price, bar, candidate_id, governor_entry) -> int:
        native = int(super()._plant_entry_gate(symbol, timestamp, side, quantity, price, bar, candidate_id,
                                               governor_entry))
        bay_id = SYMBOL_TO_BAY.get(symbol)
        record = {"candidate_id": candidate_id, "timestamp": str(timestamp), "symbol": symbol, "bay_id": bay_id,
                  "side": side, "requested_quantity": int(quantity), "native_plant_quantity": native,
                  "governor_action": governor_entry.get("action"), "fsr": governor_entry.get("size_multiplier"),
                  "governor_reason": governor_entry.get("reason")}
        result = 0
        try:
            if bay_id is None:
                raise R6GateError("SYMBOL_NOT_IN_TOPOLOGY")
            self._r6_begin(bay_id, timestamp)
            if self._r6_blocked[bay_id]:
                raise R6GateError(self._r6_blocked[bay_id])
            gate = self._r6_gates[bay_id]
            ident = CausalID(int(self._native_bar_index), self._r6_ts(timestamp), bay_id, symbol, "FLAT")
            safety = SafetyPermit(entry_allowed=native > 0, max_total_quantity=max(0, native),
                                  mandatory_trip_reason=(f"MICOM:{self._micom_trip['ansi_code']}"
                                                         if self._micom_trip else ""))
            available_before = gate.allowed - gate.current[0].filled_notional \
                - gate.current[0].acknowledged_pending_entry_notional - sum(gate.reserved.values())
            intent = gate.entry(identity=ident, native_governor=governor_entry, final_native_quantity=native,
                                fsr_already_applied=True, safety=safety, price=float(price),
                                minimum_order_notional=0.0, legacy_plant_admitted=native > 0)
            result = min(native, int(intent.order_quantity)) if intent.action == Action.ENTRY else 0
            record.update(action=intent.action.value, reason=intent.reason, authority=intent.controlling_authority,
                          event_id=intent.event_id, r6_quantity=result,
                          bay_allowed_notional=gate.allowed, bay_filled_notional=gate.current[0].filled_notional,
                          bay_ack_pending_notional=gate.current[0].acknowledged_pending_entry_notional,
                          bay_available_notional_before=available_before,
                          bay_capital=gate.current[0].bay_capital)
            if intent.action == Action.ENTRY:
                self._r6_bindings[intent.event_id] = {"candidate_id": candidate_id, "symbol": symbol,
                                                      "bay_id": bay_id, "price": float(price), "order_id": None}
                if self._r6_current is not None:
                    self._r6_current.update(event_id=intent.event_id, bay_id=bay_id)
        except (R6GateError, ValueError) as exc:
            record.update(action="BLOCK", reason=f"R6_FAIL_CLOSED:{exc}", authority="R6", r6_quantity=0)
        self.r6_counts[f"unit_master_{record['action']}"] += 1
        if native > 0 and result < native:
            self.r6_counts["r6_restricted_native_entry"] += 1
        self.r6_decisions.append(record)
        self._record_controller_event("R6_UNIT_MASTER_ENTRY", timestamp, symbol, record)
        return result


def r6_classes():
    from engine_b.orchestrator import EngineBOrchestrator
    from engine_c.orchestrator import EngineCOrchestrator

    class R6EngineBOrchestrator(R6ActivePaperMixin, EngineBOrchestrator):
        r6_variant = "B"

    class R6EngineCOrchestrator(R6ActivePaperMixin, EngineCOrchestrator):
        r6_variant = "C"

    return R6EngineBOrchestrator, R6EngineCOrchestrator
