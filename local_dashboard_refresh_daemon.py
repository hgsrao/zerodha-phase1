"""
local_dashboard_refresh_daemon.py

Standalone, Claude-free auto-refresh loop for the read-only dashboards
built this session:

    1. Chart Studies Monitor        (5-study composite, 5 symbols)
    2. P02 Native Shadow Portfolio  (Pillar I/II own rules)
    3. P01D Entry Gate Observatory  (real V3.4 authorization pipeline, dry-run)
    4. Map + Context Engine         (2026-08-25 addendum - reaction-at-a-level
                                      entries, 48 symbols, a completely
                                      separate engine from #1)

WHY THIS EXISTS
----------------
The three claude.ai Artifact links shared earlier only update when a
Claude Code session calls the Artifact tool - they cannot be refreshed by
a plain script. This daemon does NOT touch those links. Instead it keeps
three local, self-contained HTML files fresh on disk, each with a
<meta http-equiv="refresh"> tag so a browser tab left open on one just
keeps updating itself. Open the files in local_dashboard/ directly
(double-click, or drag into a browser) - no server, no internet, no
Claude session required.

WHAT IT DOES EACH CYCLE
------------------------
  - Checks the relevant source process is still alive (same
    Get-CimInstance Win32_Process check used manually all session).
    If a process is down, that dashboard is skipped (old file is left
    as-is, not overwritten with stale-labelled-as-live data) and a note
    is printed.
  - Chart Studies Monitor: reads chart_studies_live_state.json + the 5
    chart_studies_bars_<SYMBOL>.csv files directly (same logic used
    manually throughout the session) and splices a fresh SNAPSHOT into
    local_dashboard/chart_studies_template.html's placeholder.
  - P02 / P01D: just invokes their own existing, already-tested
    generator scripts unchanged (p02_native_shadow_observatory.py,
    p01d_entry_gate_observatory.py) and wraps whatever they wrote into a
    standalone, auto-refreshing HTML file.
  - Stops automatically shortly after IST market close (15:30) with one
    final refresh - matches the behaviour of the cron jobs it replaces.

SAFETY
------
  - Read-only. Never imports, edits, or calls anything that places an
    order or touches LIVE_TRADING_ENABLED.
  - Never requires or reads Kite credentials itself - it only reads
    files the already-running, separately-authenticated collector
    scripts have written, and re-runs generator scripts that likewise
    need no credentials of their own.

USAGE
-----
    python local_dashboard_refresh_daemon.py

Run it in its own terminal window, same pattern as every other
long-running tool this session. Stop with Ctrl+C any time.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
LOCAL_DIR = ROOT / "local_dashboard"
LOCAL_DIR.mkdir(exist_ok=True)

REFRESH_SECONDS = 120  # how often this daemon regenerates the files
BROWSER_REFRESH_SECONDS = 120  # how often the open HTML tab reloads itself
MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE = 15, 30

CHART_STUDIES_SYMBOLS = ["BAJFINANCE", "LAURUSLABS", "SBIN", "SUNPHARMA", "BRITANNIA"]
CHART_STUDIES_TEMPLATE = LOCAL_DIR / "chart_studies_template.html"
CHART_STUDIES_STATE = ROOT / "chart_studies_live_state.json"
CHART_STUDIES_OUT = LOCAL_DIR / "chart_studies_dashboard_local.html"

P02_DIR = ROOT / "P02_QUANT_LAB_20260816"
P02_SCRIPT = P02_DIR / "p02_native_shadow_observatory.py"
P02_SOURCE_REPORT = P02_DIR / "p02_native_shadow_observatory_report.html"
P02_OUT = LOCAL_DIR / "p02_native_shadow_dashboard_local.html"

P01D_SCRIPT = ROOT / "p01d_entry_gate_observatory.py"
P01D_SOURCE_REPORT = ROOT / "p01d_entry_gate_observatory_report.html"
P01D_OUT = LOCAL_DIR / "p01d_entry_gate_dashboard_local.html"

# 2026-08-25 addendum: the new 48-symbol Map+Context live engine's own
# real trades (a completely separate engine from Chart Studies Monitor).
MAP_CONTEXT_SCRIPT = ROOT / "map_context_engine_dashboard.py"
MAP_CONTEXT_SOURCE_REPORT = LOCAL_DIR / "map_context_engine_dashboard_report.html"
MAP_CONTEXT_OUT = LOCAL_DIR / "map_context_engine_dashboard_local.html"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def process_running(command_substring: str) -> bool:
    """Same check used manually all session: ask Windows via PowerShell
    whether any python.exe process's command line contains the given
    substring (e.g. 'entry_gate_dry_run.py')."""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" "
                "| Select-Object -ExpandProperty CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:  # pragma: no cover - defensive, printed not raised
        log(f"  process check failed ({exc}); assuming not running")
        return False
    return command_substring in (result.stdout or "")


def wrap_standalone(fragment_html: str, refresh_seconds: int) -> str:
    """Wrap an Artifact-style HTML fragment (starts with <title>/<style>,
    no <html>/<head>/<body>) into a real standalone document with a
    self-reload meta tag, so it renders correctly opened directly from
    disk in any browser."""
    title_match = re.search(r"<title>(.*?)</title>", fragment_html, re.DOTALL)
    title = title_match.group(1) if title_match else "Local Dashboard"
    body = fragment_html
    if title_match:
        body = fragment_html[: title_match.start()] + fragment_html[title_match.end():]
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f'<meta http-equiv="refresh" content="{refresh_seconds}">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{title}</title>\n</head>\n<body>\n{body}</body>\n</html>\n"
    )


def refresh_chart_studies() -> None:
    if not process_running("run_chart_studies_live_monitor.py"):
        log("Chart Studies Monitor: monitor process not running, skipped")
        return
    if not CHART_STUDIES_STATE.exists():
        log("Chart Studies Monitor: state file missing, skipped")
        return

    with open(CHART_STUDIES_STATE, encoding="utf-8") as f:
        state = json.load(f)

    snapshot = {"generated_at": state.get("generated_at"), "symbols": {}}
    for sym in CHART_STUDIES_SYMBOLS:
        sym_state = state.get("symbols", {}).get(sym, {})
        entry = {
            "state": sym_state.get("state"),
            "score": sym_state.get("score"),
            "reads": sym_state.get("reads"),
            "recent_events": sym_state.get("recent_events", []),
            "trades": sym_state.get("trades", []),
        }
        bars_path = ROOT / f"chart_studies_bars_{sym}.csv"
        price_history = []
        if bars_path.exists():
            with open(bars_path, encoding="utf-8") as bf:
                for row in csv.DictReader(bf):
                    ts = row.get("timestamp") or row.get("date") or row.get("Date")
                    close = row.get("close") or row.get("Close")
                    if ts and close:
                        price_history.append({"timestamp": ts, "close": float(close)})
        entry["priceHistory"] = price_history
        snapshot["symbols"][sym] = entry

    if not CHART_STUDIES_TEMPLATE.exists():
        log("Chart Studies Monitor: local_dashboard/chart_studies_template.html missing, skipped")
        return
    template = CHART_STUDIES_TEMPLATE.read_text(encoding="utf-8")
    snapshot_js = json.dumps(snapshot, ensure_ascii=False)
    html = template.replace("__SNAPSHOT_JSON_PLACEHOLDER__", snapshot_js)
    html = html.replace("__REFRESH_SECONDS_PLACEHOLDER__", str(BROWSER_REFRESH_SECONDS))
    CHART_STUDIES_OUT.write_text(html, encoding="utf-8")

    in_count = sum(1 for sym in CHART_STUDIES_SYMBOLS if snapshot["symbols"][sym].get("state") == "IN")
    log(f"Chart Studies Monitor: refreshed ({in_count}/{len(CHART_STUDIES_SYMBOLS)} IN) -> {CHART_STUDIES_OUT.name}")


def refresh_p02() -> None:
    if not process_running("run_p02_live_scan.py"):
        log("P02 Native Shadow Portfolio: run_p02_live_scan.py not running, skipped")
        return
    try:
        result = subprocess.run(
            [sys.executable, P02_SCRIPT.name],
            cwd=str(P02_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        log(f"P02 Native Shadow Portfolio: generator failed to run ({exc})")
        return
    if result.returncode != 0:
        log(f"P02 Native Shadow Portfolio: generator exited {result.returncode}: {result.stderr.strip()[:200]}")
        return
    if not P02_SOURCE_REPORT.exists():
        log("P02 Native Shadow Portfolio: expected report file missing after run, skipped")
        return
    fragment = P02_SOURCE_REPORT.read_text(encoding="utf-8")
    P02_OUT.write_text(wrap_standalone(fragment, BROWSER_REFRESH_SECONDS), encoding="utf-8")
    log(f"P02 Native Shadow Portfolio: {result.stdout.strip()} -> {P02_OUT.name}")


def refresh_p01d() -> None:
    if not process_running("entry_gate_dry_run.py"):
        log("P01D Entry Gate Observatory: entry_gate_dry_run.py not running, skipped")
        return
    try:
        result = subprocess.run(
            [sys.executable, P01D_SCRIPT.name],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        log(f"P01D Entry Gate Observatory: generator failed to run ({exc})")
        return
    if result.returncode != 0:
        log(f"P01D Entry Gate Observatory: generator exited {result.returncode}: {result.stderr.strip()[:200]}")
        return
    if not P01D_SOURCE_REPORT.exists():
        log("P01D Entry Gate Observatory: expected report file missing after run, skipped")
        return
    fragment = P01D_SOURCE_REPORT.read_text(encoding="utf-8")
    P01D_OUT.write_text(wrap_standalone(fragment, BROWSER_REFRESH_SECONDS), encoding="utf-8")
    log(f"P01D Entry Gate Observatory: {result.stdout.strip()} -> {P01D_OUT.name}")


def refresh_map_context_engine() -> None:
    """2026-08-25 addendum. Real entries/exits/P&L from the new 48-symbol
    Map+Context live engine - distinct from refresh_chart_studies() above,
    which only covers the original 5-symbol Chart Studies Monitor."""
    if not process_running("map_context_live_engine.py"):
        log("Map + Context Engine: map_context_live_engine.py not running, skipped")
        return
    try:
        result = subprocess.run(
            [sys.executable, MAP_CONTEXT_SCRIPT.name],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        log(f"Map + Context Engine: generator failed to run ({exc})")
        return
    if result.returncode != 0:
        log(f"Map + Context Engine: generator exited {result.returncode}: {result.stderr.strip()[:200]}")
        return
    if not MAP_CONTEXT_SOURCE_REPORT.exists():
        log("Map + Context Engine: expected report file missing after run, skipped")
        return
    fragment = MAP_CONTEXT_SOURCE_REPORT.read_text(encoding="utf-8")
    MAP_CONTEXT_OUT.write_text(wrap_standalone(fragment, BROWSER_REFRESH_SECONDS), encoding="utf-8")
    log(f"Map + Context Engine: {result.stdout.strip()} -> {MAP_CONTEXT_OUT.name}")


def market_closed_for_today() -> bool:
    now = datetime.now()
    return (now.hour, now.minute) >= (MARKET_CLOSE_HOUR, MARKET_CLOSE_MINUTE)


def main() -> None:
    log("local_dashboard_refresh_daemon starting - read-only, no order-placement code, no Kite credentials used.")
    log(f"Local dashboards will be written to: {LOCAL_DIR}")
    log("Open the *_dashboard_local.html files directly in a browser; each self-reloads.")
    log(f"Refreshing every {REFRESH_SECONDS}s; stops automatically after {MARKET_CLOSE_HOUR:02d}:{MARKET_CLOSE_MINUTE:02d} IST.")

    while True:
        cycle_start = datetime.now()
        log("--- refresh cycle ---")
        refresh_chart_studies()
        refresh_p02()
        refresh_p01d()
        refresh_map_context_engine()

        if market_closed_for_today():
            log("Past market close - this was the final refresh for today. Exiting.")
            break

        elapsed = (datetime.now() - cycle_start).total_seconds()
        sleep_for = max(5.0, REFRESH_SECONDS - elapsed)
        time.sleep(sleep_for)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped by user (Ctrl+C).")
