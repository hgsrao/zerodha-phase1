#!/bin/bash
# Real OS-level scheduled health check (installed via crontab, NOT tied to
# any Claude Code session — this keeps running whether or not Claude, the
# terminal, or the editor is open). It never launches or restarts
# calibration itself; it only observes and logs, matching the watchdog's
# own "outside the decision path" design. Idempotent, safe to run every
# few minutes indefinitely.

set -uo pipefail
PROJECT_DIR="/home/shrinivas/ECS_Project"
PIDFILE="$PROJECT_DIR/output/calibration_watchdog.pid"
HEALTHLOG="$PROJECT_DIR/output/calibration_cron_health.jsonl"
CHECKPOINT="$PROJECT_DIR/output/revision2_calibration_checkpoint.json"

cd "$PROJECT_DIR" || exit 1

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
RUNNING="false"
PID=""
if [ -f "$PIDFILE" ]; then
    PID="$(cat "$PIDFILE" 2>/dev/null)"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        RUNNING="true"
    fi
fi

SUMMARY="$(python3 - "$CHECKPOINT" <<'PYEOF'
import json, sys, math
path = sys.argv[1]
try:
    d = json.load(open(path))
    candidates = d.get("candidates", [])
    finite = [c for c in candidates if isinstance(c.get("score"), (int, float)) and math.isfinite(c["score"])]
    accepted = [c for c in candidates if c.get("accepted")]
    latest = candidates[-1] if candidates else {}
    print(json.dumps({
        "checkpoint_available": True,
        "candidates_completed": len(candidates),
        "accepted_candidates": len(accepted),
        "latest_phase": latest.get("phase"),
        "latest_guidance_score": latest.get("guidance_score"),
    }))
except Exception as exc:
    print(json.dumps({"checkpoint_available": False, "reason": str(exc)}))
PYEOF
)"

SUMMARY="$SUMMARY" TS="$TS" RUNNING="$RUNNING" PID="$PID" python3 -c "
import json, os
summary = json.loads(os.environ['SUMMARY'])
record = {
    'timestamp_utc': os.environ['TS'],
    'watchdog_running': os.environ['RUNNING'] == 'true',
    'watchdog_pid': os.environ['PID'],
}
record.update(summary)
print(json.dumps(record, sort_keys=True))
" >> "$HEALTHLOG"
