#!/bin/bash
# Starts a real, OS-level-detached Revision 2 smoke-profile calibration run.
# Launched once by hand (or by a reboot cron entry); NOT re-launched by the
# periodic health-check cron job, which only observes. Runs via nohup so it
# is a child of init, not of any terminal or Claude Code session -- it
# keeps running after this shell, the terminal, and Claude Code all exit.
#
# Scale: 48 symbols x 3000 trailing bars (~2-3 weeks of 1-min data), phase1
# trials=20, phase2 generations=5, phase3 iterations=15 (~112 candidates,
# extrapolated ~5 min/candidate at this window -> roughly 9 hours). This is
# a real search, not a smoke test of the harness -- profile stays "smoke"
# because production remains fail-closed until train/val/test sealing
# (Stage E) exists; a result here is an honest smoke-profile finding, not a
# certified production calibration.

set -euo pipefail
PROJECT_DIR="/home/shrinivas/ECS_Project"
cd "$PROJECT_DIR"

CHECKPOINT="output/revision2_calibration_checkpoint.json"
SUMMARY="output/revision2_calibration_summary.json"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# Preserve any prior checkpoint/summary instead of silently clobbering them --
# the current supervisor cannot resume from a checkpoint, so a fresh run
# needs a fresh file, but nothing gets deleted.
for f in "$CHECKPOINT" "$SUMMARY"; do
    if [ -f "$f" ]; then
        mv "$f" "${f%.json}.prev-${STAMP}.json"
    fi
done

nohup python3 scripts/calibration_watchdog.py \
    --checkpoint "$CHECKPOINT" \
    --log output/calibration_watchdog.jsonl \
    --process-log output/calibration_process.log \
    --interval-seconds 600 \
    -- python3 scripts/run_revision2_calibration.py \
        --profile smoke \
        --max-bars 3000 \
        --symbol-limit 48 \
        --phase1-trials 20 \
        --phase2-generations 5 \
        --phase3-iterations 15 \
        --seed 20260903 \
        --checkpoint "$CHECKPOINT" \
        --summary "$SUMMARY" \
    > output/calibration_watchdog_stdout.log 2>&1 &

WATCHDOG_PID=$!
echo "$WATCHDOG_PID" > output/calibration_watchdog.pid
disown
echo "Started watchdog pid $WATCHDOG_PID (detached, survives shell/session exit)."
echo "Checkpoint: $PROJECT_DIR/$CHECKPOINT"
echo "Watchdog log: $PROJECT_DIR/output/calibration_watchdog.jsonl"
echo "Process log: $PROJECT_DIR/output/calibration_process.log"
