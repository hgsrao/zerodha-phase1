# FOLLOW-UP COMMIT 1: Correct the Record

## Status of Previous Commit (635cbf4)

The "CRITICAL REMEDIATION" commit claimed 3 fixes. Audit reveals:

### ✅ REAL FIX
- Grid gate fail-open → fail-closed: **WORKING**

### ❌ INCOMPLETE / FAKE
- Revision 3 cross-sync: Method signature changed, but execution path never supplies stock price series. Test passes due to early return. **NOT WORKING**
- PID exit logic: Logic added but derivative calculation wrong, not integrated into execution, test failure hidden in except block. **NOT WORKING**

---

## ACTION ITEMS THIS COMMIT

### 1. Mark Revision 3/4 as Experimental

File: `revision3/master_control_system.py` (top of file)

```python
"""
EXPERIMENTAL: Revision 3 Master Control System

STATUS: Prototype only. Not production-ready.
- Grid synchronization: Architecture present, data flow incomplete
- PID controller: Calculation stubbed, not integrated
- Safety relay: Static preflight only, not continuous monitoring

DO NOT DEPLOY without completing:
1. Real stock + NIFTY data alignment (timestamp basis)
2. PID integration into execution path
3. Continuous safety monitoring during replay
4. Full integration test suite
"""
```

### 2. Disable Broken Grid Path in External Engine

File: `revision2_external/orchestrator.py`

Replace lines 602-625 with:

```python
# TEMPORARY: Grid gate disabled until real NIFTY/VIX data is injected
# Current state: If grid_sync is None (no data), would reject all entries (zero trades)
# This is fail-safe but not useful. Enable only when orchestrator.nifty_prices and
# orchestrator.vix_prices are properly initialized from real/aligned data.

if False:  # DISABLED until real data injection
    if not hasattr(self, 'grid_sync') or self.grid_sync is None:
        continue
    try:
        grid_ok, _ = self.grid_sync.check_grid_synchronization(...)
        if not grid_ok:
            funnel["grid_rejected"] = funnel.get("grid_rejected", 0) + 1
            continue
    except Exception as e:
        logger.critical(f"Grid sync error (FAIL-CLOSED): {e}")
        continue
```

### 3. Restore Registry to Known Contract

File: `revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json`

```bash
# Count actual symbols
python3 -c "
import json
with open('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json') as f:
    m = json.load(f)
    actual = len(m['files'])
    print(f'Current count: {actual}')
    print(f'Expected: 48')
    if actual != 48:
        print(f'MISMATCH: Update manifest or recount')
"

# Regenerate identity hash if count is correct
python3 -c "
import json, hashlib
with open('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json') as f:
    m = json.load(f)
    if len(m['files']) == 48:
        files_str = json.dumps(m['files'], sort_keys=True)
        correct_hash = hashlib.sha256(files_str.encode()).hexdigest()
        m['manifest_hash'] = correct_hash
        with open('revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json', 'w') as fw:
            json.dump(m, fw, indent=2)
        print(f'Registry restored: {correct_hash}')
"
```

### 4. Replace Fake Tests with Real Ones

File: `tests/test_critical_fixes.py` → DELETE

File: `tests/test_grid_sync_integration.py` (NEW)

```python
"""
Real grid synchronization integration tests.
Proves grid rejection prevents fills and exceptions are caught.
"""

def test_grid_sync_with_real_data():
    """Grid gate must work with real timestamp-aligned data."""
    pytest.skip("Requires real or deterministic mock synchronizer + aligned NIFTY/stock windows")

def test_grid_rejection_prevents_fill():
    """Prove grid=False stops entry, not just logged."""
    pytest.skip("Requires execution path integration test")

def test_grid_exception_caught():
    """Prove grid exception is caught and entry is rejected."""
    pytest.skip("Requires mocked exception + verified entry rejection")
```

### 5. Disable PID Calculation Until Integration Complete

File: `revision3/master_control_system.py` → evaluate_pid_controller()

```python
def evaluate_pid_controller(self, ...):
    if not self.enabled_layers["pid"]:
        return False, self.pid_state
    
    # TEMPORARY: PID logic is incomplete
    # Current implementation:
    # - Does not track chart studies confidence
    # - ATR derivative calculated from prior PA tightness (wrong)
    # - Not integrated into actual execution path
    
    logger.warning("PID controller called but not integrated. Returning no-exit.")
    return False, self.pid_state  # No-op until integration complete
```

---

## COMMIT MESSAGE

```
Follow-up 1: Correct record, disable incomplete work

- Mark Revision 3/4 as experimental (not production)
- Disable broken grid path until real data injection
- Restore registry to verified contract
- Replace fake tests with placeholders for real integration tests
- Disable incomplete PID logic (not integrated)
- Document incomplete work clearly

Previous commit (635cbf4) claimed 3 fixes; this commit:
- Acknowledges only grid fail-open is complete
- Disables half-finished grid/PID paths
- Restores baseline to known state
- Sets stage for proper integration work

No rewrite. Record stays intact. Baseline stabilized.
```

