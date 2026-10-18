# Parameter Rename Guide - Work Item 1.2
## Lambda Parameter Migration (Old → New Names)

**Status:** EXECUTION IN PROGRESS  
**Affected Files:** gates_framework.py, safety_gates_config.py, ecs_parameter_management.py  
**Migration Type:** Safe rename (functionality identical, clarity improved)

---

## RENAME MAP

### Old → New Mapping

```python
# OLD NAME → NEW NAME
lambda_risk_trigger_level          → portfolio_risk_derate_trigger
lambda_reduction_factor            → portfolio_derated_size_multiplier
```

---

## FILE-BY-FILE MIGRATION

### 1. safety_gates_config.py ✅ ALREADY CORRECT

```python
# ALREADY USES NEW NAMES
class PortfolioRiskConfig:
    portfolio_risk_derate_trigger = 0.15        # ✅ Correct
    portfolio_derated_size_multiplier = 0.80    # ✅ Correct
```

**Action:** No changes needed. File is already correct.

---

### 2. ecs_parameter_management.py ⏳ NEEDS REMOVAL

**Current state:** Has old lambda params in Tier 3 (WRONG)

```python
'lambda_risk_trigger_level': ParameterSpec(      # ❌ REMOVE
    name='lambda_risk_trigger_level',
    tier=ParameterTier.TIER_3_ADVANCED,
    min_value=0.08, max_value=0.25, default=0.15,
    ...
),
'lambda_reduction_factor': ParameterSpec(        # ❌ REMOVE
    name='lambda_reduction_factor',
    tier=ParameterTier.TIER_3_ADVANCED,
    min_value=0.5, max_value=0.99, default=0.80,
    ...
),
```

**Action Required:**
1. Delete both parameter definitions
2. These are NOT live strategy parameters
3. They belong in safety_gates_config.py (hard-coded)
4. Not in ecs_parameter_management.py (live calibration)

**After fix:**
- Tier 3 params reduce from 5 → 3 (only optimizer controls remain)
- Live config stays at 28 strategy params
- No lambda params in ecs_parameter_management.py

---

### 3. gates_framework.py ⏳ NEEDS UPDATE

**Find all references:**
```
Search for: lambda_risk_trigger_level
Search for: lambda_reduction_factor
Search for: PortfolioRiskConfig
```

**Expected locations:**

```python
# GATE 11: Lambda Derating (likely has old names)
from ecs_parameter_management import ParameterTierClassifier

# OLD CODE (current state - unclear):
lambda_trigger = params.get('lambda_risk_trigger_level', 0.15)
lambda_multiplier = params.get('lambda_reduction_factor', 0.80)

# NEW CODE (after rename):
from safety_gates_config import PortfolioRiskConfig
lambda_trigger = PortfolioRiskConfig.portfolio_risk_derate_trigger
lambda_multiplier = PortfolioRiskConfig.portfolio_derated_size_multiplier
```

**Changes Needed:**
1. Import from safety_gates_config instead of ecs_parameter_management
2. Use new constant names
3. Add comment: "# Portfolio exposure risk gate (preemptive derating)"
4. Update variable names for clarity:
   - `lambda_trigger` → `portfolio_risk_derate_trigger`
   - `lambda_multiplier` → `portfolio_derated_size_multiplier`

---

## SEARCH & REPLACE COMMANDS

### Command 1: Find old references
```bash
grep -r "lambda_risk_trigger_level" .
grep -r "lambda_reduction_factor" .
```

### Command 2: Check for consistency
```bash
grep -r "portfolio_risk_derate" .
grep -r "portfolio_derated" .
```

---

## MIGRATION CHECKLIST

### Phase 1: Remove from Live Config
- [ ] Read ecs_parameter_management.py
- [ ] Delete lambda_risk_trigger_level parameter definition
- [ ] Delete lambda_reduction_factor parameter definition
- [ ] Verify Tier 3 count reduces to 3 params
- [ ] Run validation: ParameterTierClassifier().list_all_specs()
- [ ] Commit with message: "REFACTOR: Move lambda params to safety_gates_config"

### Phase 2: Update Gates Framework
- [ ] Find all references in gates_framework.py
- [ ] Update imports (safety_gates_config instead of ecs_parameter_management)
- [ ] Replace parameter lookup calls with direct config imports
- [ ] Update variable names to use new names
- [ ] Update comments to clarify "portfolio exposure risk"
- [ ] Run unit tests: Test Gate11 derating logic
- [ ] Commit with message: "REFACTOR: Update lambda references to portfolio risk terminology"

### Phase 3: Documentation
- [ ] Update docstrings in gates_framework.py
- [ ] Update README if it mentions lambda parameters
- [ ] Create migration note: PARAMETER_RENAME_LOG.md
- [ ] Commit with message: "DOCS: Add parameter rename documentation"

