"""R1-C composite state machine. LOCAL / IN-MEMORY ONLY - no Kite calls,
no broker writes, no orders anywhere in this module. Consumes the
already-verified outputs of the three live evaluators
(`r1c_live_v2c_evaluator.LiveV2CEvaluator`,
`r1c_live_pillar2_evaluator.LivePillar2Evaluator`,
`r1c_live_pillar1_evaluator.LivePillar1Evaluator`) and applies exactly
the sequencing rule frozen in `P01D_RRME_R1C_D0_DRAFT_20260819.md`:

    V2-C permits (REVERSION_PERMITTED, 5 sessions)
        -> Pillar II sets up (REVERSION_SETUP, same session only)
        -> Pillar I confirms, strictly after Pillar II's own timestamp,
           same session
        -> COMPOSITE ENTRY

Every decision is recorded to an Evidence Ledger - entries, abstentions,
and expirations alike - never only the trades. Per D0's own §5
firewall (mirrored from the concept document): nothing in this module
computes evidence from already-exposed historical data; it only
sequences whatever live evaluator outputs it is handed.

**One genuine interpretive gap in D0, disclosed here rather than
silently resolved**: D0 freezes what happens when Pillar I fails to
confirm a Pillar II setup (ABSTAIN), and what the `REVERSION_SETUP`
window means (same session only). It does not explicitly say whether a
single unconfirmed Pillar II setup consumes the underlying
`REVERSION_PERMITTED` grant, or whether Pillar II may attempt again on
a later day within the same 5-session window. This module adopts one
explicit, defensible default - **a permission is consumed only by an
actual COMPOSITE_ENTRY or by window expiry; an unconfirmed setup
abstains for that day but leaves the permission active for the
remainder of its window** - flagged here for the owner's review, not
presented as already decided by D0's own text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

REVERSION_PERMITTED_SESSIONS = 5  # D0 §2, frozen


@dataclass
class PermissionRecord:
    symbol: str
    granted_on: date
    sessions_remaining: int
    p_reverting: float


@dataclass(frozen=True)
class LedgerEntry:
    date: str
    symbol: str
    decision: str  # COMPOSITE_ENTRY | ABSTAIN
    reason_code: str
    detail: dict = field(default_factory=dict)


class CompositeStateMachine:
    """One instance per live observation run. `process_day` is called
    exactly once per trading day, in trading-day order - it is the
    caller's responsibility to call it that way; this class has no
    calendar of its own and trusts its own call sequence as the
    session clock (matching how "5 trading sessions" is defined
    operationally for a live system: the next 5 times this method is
    called, not 5 calendar days)."""

    def __init__(self) -> None:
        self._permissions: dict[str, PermissionRecord] = {}
        self.evidence_ledger: list[LedgerEntry] = []

    def process_day(
        self, today: date, *,
        v2c_scores: list,       # list[r1c_live_v2c_evaluator.LiveV2CScore]
        pillar2_trades: list,   # list[dict], from LivePillar2Evaluator.evaluate()
        pillar1_trades: list,   # list[dict], from LivePillar1Evaluator.evaluate()
    ) -> list[LedgerEntry]:
        entries_today: list[LedgerEntry] = []

        # --- Step 1: for every symbol with an active permission, check
        # whether Pillar II set up today, and if so, whether Pillar I
        # confirmed strictly after it, same session.
        pillar2_by_symbol = {t["symbol"]: t for t in pillar2_trades}
        pillar1_by_symbol_earliest = {}
        for t in pillar1_trades:
            sym = t["symbol"]
            if sym not in pillar1_by_symbol_earliest or t["entry_time"] < pillar1_by_symbol_earliest[sym]["entry_time"]:
                pillar1_by_symbol_earliest[sym] = t

        for symbol, perm in list(self._permissions.items()):
            setup = pillar2_by_symbol.get(symbol)
            if setup is None:
                continue  # no setup today - permission simply carries over (see decrement step below)

            setup_time = setup["entry_time"]
            confirmations_after_setup = [
                t for t in pillar1_trades
                if t["symbol"] == symbol and t["entry_time"] > setup_time
            ]
            if confirmations_after_setup:
                confirmation = min(confirmations_after_setup, key=lambda t: t["entry_time"])
                entries_today.append(LedgerEntry(
                    date=today.isoformat(), symbol=symbol, decision="COMPOSITE_ENTRY",
                    reason_code="V2C_PERMIT_PILLAR2_SETUP_PILLAR1_CONFIRM",
                    detail={
                        "p_reverting": perm.p_reverting, "granted_on": perm.granted_on.isoformat(),
                        "setup_entry_time": setup_time, "setup_entry_price": setup["entry_price"],
                        "confirmation_entry_time": confirmation["entry_time"],
                        "confirmation_entry_price": confirmation["entry_price"],
                        "confirmation_stop": confirmation["stop"], "confirmation_target": confirmation["target"],
                    },
                ))
                del self._permissions[symbol]  # consumed - see module docstring
            else:
                entries_today.append(LedgerEntry(
                    date=today.isoformat(), symbol=symbol, decision="ABSTAIN",
                    reason_code="PILLAR2_SETUP_FIRED_NO_PILLAR1_CONFIRMATION_SAME_SESSION",
                    detail={"setup_entry_time": setup_time},
                ))
                # Permission itself is NOT consumed by a failed setup -
                # see module docstring's disclosed interpretive default.

        # --- Step 2: decrement remaining sessions for everything still
        # active after step 1; expire anything that's run out.
        for symbol, perm in list(self._permissions.items()):
            perm.sessions_remaining -= 1
            if perm.sessions_remaining <= 0:
                entries_today.append(LedgerEntry(
                    date=today.isoformat(), symbol=symbol, decision="ABSTAIN",
                    reason_code="PERMISSION_EXPIRED_NO_QUALIFYING_SETUP",
                    detail={"granted_on": perm.granted_on.isoformat()},
                ))
                del self._permissions[symbol]

        # --- Step 3: grant new permissions from today's V2-C scores.
        # Recorded whether granted or not - the Evidence Ledger records
        # every decision, not only trade-producing ones.
        for score in v2c_scores:
            if score.abstain:
                entries_today.append(LedgerEntry(
                    date=today.isoformat(), symbol=score.security_key, decision="ABSTAIN",
                    reason_code=f"V2C_ABSTAIN_{score.abstain_reason}",
                    detail={"event_z20": score.event_z20},
                ))
                continue
            if not score.permitted:
                entries_today.append(LedgerEntry(
                    date=today.isoformat(), symbol=score.security_key, decision="ABSTAIN",
                    reason_code="V2C_SCORED_BELOW_THRESHOLD",
                    detail={"p_reverting": score.p_reverting, "event_z20": score.event_z20},
                ))
                continue
            if score.security_key in self._permissions:
                # Already has an active permission - D0 does not specify
                # stacking or refreshing; refuse silently overwriting.
                entries_today.append(LedgerEntry(
                    date=today.isoformat(), symbol=score.security_key, decision="ABSTAIN",
                    reason_code="V2C_PERMIT_IGNORED_ALREADY_ACTIVE_PERMISSION",
                    detail={"p_reverting": score.p_reverting},
                ))
                continue
            self._permissions[score.security_key] = PermissionRecord(
                symbol=score.security_key, granted_on=today,
                sessions_remaining=REVERSION_PERMITTED_SESSIONS, p_reverting=score.p_reverting,
            )
            entries_today.append(LedgerEntry(
                date=today.isoformat(), symbol=score.security_key, decision="ABSTAIN",
                reason_code="V2C_PERMIT_GRANTED_AWAITING_SETUP",
                detail={"p_reverting": score.p_reverting, "event_z20": score.event_z20},
            ))

        self.evidence_ledger.extend(entries_today)
        return entries_today

    def active_permissions(self) -> dict[str, PermissionRecord]:
        """Read-only snapshot, for the live observer's own diagnostics -
        never consulted by anything outside this class to make a
        decision."""
        return dict(self._permissions)
