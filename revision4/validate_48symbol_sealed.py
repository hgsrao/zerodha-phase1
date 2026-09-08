"""Strict V3 one-month, shared-portfolio validation for the 48-symbol universe.

This is a validation replay, never an optimizer.  It loads the manifest-admitted
universe, merges all bar streams through TimestampOrchestrator, and produces a
sealed report only after exact portfolio reconciliation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from revision4.box_adapters import build_candidate_provider, build_exit_provider
from revision4.config_access import calculate_transaction_cost
from revision4.contracts import EffectiveConfig, ExitEvent, ExitReason
from revision4.gate16_remediation import Gate16Remediator
from revision4.gates_proper import ProperGateEvaluator
from revision4.paper_broker import PaperBroker
from revision4.portfolio import PortfolioLedger
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.range_atr_shadow import RangeATRShadowMonitor
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.validate_sunpharma_sealed import _compute_config_hash, _compute_dataset_hash, _parse_rejection_reason


MANIFEST_PATH = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
DATA_DIR = "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824"
MONTH_START = "2024-08-01"
MONTH_END = "2024-08-31"
WARMUP_BARS = 60


def _eod_flatten(ledger, last_bars, config, event_index):
    """Close every remaining position at its own final observed close."""
    exits = []
    for symbol, position in sorted(list(ledger.positions.items())):
        bar = last_bars.get(symbol)
        if bar is None:
            raise RuntimeError(f"cannot EOD flatten {symbol}: no final observed bar")
        side = "SELL" if position.direction == 1 else "BUY"
        exit_cost = calculate_transaction_cost(bar.close, position.quantity, side)
        gross = ((bar.close - position.entry_price) if position.direction == 1
                 else (position.entry_price - bar.close)) * position.quantity
        net = gross - position.cost_paid - exit_cost
        event = ExitEvent(
            exit_id=f"eod_flatten_{symbol}_{event_index}", symbol=symbol,
            timestamp_exit=bar.timestamp, bar_index_exit=event_index,
            entry_price=position.entry_price, exit_price=bar.close,
            quantity=position.quantity, direction=position.direction,
            bars_held=position.bars_held(event_index),
            entry_cost_paid=position.cost_paid, exit_cost_paid=exit_cost,
            exit_reason=ExitReason.EOD_FLATTENING, pnl_realized=net,
            pnl_pct=(gross / (position.entry_price * position.quantity) * 100)
            if position.entry_price else 0.0,
        )
        ok, reason = ledger.close_position(event, config)
        if not ok:
            raise RuntimeError(f"EOD flatten rejected for {symbol}: {reason}")
        exits.append(event)
    return exits


def run_48symbol_validation(manifest_path=MANIFEST_PATH, data_dir=DATA_DIR,
                             month_start=MONTH_START, month_end=MONTH_END):
    """Execute one sealed shared-ledger validation pass; never tune parameters."""
    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    config = EffectiveConfig()
    dataset_hash = _compute_dataset_hash(manifest_path, data_dir)
    config_hash = _compute_config_hash(config)
    if dataset_hash.startswith("error:") or config_hash.startswith("error:"):
        raise RuntimeError(f"cannot start sealed validation: {dataset_hash}; {config_hash}")

    loader = ManifestDataLoader(manifest_path, data_dir)
    symbols = sorted(loader.symbol_files)
    if len(symbols) != 48:
        raise RuntimeError(f"sealed validator requires 48 symbols, got {len(symbols)}")

    warmup_start = pd.Timestamp(month_start, tz="UTC") - pd.DateOffset(days=60)
    warmup_end = pd.Timestamp(month_start, tz="UTC") - pd.DateOffset(days=1)
    bars_by_symbol, warmup_by_symbol, last_bars = {}, {}, {}
    for number, symbol in enumerate(symbols, start=1):
        print(f"[LOAD {number:02d}/48] {symbol}", flush=True)
        bars = list(loader.get_bars_for_month(symbol, month_start, month_end))
        history = list(loader.get_bars_for_month(
            symbol, warmup_start.strftime("%Y-%m-%d"), warmup_end.strftime("%Y-%m-%d"),
        ))
        if not bars:
            raise RuntimeError(f"{symbol}: no sealed-month bars")
        if len(history) < WARMUP_BARS:
            raise RuntimeError(f"{symbol}: requires {WARMUP_BARS} warmup bars, got {len(history)}")
        warmup = history[-WARMUP_BARS:]
        warmup_by_symbol[symbol] = pd.DataFrame({
            "timestamp": [bar.timestamp for bar in warmup],
            "open": [bar.open for bar in warmup], "high": [bar.high for bar in warmup],
            "low": [bar.low for bar in warmup], "close": [bar.close for bar in warmup],
            "volume": [bar.volume for bar in warmup],
        })
        bars_by_symbol[symbol] = bars
        last_bars[symbol] = bars[-1]
    print("[RUN] chronological shared-portfolio replay", flush=True)

    ledger = PortfolioLedger(starting_cash=100_000.0)
    run_id = f"portfolio-48-{month_start.replace('-', '')}-{config_hash[:12]}-{uuid.uuid4().hex[:12]}"
    audit_path = f"diagnostic_output/{run_id}_gate16_audit.jsonl"
    remediator = Gate16Remediator(
        config, dataset_hash=dataset_hash, config_hash=config_hash,
        audit_log_path=audit_path, run_id=run_id,
    )
    shadow_monitor = RangeATRShadowMonitor()
    orchestrator = TimestampOrchestrator(
        config=config, candidate_provider=build_candidate_provider(config, warmup_by_symbol),
        exit_provider=build_exit_provider(config), ledger=ledger, broker=PaperBroker(),
        gate_evaluator=ProperGateEvaluator(config), gate16_remediator=remediator,
        candidate_observer=shadow_monitor,
    )
    result = orchestrator.run(bars_by_symbol)
    eod_exits = _eod_flatten(ledger, last_bars, config, result.timestamps_processed)

    daily_pnl = defaultdict(float)
    per_symbol = {symbol: Counter() for symbol in symbols}
    for trade in ledger.completed_trades:
        daily_pnl[pd.Timestamp(trade.exit_timestamp).date().isoformat()] += trade.net_pnl
        per_symbol[trade.symbol]["exits"] += 1
    for order in result.orders_submitted:
        per_symbol[order.symbol]["orders_submitted"] += 1
    for fill in result.fills:
        per_symbol[fill.symbol]["fills"] += 1
    rejection_breakdown = Counter()
    for event in result.event_log:
        reason = _parse_rejection_reason(event)
        if reason:
            rejection_breakdown[reason] += 1
            order_id = event[2].split(":", 1)[0]
            symbol = order_id.split("_", 1)[-1]
            if symbol in per_symbol:
                per_symbol[symbol]["gate_rejections"] += 1

    daily_pnl = dict(sorted(daily_pnl.items()))
    daily_matches_realized = abs(sum(daily_pnl.values()) - ledger.realized_pnl) <= 0.01
    exact = (not ledger.positions and not ledger.pending_orders and ledger.reserved_cash == 0.0
             and daily_matches_realized)
    if remediator.violations:
        remediator.record_reconciliation(
            max(bar.timestamp for bar in last_bars.values()),
            pending_orders=len(ledger.pending_orders), reserved_cash=ledger.reserved_cash,
            open_positions=len(ledger.positions), realized_pnl=ledger.realized_pnl,
            daily_pnl=daily_pnl, daily_pnl_matches_realized=daily_matches_realized, exact=exact,
        )
    status = "REMEDIATION_REQUIRED" if remediator.violations and exact else ("PASSED" if exact else "SHUTDOWN")
    return {
        "run_id": run_id, "timestamp": datetime.now().isoformat(), "status": status,
        "universe": {"symbol_count": len(symbols), "symbols": symbols},
        "period": f"{month_start} to {month_end}", "warmup_bars_per_symbol": WARMUP_BARS,
        "dataset_hash": dataset_hash, "config_hash": config_hash,
        "registry_contract_id": registry.CONTRACT_ID,
        "registry_hash": registry.identity_sha256(),
        "metrics": {
            "timestamps_processed": result.timestamps_processed,
            "bars_processed": result.bars_processed,
            "orders_submitted": len(result.orders_submitted), "fills": len(result.fills),
            "exits": len(ledger.completed_trades), "eod_flattens": len(eod_exits),
        },
        "per_symbol": {symbol: dict(per_symbol[symbol]) for symbol in symbols},
        "rejection_breakdown": dict(rejection_breakdown),
        "range_atr_shadow": shadow_monitor.summary(),
        "financials": {
            "starting_equity": ledger.starting_cash, "ending_equity": ledger.cash,
            "realized_pnl": ledger.realized_pnl, "total_costs": ledger.total_costs,
        },
        "daily_pnl_series": daily_pnl,
        "reconciliation": {
            "pending_orders": len(ledger.pending_orders), "reserved_cash": ledger.reserved_cash,
            "open_positions": len(ledger.positions), "daily_pnl_matches_realized": daily_matches_realized,
            "exact": exact,
        },
        "gate16_remediation": {
            "audit_log_path": audit_path, "violations": [asdict(v) for v in remediator.violations],
            "audit_chain_valid": remediator.verify_chain(),
            "persisted_audit_chain_valid": remediator.verify_persisted_chain() if remediator.violations else None,
        },
    }


if __name__ == "__main__":
    report = run_48symbol_validation()
    path = Path("diagnostic_output/48symbol_august_v3_validation_report.json")
    path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps({
        "status": report["status"], "orders": report["metrics"]["orders_submitted"],
        "fills": report["metrics"]["fills"], "exits": report["metrics"]["exits"],
        "reconciliation": report["reconciliation"]["exact"], "report": str(path),
    }, indent=2))