### Phase 4: Testing
- [ ] Unit test: PortfolioRiskConfig values unchanged
- [ ] Integration test: Gate11 derating with new param names
- [ ] Regression test: Entire gate framework still works
- [ ] Verify no old names remain in code

---

## TESTING PLAN

### Unit Test: Parameter References
```python
def test_portfolio_risk_params_present():
    from safety_gates_config import PortfolioRiskConfig
    assert PortfolioRiskConfig.portfolio_risk_derate_trigger == 0.15
    assert PortfolioRiskConfig.portfolio_derated_size_multiplier == 0.80

def test_old_params_removed_from_live_config():
    from ecs_parameter_management import ParameterTierClassifier
    classifier = ParameterTierClassifier()
    all_params = classifier.list_all_specs()
    
    assert 'lambda_risk_trigger_level' not in all_params
    assert 'lambda_reduction_factor' not in all_params
    # Only 28 live params should remain
    assert len(all_params) == 28
```

### Integration Test: Gate Functionality
```python
def test_gate11_derating_with_new_param_names():
    from gates_framework import Gate11_LambdaDerating
    from safety_gates_config import PortfolioRiskConfig
    
    gate = Gate11_LambdaDerating()
    
    # High exposure case
    exposure_ratio = 0.16  # Above 0.15 threshold
    decision = gate.evaluate(exposure_ratio)
    
    assert decision.decision == GateDecision.DERATE
    assert decision.multiplier == 0.80  # From PortfolioRiskConfig
```

---

## MIGRATION LOG

### Record of Changes

```
✅ Step 1: Lambda vs Drawdown Technical Spec completed
   File: LAMBDA_VS_DRAWDOWN_TECHNICAL_SPEC.md
   Time: 2026-09-01
   
⏳ Step 2: Parameter Rename Guide (in progress)
   File: PARAMETER_RENAME_GUIDE.md
   Time: 2026-09-01
   
⏳ Step 3: Remove from ecs_parameter_management.py
   Action: Delete lambda_* parameters
   Status: PENDING
   
⏳ Step 4: Update gates_framework.py
   Action: Import from safety_gates_config, update references
   Status: PENDING
   
⏳ Step 5: Run tests
   Action: Unit + integration tests
   Status: PENDING
   
⏳ Step 6: Commit changes
   Message: "REFACTOR: Lambda → Portfolio Risk parameter rename (safety clarity)"
   Status: PENDING
```

---

## BACKWARD COMPATIBILITY

### What breaks if we don't rename?
1. ❌ New team members confused by "lambda" terminology
2. ❌ Parameters in wrong file (live config vs. operational)
3. ❌ Code imports from inconsistent sources
4. ❌ Safety gates logic unclear (looks like optional calibration)

### What breaks if we do rename?
1. ✅ Old code that reads 'lambda_risk_trigger_level' won't find it (good - forces update)
2. ✅ Old references to ecs_parameter_management will fail (good - they shouldn't be there)
3. ✅ Any scripts that look for old names will break (good - they need fixing anyway)

### Migration Path
```
Before: Both old and new names work
After:  Only new names work (no legacy support)

Reason: These are internal infrastructure parameters, not user-facing API.
        Safety gates must be unambiguous. Old names introduce confusion.
```

---

## IMMEDIATE ACTIONS (PRIORITY ORDER)

1. **TODAY**: Remove from ecs_parameter_management.py
   - Affects: Live config only (28 params, no lambda)
   - Risk: Low (params not in use yet)
   - Time: 15 minutes

2. **TODAY**: Update gates_framework.py references
   - Affects: Safety gate logic
   - Risk: Medium (gate logic must be correct)
   - Time: 30 minutes + testing

3. **TODAY**: Run integration tests
   - Affects: Gate combinations
   - Risk: Medium (gates are critical)
   - Time: 20 minutes

4. **TODAY**: Commit and document
   - Affects: Git history
   - Risk: Low (good documentation)
   - Time: 10 minutes

**Total estimated time: 75 minutes**

---

## SUMMARY

| Item | Old Name | New Name | Location | Status |
|------|----------|----------|----------|--------|
| Derating trigger | lambda_risk_trigger_level | portfolio_risk_derate_trigger | safety_gates_config.py | ✅ CORRECT |
| Derating multiplier | lambda_reduction_factor | portfolio_derated_size_multiplier | safety_gates_config.py | ✅ CORRECT |
| Live config | (WRONG FILE) | (SHOULD NOT EXIST) | ecs_parameter_management.py | ⏳ NEEDS DELETION |
| Gate references | (OLD NAMES) | (NEW NAMES) | gates_framework.py | ⏳ NEEDS UPDATE |

---

**NEXT: Proceed to actual code updates**

