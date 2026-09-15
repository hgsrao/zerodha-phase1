"""Recorded-data to Upstox Sandbox integration harness.

This module is intentionally separate from the Zerodha production runner.  It
defaults to dry-run, caps quantity at one, accepts only sandbox.upstox.com, and
requires an explicit confirmation phrase before any network operation.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


SANDBOX_ROOT = "https://sandbox.upstox.com"
EXECUTION_CONFIRMATION = "UPSTOX_SANDBOX_ONLY"
TOKEN_ENV = "UPSTOX_SANDBOX_TOKEN"


class SandboxSafetyError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_recorded_decision(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("mode") != "READ_ONLY_SHADOW":
        raise SandboxSafetyError("recording is not marked READ_ONLY_SHADOW")
    candidate = payload.get("candidate")
    if not isinstance(candidate, dict):
        raise SandboxSafetyError("recording has no candidate")
    symbol = str(candidate.get("symbol", "")).strip().upper()
    price = Decimal(str(candidate.get("last_price", "0")))
    if not symbol or not price.is_finite() or price <= 0:
        raise SandboxSafetyError("recorded candidate is invalid")
    return {
        "source": str(payload.get("source", "RECORDED")),
        "observed_at_utc": payload.get("observed_at_utc"),
        "decision": str(payload.get("decision", "BLOCK")).upper(),
        "symbol": symbol,
        "reference_price": price,
    }


def build_sandbox_order(
    recording: Mapping[str, Any], *, instrument_token: str,
    simulate_authorized: bool = False,
) -> dict[str, Any] | None:
    decision = str(recording["decision"]).upper()
    if decision != "ENTER" and not simulate_authorized:
        return None
    token = str(instrument_token).strip()
    if not token.startswith("NSE_EQ|") or len(token) <= len("NSE_EQ|"):
        raise SandboxSafetyError("an explicit NSE_EQ Upstox instrument token is required")
    price = Decimal(str(recording["reference_price"])).quantize(Decimal("0.05"))
    return {
        "quantity": 1,
        "product": "D",
        "validity": "DAY",
        "price": float(price),
        "tag": "V34_SBX_ENTRY",
        "instrument_token": token,
        "order_type": "LIMIT",
        "transaction_type": "BUY",
        "disclosed_quantity": 0,
        "trigger_price": 0,
        "is_amo": False,
        "slice": False,
        "market_protection": 0,
    }


@dataclass
class UpstoxSandboxClient:
    access_token: str
    transport: Any
    root: str = SANDBOX_ROOT

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlparse(self.root)
        if parsed.scheme != "https" or parsed.hostname != "sandbox.upstox.com":
            raise SandboxSafetyError("refusing non-Upstox-sandbox endpoint")
        if not self.access_token.strip():
            raise SandboxSafetyError("sandbox token is required")

    def _call(self, method: str, path: str, payload: Mapping[str, Any] | None = None):
        url = self.root + path
        body = None if payload is None else json.dumps(dict(payload)).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
        }
        return self.transport(method, url, headers, body)

    def place(self, payload: Mapping[str, Any]):
        if int(payload.get("quantity", 0)) != 1:
            raise SandboxSafetyError("sandbox quantity must be exactly one")
        return self._call("POST", "/v3/order/place", payload)

    def modify(self, order_id: str, price: Decimal):
        return self._call("PUT", "/v3/order/modify", {
            "quantity": 1, "validity": "DAY", "price": float(price),
            "order_id": str(order_id), "order_type": "LIMIT",
            "disclosed_quantity": 0, "trigger_price": 0,
            "market_protection": 0,
        })

    def cancel(self, order_id: str):
        query = urllib.parse.urlencode({"order_id": str(order_id)})
        return self._call("DELETE", f"/v3/order/cancel?{query}")


def urllib_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None):
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Upstox sandbox request failed: {exc}") from exc
    if not isinstance(result, dict) or result.get("status") != "success":
        raise RuntimeError(f"Upstox sandbox rejected request: {result!r}")
    return result


def run(
    *, recording_path: Path, production_state_path: Path, runner_path: Path,
    instrument_token: str, execute: bool, confirmation: str,
    simulate_authorized: bool, transport=urllib_transport,
) -> dict[str, Any]:
    runner_source = runner_path.read_text(encoding="utf-8")
    if "LIVE_TRADING_ENABLED = False" not in runner_source:
        raise SandboxSafetyError("Zerodha production execution lock is absent")
    state_before = _sha256(production_state_path)
    recording = load_recorded_decision(recording_path)
    order = build_sandbox_order(
        recording, instrument_token=instrument_token,
        simulate_authorized=simulate_authorized,
    )
    report: dict[str, Any] = {
        "mode": "UPSTOX_SANDBOX_DRY_RUN" if not execute else "UPSTOX_SANDBOX_EXECUTED",
        "recording": {**recording, "reference_price": str(recording["reference_price"])},
        "simulation_override": bool(simulate_authorized),
        "order_generated": order is not None,
        "order_payload": order,
        "network_calls": 0,
        "zerodha_runner_started": False,
        "zerodha_live_trading_enabled": False,
    }
    if execute:
        if confirmation != EXECUTION_CONFIRMATION:
            raise SandboxSafetyError("exact sandbox execution confirmation is required")
        if order is None:
            raise SandboxSafetyError("recorded strategy produced no order")
        token = os.environ.get(TOKEN_ENV, "")
        client = UpstoxSandboxClient(token, transport)
        placed = client.place(order)
        report["network_calls"] += 1
        order_id = str((placed.get("data") or {}).get("order_id", ""))
        if not order_id:
            raise RuntimeError("sandbox place response has no order_id")
        modified_price = Decimal(str(order["price"])) - Decimal("0.05")
        report["place_response"] = placed
        report["modify_response"] = client.modify(order_id, modified_price)
        report["network_calls"] += 1
        report["cancel_response"] = client.cancel(order_id)
        report["network_calls"] += 1
    report["production_state_unchanged"] = state_before == _sha256(production_state_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Recorded-data Upstox sandbox integration")
    parser.add_argument("--recording", default="shadow_strategy_telemetry.json")
    parser.add_argument("--instrument-token", default="")
    parser.add_argument("--simulate-authorized", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--output", default="upstox_sandbox_integration_report.json")
    args = parser.parse_args()
    if args.execute and not os.environ.get(TOKEN_ENV):
        token = getpass.getpass("Paste Upstox sandbox token (input hidden): ").strip()
        if not token:
            raise SandboxSafetyError("sandbox token is required")
        os.environ[TOKEN_ENV] = token
    base = Path(__file__).resolve().parent
    report = run(
        recording_path=base / args.recording,
        production_state_path=base / "bot_state_v34.json",
        runner_path=base / "run_production_p01d_candidate.py",
        instrument_token=args.instrument_token,
        execute=args.execute, confirmation=args.confirm,
        simulate_authorized=args.simulate_authorized,
    )
    output = base / args.output
    if output.name in {"bot_state_v34.json", "shadow_strategy_telemetry.json"}:
        raise SandboxSafetyError("refusing protected output path")
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
