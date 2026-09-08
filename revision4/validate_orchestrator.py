"""
Validation replay using real TimestampOrchestrator.
Integrates manifest loader, real Revision 2 boxes (via adapters).
"""

import os
import json
import pandas as pd
from typing import Dict, Sequence
from revision4.contracts import EffectiveConfig, Bar, ExitEvent, ExitReason
from revision4.config_access import calculate_transaction_cost
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

    # Load exactly 60 warmup bars (strictly before the sealed month)
    # PA calibration requires this exact count for canonical scales
    print(f"\nLoading exactly 60 warmup bars...")
    warmup_start = pd.Timestamp(start_date, tz='UTC') - pd.DateOffset(days=60)
    warmup_end = pd.Timestamp(start_date, tz='UTC') - pd.DateOffset(days=1)

    warmup_bars_by_symbol = {}
    try:
        all_warmup = loader.get_bars_for_month(symbol, warmup_start.strftime('%Y-%m-%d'), warmup_end.strftime('%Y-%m-%d'))
        # Take exactly last 60 bars before sealed month (ensures pre-run calibration)
        warmup_data = all_warmup[-60:] if len(all_warmup) >= 60 else all_warmup

        if len(warmup_data) >= 60:
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
            print(f"✓ Loaded exactly 60 warmup bars for {symbol}")
        else:
            print(f"⚠ Only {len(warmup_data)} warmup bars available (need 60)")
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

    # EOD flattening: close all remaining open positions
    print(f"\nApplying EOD flattening...")
    final_bars = {symbol: bars[-1]} if bars else {}  # Last bar of month
    if final_bars and orchestrator.ledger.positions:
        eod_exits = []
        for pos_symbol, position in list(orchestrator.ledger.positions.items()):
            if pos_symbol in final_bars:
                bar = final_bars[pos_symbol]
                exit_price = bar.close

                # Calculate exit cost (SELL for longs, BUY for shorts)
                exit_side = "SELL" if position.direction == 1 else "BUY"
                exit_cost = calculate_transaction_cost(exit_price, int(position.quantity), exit_side)

                # Calculate P&L
                if position.direction == 1:
                    gross_pnl = (exit_price - position.entry_price) * position.quantity
                else:
                    gross_pnl = (position.entry_price - exit_price) * position.quantity
                pnl_realized = gross_pnl - position.cost_paid - exit_cost
                pnl_pct = (pnl_realized / (position.entry_price * position.quantity)) * 100.0 if position.entry_price else 0.0

                exit_event = ExitEvent(
                    exit_id=f"eod_{pos_symbol}",
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
                    pnl_pct=pnl_pct,
                )
                ok, msg = orchestrator.ledger.close_position(exit_event, config)
                if ok:
                    eod_exits.append(exit_event)
                    print(f"  ✓ Closed {pos_symbol} at EOD: ₹{pnl_realized:,.0f}")
                else:
                    print(f"  ✗ Failed to close {pos_symbol}: {msg}")
        if eod_exits:
            print(f"✓ EOD flattening complete: {len(eod_exits)} positions closed")

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
