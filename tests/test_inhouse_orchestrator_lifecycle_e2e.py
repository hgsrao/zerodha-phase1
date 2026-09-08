"""Real lifecycle coverage for the in-house multi-symbol orchestrator."""

import hashlib
import json

import pandas as pd

from inhouse_validation.cross_session_rejection import CrossSessionRejectionPolicy
from inhouse_validation.gate16_remediation import Gate16Remediator
from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator
from revision2.contracts import ProposedOrder, TradePlan
from tests.test_revision2_portfolio import _symbol_bars


def _bars():
    return {symbol: _symbol_bars(i + 1, 500) for i, symbol in enumerate(
        ("SYM_A", "SYM_B", "SYM_C", "SYM_D")
    )}


def test_gate16_breach_quarantines_flattens_and_reconciles_through_run():
    bars = _bars()
    orchestrator = Revision2PortfolioOrchestrator(list(bars), starting_equity=1_000_000.0)
    # Any non-identical next-open fill breaches, deterministically exercising
    # the real post-fill path without changing the immutable production rule.
    orchestrator.gate16_remediator = Gate16Remediator(tolerance_pct=0.0)
    orchestrator.cross_session_policy = CrossSessionRejectionPolicy()

    report = orchestrator.run(bars, warmup=40)

    assert report["status"] == "REMEDIATION_REQUIRED"
    assert len(orchestrator.gate16_remediator.violations) == 1
    assert not orchestrator.open_trades
    assert not orchestrator.pending_entries
    kinds = [event["event_type"] for event in report["event_ledger"]]
    assert {"FILL", "GATE16_BREACH", "QUARANTINE_STARTED", "POSITION_FLATTENED", "RECONCILIATION_COMPLETED"} <= set(kinds)


def test_runtime_event_ledger_hash_chain_is_intact():
    bars = _bars()
    orchestrator = Revision2PortfolioOrchestrator(list(bars), starting_equity=1_000_000.0)
    orchestrator.gate16_remediator = Gate16Remediator(tolerance_pct=0.0)
    orchestrator.cross_session_policy = CrossSessionRejectionPolicy()
    report = orchestrator.run(bars, warmup=40)

    prior = "GENESIS"
    for event in report["event_ledger"]:
        material = {"event_type": event["event_type"], "timestamp": event["timestamp"],
                    "payload": event["payload"], "prior_hash": event["prior_hash"]}
        assert event["prior_hash"] == prior
        assert event["record_hash"] == hashlib.sha256(
            json.dumps(material, sort_keys=True, default=str).encode()
        ).hexdigest()
        prior = event["record_hash"]


def test_friday_intent_is_cancelled_on_monday_by_real_run():
    timestamps = pd.date_range("2024-08-05 09:15", periods=62, freq="min", tz="Asia/Kolkata")
    bars = {"SUNPHARMA": pd.DataFrame({
        "timestamp": timestamps, "open": [100.0] * 62, "high": [101.0] * 62,
        "low": [99.0] * 62, "close": [100.0] * 62, "volume": [1_000] * 62,
    })}
    orchestrator = Revision2PortfolioOrchestrator(["SUNPHARMA"], starting_equity=100_000.0)
    orchestrator.cross_session_policy = CrossSessionRejectionPolicy()
    plan = TradePlan("BUY", 100.0, 99.0, 102.0, 1, 10)
    order = ProposedOrder("SUNPHARMA", "BUY", 1, "MARKET", None, 10, 0)
    orchestrator.pending_entries["SUNPHARMA"] = {
        "order": order, "plan": plan, "quantity": 1,
        "decision": type("D", (), {"timing_quality": 0.0})(),
        "signal_timestamp": "2024-08-02T15:29:00+05:30", "fill_bar_idx": 60,
    }
    report = orchestrator.run(bars, warmup=60)
    assert not orchestrator.pending_entries
    cancelled = [e for e in report["event_ledger"] if e["event_type"] == "ORDER_CANCELLED"]
    assert any(e["payload"]["reason"] == "CROSS_SESSION_FILL_REJECTED" for e in cancelled)
