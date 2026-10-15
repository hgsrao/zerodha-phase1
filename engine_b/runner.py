"""Build and run one deterministic offline Engine B Nautilus backtest; produce the audit report."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import LoggingConfig, RiskEngineConfig
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money

from engine_b.nautilus_market import INR, NSE, R5LegCostFeeModel, frame_to_bars, nse_equity
from engine_b.orchestrator import EngineBOrchestrator
from engine_b.strategy import R5NautilusStrategy, R5NautilusStrategyConfig
from revision2_external.grid_context import SealedGridContextProvider
from revision5.ccpp_unified_plant import CentralPlantMasterDCS

STARTING_CAPITAL = 1_000_000


def run_engine_b(data: Dict[str, pd.DataFrame], nifty: pd.DataFrame, vix: pd.DataFrame, *, warmup: int = 60,
                 orchestrator_kwargs: Optional[Dict[str, Any]] = None, log_level: str = "ERROR",
                 max_order_quantity: Optional[Dict[str, int]] = None,
                 venue_overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    symbols = sorted(data)
    kwargs = dict(plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
                  telemetry_mode="full", governor_authority="full")
    kwargs.update(orchestrator_kwargs or {})
    plant = CentralPlantMasterDCS(total_capital=STARTING_CAPITAL, db_path=":memory:")
    r5 = EngineBOrchestrator(symbols, grid_context_provider=SealedGridContextProvider(nifty, vix),
                             real_plant_dcs=plant, starting_equity=float(STARTING_CAPITAL), **kwargs)

    engine = BacktestEngine(BacktestEngineConfig(trader_id=TraderId("R5-ENGINEB-001"), risk_engine=RiskEngineConfig(),
                                                 logging=LoggingConfig(log_level=log_level)))
    venue = dict(default_leverage=Decimal(1), fee_model=R5LegCostFeeModel(), fill_model=FillModel(),
                 use_message_queue=False,          # fills reach R5 before its next same-tick candidate
                 use_market_order_acks=True, bar_execution=True)
    venue.update(venue_overrides or {})
    engine.add_venue(NSE, OmsType.NETTING, AccountType.MARGIN, [Money(STARTING_CAPITAL, INR)], base_currency=INR,
                     **venue)
    for symbol in symbols:
        instrument = nse_equity(symbol, (max_order_quantity or {}).get(symbol))
        engine.add_instrument(instrument)
        engine.add_data(frame_to_bars(symbol, data[symbol], instrument))
    strategy = R5NautilusStrategy(R5NautilusStrategyConfig(symbols=tuple(symbols), warmup=warmup), r5)
    engine.add_strategy(strategy)
    engine.run()
    outcome = r5.finish()
    report = build_report(engine, strategy, r5, outcome)
    engine.dispose()
    return report


def build_report(engine, strategy, r5: EngineBOrchestrator, outcome: Dict[str, Any]) -> Dict[str, Any]:
    trader = engine.trader
    fills = trader.generate_order_fills_report()
    positions = trader.generate_positions_report()
    account = engine.portfolio.account(NSE)
    balance = float(account.balance_total(INR).as_double())
    commissions = float(sum(c.as_double() for c in account.commissions().values()))
    nautilus_realized_net = sum(float(str(v).split()[0]) for v in positions["realized_pnl"]) if len(positions) else 0.0
    r5_report = outcome["r5_report"] or {}
    r5_trades = r5.completed_trades
    stats = engine.portfolio.analyzer.get_performance_stats_pnls()
    statuses = r5.engine_b_counts
    open_positions = {s: strategy.net_position(s) for s in strategy.symbols if strategy.net_position(s) != 0}
    return {
        "engine": "ENGINE_B_NAUTILUS_NATIVE",
        "execution": "nautilus SimulatedExchange (BacktestEngine), offline; no live venue, no Kite",
        "symbols": list(strategy.symbols),
        "bars_received": strategy.bars_received,
        "decision_ticks": strategy.ticks_dispatched,
        "max_decision_lag_ns": max(strategy.decision_lag_ns, default=0),
        "orders": {
            "submitted": statuses["event_submit"],
            "entry_submitted": statuses["entry_orders_submitted"],
            "exit_submitted": statuses["exit_orders_submitted"],
            "accepted": statuses["event_accepted"],
            "rejected": statuses["event_rejected"] + statuses["event_denied"],
            "canceled": statuses["event_canceled"],
            "expired": statuses["event_expired"],
            "fill_events": statuses["event_filled"],
            "entry_fills": statuses["entry_fills"],
            "entry_partial_fills": statuses["entry_partial_fills"],
            "exit_partial_fills": statuses["exit_partial_fills"],
            "positions_closed": statuses["positions_closed"],
            "inflight_at_end": len(r5._inflight) + len(r5._working_exits),
            "exit_residuals_parked": statuses["exit_residual_parked"],
        },
        "nautilus_accounting": {
            "starting_balance": STARTING_CAPITAL,
            "ending_balance": balance,
            "net_pnl": balance - STARTING_CAPITAL,
            "realized_net_closed_positions": nautilus_realized_net,
            "gross_realized_closed_positions": strategy.realized_gross_pnl(),
            "commissions": commissions,
            "open_positions": open_positions,
            "performance_stats_pnls": {k: (None if v != v else v) for k, v in stats.items()},
        },
        "r5_ledger": {
            "completed_trades": len(r5_trades),
            "gross_pnl": sum(t["pnl"] for t in r5_trades),
            "costs": sum(t["costs"] for t in r5_trades),
            "net_pnl": sum(t["net_pnl"] for t in r5_trades),
            "open_trades": sorted(r5.open_trades),
            "exit_reasons": dict(pd.Series([t["reason"] for t in r5_trades]).value_counts()) if r5_trades else {},
            "funnel": {k: r5_report.get(k) for k in ("pa_evaluations", "id_approvals", "mpc_plans",
                                                       "safety_approvals", "gates_passed", "orders_submitted",
                                                       "fills", "plant_entry_rejections", "execution_halted")},
        },
        "reconciliation": {
            "checks": r5.reconciliation_checks,
            "breaks": r5.reconciliation_breaks,
            "ledger_gross_error": outcome["ledger_gross_error"],
            "chronology_errors": r5.chronology_errors,
        },
        "option_a_fills": r5.option_a_fills,
        "trades": r5_trades,
        "order_audit": r5.order_audit,
        "nautilus_fills": fills.reset_index().astype(str).to_dict(orient="records") if len(fills) else [],
        "nautilus_positions": positions.reset_index().astype(str).to_dict(orient="records") if len(positions) else [],
    }
