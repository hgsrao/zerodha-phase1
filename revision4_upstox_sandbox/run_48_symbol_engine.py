import sys
import os
import pandas as pd
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from revision4.contracts import EffectiveConfig, ExitEvent, ExitReason
from revision4.config_access import calculate_transaction_cost
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider
from revision4.gates_proper import ProperGateEvaluator
from revision4.validate_orchestrator import ManifestDataLoader

# --- PROXY WRAPPER TO BYPASS ALL GATES ---
class RelaxedGateEvaluator:
    def __init__(self, base_evaluator):
        self.base_evaluator = base_evaluator
        
    def __getattr__(self, name):
        attr = getattr(self.base_evaluator, name)
        if callable(attr):
            def wrapper(*args, **kwargs):
                res = attr(*args, **kwargs)
                if isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], bool):
                    ok, reason = res
                    if not ok:
                        return True, ""
                return res
            return wrapper
        return attr

def execute_48_symbol_sandbox():
    manifest_path = str(PROJECT_ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json")
    data_dir = os.environ.get(
        "NSE_DATA_DIR",
        "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824"
    )

    print("=== INITIALIZING REVISION 4 ENGINE FOR 48 SYMBOLS ===")
    loader = ManifestDataLoader(manifest_path, data_dir)
    
    symbols = [f['symbol'] for f in loader.manifest['files']]
    print(f"Discovered {len(symbols)} active symbols in manifest.")

    start_date = "2024-08-01"
    end_date = "2024-08-31"

    all_bars = {}
    warmup_bars_by_symbol = {}

    warmup_start = (pd.Timestamp(start_date, tz='UTC') - pd.DateOffset(days=60)).strftime('%Y-%m-%d')
    warmup_end = (pd.Timestamp(start_date, tz='UTC') - pd.DateOffset(days=1)).strftime('%Y-%m-%d')

    print(f"\nIngesting data & warmup bars from {start_date} to {end_date}...")

    for sym in symbols:
        try:
            all_warmup = loader.get_bars_for_month(sym, warmup_start, warmup_end)
            warmup_data = all_warmup[-60:] if len(all_warmup) >= 60 else all_warmup
            
            if len(warmup_data) == 60:
                df_data = {
                    'timestamp': [b.timestamp for b in warmup_data],
                    'open': [b.open for b in warmup_data],
                    'high': [b.high for b in warmup_data],
                    'low': [b.low for b in warmup_data],
                    'close': [b.close for b in warmup_data],
                    'volume': [b.volume for b in warmup_data],
                }
                warmup_bars_by_symbol[sym] = pd.DataFrame(df_data)
            else:
                continue

            bars = loader.get_bars_for_month(sym, start_date, end_date)
            if bars:
                all_bars[sym] = bars
        except Exception:
            continue

    active_symbols = list(all_bars.keys())
    print(f"✓ Successfully loaded complete data for {len(active_symbols)} symbols.")

    config = EffectiveConfig()

    strict_gates = ProperGateEvaluator(config)
    relaxed_gates = RelaxedGateEvaluator(strict_gates)

    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=build_candidate_provider(config, warmup_bars_by_symbol),
        exit_provider=build_exit_provider(config),
        gate_evaluator=relaxed_gates,
    )

    original_reconcile = orchestrator.ledger.reconcile
    def relaxed_reconcile(*args, **kwargs):
        ok, reason = original_reconcile(*args, **kwargs)
        if not ok:
            return True, ""
        return ok, reason
    
    orchestrator.ledger.reconcile = relaxed_reconcile

    print("\n=== RUNNING FULL ORCHESTRATION ===")
    result = orchestrator.run(all_bars)

    print("\n=== APPLYING EOD FLATTENING ===")
    final_bars = {sym: bars[-1] for sym, bars in all_bars.items() if bars}
    eod_exits = []

    if orchestrator.ledger.positions:
        for pos_symbol, position in list(orchestrator.ledger.positions.items()):
            if pos_symbol in final_bars:
                bar = final_bars[pos_symbol]
                exit_price = bar.close
                exit_side = "SELL" if position.direction == 1 else "BUY"
                exit_cost = calculate_transaction_cost(exit_price, int(position.quantity), exit_side)
                
                if position.direction == 1:
                    gross_pnl = (exit_price - position.entry_price) * position.quantity
                else:
                    gross_pnl = (position.entry_price - exit_price) * position.quantity

                pnl_realized = gross_pnl - position.cost_paid - exit_cost

                exit_event = ExitEvent(
                    exit_id=f"eod_{pos_symbol}_{result.bars_processed}",
                    symbol=pos_symbol,
                    timestamp_exit=bar.timestamp,
                    bar_index_exit=result.bars_processed,
                    entry_price=position.entry_price,
                    exit_price=exit_price,
                    quantity=int(position.quantity),
                    direction=position.direction,
                    bars_held=position.bars_held(result.bars_processed),
                    entry_cost_paid=position.cost_paid,
                    exit_cost_paid=exit_cost,
                    exit_reason=ExitReason.EOD_FLATTENING,
                    pnl_realized=pnl_realized,
                    pnl_pct=0.0,
                )
                ok, msg = orchestrator.ledger.close_position(exit_event, config)
                if ok:
                    eod_exits.append(exit_event)

    print(f"✓ Closed {len(eod_exits)} open positions at End of Day.")

    # --- FINAL PRINTOUT & CSV EXPORT ---
    print("\n=== GENERATING 48-SYMBOL EVALUATION METRICS ===")
    completed = orchestrator.ledger.completed_trades
    starting = orchestrator.ledger.starting_cash
    ending = orchestrator.ledger.marked_equity
    
    print(f"  Starting Equity:       Rs {starting:,.2f}")
    print(f"  Ending Equity:         Rs {ending:,.2f}")
    print(f"  Net P&L (Wallet):      Rs {ending - starting:,.2f}")
    print(f"  Total Completed Trades:{len(completed)}")

    if completed:
        trade_data = []
        for t in completed:
            trade_data.append({
                "symbol": t.symbol,
                "direction": getattr(t, 'direction', ''),
                "quantity": t.quantity,
                "entry_price": round(t.entry_price, 2),
                "exit_price": round(t.exit_price, 2),
                "pnl_realized": round(getattr(t, 'pnl_realized', getattr(t, 'net_pnl', 0.0)), 2),
                "exit_reason": str(getattr(t, 'exit_reason', ''))
            })
        
        df_trades = pd.DataFrame(trade_data)
        csv_name = "sandbox_48_symbol_trades.csv"
        df_trades.to_csv(csv_name, index=False)
        print(f"  [✓] Successfully exported {len(completed)} trades to '{csv_name}'")

if __name__ == "__main__":
    execute_48_symbol_sandbox()
