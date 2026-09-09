#!/bin/bash
set -e

DATA_DIR="/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824"
LOG_FILE="diagnostic_output/sunpharma_diagnostic_$(date +%s).log"

mkdir -p diagnostic_output

echo "Starting SUNPHARMA diagnostic replay..."
echo "Log: $LOG_FILE"
echo "Timestamp: $(date)" >> "$LOG_FILE"

NSE_DATA_DIR="$DATA_DIR" python3 validate_inhouse_sunpharma_sealed.py >> "$LOG_FILE" 2>&1

REPORT_FILE="diagnostic_output/inhouse_sunpharma_sealed_report.json"
if [ -f "$REPORT_FILE" ]; then
    echo "✓ Report generated: $REPORT_FILE" | tee -a "$LOG_FILE"
    echo "Status: $(jq -r '.status' $REPORT_FILE)" | tee -a "$LOG_FILE"
    echo "Rejection funnel:" | tee -a "$LOG_FILE"
    jq '.rejection_funnel // "N/A"' "$REPORT_FILE" | tee -a "$LOG_FILE"
else
    echo "✗ Report NOT generated" | tee -a "$LOG_FILE"
fi

echo "Diagnostic complete at $(date)" >> "$LOG_FILE"
