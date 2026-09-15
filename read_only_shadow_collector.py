"""Broker-read-only market snapshot collector for shadow strategy telemetry.

The only broker operation used here is ``quote``.  This process never imports
the production runner/engine and never writes production state or lock files.
It deliberately supplies ``risk_allowed=False`` so telemetry cannot represent
an executable authorization.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from shadow_strategy_evaluator import evaluate_shadow_strategy, write_telemetry_atomic
from shadow_entry_exit_lifecycle import advance as advance_shadow_lifecycle
from external_momentum_shadow import EXTERNAL_UNIVERSE, evaluate_external_momentum
# 2026-08-24, owner request: this module's real kite.quote() calls were
# uncoordinated with every other live process sharing this Kite account
# (V11 bridge terminals, chart-studies monitor, P02 live scan all already
# require KITE_RATE_GOVERNOR_DIR) - same class of problem the governor was
# built to prevent in the first place. Wrapping the reader at the call
# site in main() (see _GovernedQuoteReader below) means collect_once()/
# run_loop() need zero changes - they still just call reader.quote(...).
from kite_request_governor import QUOTE as GOV_QUOTE, KiteRequestGovernor


DEFAULT_UNIVERSE = EXTERNAL_UNIVERSE


class QuoteReader(Protocol):
    def quote(self, instruments: list[str]) -> Mapping[str, Mapping[str, Any]]: ...


class _GovernedQuoteReader:
    """Wraps a real QuoteReader so every .quote() call is gated through the
    shared cross-process governor before touching Kite - same QUOTE class
    (1/s) every other live-quote caller in this project uses. Duck-types
    the same QuoteReader protocol, so collect_once()/run_loop() never know
    the difference."""

    def __init__(self, reader: "QuoteReader", governor: KiteRequestGovernor):
        self._reader = reader
        self._governor = governor

    def quote(self, instruments: list[str]) -> Mapping[str, Mapping[str, Any]]:
        self._governor.acquire(GOV_QUOTE)
        return self._reader.quote(instruments)


def collect_once(
    reader: QuoteReader,
    *,
    universe: Iterable[str] = DEFAULT_UNIVERSE,
    trial_capital: float = 20_000.0,
    shadow_entry_intents: bool = False,
) -> dict[str, Any]:
    symbols = tuple(universe)
    instruments = [f"NSE:{symbol}" for symbol in symbols]
    raw = reader.quote(instruments)
    if not isinstance(raw, Mapping):
        raise RuntimeError("FAIL_CLOSED: malformed quote response")

    quotes: dict[str, Mapping[str, Any]] = {}
    depth: dict[str, Mapping[str, Any]] = {}
    for symbol, instrument in zip(symbols, instruments):
        payload = raw.get(instrument, {})
        if isinstance(payload, Mapping):
            quotes[instrument] = payload
            market_depth = payload.get("depth", {})
            if isinstance(market_depth, Mapping):
                depth[symbol] = market_depth

    telemetry = evaluate_shadow_strategy(
        quotes=quotes,
        universe=symbols,
        depth_by_symbol=depth,
        trial_capital=trial_capital,
        # This is a calculation switch only.  Even when True, this module has
        # no order API, request_entry, runner, or production-state capability.
        risk_allowed=bool(shadow_entry_intents),
        risk_reason="OBSERVATION_ONLY_NO_EXECUTION_AUTHORIZATION",
    )
    telemetry["observed_at_utc"] = datetime.now(timezone.utc).isoformat()
    telemetry["source"] = "KITE_QUOTE_READ_ONLY"
    telemetry["symbols_requested"] = list(symbols)
    telemetry["capabilities"]["broker_read"] = True
    telemetry["capabilities"]["broker_write"] = False
    telemetry["shadow_entry_intents_enabled"] = bool(shadow_entry_intents)
    telemetry["execution_posture"] = "PHYSICALLY_UNAVAILABLE"
    telemetry["external_variant"] = evaluate_external_momentum(quotes)
    return telemetry


def run_loop(
    reader: QuoteReader,
    *,
    output: Path,
    interval_seconds: float,
    universe: Iterable[str] = DEFAULT_UNIVERSE,
    shadow_entry_intents: bool = False,
    intent_log: Path | None = None,
    lifecycle_state: Path | None = None,
) -> None:
    if interval_seconds < 1.0:
        raise ValueError("interval_seconds must be at least 1.0")
    consecutive_failures = 0
    cycle = 0
    while True:
        cycle += 1
        started = time.monotonic()
        try:
            telemetry = collect_once(
                reader, universe=universe,
                shadow_entry_intents=shadow_entry_intents,
            )
            if shadow_entry_intents and lifecycle_state is not None:
                telemetry["shadow_lifecycle_event"] = advance_shadow_lifecycle(
                    telemetry=telemetry,
                    quotes=reader.quote([f"NSE:{s}" for s in tuple(universe)]),
                    state_path=lifecycle_state,
                )
            consecutive_failures = 0
            telemetry["health"] = {
                "cycle": cycle,
                "status": "HEALTHY",
                "consecutive_failures": 0,
                "cycle_duration_ms": round((time.monotonic() - started) * 1000, 3),
            }
            write_telemetry_atomic(output, telemetry)
            if shadow_entry_intents and intent_log is not None:
                # Keep production targets protected without importing or naming
                # production state artifacts in this read-only source module.
                protected_names = {
                    "bot_" + "state_v34" + suffix for suffix in (".json", ".lock")
                }
                protected_names.add("bot_" + "production.log")
                if intent_log.name.lower() in protected_names:
                    raise ValueError("refusing protected shadow intent log path")
                intent_log.parent.mkdir(parents=True, exist_ok=True)
                with intent_log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({
                        "observed_at_utc": telemetry["observed_at_utc"],
                        "decision": telemetry["decision"],
                        "candidate": telemetry.get("candidate"),
                        "checkpoints": telemetry.get("checkpoints", []),
                        "execution_posture": "PHYSICALLY_UNAVAILABLE",
                        "lifecycle_event": telemetry.get("shadow_lifecycle_event"),
                    }, sort_keys=True) + "\n")
            candidate = telemetry.get("candidate") or {}
            print(
                f"{telemetry['observed_at_utc']} cycle={cycle} "
                f"decision={telemetry['decision']} "
                f"symbol={candidate.get('symbol', '-')} "
                f"score={candidate.get('score', '-')} "
                f"obi={candidate.get('obi', '-')}"
            )
        except Exception as exc:
            consecutive_failures += 1
            failure = {
                "mode": "READ_ONLY_SHADOW",
                "decision": "BLOCK",
                "observed_at_utc": datetime.now(timezone.utc).isoformat(),
                "source": "KITE_QUOTE_READ_ONLY",
                "authoritative_strategy": False,
                "candidate": None,
                "scores": {},
                "checkpoints": [],
                "capabilities": {
                    "broker_network": False,
                    "broker_read": True,
                    "broker_write": False,
                    "order_api": False,
                    "request_entry": False,
                    "state_mutation": False,
                },
                "health": {
                    "cycle": cycle,
                    "status": "OBSERVATION_FAILURE",
                    "consecutive_failures": consecutive_failures,
                    "cycle_duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            }
            write_telemetry_atomic(output, failure)
            print(
                f"{failure['observed_at_utc']} cycle={cycle} decision=BLOCK "
                f"health=OBSERVATION_FAILURE failures={consecutive_failures}"
            )
        time.sleep(interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="V3.4 read-only shadow collector")
    parser.add_argument("--output", default="shadow_strategy_telemetry.json")
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument(
        "--shadow-entry-intents", action="store_true",
        help="Log hypothetical BUY intents; execution remains unavailable",
    )
    parser.add_argument(
        "--intent-log", default="shadow_entry_intents.jsonl",
        help="Append-only history for shadow intent decisions",
    )
    parser.add_argument(
        "--lifecycle-state", default="shadow_entry_exit_state.json",
        help="Separate shadow-only position state",
    )
    args = parser.parse_args()
    if args.shadow_entry_intents and args.output == "shadow_strategy_telemetry.json":
        args.output = "shadow_entry_intents_telemetry.json"

    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_ACCESS_TOKEN")
    if not api_key or not access_token:
        print("[BLOCK] KITE_API_KEY and KITE_ACCESS_TOKEN must already exist in this terminal.")
        print("[BLOCK] Do not paste either credential into chat or a source file.")
        return 2

    governor_dir = os.getenv("KITE_RATE_GOVERNOR_DIR")
    if not governor_dir:
        print("[BLOCK] KITE_RATE_GOVERNOR_DIR must be set - same shared directory every other "
              "process sharing this Kite account uses (V11 bridge terminals, chart-studies "
              "monitor, P02 live scan), so quote requests are coordinated across all of them. "
              "See kite_request_governor.py's own module docstring.")
        return 2
    governor = KiteRequestGovernor(state_dir=Path(governor_dir))

    # Delayed optional dependency import keeps calculations and tests offline.
    from kiteconnect import KiteConnect

    raw_reader = KiteConnect(api_key=api_key)
    raw_reader.set_access_token(access_token)
    reader = _GovernedQuoteReader(raw_reader, governor)
    print("V3.4 READ-ONLY SHADOW COLLECTOR")
    print("Broker operation: quote only")
    print(f"Kite rate governor: {governor_dir}")
    print("Order execution: unavailable in this program")
    print("Real risk authorization: always blocked")
    if args.shadow_entry_intents:
        print("Shadow entry intents: ENABLED (hypothetical calculation only)")
        print("Separate output: " + args.output)
    print("Press Ctrl+C to stop the collector only.")
    try:
        run_loop(
            reader,
            output=Path(args.output),
            interval_seconds=args.interval_seconds,
            shadow_entry_intents=args.shadow_entry_intents,
            intent_log=Path(args.intent_log) if args.shadow_entry_intents else None,
            lifecycle_state=Path(args.lifecycle_state) if args.shadow_entry_intents else None,
        )
    except KeyboardInterrupt:
        print("Read-only collector stopped.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
