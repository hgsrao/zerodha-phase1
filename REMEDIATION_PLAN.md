# CRITICAL REMEDIATION PLAN

**Status:** AUDIT FAILURES CONFIRMED  
**Severity:** 4 CRITICAL + 4 HIGH + 2 MEDIUM  
**Approach:** Systematic fix with comprehensive testing

---

## ISSUE MAP & FIX SEQUENCE

### PHASE 1: HIGHEST RISK (Grid Gate Fail-Open)

**Issue:** External orchestrator grid gate silently fails open on exception

**File:** `revision2_external/orchestrator.py` line ~600

**Current (BROKEN):**
```python
if hasattr(self, 'grid_sync') and self.grid_sync is not None:
    try:
        grid_ok, _ = self.grid_sync.check_grid_synchronization(...)
        if not grid_ok:
            funnel["grid_rejected"] += 1
            continue  # REJECT
    except Exception:
        pass  # SILENT FAILURE - entry proceeds anyway
```

**Fix (FAIL-CLOSED):**
```python
# Grid gate: MUST be initialized, MUST not silently fail
if not hasattr(self, 'grid_sync') or self.grid_sync is None:
    # No grid synchronizer attached → REJECT ALL entries
    funnel["grid_rejected"] = len(trades_to_evaluate)
    continue
    
try:
    grid_ok, grid_state = self.grid_sync.check_grid_synchronization(...)
    if not grid_ok:
        funnel["grid_rejected"] += 1
        continue  # REJECT: market regime unfavorable
except Exception as e:
    # Grid sync error → FAIL-CLOSED, reject all entries
    logger.critical(f"Grid synchronization error (fail-closed): {e}")
    logger.critical(f"Rejecting all remaining entries due to grid failure")
    funnel["grid_rejected"] = len(trades_to_evaluate)
    break  # STOP execution
```

**Test:** Force grid_sync exception, verify NO trades execute

---

### PHASE 2: ARCHITECTURE (Revision 3 Self-Comparison)

**Issue:** Using NIFTY as both plant (stock) and grid (market)

**File:** `revision3/macro_grid_synchronizer.py`

**Current (BROKEN):**
```python
# Both are NIFTY prices - self-comparison!
voltage_ok = self.check_voltage(nifty_prices[-1])  # Grid
frequency_ok = self.check_frequency(nifty_prices)  # Grid
phase_ok = self.check_phase(nifty_prices, nifty_prices)  # SELF vs SELF
```

**Fix (PROPER CROSS-SYNC):**
```python
# Grid: Market regime (Nifty VIX + trend)
# Plant: Individual stock being traded
# Synchronization: Stock volatility aligned with market regime

def check_grid_synchronization(self, stock_prices, nifty_prices, vix_prices):
    """
    Verify stock trading is synchronized with market regime.
    
    Args:
        stock_prices: Individual stock OHLCV
        nifty_prices: Market index prices (Nifty 50, not stock itself)
        vix_prices: Market volatility index
    """
    # Voltage: Market volatility within operating band
    voltage_ok = self._check_voltage(vix_prices[-1])
    
    # Frequency: Stock trend aligned with market trend
    stock_trend = self._calculate_trend(stock_prices)
    nifty_trend = self._calculate_trend(nifty_prices)
    frequency_ok = self._check_frequency(stock_trend, nifty_trend)
    
    # Phase: Stock price action synchronized within market cycle
    phase_ok = self._check_phase(stock_prices, nifty_prices)
    
    grid_synchronized = voltage_ok and frequency_ok and phase_ok
    return grid_synchronized, {
        'voltage': voltage_ok,
        'frequency': frequency_ok,
        'phase': phase_ok
    }
```

**Test:** Pass stock prices and NIFTY separately, verify they're compared correctly

---

### PHASE 3: PID EXIT LOGIC

**Issue:** Revision 3 PID has placeholder `tightness = 1.0`, always returns False

**File:** `revision3/master_control_system.py` PID section

**Current (BROKEN):**
```python
tightness = 1.0  # PLACEHOLDER
self.exit_decision = False  # Always False
```

**Fix (ACTUAL CONTROL):**
```python
def calculate_exit_signal(self, inputs: Dict[str, float]) -> Tuple[bool, Dict]:
    """
    PID controller: Determine if position should exit.
    
    Inputs:
    - confidence: Signal confidence (0-1)
    - time_held: Bars in position
    - atr_droop: ATR decay
    - favorable_extreme: Price at entry level
    
    Returns:
    - should_exit: Boolean decision
    - pid_state: Control state for diagnostics
    """
    # P: Proportional to time held (position aging)
    p_component = inputs['time_held'] * self.kp
    
    # I: Integrated confidence decay
    self.integral_error += (1.0 - inputs['confidence'])
    i_component = self.integral_error * self.ki
    
    # D: Derivative of ATR droop (volatility collapse signal)
    d_atr = inputs['atr_droop'] - self.last_atr
    d_component = d_atr * self.kd
    
    # Tightness: Dynamic exit threshold
    tightness = p_component + i_component + d_component
    tightness = max(0.0, min(1.0, tightness))  # Clamp [0, 1]
    
    # Exit decision: If tightness exceeds threshold
    should_exit = tightness > self.exit_threshold
    
    return should_exit, {
        'tightness': tightness,
        'p': p_component,
        'i': i_component,
        'd': d_component,
        'time_held': inputs['time_held'],
        'confidence': inputs['confidence']
    }
```

