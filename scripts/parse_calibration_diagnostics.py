#!/usr/bin/env python3
"""Automated diagnostic parser for 48-symbol calibration sweeps.

Extracts critical metrics from calibration results to determine if the
optimizer solved the PID-death-spiral (boa constrictor) problem or if
structural decoupling is required.

Usage:
    python3 scripts/parse_calibration_diagnostics.py [--external] [--inhouse] [--both]

Default: --both (parses both engines if results exist)

Output: Clear diagnostic panel showing:
- Saturation exit viability (THE smoking gun)
- Optimizer's parameter choices (desperation indicators)
- Win rate and P&L improvement
"""

import json
import sys
from pathlib import Path
from typing import Dict, Any, Optional


class CalibrationDiagnostics:
    """Parse and diagnose calibration results."""

    BASELINE_METRICS = {
        "saturation_exits": 3,
        "total_trades": 189,
        "win_rate": 8.99,
        "net_pnl": -38940.57,
        "stop_exit_count": 147,
        "stop_exit_pct": 77.8,
    }

    SATURATION_EXIT_THRESHOLDS = {
        "solved": (15, float('inf')),      # > 15 exits = death spiral solved
        "marginal": (5, 15),               # 5-15 exits = marginal improvement
        "structural_flaw": (0, 5),         # < 5 exits = structural problem confirmed
    }

    def __init__(self, output_dir: Path = None):
        if output_dir is None:
            output_dir = Path(__file__).resolve().parents[1] / "output_external_engine"
        self.output_dir = output_dir
        self.results = {}

    def parse_external(self) -> Optional[Dict[str, Any]]:
        """Parse external engine calibration results."""
        summary_path = self.output_dir / "external_engine_48symbol_1month_calibration_summary.json"
        if not summary_path.exists():
            print(f"⚠ External engine results not found: {summary_path}")
            return None

        try:
            with open(summary_path) as f:
                data = json.load(f)
            self.results["external"] = self._extract_diagnostics("External Engine (HMM)", data)
            return self.results["external"]
        except Exception as e:
            print(f"❌ Failed to parse external results: {e}")
            return None

    def parse_inhouse(self) -> Optional[Dict[str, Any]]:
        """Parse in-house engine calibration results."""
        summary_path = self.output_dir / "inhouse_engine_48symbol_1month_calibration_summary.json"
        if not summary_path.exists():
            print(f"⚠ In-house engine results not found: {summary_path}")
            return None

        try:
            with open(summary_path) as f:
                data = json.load(f)
            self.results["inhouse"] = self._extract_diagnostics("In-House Engine (Volatility)", data)
            return self.results["inhouse"]
        except Exception as e:
            print(f"❌ Failed to parse in-house results: {e}")
            return None

    def _extract_diagnostics(self, engine_name: str, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Extract diagnostic metrics from calibration summary."""
        best_report = summary.get("best_report", {})
        best_params = summary.get("best_params", {})

        # Extract exit metrics
        saturation_pa = best_report.get("saturation_exit_pa_count", 0)
        saturation_studies = best_report.get("saturation_exit_studies_count", 0)
        total_saturation = saturation_pa + saturation_studies

        # Extract exit reason breakdown
        reason_breakdown = best_report.get("reason_breakdown", {})
        stop_count = reason_breakdown.get("stop", 0)
        stop_gap_count = reason_breakdown.get("stop_gap", 0)
        total_stops = stop_count + stop_gap_count
        total_trades = best_report.get("completed_trades", 0)
        stop_pct = (total_stops / total_trades * 100) if total_trades else 0

        # Extract outcome metrics
        net_pnl = best_report.get("net_pnl", 0)
        win_rate = best_report.get("win_rate_pct", 0)

        # Extract optimizer's parameter choices
        trailing_stop_atr = best_params.get("trailing_stop_atr_mult", "N/A")
        saturation_exit_bars_param = best_params.get("saturation_exit_bars", "N/A")
        pid_kp_exit = best_params.get("pid_kp_exit", "N/A")
        pid_ki_exit = best_params.get("pid_ki_exit", "N/A")
        pid_kd_exit = best_params.get("pid_kd_exit", "N/A")

        # Diagnostic assessment
        sat_exit_diagnosis = self._diagnose_saturation_exits(total_saturation)
        pnl_improvement = net_pnl - self.BASELINE_METRICS["net_pnl"]
        win_rate_improvement = win_rate - self.BASELINE_METRICS["win_rate"]

        return {
            "engine": engine_name,
            "saturation_exits": {
                "total": total_saturation,
                "pa": saturation_pa,
                "studies": saturation_studies,
                "vs_baseline": total_saturation - self.BASELINE_METRICS["saturation_exits"],
                "diagnosis": sat_exit_diagnosis,
            },
            "mechanical_stops": {
                "stop_count": stop_count,
                "stop_gap_count": stop_gap_count,
                "total_stops": total_stops,
                "percentage": stop_pct,
                "vs_baseline_pct": stop_pct - self.BASELINE_METRICS["stop_exit_pct"],
            },
            "trades": {
                "completed": total_trades,
                "win_rate": win_rate,
                "win_rate_improvement": win_rate_improvement,
            },
            "pnl": {
                "net": net_pnl,
                "improvement": pnl_improvement,
                "improvement_pct": (pnl_improvement / abs(self.BASELINE_METRICS["net_pnl"]) * 100) if self.BASELINE_METRICS["net_pnl"] else 0,
            },
            "optimizer_parameters": {
                "trailing_stop_atr_mult": trailing_stop_atr,
                "saturation_exit_bars": saturation_exit_bars_param,
                "pid_kp_exit": pid_kp_exit,
                "pid_ki_exit": pid_ki_exit,
                "pid_kd_exit": pid_kd_exit,
            },
            "raw": summary,
        }

    def _diagnose_saturation_exits(self, count: int) -> str:
        """Diagnose saturation exit viability based on count."""
        for diagnosis, (min_val, max_val) in self.SATURATION_EXIT_THRESHOLDS.items():
            if min_val <= count <= max_val:
                return diagnosis
        return "unknown"

    def print_diagnostic_panel(self) -> None:
        """Print formatted diagnostic panel."""
        print("\n" + "="*90)
        print("CALIBRATION DIAGNOSTIC PANEL: Death Spiral Analysis")
        print("="*90)

        if not self.results:
            print("⚠ No results to display. Run parse_external() or parse_inhouse() first.")
            return

        for engine_key, diagnostics in self.results.items():
            self._print_engine_diagnostics(diagnostics)
            print()

    def _print_engine_diagnostics(self, diag: Dict[str, Any]) -> None:
        """Print diagnostics for one engine."""
        engine = diag["engine"]
        sat = diag["saturation_exits"]
        stops = diag["mechanical_stops"]
        trades = diag["trades"]
        pnl = diag["pnl"]
        params = diag["optimizer_parameters"]

        # Color-coded diagnosis
        diagnosis_color = self._color_code(sat["diagnosis"])

        print(f"\n{engine}:")
        print("-" * 90)

        # TIER 1: Saturation Exit Viability (THE smoking gun)
        print(f"\n🔴 PRIMARY INDICATOR - Saturation Exit Viability:")
        print(f"   Total Saturation Exits:     {sat['total']:3d} ({sat['vs_baseline']:+3d} vs baseline of {self.BASELINE_METRICS['saturation_exits']})")
        print(f"      PA-based:               {sat['pa']:3d}")
        print(f"      Studies-based:          {sat['studies']:3d}")
        print(f"   Diagnosis: {diagnosis_color}{sat['diagnosis'].upper()}{self._color_reset()}")

        print(f"\n   Mechanical Stop Exits:      {stops['total_stops']:3d} ({stops['percentage']:.1f}%)")
        print(f"      'stop' triggers:        {stops['stop_count']:3d}")
        print(f"      'stop_gap' triggers:    {stops['stop_gap_count']:3d}")
        print(f"      Change vs baseline:     {stops['vs_baseline_pct']:+.1f}% (was {self.BASELINE_METRICS['stop_exit_pct']:.1f}%)")

        if stops["percentage"] > 70:
            print(f"   ⚠ OMEN: Mechanical stops still dominating—PID may still be strangling.")
        elif sat["diagnosis"] == "solved":
            print(f"   ✅ BREAKTHROUGH: Saturation exits elevated AND mechanical stops reduced.")

        # TIER 2: Optimizer's Desperation Signals
        print(f"\n🟡 OPTIMIZER'S PARAMETER CHOICES (Desperation Indicators):")
        print(f"   trailing_stop_atr_mult:     {params['trailing_stop_atr_mult']}")
        self._check_parameter_bounds("trailing_stop_atr_mult", params['trailing_stop_atr_mult'], 1.0, 8.0)

        print(f"   saturation_exit_bars:       {params['saturation_exit_bars']}")
        print(f"      (baseline was hardcoded 5, if pushed to min(2) = compensation mode)")

        print(f"   PID Gains (exit controller):")
        print(f"      kp (proportional):      {params['pid_kp_exit']}")
        print(f"      ki (integral):          {params['pid_ki_exit']}")
        print(f"      kd (derivative):        {params['pid_kd_exit']}")
        self._check_pid_desperation(params)

        # TIER 3: Outcome Metrics
        print(f"\n🟢 OUTCOME METRICS:")
        print(f"   Win Rate:                   {trades['win_rate']:.2f}% ({trades['win_rate_improvement']:+.2f}% vs {self.BASELINE_METRICS['win_rate']:.2f}%)")
        print(f"   Net P&L:                    ₹{pnl['net']:,.2f}")
        print(f"      Improvement:            ₹{pnl['improvement']:,.2f} ({pnl['improvement_pct']:+.1f}%)")

        # Final Verdict
        print(f"\n{'='*90}")
        self._print_final_verdict(sat, stops, pnl)
        print(f"{'='*90}")

    def _check_parameter_bounds(self, name: str, value, min_val: float, max_val: float) -> None:
        """Check if optimizer pushed parameter to extreme bounds."""
        if isinstance(value, str) and value == "N/A":
            return
        try:
            v = float(value)
            if v <= min_val + 0.5:
                print(f"      ⚠ ALARM: Pushed to minimum—optimizer trying to disable/underweight")
            elif v >= max_val - 0.5:
                print(f"      ⚠ ALARM: Pushed to maximum—optimizer at boundary limit")
            else:
                print(f"      ✓ Reasonable middle value")
        except (ValueError, TypeError):
            pass

    def _check_pid_desperation(self, params: Dict[str, Any]) -> None:
        """Check if PID gains were zeroed in desperation."""
        desperation_signs = []
        for gain_name in ["pid_kp_exit", "pid_ki_exit", "pid_kd_exit"]:
            gain_val = params.get(gain_name, "N/A")
            if isinstance(gain_val, (int, float)):
                if gain_val < 0.001:
                    desperation_signs.append(f"{gain_name} ≈ 0")

        if desperation_signs:
            print(f"      ⚠ DESPERATION SIGNAL: {', '.join(desperation_signs)}")
            print(f"      Optimizer zeroed PID to disable the boa constrictor.")

    def _print_final_verdict(self, sat, stops, pnl) -> None:
        """Print final architectural verdict."""
        sat_count = sat["total"]
        stop_pct = stops["percentage"]
        pnl_improvement = pnl["improvement"]

        if sat_count > 15:
            print("\n✅ VERDICT: DEATH SPIRAL SOLVED (via tuning)")
            print("   Saturation exits are now viable. The architecture works with current parameters.")
            print("   → Next: Run full 3-year validation on optimal parameters.")
        elif sat_count >= 5:
            print("\n⚠ VERDICT: MARGINAL IMPROVEMENT (tuning helped, but not breakthrough)")
            print("   Saturation exits improved slightly. PID may still be overly aggressive.")
            print("   → Next: Monitor for diminishing returns. Consider mild structural adjustment.")
        else:
            print("\n❌ VERDICT: STRUCTURAL FLAW CONFIRMED (death spiral persists)")
            print("   Saturation exits remain near zero despite optimizer effort.")
            print("   → IMMEDIATE ACTION: Decouple droop from tightness multiplier (Option 3).")
            print("   → The boa constrictor (PID decay) is mathematically trapping the ATR droop.")

        if stop_pct > 70:
            print(f"\n   ⚠ Mechanical stops still at {stop_pct:.1f}%—primary exit mechanism unchanged.")

        if pnl_improvement > 1000:
            print(f"\n   ✅ P&L improved: ₹{pnl_improvement:,.0f} (good sign regardless of saturation exits)")
        elif pnl_improvement < -1000:
            print(f"\n   ❌ P&L worsened: ₹{pnl_improvement:,.0f} (optimizer couldn't find better ground)")

    @staticmethod
    def _color_code(diagnosis: str) -> str:
        """Return color ANSI code for diagnosis."""
        colors = {
            "solved": "\033[92m",           # Green
            "marginal": "\033[93m",         # Yellow
            "structural_flaw": "\033[91m",  # Red
        }
        return colors.get(diagnosis, "")

    @staticmethod
    def _color_reset() -> str:
        """Return ANSI reset code."""
        return "\033[0m"


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Parse calibration diagnostics to assess death spiral status."
    )
    parser.add_argument(
        "--external", action="store_true",
        help="Parse external engine results only"
    )
    parser.add_argument(
        "--inhouse", action="store_true",
        help="Parse in-house engine results only"
    )
    parser.add_argument(
        "--both", action="store_true", default=True,
        help="Parse both engine results (default)"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Path to calibration output directory"
    )

    args = parser.parse_args()

    diag = CalibrationDiagnostics(output_dir=args.output_dir)

    if args.external or args.both:
        diag.parse_external()

    if args.inhouse or args.both:
        diag.parse_inhouse()

    diag.print_diagnostic_panel()

    # Return exit code based on verdict
    if diag.results:
        for engine_key, result in diag.results.items():
            if result["saturation_exits"]["diagnosis"] == "structural_flaw":
                sys.exit(1)  # Structural flaw detected

    sys.exit(0)  # All clear or marginal


if __name__ == "__main__":
    main()
