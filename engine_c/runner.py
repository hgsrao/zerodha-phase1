"""Build and run one deterministic offline Engine C Backtrader backtest; produce the audit report."""
from __future__ import annotations

from typing import Any, Dict, Optional

import backtrader as bt
import pandas as pd

from engine_c.bt_market import bars_digest, build_broker, exchange_labels, frame_to_feed
from engine_c.config import EngineCConfig
from engine_c.orchestrator import EngineCOrchestrator
from engine_c.strategy import R5BacktraderStrategy
from revision2_external.grid_context import SealedGridContextProvider
from revision5.ccpp_unified_plant import CentralPlantMasterDCS

EXECUTION_MODEL = {
    "engine": "ENGINE_C_BACKTRADER_NATIVE",
    "venue": "backtrader BackBroker (Cerebro, preload=False, runonce=False), offline; no live venue, no Kite",
    "decision_time": "close of the completed 1-minute bar (bar label + 1 minute)",
    "entry_reference_price": "decision bar close (the next open is unknown at decision time)",
    "entry_fill": "Backtrader market order, filled at the OPEN of the symbol's next delivered bar, "
                  "adverse slippage_perc, capped at that bar's high/low",
    "exit_fill": "R5 decides the exit on the completed bar (stop/target touched intrabar, governor, plant, "
                 "time); Backtrader fills a market order at the next bar's OPEN.  No intrabar stop fills.",
    "same_bar_assumption": "none: no order ever fills on the bar that produced its decision",
    "partial_fills": "only with volume_fill_perc (Backtrader FixedBarPerc filler); entry remainder cancelled "
                     "after the first execution, exit remainder keeps working at later bars",
    "end_of_data": "open positions stay open and are reported with Backtrader's unrealized P&L; "
                   "working orders are cancelled natively",
}


def data_identity(data: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    out = {}
    for symbol in sorted(data):
        labels = exchange_labels(data[symbol]["timestamp"])
        out[symbol] = {"bars": int(len(labels)), "first": str(labels.iloc[0]), "last": str(labels.iloc[-1]),
                       "sha256": bars_digest(data[symbol])}
    return out


def run_engine_c(data: Dict[str, pd.DataFrame], nifty: pd.DataFrame, vix: pd.DataFrame, *, warmup: int = 60,
                 config: Optional[EngineCConfig] = None, orchestrator_kwargs: Optional[Dict[str, Any]] = None
                 ) -> Dict[str, Any]:
    config = config or EngineCConfig()
    symbols = sorted(data)
    kwargs = dict(plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
                  telemetry_mode="full", governor_authority="full")
    kwargs.update(orchestrator_kwargs or {})
    # R5's capital normally equals the broker's starting cash; a test may set them apart to
    # exercise Backtrader's native margin rejection.
    kwargs["starting_equity"] = float(kwargs.get("starting_equity", config.starting_cash))
    plant = CentralPlantMasterDCS(total_capital=kwargs["starting_equity"], db_path=":memory:")
    r5 = EngineCOrchestrator(symbols, grid_context_provider=SealedGridContextProvider(nifty, vix),
                             real_plant_dcs=plant, **kwargs)

    cerebro = bt.Cerebro(preload=False, runonce=False, stdstats=False, quicknotify=False)
    cerebro.broker = build_broker(config)
    for symbol in symbols:                       # sorted: deterministic data and tie order
        cerebro.adddata(frame_to_feed(symbol, data[symbol]), name=symbol)
    cerebro.addstrategy(R5BacktraderStrategy, r5=r5, warmup=warmup)
    strategy = cerebro.run()[0]
    outcome = r5.finish()
    return build_report(strategy, r5, outcome, config, data)


def _max_drawdown(curve) -> float:
    peak, worst = None, 0.0
    for _, value in curve:
        peak = value if peak is None else max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def build_report(strategy: R5BacktraderStrategy, r5: EngineCOrchestrator, outcome: Dict[str, Any],
                 config: EngineCConfig, data: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    counts = r5.counts
    r5_report = outcome["r5_report"] or {}
    trades = r5.completed_trades
    final = outcome["final_reconciliation"]
    return {
        "engine": "ENGINE_C_BACKTRADER_NATIVE",
        "execution_model": EXECUTION_MODEL,
        "engine_c_config": {**config.as_dict(), "config_hash": config.config_hash()},
        "backtrader_version": bt.__version__,
        "symbols": list(strategy.symbols),
        "data_identity": data_identity(data),
        "bars_received": strategy.bars_received,
        "warmup_bars": strategy.warmup_bars,
        "decision_ticks": strategy.ticks_dispatched,
        "first_decision_label": strategy.first_decision_label,
        "orders": {
            "submitted": counts["event_submit"],
            "entry_submitted": counts["entry_orders_submitted"],
            "exit_submitted": counts["exit_orders_submitted"],
            "accepted": counts["event_accepted"],
            "rejected": counts["event_rejected"],
            "margin": counts["event_margin"],
            "canceled": counts["event_canceled"],
            "expired": counts["event_expired"],
            "fill_events": counts["event_filled"],
            "entry_fills": counts["entry_fills"],
            "entry_partial_fills": counts["entry_partial_fills"],
            "exit_partial_fill_events": counts["exit_partial_fill_events"],
            "exit_residual_resubmitted": counts["exit_residual_resubmitted"],
            "positions_closed": counts["positions_closed"],
            "entry_capacity_rejections": counts["entry_capacity_rejections"],
            "max_working_orders_per_symbol": r5.max_working_per_symbol,
            "working_at_end": len(strategy.outstanding_at_end),
        },
        "backtrader_accounting": {
            "starting_cash": config.starting_cash,
            "ending_cash": strategy.cash(),
            "ending_value": strategy.value(),
            "net_pnl": strategy.value() - config.starting_cash,
            "realized_gross": strategy.realized_gross_pnl(),
            "commissions": strategy.commissions_total(),
            "unrealized": strategy.unrealized_pnl(),
            "closed_trades": strategy.closed_trades,
            "open_positions": final["open_positions"],
            "max_drawdown_fraction": _max_drawdown(strategy.equity_curve),
        },
        "r5_ledger": {
            "completed_trades": len(trades),
            "gross_pnl": sum(t["pnl"] for t in trades),
            "costs": sum(t["costs"] for t in trades),
            "net_pnl": sum(t["net_pnl"] for t in trades),
            "open_trades": sorted(r5.open_trades),
            "exit_reasons": {str(k): int(v) for k, v in pd.Series([t["reason"] for t in trades]).value_counts().items()} if trades else {},
            "mtm_max_drawdown_fraction": r5_report.get("mtm_max_drawdown_fraction"),
            "funnel": {k: r5_report.get(k) for k in ("pa_evaluations", "id_approvals", "mpc_plans",
                                                       "safety_approvals", "gates_passed", "orders_submitted",
                                                       "fills", "plant_entry_rejections", "portfolio_cap_rejections",
                                                       "execution_halted")},
        },
        "reconciliation": {
            "checks": r5.reconciler.checks,
            "checks_by_context": r5.reconciler.by_context,
            "breaks": r5.reconciler.breaks,
            "ledger_gross_error": outcome["ledger_gross_error"],
            "final": final,
            "chronology_errors": r5.chronology_errors,
        },
        "session_carry_exits": r5.session_carry_exits,
        "decisions": r5.decisions,
        "entry_fills": r5.entry_fills,
        "capacity_reservations": r5.capacity_reservations,
        "trades": trades,
        "order_audit": r5.order_audit,
        "outstanding_orders_at_end": strategy.outstanding_at_end,
        "equity_curve": strategy.equity_curve,
    }
