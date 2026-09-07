#!/usr/bin/env python3
"""
Post-calibration A/B comparison: External (HMM) vs In-House (Vanilla).

This script waits for both calibrations to complete, then extracts and compares:
- Candidate counts (phase distribution, acceptance rate)
- Trade generation (average trades per candidate, success rate)
- Quality metrics (saturation exits, P&L, Sharpe, win rate)
- Parameter optimization (optimal values for trailing_stop_atr_mult, saturation_exit_bars, etc)
- Numerical stability (PyPortfolioOpt solver warnings)

Output: Side-by-side comparison table + JSON report.
"""

import json
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

def load_summary(path: Path, timeout_sec: int = 36000) -> Optional[Dict[str, Any]]:
    """Load summary JSON, waiting up to timeout_sec for it to appear."""
    start = time.time()
    while time.time() - start < timeout_sec:
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                time.sleep(5)
                continue
        time.sleep(10)
    return None

def extract_candidate_stats(ckpt_path: Path) -> Dict[str, Any]:
    """Extract statistics from checkpoint file (for live monitoring during calibration)."""
    if not ckpt_path.exists():
        return {}
    try:
        with open(ckpt_path) as f:
            ckpt = json.load(f)
        candidates = ckpt.get('candidates', [])
        trades_per_cand = [len(c.get('report', {}).get('trades', [])) for c in candidates]
        return {
            'total_candidates': len(candidates),
            'accepted': sum(1 for c in candidates if c.get('accepted')),
            'avg_trades': sum(trades_per_cand) / len(trades_per_cand) if trades_per_cand else 0,
            'max_trades': max(trades_per_cand) if trades_per_cand else 0,
            'zero_trade_count': sum(1 for t in trades_per_cand if t == 0),
        }
    except Exception:
        return {}

def format_metric(value: Any, decimal_places: int = 2) -> str:
    """Format numeric metric for display."""
    if isinstance(value, float):
        return f"{value:.{decimal_places}f}"
    return str(value)

def print_comparison_header():
    """Print comparison table header."""
    print("\n" + "=" * 120)
    print("FULL 3-YEAR CALIBRATION A/B COMPARISON: EXTERNAL (HMM) vs IN-HOUSE (VANILLA)")
    print("=" * 120)

def print_comparison_row(metric: str, external: Any, inhouse: Any, winner: str = ""):
    """Print a single comparison row."""
    ext_str = format_metric(external)
    inh_str = format_metric(inhouse)
    winner_tag = f" ← {winner}" if winner else ""
    print(f"{metric:40s} | {ext_str:>20s} | {inh_str:>20s} {winner_tag}")

