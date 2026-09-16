"""
Validation replay: Single symbol, one month.
Proves orchestrator execution on real data.
"""

import os
import pandas as pd
from revision4.contracts import EffectiveConfig, Bar
from revision4.orchestrator import TimestampOrchestrator
from revision4.dataset_loader import ManifestDataLoader


def run_sunpharma_august_2024() -> dict:
    """
    Validation replay: SUNPHARMA for August 2024.

    Returns:
        dict with replay results
    """
    print("=" * 70)
    print("VALIDATION REPLAY: SUNPHARMA / August 2024")
    print("=" * 70)

    # Load manifest and data
    manifest_path = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    data_dir = os.environ.get(
        "NSE_DATA_DIR",
        "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824",
    )

    print(f"\nLoading manifest: {manifest_path}")
    print(f"Data directory: {data_dir}")

    loader = ManifestDataLoader(manifest_path, data_dir)
    print(f"✓ Manifest loaded: {loader.manifest['symbol_count']} symbols")

    # Load SUNPHARMA data
    print(f"\nLoading SUNPHARMA data...")
    try:
        df = loader.load_symbol_data("SUNPHARMA")
        print(f"✓ Loaded {len(df)} bars")
        print(f"  First bar: {df.iloc[0]['timestamp']}")
        print(f"  Last bar:  {df.iloc[-1]['timestamp']}")
    except Exception as e:
        print(f"✗ Failed to load: {e}")
        return {"success": False, "error": str(e)}

    # Filter to August 2024
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
    aug_2024 = df[
        (df['timestamp'] >= pd.Timestamp('2024-08-01', tz='UTC'))
        & (df['timestamp'] <= pd.Timestamp('2024-08-31', tz='UTC'))
    ]

    if aug_2024.empty:
        print(f"✗ No data for August 2024")
        return {"success": False, "error": "No August 2024 data"}

    print(f"✓ Filtered to August 2024: {len(aug_2024)} bars")

    # Run orchestrator
    config = EffectiveConfig()
    orchestrator = TimestampOrchestrator(config, starting_cash=100_000.0)

    print(f"\nProcessing {len(aug_2024)} bars chronologically...")
    bar_index = 0
    for _, row in aug_2024.iterrows():
        timestamp = str(row['timestamp'])
        bar = Bar(
            timestamp=timestamp,
            symbol="SUNPHARMA",
            open=float(row['open']),
            high=float(row['high']),
            low=float(row['low']),
            close=float(row['close']),
            volume=int(row['volume']),
        )

        bars = {"SUNPHARMA": bar}  # Only one symbol for validation
        ok, msg = orchestrator.process_timestamp(timestamp, bar_index, bars)
        if not ok:
            print(f"✗ Processing failed at bar {bar_index}: {msg}")
            return {"success": False, "error": msg, "bar_index": bar_index}

        bar_index += 1

    print(f"✓ Processed {bar_index} bars successfully")

    # Finalize
    print(f"\nFinalizing evaluation...")
    eval = orchestrator.finalize()

    if not eval:
        print(f"✗ Evaluation failed")
        return {"success": False, "error": "Evaluation failed"}

    print(f"✓ Evaluation passed")
    print(f"\nResults:")
    print(f"  Starting Equity:    ₹{eval.starting_equity:,.0f}")
    print(f"  Ending Equity:      ₹{eval.ending_equity:,.0f}")
    print(f"  Net P&L:            ₹{eval.total_net_pnl:,.0f}")
    print(f"  Trading Sessions:   {eval.total_trading_sessions}")
    print(f"  Total Trades:       {eval.total_trades}")
    print(f"  Winning Trades:     {eval.total_wins}")
    print(f"  Win Rate:           {eval.win_rate:.1%}")
    print(f"  Profit Factor:      {eval.total_profit_factor:.2f}")
    print(f"  Safety Violations:  {eval.total_safety_violations}")

    return {
        "success": True,
        "eval": eval,
        "bars_processed": bar_index,
        "snapshots": len(orchestrator.snapshots),
    }


if __name__ == "__main__":
    result = run_sunpharma_august_2024()
    print(f"\n{'='*70}")
    print(f"Status: {'✓ PASSED' if result['success'] else '✗ FAILED'}")
    print(f"{'='*70}")
