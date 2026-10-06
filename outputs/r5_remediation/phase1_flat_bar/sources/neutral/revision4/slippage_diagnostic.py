"""
Read-only diagnostic: measure slippage distribution across sealed replay.

For every eligible order, records:
  abs(next_bar_open - decision_close) / decision_close

Reports: median, 95th, 99th percentile, maximum, rejection count at 0.1%.
"""

import os
import json
import pandas as pd
from typing import Dict, List, Tuple
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.contracts import EffectiveConfig, Bar
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider


class SlippageDiagnosticCollector:
    """Collect slippage data without enforcing gates."""

    def __init__(self):
        self.slippages: List[float] = []
        self.by_symbol: Dict[str, List[float]] = {}
        self.rejections_at_01pct = 0

    def record(self, symbol: str, decision_close: float, next_open: float):
        """Record one order's slippage."""
        slippage_pct = abs(next_open - decision_close) / decision_close * 100
        self.slippages.append(slippage_pct)

        if symbol not in self.by_symbol:
            self.by_symbol[symbol] = []
        self.by_symbol[symbol].append(slippage_pct)

        if slippage_pct > 0.1:
            self.rejections_at_01pct += 1

    def report(self, symbol_list: List[str] = None):
        """Generate report."""
        if not self.slippages:
            print("No slippage data collected")
            return

        all_slips = sorted(self.slippages)

        print("\n" + "=" * 70)
        print("SLIPPAGE DIAGNOSTIC REPORT")
        print("=" * 70)

        print(f"\nOverall Statistics (all symbols):")
        print(f"  Total orders:          {len(all_slips)}")
        print(f"  Median slippage:       {self._percentile(all_slips, 50):.4f}%")
        print(f"  95th percentile:       {self._percentile(all_slips, 95):.4f}%")
        print(f"  99th percentile:       {self._percentile(all_slips, 99):.4f}%")
        print(f"  Maximum slippage:      {max(all_slips):.4f}%")
        print(f"  Rejections at 0.1%:    {self.rejections_at_01pct} ({self.rejections_at_01pct/len(all_slips)*100:.1f}%)")

        if symbol_list:
            print(f"\nPer-Symbol Analysis:")
            for symbol in sorted(symbol_list):
                if symbol in self.by_symbol:
                    slips = sorted(self.by_symbol[symbol])
                    print(f"\n  {symbol}:")
                    print(f"    Orders:      {len(slips)}")
                    print(f"    Median:      {self._percentile(slips, 50):.4f}%")
                    print(f"    95th pct:    {self._percentile(slips, 95):.4f}%")
                    print(f"     99th pct:    {self._percentile(slips, 99):.4f}%")
                    print(f"    Max:         {max(slips):.4f}%")
                    rejections = sum(1 for s in slips if s > 0.1)
                    print(f"    Reject@0.1%: {rejections} ({rejections/len(slips)*100:.1f}%)")

        print("\n" + "=" * 70)

    def _percentile(self, data: List[float], p: int) -> float:
        """Calculate percentile."""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        idx = int(len(sorted_data) * p / 100)
        idx = min(idx, len(sorted_data) - 1)
        return sorted_data[idx]


def run_diagnostic(
    symbol: str,
    start_date: str,
    end_date: str,
    manifest_path: str,
    data_dir: str,
) -> Tuple[SlippageDiagnosticCollector, dict]:
    """
    Run diagnostic replay without gate enforcement.

    Collects slippage data for every order without stopping on rejections.
    """
    print("=" * 70)
    print(f"SLIPPAGE DIAGNOSTIC: {symbol} / {start_date} to {end_date}")
    print("=" * 70)

    # Load data
    print(f"\nLoading manifest: {manifest_path}")
    loader = ManifestDataLoader(manifest_path, data_dir)
    print(f"✓ Manifest loaded")

    print(f"\nLoading {symbol} data...")
    bars = loader.get_bars_for_month(symbol, start_date, end_date)
    print(f"✓ Loaded {len(bars)} bars")

    # Load warmup
    print(f"\nLoading exactly 60 warmup bars...")
    warmup_start = pd.Timestamp(start_date, tz='UTC') - pd.DateOffset(days=60)
    warmup_end = pd.Timestamp(start_date, tz='UTC') - pd.DateOffset(days=1)

    all_warmup = loader.get_bars_for_month(
        symbol, warmup_start.strftime('%Y-%m-%d'), warmup_end.strftime('%Y-%m-%d')
    )
    warmup_data = all_warmup[-60:] if len(all_warmup) >= 60 else all_warmup

    df_data = {
        'timestamp': [b.timestamp for b in warmup_data],
        'open': [b.open for b in warmup_data],
        'high': [b.high for b in warmup_data],
        'low': [b.low for b in warmup_data],
        'close': [b.close for b in warmup_data],
        'volume': [b.volume for b in warmup_data],
    }
    warmup_df = pd.DataFrame(df_data)
    print(f"✓ Loaded exactly {len(warmup_data)} warmup bars")

    # Build orchestrator
    config = EffectiveConfig()
    orchestrator = TimestampOrchestrator(
        config=config,
        candidate_provider=build_candidate_provider(config, {symbol: warmup_df}),
        exit_provider=build_exit_provider(config),
    )

    # Hook into broker to collect slippage data
    collector = SlippageDiagnosticCollector()
    original_try_fill = orchestrator.broker.try_fill_order

    def try_fill_with_diagnostic(order_id, bar_at_fill_time, fill_bar_index, config):
        """Wrapper to collect slippage before executing fill."""
        order = orchestrator.broker.active_orders.get(order_id)
        if order is not None:
            decision_close = order.proposal.plan.entry_price
            next_open = bar_at_fill_time.open
            collector.record(symbol, decision_close, next_open)

        # Call original (will not raise, will just return None if gate fails)
        return original_try_fill(order_id, bar_at_fill_time, fill_bar_index, config)

    orchestrator.broker.try_fill_order = try_fill_with_diagnostic

    # Run replay (will stop on first gate rejection)
    print(f"\nRunning diagnostic replay...")
    try:
        result = orchestrator.run({symbol: bars})
        print(f"✓ Replay completed: {len(result.orders_submitted)} orders submitted, {len(result.fills)} fills")
    except RuntimeError as e:
        error_msg = str(e)
        print(f"⚠ Replay stopped: {error_msg}")
        print(f"✓ Collected slippage data up to stopping point")

    return collector, {"symbol": symbol, "bars": len(bars)}


if __name__ == "__main__":
    manifest = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    data_dir = os.environ.get(
        "NSE_DATA_DIR",
        "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824",
    )

    # Run diagnostic for SUNPHARMA
    collector, info = run_diagnostic(
        symbol="SUNPHARMA",
        start_date="2024-08-01",
        end_date="2024-08-31",
        manifest_path=manifest,
        data_dir=data_dir,
    )

    # Report
    collector.report([info["symbol"]])
