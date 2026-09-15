"""Deterministic, offline paper-order simulator for V3.4 observation work.

This is not a production-broker adapter.  It has no Kite import, credentials,
network access, or production state access.  Orders and fills exist only in
memory and in an explicitly named paper telemetry JSON file.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


PAPER_TAG = "V3.4_PAPER_ONLY"


@dataclass
class PaperOrder:
    order_id: str
    symbol: str
    side: str
    quantity: int
    requested_price: str
    status: str
    filled_quantity: int = 0
    average_fill_price: str = "0"
    tag: str = PAPER_TAG


@dataclass
class PaperPosition:
    symbol: str
    quantity: int = 0
    average_price: str = "0"
    realised_pnl: str = "0"


class LocalPaperBroker:
    """In-memory broker with deterministic fill scenarios."""

    def __init__(self, seed: int = 34036):
        self._rng = random.Random(seed)
        self.orders: dict[str, PaperOrder] = {}
        self.positions: dict[str, PaperPosition] = {}

    def submit_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: int,
        reference_price: Decimal,
        scenario: str = "COMPLETE",
    ) -> PaperOrder:
        symbol = symbol.strip().upper()
        side = side.strip().upper()
        scenario = scenario.strip().upper()
        if not symbol or side not in {"BUY", "SELL"}:
            raise ValueError("invalid paper order symbol or side")
        if quantity <= 0 or reference_price <= 0:
            raise ValueError("paper quantity and price must be positive")
        if scenario not in {"COMPLETE", "PARTIAL", "REJECTED", "OPEN"}:
            raise ValueError("unsupported paper fill scenario")

        order = PaperOrder(
            order_id=f"PAPER-{uuid4().hex[:12].upper()}",
            symbol=symbol,
            side=side,
            quantity=quantity,
            requested_price=str(reference_price),
            status="OPEN",
        )
        self.orders[order.order_id] = order
        if scenario == "REJECTED":
            order.status = "REJECTED"
            return order
        if scenario == "OPEN":
            return order
        fill_quantity = quantity if scenario == "COMPLETE" else max(1, quantity // 2)
        slippage_bps = Decimal(str(self._rng.choice([-2, -1, 0, 1, 2])))
        direction = Decimal("1") if side == "BUY" else Decimal("-1")
        fill_price = reference_price * (
            Decimal("1") + direction * slippage_bps / Decimal("10000")
        )
        fill_price = fill_price.quantize(Decimal("0.01"))
        self._apply_fill(symbol, side, fill_quantity, fill_price)
        order.filled_quantity = fill_quantity
        order.average_fill_price = str(fill_price)
        order.status = "COMPLETE" if fill_quantity == quantity else "PARTIAL"
        return order

    def _apply_fill(self, symbol: str, side: str, quantity: int, price: Decimal) -> None:
        position = self.positions.setdefault(symbol, PaperPosition(symbol=symbol))
        current_qty = position.quantity
        current_avg = Decimal(position.average_price)
        signed_fill = quantity if side == "BUY" else -quantity

        if current_qty == 0 or (current_qty > 0) == (signed_fill > 0):
            new_qty = current_qty + signed_fill
            weighted = current_avg * abs(current_qty) + price * quantity
            position.quantity = new_qty
            position.average_price = str((weighted / abs(new_qty)).quantize(Decimal("0.01")))
            return

        closing = min(abs(current_qty), quantity)
        pnl_per_unit = price - current_avg if current_qty > 0 else current_avg - price
        realised = Decimal(position.realised_pnl) + pnl_per_unit * closing
        new_qty = current_qty + signed_fill
        position.quantity = new_qty
        position.realised_pnl = str(realised.quantize(Decimal("0.01")))
        if new_qty == 0:
            position.average_price = "0"
        elif (current_qty > 0) != (new_qty > 0):
            position.average_price = str(price)

    def mark_to_market(self, prices: Mapping[str, Any]) -> dict[str, Any]:
        unrealised = Decimal("0")
        realised = Decimal("0")
        rows = []
        for symbol, position in sorted(self.positions.items()):
            ltp = Decimal(str(prices.get(symbol, position.average_price)))
            average = Decimal(position.average_price)
            quantity = position.quantity
            mtm = (ltp - average) * quantity
            unrealised += mtm
            realised += Decimal(position.realised_pnl)
            rows.append({
                **asdict(position),
                "ltp": str(ltp),
                "unrealised_pnl": str(mtm.quantize(Decimal("0.01"))),
            })
        return {
            "positions": rows,
            "realised_pnl": str(realised.quantize(Decimal("0.01"))),
            "unrealised_pnl": str(unrealised.quantize(Decimal("0.01"))),
            "net_pnl": str((realised + unrealised).quantize(Decimal("0.01"))),
        }

    def telemetry(self, prices: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "mode": "LOCAL_PAPER_ONLY",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "safety": {
                "kite_imported": False,
                "broker_network_used": False,
                "production_state_used": False,
                "live_order_possible": False,
                "live_trading_enabled": False,
                "gate_4": "LOCKED",
            },
            "orders": [asdict(order) for order in self.orders.values()],
            **self.mark_to_market(prices),
        }


def write_paper_telemetry(path: Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    forbidden = {"bot_state_v34.json", "bot_state_v34.lock", "shadow_strategy_telemetry.json"}
    if target.name.lower() in forbidden:
        raise ValueError("refusing to overwrite production or shadow telemetry")
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, target)


def demo(output: Path, seed: int) -> dict[str, Any]:
    broker = LocalPaperBroker(seed=seed)
    broker.submit_order(
        symbol="RELIANCE", side="BUY", quantity=10,
        reference_price=Decimal("1500"), scenario="COMPLETE",
    )
    broker.submit_order(
        symbol="INFY", side="BUY", quantity=6,
        reference_price=Decimal("1800"), scenario="PARTIAL",
    )
    broker.submit_order(
        symbol="TCS", side="BUY", quantity=2,
        reference_price=Decimal("4000"), scenario="REJECTED",
    )
    payload = broker.telemetry({"RELIANCE": "1510", "INFY": "1795"})
    write_paper_telemetry(output, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline V3.4 paper-trading simulator")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--seed", type=int, default=34036)
    parser.add_argument("--output", default="paper_trading_telemetry.json")
    args = parser.parse_args()
    if not args.demo:
        print("[BLOCK] Specify --demo; no implicit paper order is generated.")
        return 2
    payload = demo(Path(args.output), args.seed)
    print("[PASS] Local paper-trading simulation completed")
    print(f"orders={len(payload['orders'])} net_pnl={payload['net_pnl']}")
    print(f"output={Path(args.output).resolve()}")
    print("broker_network_used=False live_order_possible=False Gate_4=LOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
