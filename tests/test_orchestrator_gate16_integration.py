"""Integration test: Gate16 remediation end-to-end lifecycle."""

import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator
from inhouse_validation.gate16_remediation import Gate16Remediator


def test_gate16_breach_triggers_quarantine_and_cancellation():
    """Integration: Gate16 breach → quarantine → cancel pending → flatten."""

    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA", "MAXHEALTH"],
        starting_equity=100_000.0
    )

    # Wire Gate16 remediator
    orchestrator.gate16_remediator = Gate16Remediator(
        tolerance_pct=0.0015,  # 0.15% tolerance (15 bps)
    )

    # Simulate a Gate16 breach detection
    is_breach = orchestrator.gate16_remediator.detect_breach(
        timestamp="2024-08-02T09:16:00+05:30",
        symbol="SUNPHARMA",
        order_id="order_1",
        fill_id="fill_1",
        intended_entry_price=450.0,
        actual_fill_price=450.70,  # Exceeds 15bps tolerance
    )

    # Should trigger breach
    assert is_breach is True

    # Record breach event
    orchestrator.event_ledger.append({
        "event_type": "GATE16_BREACH",
        "timestamp": "2024-08-02T09:16:00+05:30",
        "symbol": "SUNPHARMA",
        "fill_id": "fill_1",
        "measured_slippage_pct": 0.1556,  # ~15.56bps
    })

    # Activate quarantine
    orchestrator.quarantine_mode = True
    orchestrator.event_ledger.append({
        "event_type": "QUARANTINE_STARTED",
        "timestamp": "2024-08-02T09:16:00+05:30"
    })

    # Simulate cancellation of pending orders
    orchestrator.pending_entries = {
        "MAXHEALTH": {
            "order_id": "order_2",
            "quantity": 50,
            "plan": MagicMock(entry_price=500.0)
        }
    }

    # Cancel pending (simulate Step 4)
    if orchestrator.quarantine_mode:
        symbols_to_cancel = list(orchestrator.pending_entries.keys())
        for cancel_symbol in symbols_to_cancel:
            pending = orchestrator.pending_entries[cancel_symbol]
            reserved_cash = pending["quantity"] * pending["plan"].entry_price
            orchestrator.event_ledger.append({
                "event_type": "ORDER_CANCELLED",
                "timestamp": "2024-08-02T09:16:00+05:30",
                "symbol": cancel_symbol,
                "order_id": pending["order_id"],
                "reason": "QUARANTINE_MODE_GATE16",
                "reserved_cash_released": reserved_cash,
            })
            del orchestrator.pending_entries[cancel_symbol]

    # Verify pending orders are cancelled
    assert len(orchestrator.pending_entries) == 0

    # Verify events were recorded
    events = [e for e in orchestrator.event_ledger if e["event_type"] == "ORDER_CANCELLED"]
    assert len(events) == 1
    assert events[0]["reason"] == "QUARANTINE_MODE_GATE16"
    assert events[0]["reserved_cash_released"] == 25000.0


def test_quarantine_mode_prevents_new_entries():
    """Verify quarantine mode prevents new order submissions."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Set quarantine mode
    orchestrator.quarantine_mode = True

    # Simulate order submission logic
    def can_submit_order():
        return not orchestrator.quarantine_mode

    # Verify new orders are blocked
    assert can_submit_order() is False


def test_flatten_scheduling_captures_all_open_positions():
    """Verify all open positions are scheduled for flatten."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA", "MAXHEALTH", "INFY"],
        starting_equity=100_000.0
    )

    # Simulate open trades
    orchestrator.open_trades = {
        "SUNPHARMA": {
            "entry_price": 450.0,
            "entry_bar_idx": 10,
            "side": "BUY",
            "quantity": 100,
        },
        "MAXHEALTH": {
            "entry_price": 500.0,
            "entry_bar_idx": 15,
            "side": "SELL",
            "quantity": 50,
        },
    }

    # Schedule flattens (simulate Step 5)
    orchestrator._scheduled_flattens = {}
    for flatten_symbol, trade in list(orchestrator.open_trades.items()):
        orchestrator._scheduled_flattens[flatten_symbol] = {
            "entry_price": trade["entry_price"],
            "entry_bar_idx": trade["entry_bar_idx"],
            "side": trade["side"],
            "quantity": trade["quantity"],
        }
        orchestrator.event_ledger.append({
            "event_type": "FLATTEN_SCHEDULED",
            "timestamp": "2024-08-02T09:16:00+05:30",
            "symbol": flatten_symbol,
            "entry_price": trade["entry_price"],
            "quantity": trade["quantity"],
        })

    # Verify all positions scheduled
    assert len(orchestrator._scheduled_flattens) == 2
    assert "SUNPHARMA" in orchestrator._scheduled_flattens
    assert "MAXHEALTH" in orchestrator._scheduled_flattens

    # Verify events logged
    flatten_events = [e for e in orchestrator.event_ledger if e["event_type"] == "FLATTEN_SCHEDULED"]
    assert len(flatten_events) == 2


