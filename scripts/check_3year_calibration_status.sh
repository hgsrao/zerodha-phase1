#!/bin/bash
# Quick status check for 3-year calibrations

echo "╔════════════════════════════════════════════════════════════════════════════════════════════════╗"
echo "║                       3-YEAR CALIBRATION STATUS CHECK                                        ║"
echo "╚════════════════════════════════════════════════════════════════════════════════════════════════╝"
echo ""

# Check processes
echo "[RUNNING PROCESSES]"
EXTERNAL_PID=$(ps aux | grep "run_external_engine.*FULL_3YEAR" | grep -v grep | awk '{print $2}')
INHOUSE_PID=$(ps aux | grep "run_inhouse_engine.*FULL_3YEAR" | grep -v grep | awk '{print $2}')

if [ ! -z "$EXTERNAL_PID" ]; then
    UPTIME=$(ps -p $EXTERNAL_PID -o etime= 2>/dev/null)
    RSS=$(ps -p $EXTERNAL_PID -o rss= 2>/dev/null)
    echo "  External (PID $EXTERNAL_PID): Running for $UPTIME, using ${RSS}K memory"
else
    echo "  External: NOT RUNNING"
fi

if [ ! -z "$INHOUSE_PID" ]; then
    UPTIME=$(ps -p $INHOUSE_PID -o etime= 2>/dev/null)
    RSS=$(ps -p $INHOUSE_PID -o rss= 2>/dev/null)
    echo "  In-House (PID $INHOUSE_PID): Running for $UPTIME, using ${RSS}K memory"
else
    echo "  In-House: NOT RUNNING"
fi

echo ""
echo "[LOG FILES]"
if [ -f calibration_external_3year.log ]; then
    SIZE=$(du -h calibration_external_3year.log | awk '{print $1}')
    MODIFIED=$(date -r calibration_external_3year.log '+%H:%M:%S')
    LINES=$(wc -l < calibration_external_3year.log)
    echo "  External: $SIZE ($LINES lines, last modified $MODIFIED)"
    echo "    Last 3 lines:"
    tail -3 calibration_external_3year.log | sed 's/^/      /'
else
    echo "  External: NOT CREATED YET"
fi

if [ -f calibration_inhouse_3year.log ]; then
    SIZE=$(du -h calibration_inhouse_3year.log | awk '{print $1}')
    MODIFIED=$(date -r calibration_inhouse_3year.log '+%H:%M:%S')
    LINES=$(wc -l < calibration_inhouse_3year.log)
    echo "  In-House: $SIZE ($LINES lines, last modified $MODIFIED)"
    echo "    Last 3 lines:"
    tail -3 calibration_inhouse_3year.log | sed 's/^/      /'
else
    echo "  In-House: NOT CREATED YET"
fi

echo ""
echo "[CHECKPOINT PROGRESS]"
python3 << 'PYTHON_EOF'
import json
from pathlib import Path

def check_checkpoint(name, path):
    if not path.exists():
        print(f"  {name}: No checkpoint yet")
        return
    try:
        with open(path) as f:
            data = json.load(f)
        cands = data.get('candidates', [])
        trades = sum(len(c.get('report', {}).get('trades', [])) for c in cands)
        accepted = sum(1 for c in cands if c.get('accepted'))
        zero_trade = sum(1 for c in cands if len(c.get('report', {}).get('trades', [])) == 0)

        print(f"  {name}:")
        print(f"    Candidates: {len(cands)}")
        print(f"    Accepted: {accepted}")
        print(f"    Total trades: {trades}")
        print(f"    Zero-trade candidates: {zero_trade}")
        if len(cands) > 0:
            print(f"    Avg trades/candidate: {trades/len(cands):.1f}")
    except Exception as e:
        print(f"  {name}: Error reading checkpoint - {e}")

check_checkpoint("External", Path("output_external_engine/external_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json"))
check_checkpoint("In-House", Path("output_inhouse_engine/inhouse_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json"))
PYTHON_EOF

echo ""
echo "[SUMMARY]"
if [ ! -z "$EXTERNAL_PID" ] && [ ! -z "$INHOUSE_PID" ]; then
    echo "  ✓ Both engines running"
    echo "  Estimated completion: 4-6 hours from start time"
elif [ ! -z "$EXTERNAL_PID" ] || [ ! -z "$INHOUSE_PID" ]; then
    echo "  ⚠ One engine still running"
else
    echo "  ✗ No engines running (check logs for completion or errors)"
fi

echo ""
echo "To monitor logs in real-time:"
echo "  tail -f calibration_external_3year.log"
echo "  tail -f calibration_inhouse_3year.log"
echo ""
echo "To run A/B comparison when complete:"
echo "  python3 scripts/compare_3year_calibration_results.py"
echo ""
