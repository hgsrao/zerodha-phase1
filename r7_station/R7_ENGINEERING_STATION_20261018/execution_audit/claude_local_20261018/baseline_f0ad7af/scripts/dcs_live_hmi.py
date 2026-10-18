#!/usr/bin/env python3
"""
CCPP Dual-Loop Central DCS Supervisory HMI Dashboard
Polls candidate output artifacts, evaluates Mark V MVG limiter states,
and displays turbine bay parameters with live terminal refresh.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Paths & Network Endpoints
PROJECT_ROOT = Path(os.environ.get("HOME", "/home/srinivas")) / "projects/zerodha-phase1"
TRIAL0_RESULT = Path("/tmp/r5_stage_a_trial_0_results.json")
TRIAL1_RESULT = Path("/tmp/r5_stage_a_trial_1_results.json")
SSH_KEY = Path(os.environ.get("HOME", "/home/srinivas")) / ".ssh/id_ed25519_r5_worker"
LAPTOP_HOST = os.environ.get("R5_WORKER_SSH_TARGET", "")  # same variable as the Step-5 executor

ANSI_CLEAR = "\033[2J\033[H"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"
ANSI_RESET = "\033[0m"


def poll_pid_status(pid_name: str) -> dict:
    try:
        cmd = f"ps -eo pid,stat,%cpu,%mem,etime,args | grep '{pid_name}' | grep -v grep | head -n 1"
        out = subprocess.check_output(cmd, shell=True, text=True).strip()
        if not out:
            return {"active": False, "pid": "-", "cpu": "0.0%", "mem": "0.0%", "etime": "-"}
        parts = out.split(None, 4)
        return {
            "active": True,
            "pid": parts[0],
            "stat": parts[1],
            "cpu": f"{float(parts[2]):.1f}%",
            "mem": f"{float(parts[3]):.1f}%",
            "etime": parts[4].split()[0] if len(parts) > 4 else "-",
        }
    except Exception:
        return {"active": False, "pid": "-", "cpu": "0.0%", "mem": "0.0%", "etime": "-"}


def poll_laptop_status() -> dict:
    if not LAPTOP_HOST:
        return {"active": False, "pid": "-", "cpu": "0.0%", "etime": "-"}
    try:
        cmd = (
            f"ssh -i '{SSH_KEY}' -o BatchMode=yes -o ConnectTimeout=1 {LAPTOP_HOST} "
            f"\"ps -eo pid,stat,%cpu,%mem,etime,args | grep 'run_r5_step5_candidate' | grep -v grep | head -n 1\""
        )
        out = subprocess.check_output(cmd, shell=True, text=True, timeout=2).strip()
        if not out:
            return {"active": False, "pid": "-", "cpu": "0.0%", "etime": "-"}
        parts = out.split(None, 4)
        return {
            "active": True,
            "pid": parts[0],
            "stat": parts[1],
            "cpu": f"{float(parts[2]):.1f}%",
            "etime": parts[4].split()[0] if len(parts) > 4 else "-",
        }
    except Exception:
        return {"active": False, "pid": "-", "cpu": "OFFLINE", "etime": "-"}


def read_results_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def calculate_mark_v_mvg(drawdown_pct: float, stress_factor: float = 1.0):
    fsrn = max(0.20, 1.00 - (0.05 * stress_factor * 1.5))
    fsrt = max(0.15, 1.00 - (drawdown_pct * 12.0))
    fsra = 0.9500
    fsrs = 1.0000
    fsrm = 1.0000
    fsrmin = 0.1500

    candidates = {"FSRN": fsrn, "FSRT": fsrt, "FSRA": fsra, "FSRS": fsrs, "FSRM": fsrm}
    binding_key = min(candidates, key=candidates.get)
    fsr_sel = max(fsrmin, candidates[binding_key])
    return fsr_sel, binding_key, candidates


def main():
    try:
        while True:
            t0_stat = poll_pid_status("run_r5_step5_candidate.py")
            t1_stat = poll_laptop_status()
            t0_data = read_results_json(TRIAL0_RESULT)
            t1_data = read_results_json(TRIAL1_RESULT)

            t0_agg = t0_data.get("aggregate", {})
            t1_agg = t1_data.get("aggregate", {})

            dd_t0 = t0_agg.get("mtm_max_drawdown_fraction", 0.0084)
            pnl_t0 = t0_agg.get("net_pnl", 0.0)
            trades_t0 = t0_agg.get("completed_trades", 0)

            fsr_val, binding_name, fsr_gates = calculate_mark_v_mvg(dd_t0)

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            lines = []
            lines.append(f"{ANSI_CYAN}{'=' * 92}{ANSI_RESET}")
            lines.append(
                f"{ANSI_BOLD} CCPP DUAL-LOOP CENTRAL DCS HMI SUPERVISORY MONITOR            {now_str} {ANSI_RESET}"
            )
            lines.append(f"{ANSI_CYAN}{'=' * 92}{ANSI_RESET}")

            desk_st = f"{ANSI_GREEN}COMPUTING (PID {t0_stat['pid']}){ANSI_RESET}" if t0_stat['active'] else f"{ANSI_YELLOW}STANDBY / COMPLETE{ANSI_RESET}"
            lap_st = f"{ANSI_GREEN}COMPUTING (PID {t1_stat['pid']}){ANSI_RESET}" if t1_stat['active'] else f"{ANSI_YELLOW}STANDBY / COMPLETE{ANSI_RESET}"
            lines.append(f" {ANSI_BOLD}[DISTRIBUTED COMPUTE CORES]{ANSI_RESET}")
            lines.append(f"  Worker 01 (B760M Workstation): {desk_st} | CPU: {t0_stat['cpu']} | Time: {t0_stat['etime']}")
            lines.append(f"  Worker 02 (Blade 15 Laptop)  : {lap_st} | CPU: {t1_stat['cpu']} | Time: {t1_stat['etime']}")
            lines.append(f"{'-' * 92}")

            lines.append(f" {ANSI_BOLD}[SWITCHYARD 400kV / ECS SUPERVISORY FEEDBACK LOOP]{ANSI_RESET}")
            lines.append("  Grid Interconnect (52G) : SYNCHRONIZED (NIFTY 50 / INDIA VIX)")
            lines.append("  Plant Control Mode      : 2-LOOP CLOSED_LOOP_PAPER_APPLY")
            lines.append(f"  ANSI 86 Lockout Relay   : {ANSI_GREEN}NORMAL [RESET - NO TRIP]{ANSI_RESET}")
            lines.append(f"  Primary Frequency Bias  : Δf = +0.02 Hz | Droop Reference Deadband: ±0.03 Hz")
            lines.append(f"{'-' * 92}")

            lines.append(f" {ANSI_BOLD}{'BAY':<6} | {'UNIT':<18} | {'DROOP':<7} | {'FSR':<7} | {'52G BKR':<9} | {'DISPATCH (MW)':<16} | {'STATE'}{ANSI_RESET}")
            lines.append(f"{'-' * 92}")

            bays = [
                ("BAY 01", "GTG 1 (Nifty Auto/NRG)", "4.0%", f"{fsr_val:.3f}", "CLOSED", "INR 2,40,000", "BASE_LOAD"),
                ("BAY 02", "GTG 2 (Tech/Telecom)",   "4.0%", f"{fsr_val*0.98:.3f}", "CLOSED", "INR 2,10,000", "BASE_LOAD"),
                ("BAY 03", "CSTG 1 (Banking/Fin)",   "5.5%", f"{fsr_val*0.92:.3f}", "CLOSED", "INR 1,80,000", "CONDENSING"),
                ("BAY 04", "CSTG 2 (Metals/Infra)",  "6.5%", f"{fsr_val*0.87:.3f}", "CLOSED", "INR 1,50,000", "CONDENSING"),
                ("BAY 05", "BPSTG (FMCG/Pharma)",    "7.5%", f"{fsr_val*0.80:.3f}", "CLOSED", "INR 1,20,000", "BACK_PRESSURE"),
            ]

            for bay, unit, droop, fsr, bkr, mw, st in bays:
                lines.append(f" {bay:<6} | {unit:<18} | {droop:<7} | {fsr:<7} | {bkr:<9} | {mw:<16} | {ANSI_GREEN}{st}{ANSI_RESET}")

            lines.append(f"{'-' * 92}")

            lines.append(f" {ANSI_BOLD}[SPEEDTRONIC MARK V MINIMUM VALUE GATE (MVG) STATUS]{ANSI_RESET}")
            binding_tag = f"{ANSI_RED}{binding_name} [BINDING]{ANSI_RESET}"
            lines.append(
                f"  FSRN (Droop Speed) : {fsr_gates['FSRN']:.4f}  |  "
                f"FSRT (Exhaust Temp): {fsr_gates['FSRT']:.4f}  |  "
                f"Active Limiter: {binding_tag}"
            )
            lines.append(
                f"  FSRA (Acceleration): {fsr_gates['FSRA']:.4f}  |  "
                f"FSRS (Startup Ramp): {fsr_gates['FSRS']:.4f}  |  "
                f"FSRMIN Flameout : 0.1500"
            )
            lines.append(f"{'-' * 92}")

            lines.append(f" {ANSI_BOLD}[HRSG THERMAL MASS-ENERGY BALANCE & CLOSED-LOOP TELEMETRY]{ANSI_RESET}")
            lines.append(f"  HP Steam Header Pressure : 104.8 bar | Enthalpy Conservation: Σ(Bays) + Res ≡ Total MW")
            lines.append(f"  Trial 0 Cumulative Net PnL: INR {pnl_t0:,.2f} | Completed Trades: {trades_t0} | Max DD: {dd_t0:.3%}")
            if t1_data:
                lines.append(
                    f"  Trial 1 (Laptop) Net PnL  : INR {t1_agg.get('net_pnl', 0.0):,.2f} | "
                    f"Score: {t1_agg.get('score', 0.0):.4f} | Status: {t1_agg.get('status', 'OK')}"
                )
            lines.append(f"{ANSI_CYAN}{'=' * 92}{ANSI_RESET}")
            lines.append(" Press [Ctrl+C] to exit HMI monitor.")

            sys.stdout.write(ANSI_CLEAR + "\n".join(lines) + "\n")
            sys.stdout.flush()

            time.sleep(3)

    except KeyboardInterrupt:
        print("\nExiting DCS HMI Supervisory Monitor.")


if __name__ == "__main__":
    main()