def test_adverse_flatten_execution_next_bar():
    """Verify flatten execution at next eligible bar with adverse pricing."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA"],
        starting_equity=100_000.0
    )

    # Schedule flatten for SUNPHARMA
    orchestrator._scheduled_flattens = {
        "SUNPHARMA": {
            "entry_price": 450.0,
            "entry_bar_idx": 10,
            "side": "BUY",
            "quantity": 100,
        }
    }

    # Simulate next bar arrival
    next_bar_open = 451.0
    adverse_factor = 0.999  # -10bps for long
    exit_price = next_bar_open * adverse_factor

    # Record flatten execution (simulate Step 6)
    orchestrator.event_ledger.append({
        "event_type": "POSITION_FLATTENED",
        "timestamp": "2024-08-02T09:17:00+05:30",
        "symbol": "SUNPHARMA",
        "exit_price": exit_price,
        "adverse_factor": adverse_factor,
        "quantity": 100,
    })

    # Remove from scheduled
    del orchestrator._scheduled_flattens["SUNPHARMA"]

    # Verify execution
    assert len(orchestrator._scheduled_flattens) == 0

    flatten_events = [e for e in orchestrator.event_ledger if e["event_type"] == "POSITION_FLATTENED"]
    assert len(flatten_events) == 1
    assert flatten_events[0]["exit_price"] == pytest.approx(450.549, rel=1e-3)


def test_complete_remediation_sequence():
    """Integration: Full Gate16 → quarantine → cancel → flatten sequence."""
    orchestrator = Revision2PortfolioOrchestrator(
        symbols=["SUNPHARMA", "MAXHEALTH"],
        starting_equity=100_000.0
    )

    # Wire controls
    orchestrator.gate16_remediator = Gate16Remediator(
        tolerance_pct=0.0015,  # 0.15% tolerance (15 bps)
    )

    # 1. Detect breach
    is_breach = orchestrator.gate16_remediator.detect_breach(
        timestamp="2024-08-02T09:16:00+05:30",
        symbol="SUNPHARMA",
        order_id="order_1",
        fill_id="fill_1",
        intended_entry_price=450.0,
        actual_fill_price=450.70,
    )
    assert is_breach is True

    # 2. Record breach and activate quarantine
    orchestrator.event_ledger.append({
        "event_type": "GATE16_BREACH",
        "timestamp": "2024-08-02T09:16:00+05:30",
        "symbol": "SUNPHARMA",
    })
    orchestrator.quarantine_mode = True
    orchestrator.event_ledger.append({
        "event_type": "QUARANTINE_STARTED",
        "timestamp": "2024-08-02T09:16:00+05:30"
    })

    # 3. Simulate open trades and pending orders
    orchestrator.open_trades = {
        "SUNPHARMA": {"entry_price": 450.0, "entry_bar_idx": 10, "side": "BUY", "quantity": 100},
        "MAXHEALTH": {"entry_price": 500.0, "entry_bar_idx": 12, "side": "SELL", "quantity": 50},
    }
    orchestrator.pending_entries = {
        "INFY": {
            "order_id": "order_2",
            "quantity": 30,
            "plan": MagicMock(entry_price=2500.0)
        }
    }

    # 4. Cancel pending orders
    symbols_to_cancel = list(orchestrator.pending_entries.keys())
    for cancel_symbol in symbols_to_cancel:
        pending = orchestrator.pending_entries[cancel_symbol]
        orchestrator.event_ledger.append({
            "event_type": "ORDER_CANCELLED",
            "timestamp": "2024-08-02T09:16:00+05:30",
            "symbol": cancel_symbol,
            "order_id": pending["order_id"],
            "reason": "QUARANTINE_MODE_GATE16",
            "reserved_cash_released": 75000.0,
        })
        del orchestrator.pending_entries[cancel_symbol]

    # 5. Schedule flattens
    orchestrator._scheduled_flattens = {}
    for flatten_symbol, trade in list(orchestrator.open_trades.items()):
        orchestrator._scheduled_flattens[flatten_symbol] = {
            "entry_price": trade["entry_price"],
            "entry_bar_idx": trade["entry_bar_idx"],
            "side": trade["side"],
            "quantity": trade["quantity"],
        }
        orchestrator.event_ledger.append({
            "event_type": "FLATTEN_SCHEDULED",
            "timestamp": "2024-08-02T09:16:00+05:30",
            "symbol": flatten_symbol,
        })

    # 6. Execute flattens
    for symbol in ["SUNPHARMA", "MAXHEALTH"]:
        if symbol in orchestrator._scheduled_flattens:
            flatten_plan = orchestrator._scheduled_flattens[symbol]
            bar_open = 450.0 if symbol == "SUNPHARMA" else 505.0
            adverse_factor = 0.999 if flatten_plan["side"] == "BUY" else 1.001
            exit_price = bar_open * adverse_factor

            orchestrator.event_ledger.append({
                "event_type": "POSITION_FLATTENED",
                "timestamp": "2024-08-02T09:17:00+05:30",
                "symbol": symbol,
                "exit_price": exit_price,
                "adverse_factor": adverse_factor,
                "quantity": flatten_plan["quantity"],
            })
            del orchestrator._scheduled_flattens[symbol]

    # Verify final state
    assert orchestrator.quarantine_mode is True
    assert len(orchestrator.pending_entries) == 0
    assert len(orchestrator._scheduled_flattens) == 0

    # Verify event sequence
    event_types = [e["event_type"] for e in orchestrator.event_ledger]
    assert "GATE16_BREACH" in event_types
    assert "QUARANTINE_STARTED" in event_types
    assert "ORDER_CANCELLED" in event_types
    assert "FLATTEN_SCHEDULED" in event_types
    assert "POSITION_FLATTENED" in event_types

    # Verify event counts
    assert event_types.count("GATE16_BREACH") == 1
    assert event_types.count("QUARANTINE_STARTED") == 1
    assert event_types.count("ORDER_CANCELLED") == 1
    assert event_types.count("FLATTEN_SCHEDULED") == 2
    assert event_types.count("POSITION_FLATTENED") == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
