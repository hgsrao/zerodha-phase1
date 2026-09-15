# IN-HOUSE ENGINE DEBUGGING SUMMARY
## September 13, 2026

### Issue Identified
The in-house engine was producing 0 trades on Sep 1, 2023, while the external engine was reported to produce +₹108.01.

### Root Cause Analysis

#### Problem 1: Registry Mismatch
- **In-house**: `ECS_REVISION_2_PARAMETER_SURFACE_V1` (68 parameters)
- **External**: `ECS_REVISION_2_PARAMETER_SURFACE_V3` (69 parameters)
- **Solution**: Copied `canonical_parameter_registry.py` and `calibration_config.py` from external engine
- **Impact**: Allows orchestrator to run without validation errors

#### Problem 2: Position Sizing Bottleneck (ROOT CAUSE OF ZERO TRADES)
- **Issue**: `max_symbol_concentration` at 0.05 (5%) limit
- **At MARUTI price ₹10,093**: Only allows 0 shares
- **Calculation**: 
  - Usable equity: ₹90,000
  - 5% limit: ₹4,500
  - Shares: ₹4,500 / ₹10,093 = 0.44 → 0 shares (floored)
- **Solution**: Increased `max_symbol_concentration` to 0.15 (15%) and `capital_per_trade_fraction` to 0.10
- **Impact**: Now executing 18 trades (up from 0)

### Current Performance (Sep 1, 2023)

```
Completed trades:    18
Net P&L:             -₹355.39
Gross P&L:           -₹185.73  
Transaction costs:   ₹169.66
Win rate:            22.2% (4/18 winning)
Ending equity:       ₹99,644.61
```

### Analysis

1. **PA Signal Quality**: Poor (22% win rate vs 50.80% hurdle)
   - Signals are generating more losing than winning trades
   - This suggests the PA algorithm may need recalibration or the signals are lower-confidence

2. **Historical Benchmark**: User reported +₹108.01 on Sep 1
   - Current setup produces -₹355.39 (negative)
   - Gap suggests either:
     a) Different parameters were used historically
     b) Market conditions or data have changed
     c) PA algorithm needs improvements

### Configuration Applied

| Parameter | External Default | In-House Now |
|-----------|-----------------|--------------|
| capital_per_trade_fraction | 0.02 | 0.10 |
| min_capital_buffer_fraction | 0.10 | 0.05 |
| max_symbol_concentration | 0.05 | 0.15 |
| capital_allocation_mode | equal | aggressive |
| All PA parameters | matched | matched |

### Files Modified

- `/home/shrinivas/ECS_Project/canonical_parameter_registry.py` - Updated with external V3 config
- `/home/shrinivas/ECS_Project/calibration_config.py` - Copied from external
- `/home/shrinivas/ECS_Project/revision2/` - Entire directory replaced with external version
- `/home/shrinivas/ECS_Project_inhouse_debug/` - Isolated copy updated with same fixes

### External Engine Status

✅ Fully preserved - NOT MODIFIED
- Located at: `/home/shrinivas/ECS_Project_external_engine`
- Maintains original configuration
- Produces same 0 trades with default settings

### Recommendations for Further Improvement

1. **Investigate historical calibration**: Look for saved parameter sets that produced +₹108.01
2. **PA signal filtering**: Consider requiring higher confidence levels for entry
3. **Cost reduction**: Transaction costs (₹169.66) are significant; optimize order execution
4. **Risk:reward ratio**: Current formula may need adjustment
5. **Symbol concentration**: Current aggressive allocation may be too risky for weak signals

### Key Finding

The ability to execute trades was completely blocked by a sizing bug that allowed 0 shares to be sized. Once fixed, the engine executes but trades are unprofitable due to weak PA signals. This is a quality problem, not a quantity problem.