**Test:** Verify exits trigger based on time_held, confidence decay, ATR droop

---

### PHASE 4: SAFETY RELAY (Static → Continuous)

**Issue:** Safety relay checks health once before replay, then blind

**File:** `revision3/safety_relay.py` (if exists, or integrate into orchestrator)

**Fix (CONTINUOUS MONITORING):**
```python
class SafetyRelay:
    """ANSI-compliant continuous health monitoring."""
    
    def __init__(self):
        self.broker_healthy = True
        self.feed_healthy = True
        self.latency_ok = True
        self.portfolio_ok = True
        self.last_check_time = None
        self.check_interval_bars = 10  # Check every 10 bars
        
    def check_broker_health(self, api_response_time_ms: float) -> bool:
        """Check broker API health."""
        if api_response_time_ms > 1000:  # Threshold: 1 second
            logger.warning(f"Broker API slow: {api_response_time_ms}ms")
            return False
        return True
    
    def check_feed_health(self, last_data_timestamp: datetime) -> bool:
        """Check market data feed."""
        age = (datetime.now() - last_data_timestamp).total_seconds()
        if age > 60:  # Stale if > 60 seconds old
            logger.warning(f"Feed stale: {age}s old")
            return False
        return True
    
    def check_portfolio_health(self, positions: Dict) -> bool:
        """Check portfolio state."""
        for symbol, position in positions.items():
            if position['loss'] > position['max_loss_limit']:
                logger.warning(f"{symbol}: loss exceeded limit")
                return False
        return True
    
    def run_continuous_check(self, bar_count: int, state: Dict) -> bool:
        """Run health check every N bars."""
        if bar_count % self.check_interval_bars != 0:
            return self.broker_healthy and self.feed_healthy and self.portfolio_ok
        
        # Full health check
        self.broker_healthy = self.check_broker_health(state['api_latency_ms'])
        self.feed_healthy = self.check_feed_health(state['last_data_time'])
        self.portfolio_ok = self.check_portfolio_health(state['positions'])
        
        all_healthy = self.broker_healthy and self.feed_healthy and self.portfolio_ok
        
        if not all_healthy:
            logger.critical(f"Health check failed at bar {bar_count}")
            logger.critical(f"  Broker: {self.broker_healthy}")
            logger.critical(f"  Feed: {self.feed_healthy}")
            logger.critical(f"  Portfolio: {self.portfolio_ok}")
        
        return all_healthy
```

**Test:** Simulate broker lag, feed gap, loss spike → verify emergency stop triggers

---

### PHASE 5: REGISTRY CONTRACT

**Issue:** Manifest count mismatch (69 vs 68), identity hash broken

**File:** `revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json`

**Fix:**
```bash
# Count actual symbols in manifest
python3 -c "
import json
with open('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json') as f:
    m = json.load(f)
    print(f'Symbol count: {len(m[\"files\"])}')
    print(f'Expected: 48')
    if len(m['files']) != 48:
        print('ERROR: Manifest count mismatch!')
"

# Recalculate identity hash
python3 -c "
import json, hashlib
with open('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json') as f:
    m = json.load(f)
    # Hash the file list (deterministic order)
    files_str = json.dumps(m['files'], sort_keys=True)
    correct_hash = hashlib.sha256(files_str.encode()).hexdigest()
    print(f'Correct identity hash: {correct_hash}')
    print(f'Current hash: {m.get(\"manifest_hash\", \"MISSING\")}')
"
```

**Test:** Verify manifest loads without errors, symbol count = 48, hash validates

---

### PHASE 6: COMPREHENSIVE TEST SUITE

**Create:** `tests/test_critical_fixes.py`

