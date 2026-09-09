#!/bin/bash
set -e

export NSE_DATA_DIR="/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824"

echo "Starting 48-symbol sealed replay..."
echo "Start time: $(date)"
echo "Data directory: $NSE_DATA_DIR"
echo ""

timeout 1800 python3 -u validate_inhouse_48symbol_sealed.py 2>&1 | tee diagnostic_output/48symbol_sealed_replay_v2.log

EXIT_CODE=$?
echo ""
echo "Completion time: $(date)"
echo "Exit code: $EXIT_CODE"

if [ -f "diagnostic_output/inhouse_48symbol_sealed_report.json" ]; then
  echo "✅ Report generated successfully"
  wc -l diagnostic_output/inhouse_48symbol_sealed_report.json
else
  echo "⚠️  Report not found"
fi
