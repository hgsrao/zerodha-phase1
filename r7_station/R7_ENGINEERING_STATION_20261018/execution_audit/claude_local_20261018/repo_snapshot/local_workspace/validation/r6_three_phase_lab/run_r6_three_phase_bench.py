#!/usr/bin/env python3
"""OFFLINE electrical measurement / reactive-droop / closed-loop study for R6 AVR.

All electrical measurements are SYNTHETIC and remain separate from financial
ExposureAVR and native Engine B/C. No broker access or orders.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from math import acos, atan2, hypot, sqrt

from r6_three_phase import (
    GeneratorRating, BenchGeneratorConfig, ThreePhaseGeneratorBench,
    build_bench_controller, ThreePhaseRMS, SOURCE_BENCH,
    measure_three_phase, reactive_droop_calibration,
)
from r6_avr_core import CausalID


BAYS = (
    ("GTG2_TECH_TELECOM", 0.075),
    ("CSTG1_BFSI", 0.10),
)
STUDY_DROOPS = (0.0, 0.04, 0.075, 0.10)
T0 = datetime(2026, 8, 6, 13, 25, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def check_power_triangle(rating: GeneratorRating) -> list[dict]:
    """Known balanced three-phase 100 MVA model measurements, not live sensors."""
    cases = (("unity", 1., 0.), ("lagging_0.8", .8, .6),
             ("leading_0.8", .8, -.6))
    rows = []
    for n, (name, p_frac, q_frac) in enumerate(cases, start=1):
        p = p_frac * rating.rated_mva
        q = q_frac * rating.rated_mva
        apparent = hypot(p, q)
        phi = atan2(q, p)
        current = apparent / (sqrt(3) * rating.rated_voltage_ll_kv)
        ident = CausalID(n, (T0 + timedelta(seconds=n)).isoformat(),
                         rating.bay_id, "ELECTRICAL_BENCH")
        met = measure_three_phase(
            ThreePhaseRMS(ident, rating.rated_voltage_ll_kv,
                          current, phi, SOURCE_BENCH,
                          meter_p_mw=p, meter_q_mvar=q),
            rating,
        )
        rows.append({
            "case": name,
            "voltage_ll_kv": met.voltage_ll_kv,
            "current_line_ka": met.current_line_ka,
            "phi_deg": None if met.phi_rad is None else met.phi_rad * 180 / 3.141592653589793,
            "cos_phi": met.cos_phi,
            "mw": met.active_mw,
            "mvar": met.reactive_mvar,
            "mva": met.apparent_mva,
            "q_pu": met.q_pu,
            "pf_status": met.power_factor_status,
        })
    return rows


def replay_case(*, bay: str, droop: float, args: argparse.Namespace) -> tuple[dict, list[dict]]:
    rating = GeneratorRating(bay, args.rated_voltage_kv, args.rated_mva)
    plant = ThreePhaseGeneratorBench(BenchGeneratorConfig(rating))
    controller = build_bench_controller(rating, droop)
    previous_command = 1.0
    rows = []
    max_overshoot = 0.0
    for bar in range(1, args.bars + 1):
        instant = (T0 + timedelta(seconds=bar)).isoformat()
        # Deterministic load disturbance; synthetic, not historical market data.
        reactive_disturbance = (
            0.0 if bar <= args.bars // 3 else
            +0.02 * args.rated_mva if bar <= 2 * args.bars // 3 else
            -0.02 * args.rated_mva
        )
        out = plant.advance(
            bar_index=bar,
            timestamp=instant,
            previous_excitation_command_pu=previous_command,
            bus_voltage_pu=args.bus_voltage_pu,
            mechanical_active_power_mw=args.mechanical_mw,
            reactive_disturbance_mvar=reactive_disturbance,
            dt=1.0,
        )
        state = controller.step(out.avr_sample, dt=1.0)
        if state.trip:
            raise RuntimeError(f"AVR study tripped for {bay} at bar {bar}: {state.trip}")
        previous_command = state.excitation_command_pu
        error = state.voltage_error
        max_overshoot = max(max_overshoot, abs(error))
        rows.append({
            "bay": bay, "droop": droop, "bar": bar,
            "timestamp": instant, "bus_v_pu": args.bus_voltage_pu,
            "terminal_v_pu": out.electrical.voltage_pu,
            "q_pu": out.electrical.q_pu,
            "p_pu": out.electrical.p_pu,
            "mva": out.electrical.apparent_mva,
            "mw": out.electrical.active_mw,
            "mvar": out.electrical.reactive_mvar,
            "phi_deg": None if out.electrical.phi_rad is None else
                       out.electrical.phi_rad * 180 / 3.141592653589793,
            "cos_phi": out.electrical.cos_phi,
            "field_actual_pu": out.field_actual_pu,
            "field_command_pu": previous_command,
            "v_ref_effective_pu": state.vref_effective_pu,
            "voltage_error_pu": error,
            "limiter": state.limiter,
            "trip": state.trip,
            "measurement_provenance": out.electrical.source,
            "reactive_disturbance_mvar": reactive_disturbance,
        })
    last = rows[-1]
    summary = {
        "bay": bay, "reactive_droop": droop,
        "sample_count": len(rows),
        "max_abs_voltage_error_pu": max_overshoot,
        "final_voltage_pu": last["terminal_v_pu"],
        "final_voltage_error_pu": last["voltage_error_pu"],
        "final_q_pu": last["q_pu"],
        "final_mvar": last["mvar"],
        "final_field_pu": last["field_actual_pu"],
        "final_command_pu": last["field_command_pu"],
        "final_pf": last["cos_phi"],
        "trip_count": sum(bool(row["trip"]) for row in rows),
        "q_base": "signed Q (MVAR) / rated generator apparent power (MVA)",
        "source": "BENCH_SIMULATED",
    }
    return summary, rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="r6_three_phase_results")
    parser.add_argument("--bars", type=int, default=120)
    parser.add_argument("--rated-voltage-kv", type=float, default=11.0)
    parser.add_argument("--rated-mva", type=float, default=100.0)
    parser.add_argument("--mechanical-mw", type=float, default=30.0)
    parser.add_argument("--bus-voltage-pu", type=float, default=.985)
    args = parser.parse_args()
    if (args.bars < 30 or args.rated_voltage_kv <= 0 or args.rated_mva <= 0
            or args.bus_voltage_pu <= 0 or not 0 <= args.mechanical_mw <= args.rated_mva):
        parser.error("invalid bench limits: bars>=30, positive bases, 0<=MW<=rated MVA")
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=== THREE-PHASE ELECTRICAL CHECK — SYNTHETIC / OFFLINE ===")
    triangle = check_power_triangle(GeneratorRating(BAYS[0][0],
                                     args.rated_voltage_kv, args.rated_mva))
    for r in triangle:
        print(f"{r['case']:>12s}: φ={r['phi_deg']:>7.2f}°  PF={r['cos_phi']:.4f} "
              f" P={r['mw']:.2f}MW  Q={r['mvar']:.2f}MVAR  S={r['mva']:.2f}MVA")

    print("\n=== CASCADED AVR REACTIVE-DROOP SWEEP ===")
    records = []
    lines = []
    for bay, proposed in BAYS:
        for droop in sorted(set(STUDY_DROOPS + (proposed,))):
            result, samples = replay_case(bay=bay, droop=droop, args=args)
            records.append(result)
            lines.extend(samples)
            print(f"{bay:20s} droop={droop:5.3f}  V={result['final_voltage_pu']:.6f} "
                  f" Qpu={result['final_q_pu']:+.5f} "
                  f"Err={result['final_voltage_error_pu']:+.6f} "
                  f"field={result['final_field_pu']:.5f}")

    with (out_dir / "r6_three_phase_samples.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(lines[0]))
        writer.writeheader()
        writer.writerows(lines)

    payload = {
        "model": "DETERMINISTIC_SYNTHETIC_BALANCED_3PHASE",
        "disclaimer": "Not a real synchronous generator, no measured field, CT/PT or real MVAR; no live or paper orders submitted.",
        "rated_voltage_ll_kv": args.rated_voltage_kv,
        "rated_mva": args.rated_mva,
        "mechanical_active_mw": args.mechanical_mw,
        "power_factor_cases": triangle,
        "droop_study": records,
        "normalization": "q_pu = signed three-phase MVAR / rated generator MVA",
        "droop_semantics": "Vref_eff_pu = Vref_pu - Rq * q_pu; experimental values only",
        "protection": "the existing VoltageAVR has priority; no protections disabled",
    }
    (out_dir / "r6_three_phase_report.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n"
    )
    print("\nWrote:", out_dir / "r6_three_phase_report.json")
    print("Wrote:", out_dir / "r6_three_phase_samples.csv")
    print("No broker orders or changes to native R6 code.")


if __name__ == "__main__":
    main()
