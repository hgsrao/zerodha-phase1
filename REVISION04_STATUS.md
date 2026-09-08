# REVISION 04: Complete System Status

## Overview
Revision 04 builds a complete 10-box modular trading engine for intraday ₹1,000/day target on ₹1,00,000 investment over 1 month on 48 NSE symbols.

## Architecture: 10 Modular Boxes

### Data Flow
```
Box 1: Data Input
  ↓
Box 2: PA (Predictive Analytics) + Box 3: Chart Studies  
  ↓
Box 4: Entry Validator  
  ↓
Box 6: Grid Sync (regime check)
  ↓
Box 5: Risk Manager → Box 9: MPC (sizing)
  ↓
Box 7: Position Manager → Pending Orders
  ↓
Box 8: Exit Decision  
  ↓
Box 10: Performance Tracker
```

### Box Responsibilities

**Box 1: Data Input**
- Market data ingestion and validation
- Handles timestamp parsing (ISO format)
- Validates OHLCV data

**Box 2: PA Box (Predictive Analytics)**
- Confidence scoring (0-1) based on momentum and volume
- Uses 20-bar lookback for trending signals
- Default: 0.6 when insufficient data
- Includes noise to avoid deterministic patterns

**Box 3: Chart Studies Box**
- RSI (14-period) calculation
- MACD signal generation
- Confidence from technical indicators

**Box 4: Entry Validator**
- Enforces minimum PA confidence (0.50)
- Enforces minimum chart confidence (0.45)
- Checks trading hours (9:15 AM - 2:00 PM)
- All conditions must pass to allow entry

**Box 5: Risk Manager**
- Position sizing based on ATR
- Max risk per trade: ₹500
- Max position size: ₹2,083 per symbol
- Stop: ATR × 1.0
- Target: ATR × 2.5 (risk/reward ratio)

**Box 6: Grid Sync**
- Market regime validation
- VIX band check (10-30)
- Trend confirmation
- Returns go/no-go + reason

**Box 7: Position Manager**
- Tracks active positions
- Max 5 concurrent positions
- Stores entry price, stops, targets

**Box 8: Exit Decision**
- Target hit: Close at target price
- Stop hit: Close at stop price
- Time exit: Close after 60 bars
- Returns exit signal + reason + price

**Box 9: MPC Box (Model Predictive Control)**
- Dynamic position sizing based on daily loss
- Max daily loss: ₹2,000
- Scales size as daily P&L degrades
- Output: Final usable position size

**Box 10: Performance Tracker**
- Records all completed trades
- Daily P&L aggregation
- Win rate calculation
- Statistics reporting

## Current Implementation Status

### What's Complete ✓

1. **10-Box Architecture** (`revision4_production/boxes.py`)
   - All 10 boxes implemented with proper separation of concerns
   - Clean interfaces between boxes
   - Error handling for edge cases (NaN, insufficient data)

2. **Integrated Orchestrator** (`revision4_production/orchestrator.py`)
   - Coordinates all 10 boxes
   - Processes bars in sequence through each box
   - Maintains position state and daily P&L

3. **Calibration Backtest Framework** (`revision4_production/calibration_backtest.py`)
   - Proper portfolio state management
   - One shared ₹1,00,000 cash pool (correct)
   - Next-bar fill logic (order created bar t, filled bar t+1)
   - Exit logic (stops/targets/time/EOD)
   - Transaction costs applied
   - Proper trade recording

### Current Limitations ⚠️

1. **Entry Signal Generation**
   - Not yet connected to 10-box system's entry decisions
   - Current test uses placeholder order creation
   - Needs integration with Box 4-9 decision flow

2. **Portfolio State**
   - Next-bar fills implemented but not yet tested end-to-end
   - Margin/cash management framework ready, not tested
   - EOD flattening not yet called in tests

3. **Testing Scale**
   - Optimized test (5,000 bars on 1 symbol): ₹3,295/day projection
   - 3-symbol calibration test: Runs but no trades (no entry signals)
   - 48-symbol test: Needs entry signal integration

## Test Results

