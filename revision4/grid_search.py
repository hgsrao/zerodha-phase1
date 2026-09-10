import os
import json
import itertools
import pandas as pd
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.contracts import EffectiveConfig
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider

def evaluate_configuration(config: EffectiveConfig, segment_bars: dict, segment_warmups: dict) -> float:
    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=build_candidate_provider(config, segment_warmups),
        exit_provider=build_exit_provider(config),
    )
    try:
        orchestrator.run(segment_bars)
        ledger = getattr(orchestrator, "ledger", None)
        if ledger and hasattr(ledger, "calculate_equity"):
            return ledger.calculate_equity()
        elif ledger:
            return ledger.cash + ledger.reserved_cash
    except Exception:
        pass
    return 0.0

def run_grid_search():
    manifest_file = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    dataset_dir = os.environ.get(
        "NSE_DATA_DIR",
        "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824",
    )

    with open(manifest_file) as f:
        manifest_data = json.load(f)
        symbols = [item["symbol"] for item in manifest_data["files"]]

    loader = ManifestDataLoader(manifest_file, dataset_dir)
    
    start_date, end_date = "2023-09-01", "2023-09-07"
    target_start_ts = pd.Timestamp(start_date, tz="UTC")
    target_end_ts = pd.Timestamp(end_date, tz="UTC")
    w_start_ts = target_start_ts - pd.DateOffset(days=60)
    w_end_ts = target_start_ts - pd.Timedelta(days=1)

    segment_bars = {}
    segment_warmups = {}

    for symbol in symbols:
        full_bars = loader.get_bars_for_month(symbol, "2023-07-03", "2023-09-30")
        if not full_bars:
            continue
        warmup_list, segment_list = [], []
        for b in full_bars:
            b_ts = pd.Timestamp(b.timestamp)
            if w_start_ts <= b_ts <= w_end_ts:
                warmup_list.append(b)
            elif target_start_ts <= b_ts <= target_end_ts:
                segment_list.append(b)
        if segment_list:
            segment_bars[symbol] = segment_list
        if warmup_list:
            segment_warmups[symbol] = pd.DataFrame({
                "timestamp": [b.timestamp for b in warmup_list],
                "open": [b.open for b in warmup_list],
                "high": [b.high for b in warmup_list],
                "low": [b.low for b in warmup_list],
                "close": [b.close for b in warmup_list],
                "volume": [b.volume for b in warmup_list],
            })

    grid = {
        "max_positions_live": [10, 48],
        "stop_loss_atr_mult": [1.0, 1.2, 1.5],
        "profit_target_atr_mult": [1.5, 2.0],
        "pid_kp_entry": [0.10, 0.15]
    }

    keys = grid.keys()
    combinations = list(itertools.product(*grid.values()))
    print(f"Executing grid search across {len(combinations)} parameter combinations...")

    best_equity = 0.0
    best_params = None

    for idx, combo in enumerate(combinations, 1):
        param_dict = dict(zip(keys, combo))
        config = EffectiveConfig()
        
        for k, v in param_dict.items():
            setattr(config, k, v)
        config.max_concurrent_positions = param_dict["max_positions_live"]

        equity = evaluate_configuration(config, segment_bars, segment_warmups)
        print(f"[{idx}/{len(combinations)}] Params: {param_dict} => Equity: ₹{equity:,.2f}")

        if equity > best_equity:
            best_equity = equity
            best_params = param_dict

    print("\n" + "=" * 50)
    print(f"Best Equity: ₹{best_equity:,.2f}")
    print(f"Best Parameters: {best_params}")
    print("=" * 50)

if __name__ == "__main__":
    run_grid_search()