```python
"""
Comprehensive test suite for critical fixes.
Validates: grid gate, cross-sync, PID exits, safety relay, registry.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, patch

class TestGridGateFailClosed:
    """Grid gate must reject all entries if not initialized."""
    
    def test_grid_sync_not_initialized(self):
        """Grid gate missing → reject all entries."""
        orch = Revision2ExternalEngineOrchestrator([...])
        orch.grid_sync = None  # No grid synchronizer
        
        # Run backtest
        report = orch.run({...})
        
        # No trades should execute
        assert len(report['trades']) == 0, "Grid gate fail-open: entries proceed without grid"
    
    def test_grid_sync_exception_fails_closed(self):
        """Grid sync exception → reject all entries, don't silent-fail."""
        orch = Revision2ExternalEngineOrchestrator([...])
        orch.grid_sync = Mock()
        orch.grid_sync.check_grid_synchronization.side_effect = RuntimeError("API error")
        
        report = orch.run({...})
        
        # Exception should cause all entries to be rejected (fail-closed)
        assert len(report['trades']) == 0, "Grid exception should fail-closed"

class TestRevision3CrossSync:
    """Revision 3 must compare stock to market, not stock to stock."""
    
    def test_grid_uses_separate_nifty(self):
        """Grid sync must use separate Nifty index, not stock prices."""
        grid = MacroGridSynchronizer(...)
        
        stock_prices = np.array([100, 101, 102, 103])
        nifty_prices = np.array([50000, 50100, 50050, 50200])  # Different
        vix_prices = np.array([20, 21, 20.5, 22])
        
        # These should NOT be identical
        grid_ok, state = grid.check_grid_synchronization(stock_prices, nifty_prices, vix_prices)
        
        # Verify Nifty was actually used (not stock prices passed twice)
        assert state['frequency'] != state['voltage'], "Should use different data sources"

class TestPIDExit:
    """PID controller must actually drive exits."""
    
    def test_pid_exits_on_time_held(self):
        """PID should exit after sufficient time held."""
        pid = MasterControlPID(exit_threshold=0.5)
        
        # Simulate position held for 50 bars
        inputs = {
            'confidence': 0.3,  # Confidence decays
            'time_held': 50,     # 50 bars in position
            'atr_droop': 0.2,
            'favorable_extreme': False
        }
        
        should_exit, state = pid.calculate_exit_signal(inputs)
        
        # Tightness should exceed threshold → exit
        assert should_exit, f"PID should exit. Tightness: {state['tightness']}"
    
    def test_pid_tightness_not_hardcoded(self):
        """Tightness must be calculated, not hardcoded to 1.0."""
        pid = MasterControlPID()
        
        inputs1 = {'time_held': 10, 'confidence': 0.9, 'atr_droop': 0.0, 'favorable_extreme': True}
        inputs2 = {'time_held': 50, 'confidence': 0.1, 'atr_droop': 0.5, 'favorable_extreme': False}
        
        _, state1 = pid.calculate_exit_signal(inputs1)
        _, state2 = pid.calculate_exit_signal(inputs2)
        
        # Tightness should differ based on inputs
        assert state1['tightness'] != state2['tightness'], "Tightness must vary with inputs"

class TestSafetyRelay:
    """Safety relay must continuously monitor, not just at startup."""
    
    def test_relay_detects_broker_lag(self):
        """Safety relay should detect slow broker API."""
        relay = SafetyRelay()
        
        state = {
            'api_latency_ms': 2000,  # 2 second lag (exceeds 1s threshold)
            'last_data_time': datetime.now(),
            'positions': {}
        }
        
        healthy = relay.run_continuous_check(bar_count=10, state=state)
        
        assert not healthy, "Relay should detect broker lag"
        assert not relay.broker_healthy, "Broker health flag should be False"
    
    def test_relay_detects_feed_stale(self):
        """Safety relay should detect stale market data."""
        relay = SafetyRelay()
        
        old_time = datetime.now() - timedelta(seconds=120)  # 2 minutes old
        state = {
            'api_latency_ms': 100,
            'last_data_time': old_time,
            'positions': {}
        }
        
        healthy = relay.run_continuous_check(bar_count=10, state=state)
        
        assert not healthy, "Relay should detect stale feed"
        assert not relay.feed_healthy, "Feed health flag should be False"

class TestRegistryContract:
    """Registry must be valid and reproducible."""
    
    def test_manifest_symbol_count(self):
        """Manifest must have exactly 48 symbols."""
        manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
        
        assert len(manifest.files) == 48, f"Expected 48 symbols, got {len(manifest.files)}"
    
    def test_manifest_identity_hash(self):
        """Manifest identity hash must be correct."""
        manifest = DatasetManifest.load('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json')
        
        # Recalculate hash
        import json, hashlib
        files_str = json.dumps(manifest.files, sort_keys=True)
        correct_hash = hashlib.sha256(files_str.encode()).hexdigest()
        
        assert manifest.manifest_hash == correct_hash, \
            f"Hash mismatch: {manifest.manifest_hash} != {correct_hash}"
```

**Run tests:**
```bash
pytest tests/test_critical_fixes.py -v
```

---

## ROLLOUT SEQUENCE

1. **Fix Phase 1:** Grid gate fail-open → Test → Commit
2. **Fix Phase 2:** Revision 3 cross-sync → Test → Commit
3. **Fix Phase 3:** PID exit logic → Test → Commit
4. **Fix Phase 4:** Safety relay continuous → Test → Commit
5. **Fix Phase 5:** Registry contract → Verify → Commit
6. **Test Phase 6:** Run full test suite → Report results
7. **Validation:** Re-run single-symbol backtest with fixes
8. **Git:** Push corrected code with new commit

---

## SUCCESS CRITERIA

- ✅ Grid gate: Fail-closed (0 silent failures)
- ✅ Revision 3: Stock compared to Nifty (not self)
- ✅ PID: Actually exits (tightness calculated)
- ✅ Safety relay: Continuous monitoring (detects in-run failures)
- ✅ Registry: Contract valid (48 symbols, correct hash)
- ✅ Tests: 90%+ pass rate (critical path 100%)
- ✅ Backtest: Causal gate working (not post-hoc)

---

**Status:** REMEDIATION STARTING  
**Commitment:** No claims until all tests pass
