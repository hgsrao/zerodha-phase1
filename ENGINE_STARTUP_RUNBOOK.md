# Engine Startup Runbook

One page, updated whenever an engine is added/removed. Written 2026-08-24
after a session where credential typos, a wrong working directory, and a
duplicate stale process each caused real confusion. All 6 project-root
`start_*.ps1` scripts below were built the same day to remove those
specific failure points — each one sets its own window title, `cd`s to
the right place itself, and refuses to start with a clear one-line error
instead of a raw traceback if a credential is missing.

**Every engine here is read-only/paper. `LIVE_TRADING_ENABLED` stays
False everywhere; no order-placement code exists in any of these paths.**

## Step 0 — once per morning, before anything else

Zerodha access tokens expire daily. In ONE window (any window, doesn't
matter which):

```powershell
cd C:\Users\Dishan\Documents\Codex\Zerodha_live_bot_3.4_ENTRY_UNKNOWN
.\get_kite_access_token.ps1
```

Follow its prompts (opens a login URL, you paste back the request token).
It prints `KITE_ACCESS_TOKEN is set for this terminal session` at the end
— **copy that token value somewhere you can paste from** (e.g. a scratch
note), because every other window below needs it pasted into THAT
window too:

```powershell
$env:KITE_ACCESS_TOKEN = "..."
```

You do **not** need to set `KITE_API_KEY` anywhere — it's already
persisted at the Windows User level and every new window inherits it
automatically. Only the daily-expiring access token needs re-entering.

## The 8 engines

| # | Name | Script to run | Symbols/scope | Needs Kite token? |
|---|------|----------------|----------------|:---:|
| 1 | Chart Studies Monitor | `.\start_chart_studies_monitor.ps1` | BAJFINANCE, LAURUSLABS, SBIN, SUNPHARMA, BRITANNIA | Yes |
| 2 | P02 Live Scan | `.\start_p02_live_scan.ps1` | NIFTY 50 universe (Pillar I/II own rules) | Yes |
| 3 | Read-Only Shadow Collector | `.\start_read_only_shadow_collector.ps1` | EXTERNAL_CROSS_SECTIONAL_12_1 momentum candidates | Yes |
| 4 | ORB Shadow Collector | `.\start_orb_shadow_collector.ps1` | Opening-range-breakout scan | Yes — **start near 09:15 IST** |
| 5 | P01D Entry Gate Dry Run | `.\start_entry_gate_dry_run.ps1` | Real V3.4 authorization pipeline (dry-run) | Yes |
| 6 | Local Dashboard Daemon | `.\start_local_dashboard_daemon.ps1` | Refreshes 3 local HTML dashboards every 2 min from 1/2/5's own output | **No** — start this LAST |
| 7 | V11 Bridge — Terminal A | `.\start_terminal_a_original.ps1` | Original 19-symbol universe, SHADOW_MODE | Yes (self-prompts for login if missing) |
| 8 | V11 Bridge — Terminal B | `.\start_terminal_b_expanded.ps1` | Expanded 50-symbol universe, SHADOW_MODE | Yes (self-prompts for login if missing) |

Engines 1–5, 7, 8 each need their own separate PowerShell window (7 Kite
windows total), each with `$env:KITE_ACCESS_TOKEN` pasted in before
running the script. Engine 6 needs no credentials at all — run it in an
8th window once 1, 2 and 5 are already up.

## Per-window steps (engines 1–5)

```powershell
cd C:\Users\Dishan\Documents\Codex\Zerodha_live_bot_3.4_ENTRY_UNKNOWN
$env:KITE_ACCESS_TOKEN = "..."
.\start_chart_studies_monitor.ps1
```

Same pattern for the other 4 — just swap the last line for that engine's
script name from the table above. The script sets the window's title
itself (e.g. "ENGINE 1 - Chart Studies Monitor"), so once it's running
you can tell windows apart at a glance in the taskbar/Alt-Tab — no more
guessing which blank PowerShell window is which.

## Engine 6 (no credentials)

```powershell
cd C:\Users\Dishan\Documents\Codex\Zerodha_live_bot_3.4_ENTRY_UNKNOWN
.\start_local_dashboard_daemon.ps1
```

Then open (or leave open, they self-reload every 2 minutes):
- `local_dashboard\chart_studies_dashboard_local.html`
- `local_dashboard\p02_native_shadow_dashboard_local.html`
- `local_dashboard\p01d_entry_gate_dashboard_local.html`

## Engines 7 & 8 (V11 Bridge)

```powershell
cd C:\Users\Dishan\Documents\Codex\Zerodha_live_bot_3.4_ENTRY_UNKNOWN
.\start_terminal_a_original.ps1
```
(and separately, in its own window, `.\start_terminal_b_expanded.ps1`).
These already self-check for a missing token and walk you through
`get_kite_access_token.ps1` if needed — no manual `$env:` line required
for these two specifically.

## Verifying everything is actually up

From any window:

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Select-Object ProcessId, CommandLine
```

You should see exactly one PID per engine's script name. **If you ever
see the SAME script name twice, kill the older one immediately** (check
`CreationDate`) — a duplicate causes silent file corruption (missing
fields, contradictory trade records). This happened twice in one day
during development; it's the single most common thing to check when
something looks wrong.

## End of day

Each of engines 1–5 handles its own EOD statement automatically past
15:30 IST (or per its own documented close-time convention) — nothing to
do manually. `Ctrl+C` any window to stop it cleanly; engine 6 keeps
serving whatever was last written until you stop it too.
