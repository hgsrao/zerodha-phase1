"""
Revision 5 dual-engine operating interlocks.

This module contains execution-neutral plant rules only.

ENGINE_A
--------
* Intraday service.
* New-entry cutoff: 14:30 IST.
* Mandatory square-off becomes due at 15:15 IST.
* Maximum one CONFIRMED FILL per turbine bay per trading date.
* Fill latch is persisted in SQLite and survives process restarts.

ENGINE_B
--------
* Continuous / positional service.
* Does not consume or obey Engine-A daily fill latches.
* The Engine-A 14:30 cutoff does not apply.

Important:
The latch is consumed only after a confirmed successful fill. Candidate,
rejected, cancelled, or never-filled orders must not consume it.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from typing import Tuple
from zoneinfo import ZoneInfo

from revision5.topology import BAY_IDS


IST = ZoneInfo("Asia/Kolkata")

ENGINE_A = "ENGINE_A"
ENGINE_B = "ENGINE_B"
VALID_ENGINES = (ENGINE_A, ENGINE_B)

ENGINE_A_ENTRY_CUTOFF = time(14, 30)
ENGINE_A_SQUAREOFF_TIME = time(15, 15)


def as_ist(value: datetime) -> datetime:
    """
    Normalize timestamps to Asia/Kolkata.

    Naive datetimes are explicitly interpreted as IST for deterministic
    historical replay compatibility.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def trading_date(value: datetime) -> date:
    return as_ist(value).date()


def validate_engine(engine_mode: str) -> None:
    if engine_mode not in VALID_ENGINES:
        raise ValueError(
            f"Unknown engine mode {engine_mode!r}; "
            f"expected one of {VALID_ENGINES}"
        )


def validate_bay(bay_id: str) -> None:
    if bay_id not in BAY_IDS:
        raise ValueError(
            f"Unknown Revision-5 turbine bay {bay_id!r}"
        )


def entry_time_allowed(
    engine_mode: str,
    current_dt: datetime,
) -> Tuple[bool, str]:
    """
    Check only the Revision-5 Engine-A intraday cutoff.

    Market-session availability is deliberately a separate responsibility.
    ENGINE_B is therefore not rejected by this particular 14:30 rule.
    """
    validate_engine(engine_mode)
    now = as_ist(current_dt)

    if (
        engine_mode == ENGINE_A
        and now.time() >= ENGINE_A_ENTRY_CUTOFF
    ):
        return (
            False,
            "ENGINE_A_ENTRY_CUTOFF_14_30_IST",
        )

    return True, "ENTRY_TIME_ALLOWED"


def engine_a_squareoff_due(current_dt: datetime) -> bool:
    """True from 15:15 IST onward for Engine-A position liquidation."""
    return as_ist(current_dt).time() >= ENGINE_A_SQUAREOFF_TIME


class EngineStateStore:
    """
    Persistent Revision-5 operating-state store.

    The first persisted invariant is the Engine-A one-fill-per-bay/day
    interlock. SQLite makes the latch restart-safe while remaining usable
    by both replay and paper/live adapters.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            isolation_level=None,
        )
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def _initialize(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS engine_a_daily_fill_latch (
                    trading_date TEXT NOT NULL,
                    bay_id TEXT NOT NULL,
                    consumed INTEGER NOT NULL
                        CHECK (consumed IN (0, 1)),
                    filled_at_ist TEXT,
                    PRIMARY KEY (trading_date, bay_id)
                )
                """
            )

    @staticmethod
    def _date_key(current_dt: datetime) -> str:
        return trading_date(current_dt).isoformat()

    def engine_a_fill_used(
        self,
        bay_id: str,
        current_dt: datetime,
    ) -> bool:
        validate_bay(bay_id)
        key = self._date_key(current_dt)

        with self._connect() as con:
            row = con.execute(
                """
                SELECT consumed
                FROM engine_a_daily_fill_latch
                WHERE trading_date = ? AND bay_id = ?
                """,
                (key, bay_id),
            ).fetchone()

        return bool(row and row[0] == 1)

    def consume_engine_a_fill(
        self,
        bay_id: str,
        filled_at: datetime,
    ) -> bool:
        """
        Atomically consume one Engine-A bay allowance.

        Returns:
            True  -> latch was available and is now consumed.
            False -> bay had already consumed its allowance that day.
        """
        validate_bay(bay_id)

        filled_ist = as_ist(filled_at)
        key = filled_ist.date().isoformat()
        stamp = filled_ist.isoformat()

        con = self._connect()

        try:
            con.execute("BEGIN IMMEDIATE")

            row = con.execute(
                """
                SELECT consumed
                FROM engine_a_daily_fill_latch
                WHERE trading_date = ? AND bay_id = ?
                """,
                (key, bay_id),
            ).fetchone()

            if row and row[0] == 1:
                con.execute("ROLLBACK")
                return False

            con.execute(
                """
                INSERT INTO engine_a_daily_fill_latch (
                    trading_date,
                    bay_id,
                    consumed,
                    filled_at_ist
                )
                VALUES (?, ?, 1, ?)
                ON CONFLICT(trading_date, bay_id)
                DO UPDATE SET
                    consumed = 1,
                    filled_at_ist = excluded.filled_at_ist
                """,
                (key, bay_id, stamp),
            )

            con.execute("COMMIT")
            return True

        except Exception:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

        finally:
            con.close()

    def entry_allowed(
        self,
        engine_mode: str,
        bay_id: str,
        current_dt: datetime,
    ) -> Tuple[bool, str]:
        """
        Combined Revision-5 time/latch admission interlock.

        This does NOT perform signal, PID, AVR, risk, exposure,
        market-session, or broker checks.
        """
        validate_engine(engine_mode)
        validate_bay(bay_id)

        allowed, reason = entry_time_allowed(
            engine_mode,
            current_dt,
        )

        if not allowed:
            return False, reason

        if (
            engine_mode == ENGINE_A
            and self.engine_a_fill_used(bay_id, current_dt)
        ):
            return False, "ENGINE_A_DAILY_BAY_FILL_ALREADY_USED"

        return True, "ENGINE_INTERLOCKS_CLEAR"
