"""Shared command-line options for the R5 controller-authority modes (Engines A, B and C runners).

Defaults reproduce the baseline engine exactly; every non-default value is an opt-in behaviour
change (see docs/r5_parameter_authority/BEHAVIOUR_CHANGES_REQUIRING_APPROVAL.md), except
--authority-telemetry, which records telemetry only.
"""
from __future__ import annotations

import argparse
from typing import Any, Dict


def add_authority_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("R5 controller authority (opt-in)")
    group.add_argument("--final-exit-authority", action="store_true",
                       help="final execution controller EXIT/EXIT_NEXT_BAR arms a next-open exit")
    group.add_argument("--governor-position-mode", choices=["legacy", "v3"], default="legacy")
    group.add_argument("--avr-voltage-mode", choices=["legacy", "measured"], default="legacy")
    group.add_argument("--dispatcher-allocation-mode", choices=["legacy", "bounded"], default="legacy")
    group.add_argument("--hrsg-correlation-mode", choices=["off", "causal_bay_returns"], default="off")
    group.add_argument("--entry-fill-anchor-mode", choices=["reanchor_distances", "box6_levels"],
                       default="reanchor_distances", help="B/C: keep the Box 6 PID-derived stop/target levels at fill")
    group.add_argument("--unit-protection-mode", choices=["pre_synchronization", "pre_sync_and_in_flight"],
                       default="pre_synchronization", help="also run SEL-300G ANSI 87G / 32R on every held bar")
    group.add_argument("--authority-telemetry", action="store_true",
                       help="record controller parameter / AVR / dispatcher / HRSG telemetry (no decision effect)")


def authority_kwargs(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "final_exit_authority": bool(args.final_exit_authority),
        "governor_position_mode": args.governor_position_mode,
        "avr_voltage_mode": args.avr_voltage_mode,
        "dispatcher_allocation_mode": args.dispatcher_allocation_mode,
        "hrsg_correlation_mode": args.hrsg_correlation_mode,
        "authority_telemetry": bool(args.authority_telemetry),
        "entry_fill_anchor_mode": args.entry_fill_anchor_mode,
        "unit_protection_mode": args.unit_protection_mode,
    }
