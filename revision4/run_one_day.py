import os
import json
import traceback
import pandas as pd
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.contracts import EffectiveConfig
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider

def run_one_day_replay(manifest_path: str, data_dir: str, target_date: str = "2023-09-01"):
    print("=" * 80)
    print(f"INITIALIZING OPTIMIZED 1-DAY 48-SYMBOL REPLAY: {target_date}")
    print("=" * 80)

    with open(manifest_path) as f:
        manifest_data = json.load(f)
        symbols = [item["symbol"] for item in manifest_data["files"]]

    loader = ManifestDataLoader(manifest_path, data_dir)
    
    segment_bars = {}
    segment_warmups = {}

    target_ts = pd.Timestamp(target_date, tz="UTC")
    w_start_ts = target_ts - pd.DateOffset(days=60)
    w_end_ts = target_ts - pd.Timedelta(days=1)

    print(f"→ Pre-loading and caching data for {len(symbols)} symbols...")
    for symbol in symbols:
        full_bars = loader.get_bars_for_month(symbol, "2023-07-03", "2023-09-30")
        if not full_bars:
            continue

        warmup_list = []
        target_list = []
        for b in full_bars:
            b_ts = pd.Timestamp(b.timestamp)
            if w_start_ts <= b_ts <= w_end_ts:
                warmup_list.append(b)
            elif b_ts.strftime("%Y-%m-%d") == target_date:
                target_list.append(b)

        if target_list:
            segment_bars[symbol] = target_list

        if warmup_list:
            segment_warmups[symbol] = pd.DataFrame({
                "timestamp": [b.timestamp for b in warmup_list],
                "open": [b.open for b in warmup_list],
                "high": [b.high for b in warmup_list],
                "low": [b.low for b in warmup_list],
                "close": [b.close for b in warmup_list],
                "volume": [b.volume for b in warmup_list],
            })

    print(f"→ Loaded target bars for {len(segment_bars)} symbols and warmup for {len(segment_warmups)} symbols.")

    config = EffectiveConfig()
    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=build_candidate_provider(config, segment_warmups),
        exit_provider=build_exit_provider(config),
    )

    try:
        orchestrator.run(segment_bars)
        print("  ✓ 1-day replay executed successfully.")
        
        # Print portfolio summary / P&L if ledger is accessible on orchestrator
        if hasattr(orchestrator, "ledger") and orchestrator.ledger:
            ledger = orchestrator.ledger
            print("\n" + "=" * 40)
            print("PORTFOLIO PERFORMANCE SUMMARY")
            print("=" * 40)
            print(f"Final Cash:         ₹{ledger.cash:,.2f}")
            print(f"Reserved Cash:      ₹{ledger.reserved_cash:,.2f}")
            print(f"Active Positions:   {len(ledger.positions)}")
            print(f"Pending Orders:     {len(ledger.pending_orders)}")
            print("=" * 40)
    except Exception as e:
        print("  ✖ Replay halted due to exception:")
        traceback.print_exc()

if __name__ == "__main__":
    manifest_file = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    dataset_dir = os.environ.get(
        "NSE_DATA_DIR",
        "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824",
    )
    run_one_day_replay(manifest_file, dataset_dir)
