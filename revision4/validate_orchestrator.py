"""
Validation replay using real TimestampOrchestrator.
Integrates manifest loader, real Revision 2 boxes (via adapters).
"""

import os
import json
import pandas as pd
from typing import Dict, Sequence
from revision4.contracts import EffectiveConfig, Bar
from revision4.dataset_seal import DatasetValidator
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider
from revision4.research_target import SealedRunEvaluation, BenchmarkConfig


class ManifestDataLoader:
    """Load real NSE data from manifest."""

    def __init__(self, manifest_path: str, data_dir: str):
        self.manifest_path = manifest_path
        self.data_dir = data_dir
        with open(manifest_path) as f:
            self.manifest = json.load(f)
        self.symbol_files = {f['symbol']: f for f in self.manifest['files']}

    def load_symbol_data(self, symbol: str) -> pd.DataFrame:
        """Load one symbol's CSV."""
        if symbol not in self.symbol_files:
            raise ValueError(f"Symbol {symbol} not in manifest")

        file_info = self.symbol_files[symbol]
        csv_path = os.path.join(self.data_dir, file_info['filename'])

        if not os.path.exists(csv_path):
            raise FileNotFoundError(f"Data file not found: {csv_path}")

        df = pd.read_csv(csv_path)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        return df.sort_values('timestamp')

    def get_bars_for_month(self, symbol: str, start_date: str, end_date: str) -> Sequence[Bar]:
        """Get all bars for symbol in date range as sequence of Bar objects."""
        df = self.load_symbol_data(symbol)

        start = pd.Timestamp(start_date, tz='UTC')
        # End dates are calendar dates.  Use an exclusive next-day boundary
        # so the final market session is never truncated at midnight.
        end_exclusive = pd.Timestamp(end_date, tz='UTC') + pd.DateOffset(days=1)
        filtered = df[(df['timestamp'] >= start) & (df['timestamp'] < end_exclusive)]

        bars = []
        for _, row in filtered.iterrows():
            bar = Bar(
                timestamp=str(row['timestamp']),
                symbol=symbol,
                open=float(row['open']),
                high=float(row['high']),
                low=float(row['low']),
                close=float(row['close']),
                volume=int(row['volume']),
            )
            bars.append(bar)
        return bars


def run_validation_replay(
    symbol: str,
    start_date: str,
    end_date: str,
    manifest_path: str,
    data_dir: str,
) -> dict:
    """
    Run chronological validation replay on real data.

    Args:
        symbol: Single symbol to test (e.g., "SUNPHARMA")
        start_date: Start date (e.g., "2024-08-01")
        end_date: End date (e.g., "2024-08-31")
        manifest_path: Path to manifest JSON
        data_dir: Path to CSV data directory

    Returns:
        Result dict with replay metrics
    """
    print("=" * 70)
    print(f"VALIDATION REPLAY: {symbol} / {start_date} to {end_date}")
    print("=" * 70)

    # Load data
    print(f"\nLoading manifest: {manifest_path}")
    # Do the byte-level seal check before reading a CSV.  A replay on data
    # that differs from its frozen manifest is invalid, even if it runs.
    try:
        seal = DatasetValidator(manifest_path).load_manifest(data_dir)
    except RuntimeError as exc:
        return {"success": False, "error": f"dataset seal failed: {exc}"}
    if symbol not in seal.symbols:
        return {"success": False, "error": f"{symbol} is not admitted by the frozen manifest"}
    loader = ManifestDataLoader(manifest_path, data_dir)
    print(f"✓ Manifest loaded: {loader.manifest['symbol_count']} symbols")

    print(f"\nLoading {symbol} data...")
    bars = loader.get_bars_for_month(symbol, start_date, end_date)
    print(f"✓ Loaded {len(bars)} bars")

    if not bars:
        return {"success": False, "error": f"No data for {symbol} in range"}

    # Load warmup bars (60 bars strictly before the sealed month)
    # This is required for PA calibration to avoid degenerate scales
    print(f"\nLoading warmup bars...")
    warmup_start = pd.Timestamp(start_date, tz='UTC') - pd.DateOffset(days=60)
    warmup_end = pd.Timestamp(start_date, tz='UTC') - pd.DateOffset(days=1)

    warmup_bars_by_symbol = {}
    try:
        warmup_data = loader.get_bars_for_month(symbol, warmup_start.strftime('%Y-%m-%d'), warmup_end.strftime('%Y-%m-%d'))
        if len(warmup_data) >= 30:
            # Convert to DataFrame format that PA expects
            df_data = {
                'timestamp': [b.timestamp for b in warmup_data],
                'open': [b.open for b in warmup_data],
                'high': [b.high for b in warmup_data],
                'low': [b.low for b in warmup_data],
                'close': [b.close for b in warmup_data],
                'volume': [b.volume for b in warmup_data],
            }
            warmup_df = pd.DataFrame(df_data)
            warmup_bars_by_symbol[symbol] = warmup_df
            print(f"✓ Loaded {len(warmup_data)} warmup bars for {symbol}")
        else:
            print(f"⚠ Only {len(warmup_data)} warmup bars available (need 30+)")
    except Exception as e:
        print(f"⚠ Failed to load warmup bars: {e}")

    # Build orchestrator with real callbacks
    config = EffectiveConfig()
    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=build_candidate_provider(config, warmup_bars_by_symbol),
        exit_provider=build_exit_provider(config),
    )

    # Run replay
    print(f"\nRunning orchestrator replay...")
    try:
        result = orchestrator.run({symbol: bars})
    except Exception as e:
        return {"success": False, "error": str(e)}

    print(f"✓ Replay complete")
    print(f"\nResults:")
    print(f"  Timestamps processed:   {result.timestamps_processed}")
    print(f"  Bars processed:         {result.bars_processed}")
    print(f"  Orders submitted:       {len(result.orders_submitted)}")
    print(f"  Fills:                  {len(result.fills)}")
    print(f"  Exits:                  {len(result.exits)}")

    # Evaluate
    print(f"\nGenerating evaluation...")
    try:
        eval = SealedRunEvaluation.create(
            completed_trades=orchestrator.ledger.completed_trades,
            starting_equity=orchestrator.ledger.starting_cash,
            ending_equity=orchestrator.ledger.marked_equity,
            benchmark=BenchmarkConfig(),
        )
        print(f"✓ Evaluation passed")
        print(f"\nMetrics:")
        print(f"  Starting Equity:        ₹{eval.starting_equity:,.0f}")
        print(f"  Ending Equity:          ₹{eval.ending_equity:,.0f}")
        print(f"  Net P&L:                ₹{eval.total_net_pnl:,.0f}")
        print(f"  Total Trades:           {eval.total_trades}")
        print(f"  Safety Violations:      {eval.total_safety_violations}")

        return {
            "success": True,
            "eval": eval,
            "result": result,
        }
    except Exception as e:
        # A zero-trade or unreconciled ledger is diagnostic output, not a
        # successful validation run.
        return {"success": False, "eval": None, "error": str(e), "result": result}


if __name__ == "__main__":
    manifest = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    data_dir = os.environ.get(
        "NSE_DATA_DIR",
        "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824",
    )

    result = run_validation_replay(
        symbol="SUNPHARMA",
        start_date="2024-08-01",
        end_date="2024-08-31",
        manifest_path=manifest,
        data_dir=data_dir,
    )

    print(f"\n{'='*70}")
    print(f"Status: {'✓ PASSED' if result['success'] else '✗ FAILED'}")
    if result.get('error'):
        print(f"Error: {result['error']}")
    print(f"{'='*70}")