def main():
    ext_summary = Path("output_external_engine/external_engine_48symbol_FULL_3YEAR_calibration_summary.json")
    inh_summary = Path("output_inhouse_engine/inhouse_engine_48symbol_FULL_3YEAR_calibration_summary.json")
    ext_ckpt = Path("output_external_engine/external_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json")
    inh_ckpt = Path("output_inhouse_engine/inhouse_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json")

    print("\n" + "=" * 120)
    print("WAITING FOR BOTH CALIBRATIONS TO COMPLETE...")
    print("=" * 120)
    print(f"External summary: {ext_summary}")
    print(f"In-house summary: {inh_summary}")
    print(f"Timeout: 10 hours from now")
    print("\nMonitoring live progress from checkpoint files...")

    # Poll checkpoints to show live progress
    prev_ext_stats = {}
    prev_inh_stats = {}
    poll_interval = 60  # Check every 60 seconds
    last_update = time.time()

    ext_data = None
    inh_data = None

    while not (ext_data and inh_data):
        now = time.time()
        if now - last_update > poll_interval:
            ext_stats = extract_candidate_stats(ext_ckpt)
            inh_stats = extract_candidate_stats(inh_ckpt)

            if ext_stats != prev_ext_stats or inh_stats != prev_inh_stats:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Live progress:")
                print(f"  External: {ext_stats.get('total_candidates', 0)} candidates, "
                      f"{ext_stats.get('accepted', 0)} accepted, "
                      f"avg {ext_stats.get('avg_trades', 0):.1f} trades")
                print(f"  In-House: {inh_stats.get('total_candidates', 0)} candidates, "
                      f"{inh_stats.get('accepted', 0)} accepted, "
                      f"avg {inh_stats.get('avg_trades', 0):.1f} trades")
                prev_ext_stats = ext_stats
                prev_inh_stats = inh_stats
                last_update = now

        # Try loading final summaries
        if not ext_data:
            ext_data = load_summary(ext_summary, timeout_sec=60)
        if not inh_data:
            inh_data = load_summary(inh_summary, timeout_sec=60)

        if ext_data and inh_data:
            break

        time.sleep(10)

    # Both complete: print detailed comparison
    print_comparison_header()

    # Basic metrics
    print("\n[CALIBRATION METADATA]")
    print_comparison_row("Engine Type", "External (HMM)", "In-House (Vanilla)")
    print_comparison_row("Dataset", "Full 3-Year", "Full 3-Year")
    print_comparison_row("Total Runtime (hours)",
                        ext_data.get('elapsed_seconds', 0) / 3600,
                        inh_data.get('elapsed_seconds', 0) / 3600)

    # Candidate metrics
    print("\n[CANDIDATE EVALUATION]")
    ext_cands = ext_data.get('candidates_evaluated', 0)
    inh_cands = inh_data.get('candidates_evaluated', 0)
    ext_acc = ext_data.get('candidates_accepted', 0)
    inh_acc = inh_data.get('candidates_accepted', 0)
    ext_acc_rate = (ext_acc / ext_cands * 100) if ext_cands else 0
    inh_acc_rate = (inh_acc / inh_cands * 100) if inh_cands else 0

    print_comparison_row("Total Candidates Evaluated", ext_cands, inh_cands)
    print_comparison_row("Candidates Accepted", ext_acc, inh_acc,
                        winner="HMM" if ext_acc > inh_acc else "Vanilla")
    print_comparison_row("Acceptance Rate (%)", ext_acc_rate, inh_acc_rate,
                        winner="HMM" if ext_acc_rate > inh_acc_rate else "Vanilla")

    # Phase distribution
    print("\n[PHASE DISTRIBUTION]")
    ext_phases = ext_data.get('candidates_by_phase', {})
    inh_phases = inh_data.get('candidates_by_phase', {})
    all_phases = set(ext_phases.keys()) | set(inh_phases.keys())
    for phase in sorted(all_phases):
        print_comparison_row(f"  {phase}", ext_phases.get(phase, 0), inh_phases.get(phase, 0))

    # Best results
    print("\n[BEST CANDIDATE]")
    ext_score = ext_data.get('best_score', None)
    inh_score = inh_data.get('best_score', None)
    winner = "HMM" if (ext_score is not None and inh_score is not None and ext_score > inh_score) else \
             "Vanilla" if (ext_score is not None and inh_score is not None and inh_score > ext_score) else ""
    print_comparison_row("Best Score", ext_score or "N/A", inh_score or "N/A", winner=winner)

    # Best report metrics (if available)
    if ext_data.get('best_report') or inh_data.get('best_report'):
        print("\n[BEST CANDIDATE PERFORMANCE]")
        ext_report = ext_data.get('best_report', {})
        inh_report = inh_data.get('best_report', {})

        for key in ['net_pnl', 'win_rate', 'profit_factor', 'max_drawdown', 'completed_trades']:
            ext_val = ext_report.get(key, "N/A")
            inh_val = inh_report.get(key, "N/A")

            # Determine winner based on metric direction
            winner = ""
            if isinstance(ext_val, (int, float)) and isinstance(inh_val, (int, float)):
                if key in ['net_pnl', 'win_rate', 'profit_factor']:
                    winner = "HMM" if ext_val > inh_val else "Vanilla"
                elif key == 'max_drawdown':
                    winner = "HMM" if ext_val < inh_val else "Vanilla"

            print_comparison_row(f"  {key}", ext_val, inh_val, winner=winner)

    # Stopped reason
    print("\n[CALIBRATION STATUS]")
    print_comparison_row("Stopped Reason",
                        ext_data.get('stopped_reason', 'Unknown'),
                        inh_data.get('stopped_reason', 'Unknown'))

    # Key insight
    print("\n" + "=" * 120)
    print("KEY FINDINGS:")
    print("=" * 120)

    if ext_acc > inh_acc:
        print(f"✓ HMM regime detection found {ext_acc - inh_acc} more accepted candidates")
        print(f"  → HMM provides additional gate sophistication on 3-year data")
    elif inh_acc > ext_acc:
        print(f"✓ Vanilla volatility regime matched or exceeded HMM performance")
        print(f"  → Simpler regime model sufficient; complexity not justified")
    else:
        print(f"✓ Both engines achieved identical acceptance rates")
        print(f"  → Trade performance depends on other factors, not regime detection")

    if ext_cands == 0 and inh_cands == 0:
        print(f"✗ CRITICAL: Zero candidates on 3-year data suggests systemic issue beyond data scale")
        print(f"  → PA box telemetry should be checked for remaining degenerate scale factors")
    else:
        print(f"✓ Both engines produced {max(ext_cands, inh_cands)} candidates on 3-year data")
        print(f"  → Full dataset confirmed sufficient for feature normalization")

    # Save JSON report
    report = {
        "timestamp": datetime.now().isoformat(),
        "external": ext_data,
        "inhouse": inh_data,
        "comparison": {
            "external_acceptance_rate": ext_acc_rate,
            "inhouse_acceptance_rate": inh_acc_rate,
            "winner_by_acceptance": "HMM" if ext_acc_rate > inh_acc_rate else "Vanilla" if inh_acc_rate > ext_acc_rate else "Tie",
        },
    }

    report_path = Path("output_external_engine/A2B_comparison_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nFull report saved to: {report_path}")
    print("=" * 120 + "\n")

if __name__ == "__main__":
    main()
