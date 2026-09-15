"""P0-3B-E non-trading ENTRY_SUBMITTING/ENTRY_UNKNOWN soak harness.

Safety boundary: this module imports only the candidate engine and test fakes.
It does not import the production runner, Kite SDK, credentials, or networking.
"""

from __future__ import annotations

import argparse
import random
import time

from test_v34_entry_submit_restart_recovery import (
    AmbiguousSubmitBroker,
    FakeBroker,
    exact_entry_order,
    make_engine,
    make_state,
)


class AcceptedResponseLostBroker(AmbiguousSubmitBroker):
    def __init__(self, visible_after: int, duplicate: bool = False):
        super().__init__()
        self.visible_after = visible_after
        self.duplicate = duplicate
        self.observations = 0
        self.accepted = False

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        self.accepted = True
        raise TimeoutError("simulated response loss after broker acceptance")

    def get_orders(self):
        if not self.accepted:
            return []
        self.observations += 1
        if self.observations <= self.visible_after:
            return []
        orders = [exact_entry_order("SOAK-ENTRY-1")]
        if self.duplicate:
            orders.append(exact_entry_order("SOAK-ENTRY-2"))
        return orders


class MalformedObservationBroker(AmbiguousSubmitBroker):
    def __init__(self):
        super().__init__()
        self.submitted = False

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        self.submitted = True
        raise TimeoutError("simulated response loss")

    def get_orders(self):
        if not self.submitted:
            return []
        return [{"order_id": "MALFORMED"}]


def restart(engine, broker):
    """Simulate process restart while preserving only durable state."""
    return make_engine(engine.state, broker)


def run_scenario(mode: str, delay: int = 0) -> None:
    if mode == "accepted":
        broker = AcceptedResponseLostBroker(visible_after=delay)
        engine = make_engine(make_state(), broker)
        assert engine.step() == "STATE_CHANGED"
        assert engine.state.status == "ENTRY_UNKNOWN"
        engine = restart(engine, broker)
        for _ in range(delay):
            assert engine.step() == "NO_ACTION"
            assert len(broker.place_order_calls) == 1
            engine = restart(engine, broker)
        assert engine.step() == "STATE_CHANGED"
        assert engine.state.status == "ENTRY_PENDING"
        assert engine.state.active_trade.entry_order_id == "SOAK-ENTRY-1"
        assert len(broker.place_order_calls) == 1
        return

    if mode == "not_accepted":
        broker = AmbiguousSubmitBroker()
        engine = make_engine(make_state(), broker)
        assert engine.step() == "STATE_CHANGED"
        engine = restart(engine, broker)
        for _ in range(3):
            assert engine.step() == "NO_ACTION"
            assert len(broker.place_order_calls) == 1
            engine = restart(engine, broker)
        assert engine.step() == "HALTED"
        assert engine.state.status == "RECONCILIATION_HALT"
        assert len(broker.place_order_calls) == 1
        return

    if mode == "duplicate":
        broker = AcceptedResponseLostBroker(visible_after=0, duplicate=True)
        engine = make_engine(make_state(), broker)
        assert engine.step() == "STATE_CHANGED"
        engine = restart(engine, broker)
        assert engine.step() == "HALTED"
        assert engine.state.status == "RECONCILIATION_HALT"
        assert len(broker.place_order_calls) == 1
        return

    if mode == "malformed":
        broker = MalformedObservationBroker()
        engine = make_engine(make_state(), broker)
        assert engine.step() == "STATE_CHANGED"
        engine = restart(engine, broker)
        assert engine.step() == "HALTED"
        assert engine.state.status == "RECONCILIATION_HALT"
        assert len(broker.place_order_calls) == 1
        return

    raise AssertionError(f"unknown soak mode: {mode}")


def run_campaign(cycles: int, seed: int) -> dict[str, int]:
    rng = random.Random(seed)
    counts = {"accepted": 0, "not_accepted": 0, "duplicate": 0, "malformed": 0}
    modes = tuple(counts)
    for _ in range(cycles):
        mode = rng.choice(modes)
        delay = rng.randint(0, 3) if mode == "accepted" else 0
        run_scenario(mode, delay)
        counts[mode] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=34035)
    parser.add_argument("--duration-minutes", type=float, default=0.0)
    args = parser.parse_args()
    if args.cycles < 0 or args.duration_minutes < 0:
        parser.error("cycles and duration must be non-negative")

    started = time.monotonic()
    counts = run_campaign(args.cycles, args.seed)
    timed_cycles = 0
    deadline = time.monotonic() + (args.duration_minutes * 60)
    while time.monotonic() < deadline:
        batch = run_campaign(100, args.seed + timed_cycles + 1)
        for mode, count in batch.items():
            counts[mode] += count
        timed_cycles += 100

    elapsed = time.monotonic() - started
    total = sum(counts.values())
    print("[PASS] P0-3B-E non-trading staging soak")
    print(f"cycles={total} elapsed_seconds={elapsed:.3f} seed={args.seed}")
    print(" ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    print("duplicate_automatic_submissions=0")
    print("production_runner_imported=False broker_network_used=False")
    print("LIVE_TRADING_ENABLED=False Gate_4=LOCKED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
