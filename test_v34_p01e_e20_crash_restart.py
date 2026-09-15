
"""
V3.4 P0-1E / E20 — Process-Crash & Restart Acceptance Harness
===============================================================

Purpose
-------
Exercise the P0-1D emergency-exit state machine across a real process
boundary using:

* the production JsonFileStore implementation;
* a real temporary JSON state file;
* a file-backed deterministic broker model;
* fresh Python processes for each lifecycle phase.

The broker model records emergency SELL submissions in a separate JSON file,
so the assertion "total_sell_submissions == 1" spans process boundaries.

This harness does NOT contact Zerodha and never enables live trading.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest


ENGINE_PATH = Path(__file__).with_name("institutional_engine_v34_p01d_candidate.py")
RUNNER_PATH = Path(__file__).with_name("run_production_p01d_candidate.py")


def _worker_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run_worker(mode: str, state_file: Path, broker_file: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            mode,
            str(state_file),
            str(broker_file),
        ],
        cwd=str(Path(__file__).resolve().parent),
        env=_worker_env(Path(__file__).resolve().parent),
        text=True,
        capture_output=True,
        timeout=20,
    )


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _assert_worker_ok(result: subprocess.CompletedProcess, label: str) -> None:
    if result.returncode != 0:
        raise AssertionError(
            f"{label} failed.\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Worker-only deterministic broker
# ---------------------------------------------------------------------------

class FileBackedBroker:
    def __init__(self, broker_file: str):
        self.path = Path(broker_file)
        self.data = _read_json(self.path)

    def _save(self) -> None:
        _write_json(self.path, self.data)

    def get_positions(self):
        qty = int(self.data.get("position_qty", 20))
        if qty <= 0:
            return []
        return [{
            "tradingsymbol": "RELIANCE",
            "exchange": "NSE",
            "product": "MIS",
            "quantity": qty,
            "average_price": 1000.0,
        }]

    def ltp(self, symbols):
        return {
            "NSE:RELIANCE": {
                "last_price": float(self.data.get("ltp", 1000.0))
            }
        }

    def get_tick_size(self, symbol):
        return Decimal("0.05")

    def get_orders(self):
        return list(self.data.get("orders", []))

    def get_order_details(self, order_id):
        for order in self.data.get("orders", []):
            if str(order.get("order_id")) == str(order_id):
                return dict(order)
        raise RuntimeError("order not found")

    def submit_emergency_exit(self, **kwargs):
        self.data["total_sell_submissions"] = (
            int(self.data.get("total_sell_submissions", 0)) + 1
        )

        oid = f"EXIT-{self.data['total_sell_submissions']}"

        order = {
            "order_id": oid,
            "exchange": "NSE",
            "tradingsymbol": kwargs["symbol"],
            "transaction_type": "SELL",
            "product": "MIS",
            "order_type": "SL-M",
            "quantity": int(kwargs["quantity"]),
            "trigger_price": str(kwargs["trigger_price"]),
            "market_protection": str(kwargs["market_protection"]),
            "tag": kwargs["tag"],
            "status": "OPEN",
        }

        if self.data.get("record_order_before_timeout", True):
            self.data.setdefault("orders", []).append(order)

        self._save()

        if self.data.get("timeout_on_submission", True):
            raise TimeoutError("simulated network timeout after broker acceptance")

        return oid


class FakeClock:
    def now(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime(2026, 8, 12, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


class NoopAudit:
    def log(self, *args, **kwargs):
        pass


class NoopAlert:
    def send(self, *args, **kwargs):
        pass


class NoopLock:
    def acquire(self):
        return object()


class NoopTerminator:
    def __init__(self):
        self.halted = False
        self.reason = None

    def halt(self, reason):
        self.halted = True
        self.reason = reason


def _build_engine(state_file: str, broker_file: str):
    from institutional_engine_v34_p01d_candidate import (
        Config,
        TradingEngineV34,
        TradeContext,
    )

    # The production runner imports KiteConnect at module load time. E20 is a
    # local crash/restart test and must not require the real Zerodha SDK or
    # credentials. Provide only the import-time symbol required by the runner;
    # no broker API is reachable from this harness.
    if "kiteconnect" not in sys.modules:
        import types
        fake_kiteconnect = types.ModuleType("kiteconnect")
        class _KiteConnectStub:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("KiteConnect stub must never be instantiated in E20.")
        fake_kiteconnect.KiteConnect = _KiteConnectStub
        sys.modules["kiteconnect"] = fake_kiteconnect

    from run_production_p01d_candidate import JsonFileStore

    store = JsonFileStore(state_file)
    broker = FileBackedBroker(broker_file)

    engine = TradingEngineV34(
        broker=broker,
        clock=FakeClock(),
        sleeper=lambda _: None,
        store=store,
        audit=NoopAudit(),
        alert=NoopAlert(),
        lock=NoopLock(),
        terminator=NoopTerminator(),
        cfg=Config(
            alert_webhook_url="",
            max_daily_loss=Decimal("2000"),
            market_protection_pct=Decimal("-1"),
            observation_retry_budget=3,
        ),
    )

    return engine, store, broker, TradeContext


def _seed_exit_submit(state_file: str, broker_file: str) -> None:
    engine, store, broker, TradeContext = _build_engine(state_file, broker_file)

    engine.state.status = "EXIT_SUBMIT"
    engine.state.active_trade = TradeContext(
        symbol="RELIANCE",
        entry_tag="V3.4_ENTRY",
        target_qty=20,
        tranche_qty=20,
        filled_qty=20,
        pending_qty=20,
        avg_entry_price=Decimal("1000"),
    )
    store.save(engine.state)


def _worker_phase1_timeout(state_file: str, broker_file: str) -> None:
    """
    Process A:
      EXIT_SUBMIT -> durable EXIT_SUBMITTING -> broker accepts -> response lost
      -> EXIT_UNKNOWN.

    This process terminates normally immediately after the ambiguous result.
    The next phase is a genuinely fresh Python process.
    """
    engine, store, broker, _ = _build_engine(state_file, broker_file)

    assert engine.state.status == "EXIT_SUBMIT"

    result = engine.step()

    assert result == "STATE_CHANGED"
    assert engine.state.status == "EXIT_UNKNOWN"

    persisted = store.load("2026-08-12")
    assert persisted.status == "EXIT_UNKNOWN"
    assert persisted.active_trade is not None
    assert persisted.active_trade.exit_submission_fingerprint is not None


def _worker_phase2_restart_reconcile(state_file: str, broker_file: str) -> None:
    """
    Process B:
      fresh process + fresh JsonFileStore load -> exact broker order recovery.
    """
    engine, store, broker, _ = _build_engine(state_file, broker_file)

    # Fresh process has loaded durable EXIT_UNKNOWN. Reconciliation happens on
    # the first engine step; construction alone must not claim broker reality.
    assert engine.state.status == "EXIT_UNKNOWN"
    result = engine.step()
    assert result == "STATE_CHANGED"

    assert engine.state.status == "EXIT_PENDING"
    assert engine.state.active_trade is not None
    assert engine.state.active_trade.exit_order_id == "EXIT-1"

    # A second step must remain management/reconciliation only. It must not
    # create another emergency SELL.
    before = int(_read_json(Path(broker_file))["total_sell_submissions"])
    engine.step()
    after = int(_read_json(Path(broker_file))["total_sell_submissions"])

    assert before == 1
    assert after == 1


def _worker_phase3_restart_pending(state_file: str, broker_file: str) -> None:
    """
    Process C:
      another fresh boot from EXIT_PENDING must still not submit another exit.
    """
    engine, store, broker, _ = _build_engine(state_file, broker_file)

    assert engine.state.status == "EXIT_PENDING"
    assert engine.state.active_trade.exit_order_id == "EXIT-1"

    before = int(_read_json(Path(broker_file))["total_sell_submissions"])
    engine.step()
    after = int(_read_json(Path(broker_file))["total_sell_submissions"])

    assert after == before == 1


def _worker_no_order_restart(state_file: str, broker_file: str) -> None:
    """
    Fresh process sees durable EXIT_SUBMITTING but no broker order.
    It must transition to EXIT_UNKNOWN, never submit.
    """
    engine, store, broker, _ = _build_engine(state_file, broker_file)

    assert engine.state.status == "EXIT_SUBMITTING"
    result = engine.step()
    assert result == "NO_ACTION"
    assert engine.state.status == "EXIT_UNKNOWN"
    assert int(_read_json(Path(broker_file))["total_sell_submissions"]) == 0


def _worker_fingerprint_mismatch_observed_broker(state_file: str, broker_file: str) -> None:
    """
    Fresh process sees a broker near-match. The immutable historical fingerprint
    must remain unchanged; the order must not be adopted or modified.
    """
    engine, store, broker, _ = _build_engine(state_file, broker_file)

    before_fp = dict(engine.state.active_trade.exit_submission_fingerprint)

    assert engine.state.status == "EXIT_SUBMITTING"
    result = engine.step()

    assert result == "NO_ACTION"
    assert engine.state.status == "EXIT_UNKNOWN"
    assert engine.state.active_trade.exit_order_id is None
    assert engine.state.active_trade.exit_submission_fingerprint == before_fp
    assert int(_read_json(Path(broker_file))["total_sell_submissions"]) == 0


# ---------------------------------------------------------------------------
# E20-01 — Full accepted-order / lost-response / crash / restart lifecycle
# ---------------------------------------------------------------------------

def test_E20_01_process_death_boundary_exact_recovery_and_zero_duplicate():
    with tempfile.TemporaryDirectory(prefix="v34_e20_") as td:
        root = Path(td)
        state_file = root / "bot_state_v34.json"
        broker_file = root / "broker_reality.json"

        _write_json(
            broker_file,
            {
                "position_qty": 20,
                "ltp": 1000.0,
                "orders": [],
                "total_sell_submissions": 0,
                "timeout_on_submission": True,
                "record_order_before_timeout": True,
            },
        )

        # Seed state in a controlled process-equivalent step.
        _seed_exit_submit(str(state_file), str(broker_file))

        # Process A: side effect occurs, response is lost.
        r1 = _run_worker("phase1_timeout", state_file, broker_file)
        _assert_worker_ok(r1, "phase1_timeout")

        broker_after_a = _read_json(broker_file)
        assert broker_after_a["total_sell_submissions"] == 1
        assert len(broker_after_a["orders"]) == 1

        state_after_a = _read_json(state_file)
        assert state_after_a["status"] == "EXIT_UNKNOWN"

        # Process B: fresh interpreter + fresh JsonFileStore + broker reality.
        r2 = _run_worker("phase2_restart_reconcile", state_file, broker_file)
        _assert_worker_ok(r2, "phase2_restart_reconcile")

        # Process C: another fresh restart must still not duplicate.
        r3 = _run_worker("phase3_restart_pending", state_file, broker_file)
        _assert_worker_ok(r3, "phase3_restart_pending")

        broker_final = _read_json(broker_file)
        assert broker_final["total_sell_submissions"] == 1


# ---------------------------------------------------------------------------
# E20-02 — Crash/restart from durable EXIT_SUBMITTING with no visible order
# ---------------------------------------------------------------------------

def test_E20_02_durable_submitting_without_order_becomes_unknown_no_retry():
    with tempfile.TemporaryDirectory(prefix="v34_e20_no_order_") as td:
        root = Path(td)
        state_file = root / "bot_state_v34.json"
        broker_file = root / "broker_reality.json"

        _write_json(
            broker_file,
            {
                "position_qty": 20,
                "ltp": 1000.0,
                "orders": [],
                "total_sell_submissions": 0,
                "timeout_on_submission": False,
                "record_order_before_timeout": False,
            },
        )

        # Seed an already-crossed durable boundary.
        _seed_exit_submit(str(state_file), str(broker_file))

        # Convert durable EXIT_SUBMIT to EXIT_SUBMITTING exactly as a crash
        # boundary would leave it, without allowing the broker side effect.
        raw = _read_json(state_file)
        raw["status"] = "EXIT_SUBMITTING"
        raw["active_trade"]["exit_submission_fingerprint"] = {
            "exchange": "NSE",
            "tradingsymbol": "RELIANCE",
            "transaction_type": "SELL",
            "product": "MIS",
            "order_type": "SL-M",
            "quantity": 20,
            "trigger_price": "999.95",
            "market_protection": "-1",
            "tag": "V3.4_EXIT",
        }
        _write_json(state_file, raw)

        r = _run_worker("no_order_restart", state_file, broker_file)
        _assert_worker_ok(r, "no_order_restart")

        assert _read_json(broker_file)["total_sell_submissions"] == 0
        assert _read_json(state_file)["status"] == "EXIT_UNKNOWN"


# ---------------------------------------------------------------------------
# E20-03 — Tampered historical intent must never be rewritten
# ---------------------------------------------------------------------------

def test_E20_03_broker_near_match_never_rewrites_historical_intent():
    with tempfile.TemporaryDirectory(prefix="v34_e20_mismatch_") as td:
        root = Path(td)
        state_file = root / "bot_state_v34.json"
        broker_file = root / "broker_reality.json"

        _write_json(
            broker_file,
            {
                "position_qty": 20,
                "ltp": 1000.0,
                "orders": [{
                    "order_id": "STALE-1",
                    "exchange": "NSE",
                    "tradingsymbol": "RELIANCE",
                    "transaction_type": "SELL",
                    "product": "MIS",
                    "order_type": "SL-M",
                    "quantity": 19,  # deliberate near-match
                    "trigger_price": "999.95",
                    "market_protection": "-1",
                    "tag": "V3.4_EXIT",
                    "status": "OPEN",
                }],
                "total_sell_submissions": 0,
            },
        )

        _seed_exit_submit(str(state_file), str(broker_file))

        raw = _read_json(state_file)
        raw["status"] = "EXIT_SUBMITTING"
        raw["active_trade"]["exit_submission_fingerprint"] = {
            "exchange": "NSE",
            "tradingsymbol": "RELIANCE",
            "transaction_type": "SELL",
            "product": "MIS",
            "order_type": "SL-M",
            "quantity": 20,
            "trigger_price": "999.95",
            "market_protection": "-1",
            "tag": "V3.4_EXIT",
        }
        _write_json(state_file, raw)

        before = raw["active_trade"]["exit_submission_fingerprint"]

        r = _run_worker("fingerprint_mismatch_observed_broker", state_file, broker_file)
        _assert_worker_ok(r, "fingerprint_mismatch_observed_broker")

        final = _read_json(state_file)
        assert final["active_trade"]["exit_submission_fingerprint"] == before
        assert final["active_trade"]["exit_order_id"] is None
        assert final["status"] == "EXIT_UNKNOWN"
        assert _read_json(broker_file)["total_sell_submissions"] == 0


# ---------------------------------------------------------------------------
# Worker dispatcher
# ---------------------------------------------------------------------------

if __name__ == "__main__" and len(sys.argv) >= 5 and sys.argv[1] == "--worker":
    mode = sys.argv[2]
    state_file = sys.argv[3]
    broker_file = sys.argv[4]

    workers = {
        "phase1_timeout": _worker_phase1_timeout,
        "phase2_restart_reconcile": _worker_phase2_restart_reconcile,
        "phase3_restart_pending": _worker_phase3_restart_pending,
        "no_order_restart": _worker_no_order_restart,
        "fingerprint_mismatch_observed_broker": _worker_fingerprint_mismatch_observed_broker,
    }

    if mode not in workers:
        raise SystemExit(f"Unknown worker mode: {mode}")

    workers[mode](state_file, broker_file)
    raise SystemExit(0)
