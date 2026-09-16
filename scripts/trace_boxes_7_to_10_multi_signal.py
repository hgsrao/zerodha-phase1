#!/usr/bin/env python3
"""Passes several real inputs through Boxes 7-10 of the external engine and
prints exactly what each one produces -- real code, real signals, not a
re-derivation. Box 6 is deliberately skipped (already closed-loop, tuned,
and verified this session).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from canonical_parameter_registry import CanonicalParameterRegistry
from gates_framework import EntrySignal, SystemState
from revision2.boxes import P01DBox, SafetyGatesTargetBox
from revision2.contracts import EffectiveConfig, SafetyContract, TradePlan
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision2_external.position_sizing_pyportfolioopt import PyPortfolioOptPositionManagerBox, compute_portfolio_weights
from runtime.operating_mode import PaperBrokerAdapter
from datetime import datetime


def main():
    registry = CanonicalParameterRegistry()
    values = {name: spec.default for name, spec in registry.params.items()}
    config = EffectiveConfig.build(values, registry_hash=registry.FROZEN_IDENTITY_SHA256)
    safety_contract = SafetyContract.from_registry(registry)

    plan = TradePlan(side="BUY", entry_price=1000.0, stop_price=990.0, target_price=1020.0,
                      minimum_hold_bars=2, maximum_hold_bars=60)

    print("=" * 100)
    print("BOX 7 -- SafetyGates (target-surface pre/post-sizing)")
    print("=" * 100)
    sg = SafetyGatesTargetBox()
    for peak, current in [(1_000_000, 1_000_000), (1_000_000, 850_000), (1_000_000, 700_000)]:
        equity_curve = [peak, current]
        approved, reason, size_mult, _ = sg.evaluate_pre_sizing(equity_curve, config)
        dd = (peak - current) / peak
        print(f"drawdown={dd:.1%} -> pre_sizing approved={approved}  size_mult={size_mult:.2f}  reason={reason!r}")

    for qty, target in [(10, 1020.0), (100, 1002.0), (100, 1020.0)]:
        p = TradePlan(side="BUY", entry_price=1000.0, stop_price=999.0, target_price=target,
                       minimum_hold_bars=2, maximum_hold_bars=60)
        ok, reason, _ = sg.evaluate_post_sizing([1_000_000], p, qty, config)
        print(f"qty={qty}, target={target} -> post_sizing approved={ok}  reason={reason!r}")

    print()
    print("=" * 100)
    print("BOX 7b -- EntryDecisionEngine (18-gate), 3 real different SystemStates")
    print("=" * 100)
    # Reuses the orchestrator's own real gate-config builder (no data
    # needed to construct it) rather than re-deriving SafetyGateConfig by
    # hand, which risks silently drifting from what's actually wired.
    orch = Revision2ExternalEngineOrchestrator(["INFY"], registry, starting_equity=1_000_000.0)
    entry_signal = EntrySignal(symbol="INFY", entry_price=1000.0, stop_loss_price=990.0, profit_target_price=1020.0,
                                confidence=0.6, suggested_quantity=100, position_notional=100_000.0, risk_reward_ratio=2.0)
    scenarios = [
        ("healthy", SystemState(portfolio_value=1_000_000, current_dd_percent=0.0, current_lambda=0.1,
                                  daily_realized_loss=0.0, daily_unrealized_loss=0.0, open_positions_count=0, open_positions=[],
                                  market_data_age_seconds=0, broker_connected=True, broker_offline_seconds=0,
                                  kill_switch_active=False, circuit_breaker_triggered=False)),
        ("near drawdown halt", SystemState(portfolio_value=1_000_000, current_dd_percent=0.24, current_lambda=0.1,
                                  daily_realized_loss=0.0, daily_unrealized_loss=0.0, open_positions_count=0, open_positions=[],
                                  market_data_age_seconds=0, broker_connected=True, broker_offline_seconds=0,
                                  kill_switch_active=False, circuit_breaker_triggered=False)),
        ("kill switch active", SystemState(portfolio_value=1_000_000, current_dd_percent=0.0, current_lambda=0.1,
                                  daily_realized_loss=0.0, daily_unrealized_loss=0.0, open_positions_count=0, open_positions=[],
                                  market_data_age_seconds=0, broker_connected=True, broker_offline_seconds=0,
                                  kill_switch_active=True, circuit_breaker_triggered=False)),
    ]
    for label, state in scenarios:
        result = orch.entry_decision_engine.evaluate(
            state, signal=entry_signal, current_time=datetime(2024, 1, 2, 10, 0), proposed_quantity=100,
            target_price=1000.0, fill_price=1000.0, expected_qty=100, actual_qty=100, symbol="INFY",
            seen_recent=False, proposed_notional=100_000.0,
        )
        print(f"{label}: passed={result['passed']}  gate={result.get('gate')}  reason={result.get('reason')}")

    print()
    print("=" * 100)
    print("BOX 8 -- PositionManager (PyPortfolioOpt)")
    print("=" * 100)
    pm = PyPortfolioOptPositionManagerBox()
    for equity, size_mult, weight in [(1_000_000, 1.0, 0.5), (1_000_000, 0.5, 0.5), (500_000, 1.0, 0.1)]:
        qty, _ = pm.size(plan, equity, size_mult, config, symbol="INFY",
                          portfolio_weights={"INFY": weight}, max_exposure_per_symbol_fraction=0.15)
        print(f"equity={equity}, size_mult={size_mult}, portfolio_weight={weight} -> quantity={qty}")

    print()
    print("real PyPortfolioOpt reweighting across 3 different price histories (a genuine feedback mechanism):")
    import pandas as pd
    import numpy as np
    idx = pd.date_range("2024-01-01", periods=60, freq="min")
    rng = np.random.default_rng(0)
    trending_up = pd.Series(1000 * (1.0007 ** np.arange(60)) * (1 + rng.normal(0, 0.001, 60)), index=idx)
    trending_down = pd.Series(1000 * (0.9993 ** np.arange(60)) * (1 + rng.normal(0, 0.001, 60)), index=idx)
    flat = pd.Series(1000 * (1 + rng.normal(0, 0.0005, 60)), index=idx)
    for label, prices_a in [("A trending UP, B flat", trending_up), ("A trending DOWN, B flat", trending_down), ("A flat, B flat", flat)]:
        weights = compute_portfolio_weights({"A": prices_a, "B": flat})
        print(f"  {label} -> weights: {({k: round(v, 3) for k, v in weights.items()})}")

    print()
    print("=" * 100)
    print("BOX 9 -- P01D (order authorization)")
    print("=" * 100)
    p01d = P01DBox()
    for qty in [0, 10, 100]:
        order, _ = p01d.create_order("INFY", plan, qty, config)
        print(f"quantity={qty} -> order={order}")

    print()
    print("=" * 100)
    print("BOX 10 -- UnifiedExecution (PaperBrokerAdapter, the one actually used in every backtest this session)")
    print("=" * 100)
    broker = PaperBrokerAdapter(account_id="TRACE-DEMO")
    for side, qty, price in [("BUY", 100, 1000.0), ("SELL", 100, 1005.0), ("BUY", 50, 998.0)]:
        fill = broker.place_order(symbol="INFY", side=side, quantity=qty, order_type="MARKET",
                                    market_price=price, config=safety_contract.as_dict(), parameter_registry=registry)
        print(f"{side} {qty} @ requested {price} -> filled_price={fill.get('filled_price')}  passed={fill.get('passed')}  realized_pnl={fill.get('realized_pnl')}")
    print()
    print("Live Kite adapter's real feedback mechanism (not exercised here -- no live broker connection):")
    print("  tenacity retries ONLY on NetworkException (transient connectivity failure), never on a real")
    print("  order rejection (KiteException) -- already verified this session with a mocked KiteConnect client.")


if __name__ == "__main__":
    main()
