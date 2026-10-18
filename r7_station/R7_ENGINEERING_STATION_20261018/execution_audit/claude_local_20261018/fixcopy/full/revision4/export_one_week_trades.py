import os
import json
import pandas as pd
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.contracts import EffectiveConfig
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider

def export_one_week_trades(manifest_path: str, data_dir: str, start_date: str = "2023-09-01", end_date: str = "2023-09-07"):
    with open(manifest_path) as f:
        manifest_data = json.load(f)
        symbols = [item["symbol"] for item in manifest_data["files"]]

    loader = ManifestDataLoader(manifest_path, data_dir)
    segment_bars = {}
    segment_warmups = {}

    target_start_ts = pd.Timestamp(start_date, tz="UTC")
    target_end_ts = pd.Timestamp(end_date, tz="UTC")
    w_start_ts = target_start_ts - pd.DateOffset(days=60)
    w_end_ts = target_start_ts - pd.Timedelta(days=1)

    print(f"→ Loading 1-week data ({start_date} to {end_date}) with 60-bar warmup...")
    for symbol in symbols:
        full_bars = loader.get_bars_for_month(symbol, "2023-07-03", "2023-09-30")
        if not full_bars:
            continue

        warmup_list = []
        segment_list = []
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
            if hasattr(attr, "positions") or hasattr(attr, "closed_trades") or hasattr(attr, "trades"):
                ledger = attr
                break

    print("\n" + "=" * 80)
    print(f"1-WEEK REPLAY PERFORMANCE & TRADE REPORT ({start_date} to {end_date})")
    print("=" * 80)

    if ledger:
        print(f"Ending Cash:      ₹{getattr(ledger, 'cash', 0.0):,.2f}")
        print(f"Reserved Cash:    ₹{getattr(ledger, 'reserved_cash', 0.0):,.2f}")
        
        positions = getattr(ledger, "positions", {})
        print(f"\nActive Positions at End ({len(positions)} symbols):")
        if positions:
            for sym, pos in positions.items():
                entry_p = getattr(pos, 'entry_price', getattr(pos, 'price', 'N/A'))
                qty = getattr(pos, 'quantity', 'N/A')
                print(f"  • {sym}: Qty = {qty}, Entry Price = {entry_p}")
        else:
            print("  (No active positions remaining)")

        trades = []
        for attr_candidate in ["closed_trades", "trades", "trade_history", "history", "fills"]:
            if hasattr(ledger, attr_candidate):
                trades = getattr(ledger, attr_candidate)
                break
        
        if not trades and hasattr(orchestrator, "broker"):
            for attr_candidate in ["closed_trades", "trades", "history", "fills"]:
                if hasattr(orchestrator.broker, attr_candidate):
                    trades = getattr(orchestrator.broker, attr_candidate)
                    break

        if trades:
            print(f"\nRecorded Trades / Fills ({len(trades)}):")
            for idx, t in enumerate(trades, 1):
                print(f"  {idx}. {t}")
        else:
            print("\nNo closed trades recorded in this window.")
    else:
        print("Ledger telemetry object not found.")
    print("=" * 80)

if __name__ == "__main__":
    manifest_file = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    dataset_dir = os.environ.get(
        "NSE_DATA_DIR",
        "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824",
    )
    export_one_week_trades(manifest_file, dataset_dir)
