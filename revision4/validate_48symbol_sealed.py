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
from dataclasses import asdict, replace
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
from revision4.timestamp_orchestrator import TimestampOrchestrator, TimestampReplayResult
from revision4.range_atr_shadow import RangeATRShadowMonitor
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.validate_sunpharma_sealed import _compute_config_hash, _compute_dataset_hash, _parse_rejection_reason


MANIFEST_PATH = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
DATA_DIR = "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824"
MONTH_START = "2024-08-01"
MONTH_END = "2024-08-31"
WARMUP_BARS = 60


def _build_calibration_config(registry: CanonicalParameterRegistry, overrides=None) -> EffectiveConfig:
    """Build one candidate configuration while refusing safety-policy edits."""
    overrides = dict(overrides or {})
    if not overrides:
        return EffectiveConfig()

    invalid = []
    for name, value in overrides.items():
        try:
            spec = registry.get(name)
        except KeyError:
            invalid.append(f"unknown parameter {name}")
            continue
        if not spec.calibratable:
            invalid.append(f"immutable parameter {name}")
            continue
        if not hasattr(EffectiveConfig(), name):
            invalid.append(f"V3 config does not expose {name}")
            continue
        if spec.param_type in ("int", "float") and not (spec.minimum <= value <= spec.maximum):
            invalid.append(f"{name} outside [{spec.minimum}, {spec.maximum}]")
    if invalid:
        raise ValueError("invalid V3 calibration override(s): " + "; ".join(invalid))
    return replace(EffectiveConfig(), **overrides)


def _partition_by_session(bars_by_symbol):
    """Partition symbol streams by their observed market date, preserving order."""
    sessions = defaultdict(lambda: defaultdict(list))
    for symbol, bars in bars_by_symbol.items():
        for bar in bars:
            sessions[pd.Timestamp(bar.timestamp).date().isoformat()][symbol].append(bar)
    return {date: dict(symbol_bars) for date, symbol_bars in sorted(sessions.items())}


def _completed_trade_ledger(completed_trades):
    """Serialize the authoritative ledger without losing intraday evidence."""
    records = []
    for trade in completed_trades:
        entry_timestamp = pd.Timestamp(trade.entry_timestamp).isoformat()
        exit_timestamp = pd.Timestamp(trade.exit_timestamp).isoformat()
        entry_date = pd.Timestamp(trade.entry_timestamp).date().isoformat()
        exit_date = pd.Timestamp(trade.exit_timestamp).date().isoformat()
        reason = getattr(trade.exit_reason, "value", str(trade.exit_reason))
        records.append({
            "trade_id": trade.trade_id,
            "symbol": trade.symbol,
            "side": "LONG" if trade.direction == 1 else "SHORT",
            "direction": trade.direction,
            "entry_timestamp": entry_timestamp,
            "exit_timestamp": exit_timestamp,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "quantity": trade.quantity,
            "entry_cost": trade.entry_cost,
            "exit_cost": trade.exit_cost,
            "gross_pnl": trade.gross_pnl,
            "net_pnl": trade.net_pnl,
            "exit_reason": reason,
            "same_session": entry_date == exit_date,
        })
    return sorted(records, key=lambda record: (
        record["entry_timestamp"], record["exit_timestamp"], record["trade_id"]
    ))


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
                             month_start=MONTH_START, month_end=MONTH_END,
                             calibration_overrides=None):
    """Execute one sealed shared-ledger validation pass; never tune parameters."""
    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    config = _build_calibration_config(registry, calibration_overrides)
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
    candidate_provider = build_candidate_provider(config, warmup_by_symbol)
    ten_box = candidate_provider.ten_box_integration
    orchestrator = TimestampOrchestrator(
        config=config, candidate_provider=candidate_provider,
        exit_provider=build_exit_provider(config, ten_box), ledger=ledger, broker=PaperBroker(),
        gate_evaluator=ProperGateEvaluator(config), gate16_remediator=remediator,
        candidate_observer=shadow_monitor, exit_observer=ten_box.record_exit,
    )
    # A selected range may span many market sessions.  Run those sessions in
    # chronological order against the *same* model, broker and portfolio,
    # then flatten at every session close.  This prevents a monthly replay
    # from silently becoming an overnight strategy.
    session_results = []
    eod_exits = []
    processed_timestamps = 0
    for _session_date, session_bars in _partition_by_session(bars_by_symbol).items():
        session_result = orchestrator.run(session_bars)
        session_results.append(session_result)
        processed_timestamps += session_result.timestamps_processed
        session_last_bars = {symbol: stream[-1] for symbol, stream in session_bars.items()}
        session_eod_exits = _eod_flatten(ledger, session_last_bars, config, processed_timestamps)
        for event in session_eod_exits:
            ten_box.record_exit(event)
        eod_exits.extend(session_eod_exits)

    result = TimestampReplayResult(
        timestamps_processed=sum(item.timestamps_processed for item in session_results),
        bars_processed=sum(item.bars_processed for item in session_results),
        orders_submitted=tuple(order for item in session_results for order in item.orders_submitted),
        fills=tuple(fill for item in session_results for fill in item.fills),
        exits=tuple(exit_event for item in session_results for exit_event in item.exits) + tuple(eod_exits),
        event_log=tuple(event for item in session_results for event in item.event_log),
    )

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
    completed_trade_ledger = _completed_trade_ledger(ledger.completed_trades)
    cross_session_trades = [
        trade for trade in completed_trade_ledger if not trade["same_session"]
    ]
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
        "ten_box_audit": {**ten_box.audit.report(), "performance": ten_box.performance_report()},
        "financials": {
            "starting_equity": ledger.starting_cash, "ending_equity": ledger.cash,
            "realized_pnl": ledger.realized_pnl, "total_costs": ledger.total_costs,
        },
        "daily_pnl_series": daily_pnl,
        "completed_trade_ledger": completed_trade_ledger,
        "intraday_audit": {
            "completed_trade_count": len(completed_trade_ledger),
            "same_session_trade_count": len(completed_trade_ledger) - len(cross_session_trades),
            "cross_session_trade_count": len(cross_session_trades),
            "all_trades_same_session": not cross_session_trades,
            "cross_session_trade_ids": [trade["trade_id"] for trade in cross_session_trades],
        },
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
