import os
import json
import pandas as pd
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.contracts import EffectiveConfig
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider

def export_trades(manifest_path: str, data_dir: str, target_date: str = "2023-09-01"):
    with open(manifest_path) as f:
        manifest_data = json.load(f)
        symbols = [item["symbol"] for item in manifest_data["files"]]

    loader = ManifestDataLoader(manifest_path, data_dir)
    segment_bars = {}
    segment_warmups = {}

    target_ts = pd.Timestamp(target_date, tz="UTC")
    w_start_ts = target_ts - pd.DateOffset(days=60)
    w_end_ts = target_ts - pd.Timedelta(days=1)

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

    config = EffectiveConfig()
    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=build_candidate_provider(config, segment_warmups),
        exit_provider=build_exit_provider(config),
    )

    orchestrator.run(segment_bars)

    ledger = getattr(orchestrator, "ledger", None)
    if not ledger:
        for attr_name in dir(orchestrator):
            attr = getattr(orchestrator, attr_name)
            if hasattr(attr, "closed_trades") or hasattr(attr, "trades") or hasattr(attr, "positions"):
                ledger = attr
                break

    print("\n" + "=" * 80)
    print(f"DETAILED TRADE & P&L LOG FOR {target_date}")
    print("=" * 80)

    if ledger:
        print(f"Ending Cash:    ₹{getattr(ledger, 'cash', 0.0):,.2f}")
        print(f"Reserved Cash:  ₹{getattr(ledger, 'reserved_cash', 0.0):,.2f}")
        
        positions = getattr(ledger, "positions", {})
        print(f"Active Positions ({len(positions)}):")
        for sym, pos in positions.items():
            print(f"  - {sym}: Qty={pos.quantity}, EntryPrice={getattr(pos, 'entry_price', 'N/A')}")

        trades = getattr(ledger, "closed_trades", getattr(ledger, "trades", []))
        if trades:
            print(f"\nClosed Trades ({len(trades)}):")
            for t in trades:
                print(f"  {t}")
        else:
            print("\nNo closed trades recorded in this session window.")
    else:
        print("Ledger telemetry object not found on orchestrator.")
    print("=" * 80)

if __name__ == "__main__":
    manifest_file = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    dataset_dir = os.environ.get(
        "NSE_DATA_DIR",
        "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824",
    )
    export_trades(manifest_file, dataset_dir)
