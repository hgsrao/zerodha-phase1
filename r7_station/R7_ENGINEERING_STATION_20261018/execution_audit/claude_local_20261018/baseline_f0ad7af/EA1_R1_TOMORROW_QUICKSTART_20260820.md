# EA1-R1 Fresh Shadow Trial — Quick Start (2026-08-20, after 09:15 IST)

One-page reference for tomorrow. Full detail: `EA1_R1_CERTIFICATION_20260819.md`.

**If working from the laptop**: copy `D:\Zerodha_live_bot_3.4_ENTRY_UNKNOWN_BACKUP_20260819\`
(project code) and `D:\zerodha_data_BACKUP_20260819\` (real state - restore
to `C:\zerodha_data\...` on the laptop) before starting. Stop both
terminals on this desktop first - never run both machines against the
same Kite account at once.

## Per terminal, in its own window

**Terminal A:**
```powershell
$env:RUNNER_DATA_DIR = "C:\zerodha_data\runner_original"
$env:PLAN_STORE_DIR = "C:\zerodha_data\plans_original"
$env:DP_CHARGE_PER_SYMBOL = "15.34"
$env:SHADOW_MODE = "true"
$env:UNIVERSE_MODE = "original"
$env:KITE_RATE_GOVERNOR_DIR = "C:\zerodha_data\kite_rate_governor"
# your KITE_API_KEY / KITE_ACCESS_TOKEN as usual
```

**Terminal B:** same, but:
```powershell
$env:RUNNER_DATA_DIR = "C:\zerodha_data\runner_expanded"
$env:PLAN_STORE_DIR = "C:\zerodha_data\plans_expanded"
$env:UNIVERSE_MODE = "expanded"
```
`KITE_RATE_GOVERNOR_DIR` must be the **identical** path in both windows.

## Sequence, per terminal

1. **Start the daemon** (if not already running):
   ```powershell
   python v34_bridge_runner_main.py
   ```
2. **In a second window for that same terminal** (same env vars set), **generate a fresh plan**:
   ```powershell
   python v34_bridge_trigger_main.py
   ```
   Watch the last log line: `trigger run complete: target_id=... outcome=... plan_status=...`. Copy the `target_id`.
3. **Approve it** (human-in-the-loop gate, asks for confirmation):
   ```powershell
   python v34_bridge_approve_plan.py <target_id>
   ```
4. **Watch the daemon window** — it picks up the approved plan on its next 5s poll and drives it. Paste me the output if anything unexpected happens; I'll interpret it live, same as today.

## What "healthy" looks like

- Clean `WOULD_SUBMIT` → `EA1_SHADOW_MODE` declines for each entry (this is success, not a problem — shadow mode is designed to always decline before reaching the real broker).
- Plan reaches `PARTIAL` or `COMPLETE`.
- If a real Kite fault happens (429, network error): it should now resolve itself automatically within a cycle or two — watch for `AUTO_RESOLVED_ENTRY_SUBMIT_HALT` or `QUOTE_FETCH_DEGRADED` in the daemon's log. If the plan halts and does **not** self-resolve within a few cycles, tell me the log output and I'll walk you through `v34_bridge_reconcile_halted_plan.py --target-id <id> --note "..."` same as before.

## If something looks stuck

```powershell
python v34_bridge_reconcile_halted_plan.py --target-id <target_id> --note "your note"
```
Shows a preview first, asks for confirmation before touching anything.
