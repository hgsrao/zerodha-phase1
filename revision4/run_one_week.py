import os
import json
import traceback
import pandas as pd
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.contracts import EffectiveConfig
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider

def run_one_week_replay(manifest_path: str, data_dir: str, start_date: str = "2023-09-01", end_date: str = "2023-09-07"):
    print("=" * 80)
    print(f"INITIALIZING 1-WEEK 48-SYMBOL REPLAY: {start_date} to {end_date}")
    print("=" * 80)

    with open(manifest_path) as f:
        manifest_data = json.load(f)
        symbols = [item["symbol"] for item in manifest_data["files"]]

    loader = ManifestDataLoader(manifest_path, data_dir)
    
    segment_bars = {}
    segment_warmups = {}

    for symbol in symbols:
        bars = loader.get_bars_for_month(symbol, start_date, end_date)
        if bars:
            segment_bars[symbol] = bars

        w_start = (pd.Timestamp(start_date, tz="UTC") - pd.DateOffset(days=60)).strftime("%Y-%m-%d")
        w_end = (pd.Timestamp(start_date, tz="UTC") - pd.DateOffset(days=1)).strftime("%Y-%m-%d")
        all_w = loader.get_bars_for_month(symbol, w_start, w_end)
        warmup_data = all_w[-60:] if len(all_w) >= 60 else all_w
        
        if warmup_data:
            segment_warmups[symbol] = pd.DataFrame({
                "timestamp": [b.timestamp for b in warmup_data],
                "open": [b.open for b in warmup_data],
                "high": [b.high for b in warmup_data],
                "low": [b.low for b in warmup_data],
                "close": [b.close for b in warmup_data],
                "volume": [b.volume for b in warmup_data],
            })

    config = EffectiveConfig()
    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=build_candidate_provider(config, segment_warmups),
        exit_provider=build_exit_provider(config),
    )

    try:
        orchestrator.run(segment_bars)
        print("  ✓ 1-week replay executed successfully.")
    except Exception as e:
        print("  ✖ Replay halted due to exception:")
        traceback.print_exc()

if __name__ == "__main__":
    manifest_file = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    dataset_dir = os.environ.get(
        "NSE_DATA_DIR",
        "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824",
    )
    run_one_week_replay(manifest_file, dataset_dir)
