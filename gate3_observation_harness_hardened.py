"""
V3.4 Gate 3A.2 — Read-Only REST Broker Observation Harness
============================================================
Institutional-grade read-only monitoring harness for Zerodha Kite Connect.
Features:
  - AST-based zero-execution self-audit.
  - Automatic session generation from request_token + api_secret (if access_token missing).
  - Strict ReadOnlyKite facade with explicit read-only whitelist.
  - Integration with production KiteBrokerAdapter.get_daily_risk_snapshot().
  - Strict freshness (<= 2 seconds) and validity checks using production logic.
  - Robust historical candle validation for both M&M and RELIANCE.
  - Production kill-switch file path observation.
  - Whitelist-based append-only JSONL telemetry.
  - Aggregate fail-closed acceptance evaluation.
"""

import ast
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from pathlib import Path
import json
import os
import sys
import time

from kiteconnect import KiteConnect
from run_production_p01d_candidate import (
    KiteBrokerAdapter,
    BrokerRiskSnapshot,
    P03_KILL_SWITCH_FILE,
)

IST = ZoneInfo("Asia/Kolkata")
TELEMETRY_DIR = Path("gate3_telemetry")
MAX_SNAPSHOT_AGE_SECONDS = Decimal("2")


def phase(message: str):
    print(f"[PHASE] {message}", flush=True)


class ReadOnlyKite:
    """
    Strict read-only facade around KiteConnect.
    Exposes only whitelisted read-only methods required for observation.
    Contains zero execution primitives (no place_order, modify_order, cancel_order).
    """
    def __init__(self, kite_client: KiteConnect):
        self._kite = kite_client

    def profile(self):
        return self._kite.profile()

    def margins(self):
        return self._kite.margins()

    def orders(self):
        return self._kite.orders()

    def trades(self):
        return self._kite.trades()

    def positions(self):
        return self._kite.positions()

    def instruments(self, exchange=None):
        return self._kite.instruments(exchange)

    def quote(self, instruments):
        return self._kite.quote(instruments)

    def historical_data(self, instrument_token, from_date, to_date, interval, continuous=False, oi=False):
        return self._kite.historical_data(instrument_token, from_date, to_date, interval, continuous, oi)

    def order_charges(self, order_id):
        return self._kite.order_charges(order_id)


