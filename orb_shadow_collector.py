"""Broker-read-only ORB (Opening Range Breakout) live observer - EXPLORATORY.

Standalone and separate from read_only_shadow_collector.py (the momentum
shadow). The only broker operation used here is `quote`. This process never
imports the production runner/engine and never writes production state or
lock files. See orb_shadow_observer.py's module docstring for why this
specific strategy has not been, and cannot honestly be, backtested with
what this project has on disk - it is observed live as an exercise, not
presented as validated.
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol
from zoneinfo import ZoneInfo

from extended_history_windows import EXCLUDED_SYMBOLS
from orb_shadow_observer import advance, load_state, save_state
from portfolio_brain_v9 import SECTORS
from shadow_strategy_evaluator import write_telemetry_atomic
# 2026-08-24, owner request: same governor-coordination gap and fix as
# read_only_shadow_collector.py - see that file's own comment for the
# full reasoning. Wrapping the reader at the call site in main() means
# run_loop() needs zero changes.
from kite_request_governor import QUOTE as GOV_QUOTE, KiteRequestGovernor

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_UNIVERSE = tuple(sorted(set(SECTORS) - EXCLUDED_SYMBOLS))


class QuoteReader(Protocol):
    def quote(self, instruments: list[str]) -> Mapping[str, Mapping[str, Any]]: ...


class _GovernedQuoteReader:
    """Wraps a real QuoteReader so every .quote() call is gated through the
    shared cross-process governor before touching Kite - same QUOTE class
    (1/s) every other live-quote caller in this project uses."""

    def __init__(self, reader: "QuoteReader", governor: KiteRequestGovernor):
        self._reader = reader
        self._governor = governor

    def quote(self, instruments: list[str]) -> Mapping[str, Mapping[str, Any]]:
        self._governor.acquire(GOV_QUOTE)
        return self._reader.quote(instruments)


def run_loop(
    reader: QuoteReader, *, output: Path, state_path: Path,
    interval_seconds: float, universe: Iterable[str] = DEFAULT_UNIVERSE,
    orb_minutes: float = 15.0, breakout_buffer_bps: float = 10.0,
) -> None:
    if interval_seconds < 1.0:
        raise ValueError("interval_seconds must be at least 1.0")
    symbols = tuple(universe)
    instruments = [f"NSE:{symbol}" for symbol in symbols]
    state = load_state(state_path)
    cycle = 0
    consecutive_failures = 0
    while True:
        cycle += 1
        started = time.monotonic()
        try:
            raw = reader.quote(instruments)
            if not isinstance(raw, Mapping):
                raise RuntimeError("FAIL_CLOSED: malformed quote response")
            now = datetime.now(IST)
            state, telemetry = advance(
                state, quotes=raw, universe=symbols, now=now,
                orb_minutes=orb_minutes, breakout_buffer_bps=breakout_buffer_bps,
            )
            save_state(state_path, state)
            telemetry["observed_at_utc"] = now.isoformat()
            telemetry["source"] = "KITE_QUOTE_READ_ONLY"
            telemetry["cycle"] = cycle
            telemetry["cycle_duration_ms"] = round((time.monotonic() - started) * 1000, 3)
            consecutive_failures = 0
            top = telemetry.get("top_candidate") or {}
            print(
                f"{telemetry['observed_at_utc']} cycle={cycle} decision={telemetry['decision']} "
                f"ranges_established={len(telemetry['symbols_with_established_range'])}/{len(symbols)} "
                f"top={top.get('symbol', '-')} strength={top.get('breakout_strength_pct', '-')}"
            )
        except Exception as exc:
            consecutive_failures += 1
            telemetry = {
                "mode": "READ_ONLY_SHADOW", "strategy_id": "ORB_EXPLORATORY",
                "decision": "BLOCK", "authoritative_strategy": False, "backtested": False,
                "observed_at_utc": datetime.now(IST).isoformat(), "source": "KITE_QUOTE_READ_ONLY",
                "cycle": cycle, "consecutive_failures": consecutive_failures,
                "error_type": type(exc).__name__, "error": str(exc),
                "capabilities": {
                    "broker_network": False, "order_api": False,
                    "request_entry": False, "state_mutation": False,
                },
            }
            print(f"{telemetry['observed_at_utc']} cycle={cycle} decision=BLOCK "
                  f"error={type(exc).__name__} failures={consecutive_failures}")
        write_telemetry_atomic(output, telemetry)
        time.sleep(interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="V3.4 read-only ORB shadow observer (exploratory)")
    parser.add_argument("--output", default="orb_shadow_telemetry.json")
    parser.add_argument("--state", default="orb_shadow_state.json")
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument("--orb-minutes", type=float, default=15.0)
    parser.add_argument("--breakout-buffer-bps", type=float, default=10.0)
    args = parser.parse_args()

    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_ACCESS_TOKEN")
    if not api_key or not access_token:
        print("[BLOCK] KITE_API_KEY and KITE_ACCESS_TOKEN must already exist in this terminal.")
        print("[BLOCK] Do not paste either credential into chat or a source file.")
        return 2

    governor_dir = os.getenv("KITE_RATE_GOVERNOR_DIR")
    if not governor_dir:
        print("[BLOCK] KITE_RATE_GOVERNOR_DIR must be set - same shared directory every other "
              "process sharing this Kite account uses, so quote requests are coordinated "
              "across all of them. See kite_request_governor.py's own module docstring.")
        return 2
    governor = KiteRequestGovernor(state_dir=Path(governor_dir))

    from kiteconnect import KiteConnect
    raw_reader = KiteConnect(api_key=api_key)
    raw_reader.set_access_token(access_token)
    reader = _GovernedQuoteReader(raw_reader, governor)

    print("V3.4 ORB SHADOW OBSERVER - EXPLORATORY, NOT BACKTESTED")
    print("Broker operation: quote only")
    print(f"Kite rate governor: {governor_dir}")
    print("Order execution: unavailable in this program")
    print(f"Universe: {len(DEFAULT_UNIVERSE)} symbols")
    print(f"Opening range window: first {args.orb_minutes} minutes of the session")
    print("Press Ctrl+C to stop.")
    try:
        run_loop(
            reader, output=Path(args.output), state_path=Path(args.state),
            interval_seconds=args.interval_seconds, orb_minutes=args.orb_minutes,
            breakout_buffer_bps=args.breakout_buffer_bps,
        )
    except KeyboardInterrupt:
        print("ORB shadow observer stopped.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
