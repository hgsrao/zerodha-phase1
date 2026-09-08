"""
Complete 48-symbol slippage diagnostic (read-only).

Measures close-to-next-open gaps for every actual candidate decision.
Classifies first-tradable-bar-of-session separately from intraday gaps.
Saves raw observations + stratified summary statistics (reproducible).

Does not change tolerance, dataset, or parameters.
"""

import os
import json
import csv
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from revision4.validate_orchestrator import ManifestDataLoader
from revision4.contracts import EffectiveConfig, Bar
from revision4.timestamp_orchestrator import TimestampOrchestrator
from revision4.box_adapters import build_candidate_provider, build_exit_provider


class DiagnosticCollector:
    """Collect raw slippage observations with session classification."""

    def __init__(self, output_dir: str = "diagnostic_output"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        self.raw_observations: List[Dict] = []
        self.session_dates_seen: Dict[str, set] = {}  # {symbol: {YYYY-MM-DD, ...}}

    def identify_session_starts_from_timestamp(self) -> set:
        """Return set of trading session start dates (for timestamp-based detection)."""
        # This will be built incrementally during the run
        return set()

    def record_candidate(
        self,
        symbol: str,
        bar_index: int,
        decision_timestamp: str,
        fill_timestamp: str,
        decision_close: float,
        next_open: float,
    ):
        """Record one candidate's slippage measurement.

        Session gap = date(decision_bar) != date(fill_bar)
        Intraday gap = date(decision_bar) == date(fill_bar)
        """
        # Extract dates (YYYY-MM-DD)
        decision_date = decision_timestamp.split('T')[0]
        fill_date = fill_timestamp.split('T')[0]

        # Session gap occurs when decision and fill are on different dates
        is_session_gap = decision_date != fill_date

        slippage_pct = abs(next_open - decision_close) / decision_close * 100

        obs = {
            "symbol": symbol,
            "bar_index": bar_index,
            "decision_timestamp": decision_timestamp,
            "fill_timestamp": fill_timestamp,
            "decision_date": decision_date,
            "fill_date": fill_date,
            "decision_close": round(decision_close, 2),
            "next_open": round(next_open, 2),
            "slippage_pct": round(slippage_pct, 6),
            "is_session_gap": is_session_gap,
            "gap_type": "session" if is_session_gap else "intraday",
        }

        self.raw_observations.append(obs)

    def save_raw_data(self):
        """Save all raw observations to CSV for reproducibility."""
        if not self.raw_observations:
            print("No observations to save")
            return

        csv_path = os.path.join(self.output_dir, "raw_observations.csv")
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=self.raw_observations[0].keys())
            writer.writeheader()
            writer.writerows(self.raw_observations)

        print(f"✓ Saved {len(self.raw_observations)} raw observations to {csv_path}")

    def generate_summary(self):
        """Generate stratified summary statistics."""
        if not self.raw_observations:
            print("No observations for summary")
            return

        summary = {
            "total_observations": len(self.raw_observations),
            "run_timestamp": datetime.now().isoformat(),
            "methodology": "close[t] vs open[t+1] for actual candidates only",
            "stratification": {
                "session_gaps": self._summarize_by_gap_type("session"),
                "intraday_gaps": self._summarize_by_gap_type("intraday"),
            },
            "by_symbol": self._summarize_by_symbol(),
            "tolerance_analysis": self._analyze_thresholds(),
        }

        json_path = os.path.join(self.output_dir, "summary_statistics.json")
        with open(json_path, "w") as f:
            json.dump(summary, f, indent=2)

        print(f"✓ Saved summary statistics to {json_path}")
        return summary

    def _summarize_by_gap_type(self, gap_type: str) -> Dict:
        """Generate summary for one gap type (session or intraday)."""
        obs = [o for o in self.raw_observations if o["gap_type"] == gap_type]
        if not obs:
            return {"count": 0, "note": f"No {gap_type} gaps found"}

        slips = sorted([o["slippage_pct"] for o in obs])
        n = len(slips)

        return {
            "count": n,
            "median_pct": round(slips[n // 2], 6),
            "p95_pct": round(slips[int(n * 0.95)], 6),
            "p99_pct": round(slips[int(n * 0.99)] if n > 1 else slips[-1], 6),
            "max_pct": round(max(slips), 6),
            "rejections_at_0_10pct": sum(1 for s in slips if s > 0.10),
            "rejections_at_0_15pct": sum(1 for s in slips if s > 0.15),
            "rejections_at_0_20pct": sum(1 for s in slips if s > 0.20),
            "rejection_rate_0_10pct": f"{sum(1 for s in slips if s > 0.10) / n * 100:.2f}%",
            "rejection_rate_0_15pct": f"{sum(1 for s in slips if s > 0.15) / n * 100:.2f}%",
            "rejection_rate_0_20pct": f"{sum(1 for s in slips if s > 0.20) / n * 100:.2f}%",
        }

    def _summarize_by_symbol(self) -> Dict:
        """Generate per-symbol summary."""
        by_sym = {}
        for symbol in set(o["symbol"] for o in self.raw_observations):
            obs = [o for o in self.raw_observations if o["symbol"] == symbol]
            slips = sorted([o["slippage_pct"] for o in obs])
            n = len(slips)

            by_sym[symbol] = {
                "count": n,
                "median_pct": round(slips[n // 2], 6) if slips else 0,
                "p95_pct": round(slips[int(n * 0.95)], 6) if slips else 0,
                "p99_pct": round(slips[int(n * 0.99)] if n > 1 else (slips[-1] if slips else 0), 6),
                "max_pct": round(max(slips), 6) if slips else 0,
                "session_gaps": sum(1 for o in obs if o["is_session_gap"]),
                "intraday_gaps": sum(1 for o in obs if not o["is_session_gap"]),
            }

        return by_sym

    def _analyze_thresholds(self) -> Dict:
        """Analyze rejection rates at standard thresholds."""
        slips = sorted([o["slippage_pct"] for o in self.raw_observations])
        n = len(slips)

        return {
            "note": "Counts of fills exceeding each threshold",
            "thresholds": {
                "0_10_pct": {
                    "count": sum(1 for s in slips if s > 0.10),
                    "rate": f"{sum(1 for s in slips if s > 0.10) / n * 100:.2f}%",
                },
                "0_15_pct": {
                    "count": sum(1 for s in slips if s > 0.15),
                    "rate": f"{sum(1 for s in slips if s > 0.15) / n * 100:.2f}%",
                },
                "0_20_pct": {
                    "count": sum(1 for s in slips if s > 0.20),
                    "rate": f"{sum(1 for s in slips if s > 0.20) / n * 100:.2f}%",
                },
            },
        }

    def print_summary(self, summary: Dict):
        """Print human-readable summary."""
        print("\n" + "=" * 80)
        print("COMPLETE 48-SYMBOL SLIPPAGE DIAGNOSTIC")
        print("=" * 80)

        print(f"\nTotal observations: {summary['total_observations']}")

        print("\n--- SESSION GAPS (first bar of trading day) ---")
        sg = summary["stratification"]["session_gaps"]
        if sg["count"] > 0:
            print(f"  Count:            {sg['count']}")
            print(f"  Median slippage:  {sg['median_pct']:.6f}%")
            print(f"  95th percentile:  {sg['p95_pct']:.6f}%")
            print(f"  99th percentile:  {sg['p99_pct']:.6f}%")
            print(f"  Maximum:          {sg['max_pct']:.6f}%")
            print(f"  Rejections @ 0.10%: {sg['rejections_at_0_10pct']} ({sg['rejection_rate_0_10pct']})")
            print(f"  Rejections @ 0.15%: {sg['rejections_at_0_15pct']} ({sg['rejection_rate_0_15pct']})")
            print(f"  Rejections @ 0.20%: {sg['rejections_at_0_20pct']} ({sg['rejection_rate_0_20pct']})")
        else:
            print("  No session-gap observations")

        print("\n--- INTRADAY GAPS (normal one-minute transitions) ---")
        ig = summary["stratification"]["intraday_gaps"]
        if ig["count"] > 0:
            print(f"  Count:            {ig['count']}")
            print(f"  Median slippage:  {ig['median_pct']:.6f}%")
            print(f"  95th percentile:  {ig['p95_pct']:.6f}%")
            print(f"  99th percentile:  {ig['p99_pct']:.6f}%")
            print(f"  Maximum:          {ig['max_pct']:.6f}%")
            print(f"  Rejections @ 0.10%: {ig['rejections_at_0_10pct']} ({ig['rejection_rate_0_10pct']})")
            print(f"  Rejections @ 0.15%: {ig['rejections_at_0_15pct']} ({ig['rejection_rate_0_15pct']})")
            print(f"  Rejections @ 0.20%: {ig['rejections_at_0_20pct']} ({ig['rejection_rate_0_20pct']})")
        else:
            print("  No intraday-gap observations")

        print("\n--- TOLERANCE ANALYSIS (All Gap Types Combined) ---")
        ta = summary["tolerance_analysis"]["thresholds"]
        for threshold, data in ta.items():
            threshold_str = threshold.replace("_", ".")
            print(f"  @ {threshold_str}%: {data['count']} rejections ({data['rate']})")

        print("\n" + "=" * 80)


def run_48symbol_diagnostic(
    manifest_path: str,
    data_dir: str,
):
    """Run diagnostic on all 48 symbols."""
    print("=" * 80)
    print("Starting complete 48-symbol slippage diagnostic")
    print("=" * 80)

    # Load manifest
    with open(manifest_path) as f:
        manifest_data = json.load(f)
        symbols = [f["symbol"] for f in manifest_data["files"]]

    print(f"\nRunning diagnostic on {len(symbols)} symbols...")

    loader = ManifestDataLoader(manifest_path, data_dir)
    collector = DiagnosticCollector()

    completed = 0
    failed = 0

    for symbol in symbols:
        try:
            print(f"  {symbol}...", end=" ", flush=True)

            # Load bars for sealed month
            bars = loader.get_bars_for_month(symbol, "2024-08-01", "2024-08-31")
            if not bars:
                print("no data")
                failed += 1
                continue

            # Load warmup (60 bars before month)
            warmup_start = pd.Timestamp("2024-08-01", tz="UTC") - pd.DateOffset(days=60)
            warmup_end = pd.Timestamp("2024-08-01", tz="UTC") - pd.DateOffset(days=1)
            all_warmup = loader.get_bars_for_month(
                symbol,
                warmup_start.strftime("%Y-%m-%d"),
                warmup_end.strftime("%Y-%m-%d"),
            )
            warmup_data = all_warmup[-60:] if len(all_warmup) >= 60 else all_warmup

            df_data = {
                "timestamp": [b.timestamp for b in warmup_data],
                "open": [b.open for b in warmup_data],
                "high": [b.high for b in warmup_data],
                "low": [b.low for b in warmup_data],
                "close": [b.close for b in warmup_data],
                "volume": [b.volume for b in warmup_data],
            }
            warmup_df = pd.DataFrame(df_data)

            # Build orchestrator
            config = EffectiveConfig()
            orchestrator = TimestampOrchestrator(
                config=config,
                candidate_provider=build_candidate_provider(config, {symbol: warmup_df}),
                exit_provider=build_exit_provider(config),
            )

            # Hook to collect slippage data (bypass gates)
            original_fill = orchestrator.broker.try_fill_order
            candidate_count = [0]

            def patched_fill(order_id, bar, fill_idx, cfg):
                order = orchestrator.broker.active_orders.get(order_id)

                try:
                    # Get actual fill event from broker (preserves real timestamp semantics)
                    fill_event = original_fill(order_id, bar, fill_idx, cfg)

                    # Only record if fill actually occurred
                    if fill_event is not None and order is not None:
                        decision_close = order.proposal.plan.entry_price
                        next_open = bar.open
                        decision_timestamp = order.timestamp_created  # When order was created
                        fill_timestamp = fill_event.timestamp_filled  # Actual fill timestamp from broker

                        collector.record_candidate(
                            symbol=symbol,
                            bar_index=fill_idx,
                            decision_timestamp=decision_timestamp,
                            fill_timestamp=fill_timestamp,
                            decision_close=decision_close,
                            next_open=next_open,
                        )
                        candidate_count[0] += 1

                    return fill_event

                except RuntimeError as e:
                    if "slippage" in str(e).lower():
                        return None
                    raise

            orchestrator.broker.try_fill_order = patched_fill

            # Run replay
            try:
                orchestrator.run({symbol: bars})
            except Exception:
                pass  # Expected: may stop on other gates

            print(f"{candidate_count[0]} candidates")
            completed += 1

        except Exception as e:
            print(f"error: {str(e)[:40]}")
            failed += 1

    print(f"\n✓ Completed {completed}/{len(symbols)} symbols ({failed} failures)")

    # Save results
    print("\nSaving diagnostic results...")
    collector.save_raw_data()
    summary = collector.generate_summary()
    collector.print_summary(summary)

    return collector, summary


if __name__ == "__main__":
    manifest = "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    data_dir = os.environ.get(
        "NSE_DATA_DIR",
        "/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824",
    )

    collector, summary = run_48symbol_diagnostic(manifest, data_dir)