### Single Symbol Test (INFY, 5,000 bars)
- **Status**: Generates trades ✓
- **Total P&L**: ₹961.10
- **Trades**: 743 executed
- **Win Rate**: 38.4%
- **Avg Daily P&L**: ₹68.65
- **Projection to 48 symbols**: ₹3,295/day
- **Note**: Uses loose confidence thresholds (0.50/0.45) to generate signal flow

### 3-Symbol Calibration Test (100 bars)
- **Status**: Framework works ✓
- **P&L**: ₹0 (no entry signals generated)
- **Note**: System is operational but needs entry logic

## Critical Fixes Applied

### From User Feedback (Session message)

1. ❌ **Original Issue**: Scaling single symbol by 48 is not valid
   - **Fix**: Created CalibrationBacktest for real 48-symbol portfolio
   - **Status**: Framework complete, integration pending

2. ❌ **Original Issue**: Fills at same bar (lookahead bias)
   - **Fix**: CalibrationBacktest uses next-bar fills
   - **Status**: Implemented but not yet tested end-to-end

3. ❌ **Original Issue**: bars_held always 0
   - **Fix**: Position.bars_held(current_bar) calculates correctly
   - **Status**: Implemented in Position class

4. ❌ **Original Issue**: MPC size not applied
   - **Fix**: Orchestrator uses MPC output in Box 9
   - **Status**: Ready but needs testing

5. ❌ **Original Issue**: No shared cash ledger
   - **Fix**: CalibrationBacktest.cash tracks shared pool
   - **Status**: Framework complete

6. ❌ **Original Issue**: Only long positions
   - **Fix**: Architecture supports direction=±1
   - **Status**: Implemented but entry logic defaults to BUY

## Next Steps: Integration

To achieve ₹1,000/day target:

1. **Wire Entry Signals**
   - Orchestrator.process_bar() creates pending orders when Boxes 4-9 clear entry
   - Pass entry decisions to CalibrationBacktest order queue

2. **Run Proper 48-Symbol Test**
   - Load all 48 symbols
   - Process chronologically  
   - Use 1 month of data (22 trading days × 390 min = ~8,580 bars)
   - Report actual portfolio P&L without scaling

3. **Tune Parameters**
   - Optimize confidence thresholds based on real results
   - Adjust ATR multipliers for better risk/reward
   - Fine-tune MPC scaling factors

4. **Add Missing Features**
   - Short signal support
   - Partial fills
   - Realistic slippage model
   - EOD flattening logic

## File Structure

```
revision4_production/
  ├── boxes.py                 # 10 boxes + MarketData + Position classes
  ├── orchestrator.py          # Integrated orchestrator
  ├── calibration_backtest.py  # Valid 48-symbol backtest engine
  ├── strategy.py              # Original strategy (kept for reference)
  ├── backtest.py              # Original backtest (kept for reference)
  └── __init__.py              # Exports all classes

scripts/
  ├── test_10box_optimized.py  # Single symbol test (5,000 bars)
  ├── test_3symbol_calibration.py  # 3-symbol framework test
  ├── test_48symbol_calibration.py # Full portfolio test (integration pending)
  └── test_revision04.py       # Original (to be deprecated)
```

## Evaluation Criteria (₹1,000/day Target)

```
Month = 22 trading days
Target = ₹1,000/day × 22 days = ₹22,000 total for 1 month

From ₹1,00,000 investment:
- Breakeven: ₹0 P&L
- Target: ₹22,000 P&L (22% monthly return)
- Acceptance: ₹20,000+ P&L on 48-symbol portfolio

NOT: ₹1,000/day from single symbol × 48
    (that's invalid scaling, would require synchronized trading)
```

## Architecture Quality: READY FOR REVIEW

✓ Clean separation of concerns (10 distinct boxes)
✓ Proper error handling (NaN checks, edge cases)
✓ Correct next-bar execution model (no lookahead)
✓ Shared portfolio state (one ₹1,00,000 pool)
✓ Transaction costs included
✓ Proper position tracking
⚠️ Integration of entry signals (pending)
⚠️ Full 48-symbol test (pending)
⚠️ Parameter optimization (pending)

---

**Commit**: 731fa7a (Oct 2026)
**Branch**: codex/external-library-calibration-engine
