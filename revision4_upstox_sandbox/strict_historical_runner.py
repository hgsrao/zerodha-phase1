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
from revision4.research_target import SealedRunEvaluation, BenchmarkConfig

def run_strict_replay():
    manifest_path = str(PROJECT_ROOT / "revision2" / "DATASET_MANIFEST_48SYMBOL_1MIN.json")
    data_dir = os.environ.get(
        "NSE_DATA_DIR",
        "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824"
    )

    print("=== INITIALIZING STRICT REVISION 4 ENGINE ===")
    loader = ManifestDataLoader(manifest_path, data_dir)
    symbols = [f['symbol'] for f in loader.manifest['files']]
    
    start_date = "2024-08-01"
    end_date = "2024-08-31"
    all_bars = {}
    warmup_bars_by_symbol = {}

    warmup_start = (pd.Timestamp(start_date, tz='UTC') - pd.DateOffset(days=60)).strftime('%Y-%m-%d')
    warmup_end = (pd.Timestamp(start_date, tz='UTC') - pd.DateOffset(days=1)).strftime('%Y-%m-%d')

    for sym in symbols:
        # STRICT RULE: No silent exception skipping. Let it crash if data is corrupt.
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
            raise ValueError(f"CRITICAL: Insufficient warmup data for {sym}. Halting.")

        bars = loader.get_bars_for_month(sym, start_date, end_date)
        if not bars:
            raise ValueError(f"CRITICAL: Missing historical data for {sym}. Halting.")
        all_bars[sym] = bars

    print(f"✓ STRICT: Successfully loaded complete, uncorrupted data for all {len(symbols)} symbols.")

    config = EffectiveConfig()
    
    # STRICT RULE: No bypass wrappers.
    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=build_candidate_provider(config, warmup_bars_by_symbol),
        exit_provider=build_exit_provider(config),
        gate_evaluator=ProperGateEvaluator(config),
    )

    print("\n=== RUNNING STRICT ORCHESTRATION ===")
    result = orchestrator.run(all_bars)

    print("\n=== APPLYING EOD FLATTENING ===")
    final_bars = {sym: bars[-1] for sym, bars in all_bars.items() if bars}
    
    for pos_symbol, position in list(orchestrator.ledger.positions.items()):
        if pos_symbol in final_bars:
            bar = final_bars[pos_symbol]
            exit_price = bar.close
            exit_side = "SELL" if position.direction == 1 else "BUY"
            exit_cost = calculate_transaction_cost(exit_price, int(position.quantity), exit_side)
            
            gross_pnl = ((exit_price - position.entry_price) * position.quantity) if position.direction == 1 else ((position.entry_price - exit_price) * position.quantity)
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
            orchestrator.ledger.close_position(exit_event, config)

    # STRICT RULE: Generate the official Sealed Run Evaluation
    print("\n=== GENERATING SEALED RUN REPORT ===")
    eval_result = SealedRunEvaluation.create(
        completed_trades=orchestrator.ledger.completed_trades,
        starting_equity=orchestrator.ledger.starting_cash,
        ending_equity=orchestrator.ledger.marked_equity,
        benchmark=BenchmarkConfig(),
    )
    
    report_path = PROJECT_ROOT / "revision4_upstox_sandbox" / f"sealed_report_{eval_result.run_id}.json"
    with open(report_path, "w") as f:
        f.write(eval_result.model_dump_json(indent=2))

    print(f"✓ Sealed report generated: {report_path.name}")
    print(f"  Total Valid Trades: {eval_result.total_trades}")
    print(f"  Safety Violations Triggered: {eval_result.total_safety_violations}")
    print(f"  Net P&L: Rs {eval_result.total_net_pnl:,.2f}")

if __name__ == "__main__":
    run_strict_replay()