def self_audit():
    script_path = Path(__file__).resolve()
    try:
        tree = ast.parse(script_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[FATAL] Self-audit failed to parse source tree: {exc}")
        sys.exit(1)

    forbidden = {"place_order", "modify_order", "cancel_order"}

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in forbidden:
            print(f"[FATAL SECURITY ERROR] Forbidden function definition found: {node.name}")
            sys.exit(1)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in forbidden:
                print(f"[FATAL SECURITY ERROR] Forbidden method call found: {func.attr}")
                sys.exit(1)
            elif isinstance(func, ast.Name) and func.id in forbidden:
                print(f"[FATAL SECURITY ERROR] Forbidden function call found: {func.id}")
                sys.exit(1)

    print("[PASS] Stage 3A.2 AST Self-Audit: Zero execution primitives detected in source tree.")


def get_kill_switch_status(path_str: str) -> str:
    try:
        p = Path(path_str)
        if not p.exists():
            return "ABSENT"
        if p.is_file():
            return "PRESENT"
        return "UNREADABLE"
    except Exception:
        return "UNREADABLE"


def append_telemetry(record: dict):
    TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now(IST).date().isoformat()
    telemetry_file = TELEMETRY_DIR / f"{today_str}.jsonl"

    allowed_keys = {
        "timestamp_utc", "timestamp_ist", "success", "latency_ms",
        "snapshot_age_ms", "snapshot_valid", "orders_count", "trades_count",
        "positions_count", "instrument_status", "market_data_status",
        "historical_data_status", "kill_switch_status", "error_class"
    }
    clean_record = {k: record[k] for k in allowed_keys if k in record}

    with open(telemetry_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(clean_record) + "\n")


def validate_candles(candles: list) -> str:
    if not isinstance(candles, list) or len(candles) == 0:
        return "EMPTY_CANDLES"

    prev_time = None
    for c in candles:
        curr_time = c.get("date")
        if prev_time is not None:
            if curr_time <= prev_time:
                return "DUPLICATE_OR_NON_MONOTONIC_TIMESTAMP"
        
        op = c.get("open")
        hi = c.get("high")
        lo = c.get("low")
        cl = c.get("close")
        vol = c.get("volume")

        if None in (op, hi, lo, cl, vol):
            return "MALFORMED_CANDLE_FIELD"

        if hi < op or hi < cl or lo > op or lo > cl or vol < 0:
            return "INVALID_OHLC_INVARIANT"

        prev_time = curr_time

    return "VERIFIED"


def initialize_kite_client():
    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_ACCESS_TOKEN")
    request_token = os.getenv("KITE_REQUEST_TOKEN")
    api_secret = os.getenv("KITE_API_SECRET")

    if not api_key:
        raise RuntimeError("MISSING_KITE_API_KEY")

    kite = KiteConnect(api_key=api_key)

    if access_token:
        kite.set_access_token(access_token)
    elif request_token and api_secret:
        print("[INFO] Exchanging request_token for access_token...")
        data = kite.generate_session(request_token=request_token, api_secret=api_secret)
        kite.set_access_token(data["access_token"])
        print("[SUCCESS] Session generated successfully.")
    else:
        raise RuntimeError("MISSING_CREDENTIALS: Provide either KITE_ACCESS_TOKEN or (KITE_REQUEST_TOKEN and KITE_API_SECRET)")

    return kite


def run_observation_cycle():
    phase("SELF_AUDIT")
    self_audit()

    now_utc = datetime.now(timezone.utc)
    now_ist = datetime.now(IST)

    checks = {
        "profile": False,
        "risk_snapshot": False,
        "orders": False,
        "trades": False,
        "positions": False,
        "instruments": False,
        "mm_quote": False,
        "reliance_quote": False,
        "mm_history": False,
        "reliance_history": False,
        "kill_switch": False,
        "freshness": False,
    }

    record = {
        "timestamp_utc": now_utc.isoformat(),
        "timestamp_ist": now_ist.isoformat(),
        "success": False,
        "latency_ms": 0.0,
        "snapshot_age_ms": None,
        "snapshot_valid": False,
        "orders_count": 0,
        "trades_count": 0,
        "positions_count": 0,
        "instrument_status": "PENDING",
        "market_data_status": "PENDING",
        "historical_data_status": "PENDING",
        "kill_switch_status": get_kill_switch_status(P03_KILL_SWITCH_FILE),
        "error_class": None,
    }

    if record["kill_switch_status"] in ("ABSENT", "PRESENT"):
        checks["kill_switch"] = True

    phase("SESSION_INIT")
    try:
        raw_kite = initialize_kite_client()
    except Exception as exc:
        record["error_class"] = type(exc).__name__
        append_telemetry(record)
        print(f"[FAIL] Failed to initialize Zerodha session: {exc}")
        return False

    readonly_kite = ReadOnlyKite(raw_kite)
    
    # Corrected constructor call without live_titles
    adapter = KiteBrokerAdapter(readonly_kite, live_trading=False)

    start_perf = time.perf_counter()

    phase("BROKER_OBSERVATION")
    try:
        profile = readonly_kite.profile()
        if profile and "user_id" in profile:
            checks["profile"] = True

        phase("PROFILE_AND_TRADES")
        trades = readonly_kite.trades()
        checks["trades"] = isinstance(trades, list)

        phase("RISK_SNAPSHOT")
        snapshot = adapter.get_daily_risk_snapshot()
        record["snapshot_valid"] = snapshot.valid
        checks["risk_snapshot"] = True
        checks["orders"] = True
        checks["positions"] = True

        age_seconds = (datetime.now(timezone.utc) - snapshot.observation_timestamp).total_seconds()
        record["snapshot_age_ms"] = round(age_seconds * 1000.0, 2)

        if snapshot.valid and snapshot.is_fresh(MAX_SNAPSHOT_AGE_SECONDS):
            checks["freshness"] = True

        phase("INSTRUMENTS")
        instruments = readonly_kite.instruments("NSE")
        target_tokens = {}
        duplicate_detected = False

        if isinstance(instruments, list):
            for inst in instruments:
                sym = inst.get("tradingsymbol")
                if sym in ["M&M", "RELIANCE"]:
                    if (
                        inst.get("exchange") == "NSE"
                        and inst.get("instrument_type") == "EQ"
                        and float(inst.get("tick_size", 0)) > 0
                        and int(inst.get("lot_size", 0)) > 0
                        and inst.get("instrument_token") is not None
                    ):
                        if sym in target_tokens:
                            duplicate_detected = True
                        target_tokens[sym] = inst.get("instrument_token")

        if len(target_tokens) == 2 and not duplicate_detected:
            checks["instruments"] = True
            record["instrument_status"] = "VERIFIED"
        else:
            record["instrument_status"] = "FAILED_STRICT_VALIDATION_OR_DUPLICATE"

        phase("QUOTES")
        quotes = readonly_kite.quote(["NSE:M&M", "NSE:RELIANCE"])
        if isinstance(quotes, dict):
            mm_quote = quotes.get("NSE:M&M")
            rel_quote = quotes.get("NSE:RELIANCE")

            if mm_quote and float(mm_quote.get("last_price", 0)) > 0:
                checks["mm_quote"] = True
            if rel_quote and float(rel_quote.get("last_price", 0)) > 0:
                checks["reliance_quote"] = True

            if checks["mm_quote"] and checks["reliance_quote"]:
                record["market_data_status"] = "VERIFIED"
            else:
                record["market_data_status"] = "PARTIAL_OR_INVALID_QUOTES"
        else:
            record["market_data_status"] = "QUOTE_UNAVAILABLE"

        phase("HISTORICAL_DATA")
        to_date = datetime.now(IST)
        from_date = to_date - timedelta(days=2)

        mm_token = target_tokens.get("M&M")
        rel_token = target_tokens.get("RELIANCE")

        if mm_token:
            mm_candles = readonly_kite.historical_data(mm_token, from_date, to_date, interval="5minute")
            if validate_candles(mm_candles) == "VERIFIED":
                checks["mm_history"] = True

        if rel_token:
            rel_candles = readonly_kite.historical_data(rel_token, from_date, to_date, interval="5minute")
            if validate_candles(rel_candles) == "VERIFIED":
                checks["reliance_history"] = True

        if checks["mm_history"] and checks["reliance_history"]:
            record["historical_data_status"] = "VERIFIED"
        else:
            record["historical_data_status"] = "HISTORICAL_VALIDATION_FAILED"

        elapsed_ms = (time.perf_counter() - start_perf) * 1000.0
        record["latency_ms"] = round(elapsed_ms, 2)

        success = all(checks.values())
        record["success"] = success

        append_telemetry(record)
        print(f"----------------------------------------------------")
        print(f"GATE 3A.2 OBSERVATION SUMMARY: {'SUCCESS' if success else 'FAILURE'}")
        print(f"Checks: {json.dumps(checks, indent=2)}")
        print(f"Latency: {record['latency_ms']}ms | Snapshot Age: {record['snapshot_age_ms']}ms")
        print(f"----------------------------------------------------")
        return success

    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_perf) * 1000.0
        record["latency_ms"] = round(elapsed_ms, 2)
        record["success"] = False
        record["error_class"] = type(exc).__name__
        append_telemetry(record)
        print(f"[ERROR] Gate 3A.2 observation failed with exception: {type(exc).__name__}: {exc}")
        return False


if __name__ == "__main__":
    print("----------------------------------------------------", flush=True)
    print("V3.4 GATE 3A.2 — READ-ONLY REST OBSERVATION HARNESS", flush=True)
    print("----------------------------------------------------", flush=True)
    try:
        print("[PHASE] START", flush=True)
        success = run_observation_cycle()
        print(f"[RESULT] Gate 3A.2 {'PASS' if success else 'FAIL'}", flush=True)
        print(f"[RESULT] EXIT_CODE={0 if success else 1}", flush=True)
        sys.exit(0 if success else 1)
    except SystemExit:
        raise
    except BaseException as exc:
        print(f"[FATAL] Unhandled harness exception: {type(exc).__name__}: {exc}", flush=True)
        print("[RESULT] Gate 3A.2 FAIL", flush=True)
        print("[RESULT] EXIT_CODE=1", flush=True)
        sys.exit(1)
