#!/bin/bash
# Real-time monitor for baseline engine traces
# Shows live progress without blocking

echo "╔════════════════════════════════════════════════════════════════════════════════════╗"
echo "║         BASELINE ENGINE TRACE MONITOR - REAL-TIME (Non-Blocking)                  ║"
echo "╚════════════════════════════════════════════════════════════════════════════════════╝"
echo ""

while true; do
    clear

    echo "╔════════════════════════════════════════════════════════════════════════════════════╗"
    echo "║         BASELINE ENGINE TRACE MONITOR - $(date '+%H:%M:%S')                        ║"
    echo "╚════════════════════════════════════════════════════════════════════════════════════╝"
    echo ""

    # External Engine
    echo "━━━━ EXTERNAL ENGINE (HMM + PyPortfolioOpt) ━━━━"
    if [ -f trace_external_INFY_3year.log ]; then
        LINES=$(wc -l < trace_external_INFY_3year.log)
        LAST_STEP=$(grep "^\[" trace_external_INFY_3year.log | tail -1 | grep -o "\[[0-9]*\]" | head -1)
        echo "  Status: RUNNING | Steps completed: $LINES lines | Last step: $LAST_STEP"
        echo ""
        echo "  Last 5 entries:"
        tail -5 trace_external_INFY_3year.log | sed 's/^/    /'
    else
        echo "  Status: Waiting to start..."
    fi

    echo ""
    echo "━━━━ IN-HOUSE ENGINE (Vanilla Volatility + Simple Sizing) ━━━━"
    if [ -f trace_inhouse_INFY_3year.log ]; then
        LINES=$(wc -l < trace_inhouse_INFY_3year.log)
        LAST_STEP=$(grep "^\[" trace_inhouse_INFY_3year.log | tail -1 | grep -o "\[[0-9]*\]" | head -1)
        echo "  Status: RUNNING | Steps completed: $LINES lines | Last step: $LAST_STEP"
        echo ""
        echo "  Last 5 entries:"
        tail -5 trace_inhouse_INFY_3year.log | sed 's/^/    /'
    else
        echo "  Status: Queued (waiting for external engine to complete)"
    fi

    echo ""
    echo "━━━━ PROCESS STATUS ━━━━"
    if ps aux | grep -q "[p]ython3 scripts/baseline_engines_full_trace_single_symbol.py"; then
        echo "  ✓ Trace script running"
        ps aux | grep "[p]ython3 scripts/baseline_engines" | awk '{print "    PID: " $2 " | CPU: " $3 "% | Memory: " $6 "KB"}'
    else
        echo "  ✗ Trace script not running"
    fi

    echo ""
    echo "Press Ctrl+C to stop monitoring. Refreshing every 10 seconds..."
    echo ""

    sleep 10
done
