# Parameter Optimization Roadmap — Research to Production

**Date:** August 30, 2026  
**Context:** Post-audit remediation. STAGE2_CALIBRATION performs random search, not Bayesian optimization.

---

## Current State (STAGE2)

### What STAGE2_CALIBRATION Does

✅ **Honest Implementation:**
- Random parameter sampling from specified ranges
- Each iteration: draw random values → backtest → log results
- 24-hour exploratory run (~300-700 iterations typical)
- Output: Parameter samples + win rates (exploratory data)

❌ **What It Does NOT Do:**
- Bayesian adaptive sampling (no prior/posterior)
- Intelligent parameter selection based on history
- Convergence analysis (doesn't detect optimum)
- Cross-validation (single epoch only)

### Use Case

**Appropriate for:**
- Initial parameter space exploration
- Baseline testing before sophisticated optimization
- Research/understanding what ranges matter
- Quick feasibility checks

**NOT appropriate for:**
- Production parameter deployment
- Investment decisions
- Published research claims
- Claimed "optimization"

---

## Roadmap: If Pursuing Serious Optimization

### Phase 1: Foundation (Weeks 1-2)

**Goal:** Implement honest, verifiable parameter search

```
Task 1.1: Implement Bayesian Optimization
- Library: scikit-optimize (skopt.BayesSearchCV) or optuna
- Implement Gaussian Process posterior
- Track acquisition function (EI, UCB, etc.)
- Add convergence plots

Task 1.2: Multi-Epoch Validation
- Split data: train (60%) / validation (20%) / holdout (20%)
- Optimize on train, evaluate on validation
- Final test on holdout (never seen before)
- Report all three scores

Task 1.3: Instrumentation
- Log every iteration: parameters, objective, metadata
- Generate convergence plot
- Document why optimum was found
```

**Output:** Honest optimizer that can explain its choices

### Phase 2: Robustness (Weeks 3-4)

**Goal:** Verify results are real, not artifacts

```
Task 2.1: Parameter Sensitivity Analysis
- SHAP values or Sobol indices
- Which parameters actually matter?
- Which have negligible effect?
- Visualize importance rankings

Task 2.2: Out-of-Sample Validation
- Test optimized parameters on:
  - Different date ranges
  - Different market regimes
  - Synthetic/perturbed data
- Do they generalize?

Task 2.3: Statistical Significance
- Hypothesis testing: optimized vs. baseline vs. random
- Confidence intervals on win rate
- Report uncertainty explicitly
```

**Output:** Evidence that optimization isn't overfitting

### Phase 3: Documentation (Week 5)

**Goal:** Publish findings honestly

```
Task 3.1: Technical Report
- Optimization methodology (describe algorithm)
- Data used (date range, symbols, regime)
- Hyperparameter choices (acquisition, bounds, etc.)
- Results with uncertainty bands
- Limitations explicitly stated

Task 3.2: Code Repository
- Clean, runnable code with no shortcuts
- Reproducible from published data
- Independent verification possible
- Comments explaining key decisions

Task 3.3: Governance Document
- Who can modify parameters? (approval gate)
- When to re-optimize? (drift detection)
- What triggers rollback? (failure modes)
- How to prove it still works?
```

**Output:** External audit-ready documentation

---

## What NOT to Do

### Anti-Patterns to Avoid

1. ❌ **Claim "optimization" for random search**
   - If you're sampling randomly, say so
   - If using Bayesian, show the acquisition function
   - If using genetics, show the selection pressure

2. ❌ **Test on training data only**
   - Reports win rate on same data used to optimize
   - Overfitting not detectable
   - Generalization unknown

3. ❌ **Hide hyperparameter choices**
   - "We tuned Bayesian for best results"
   - Which acquisition? Which prior? Which stopping rule?
   - If not documented, not reproducible

4. ❌ **Ignore regime changes**
   - Optimize on bull market, deploy in crash
   - Parameters optimal for one index ≠ another
   - Real market has regime shifts

5. ❌ **Claim certainty without uncertainty bands**
   - "50% win rate ← true measure is 48-52% confidence"
   - 100 trades gives wider bands than 10,000
   - Sample size matters

---

## Current STAGE2 As-Is (For Research)

### Acceptable Use

The current random search is fine **if**:

1. **Labeled honestly** ✅ (now done via updated docstring)
   - Called "random search" not "optimization"
   - Code comments explain sampling method

2. **Used exploratively** ✅
   - "Which parameter ranges seem to matter?"
   - "What win rates are achievable in this regime?"
   - Not: "These are optimal parameters"

3. **Results treated as exploratory** ✅
   - Not used for production deployment without further validation
   - Not published as validated strategy
   - Viewed as hypothesis generation, not hypothesis testing

4. **Tied to manual verification** ✅
   - Owner inspects parameter sets and results
   - Human judgment before any deployment
   - Parameter approval gate (not automated)

### Next 24-Hour Run (Aug 30-31)

**What to expect:**
- 300-700 parameter sets tested
- Win rates will vary (randomly)
- Best might be 50-60%, worst might be 30%
- Variation is noise, not signal

**What to do with results:**
1. ✅ Look at best N parameter sets
2. ✅ Check if ranges make intuitive sense
3. ✅ Run best sets again on different data period
4. ✅ If results similar → maybe real
5. ✅ If results different → was overfitting

**What NOT to do:**
1. ❌ Deploy best set to live capital
2. ❌ Claim "optimization" was successful
3. ❌ Publish without disclaimer

---

## Safety & Governance

### Before Any Parameter Deployment

**Checklist:**
- [ ] Parameter source documented (random search? Bayesian? grid?)
- [ ] Data used documented (dates, symbols, regime)
- [ ] Out-of-sample test completed (yes/no)
- [ ] Owner approval + signature
- [ ] Can rollback? (previous parameters saved)
- [ ] Monitoring in place? (detect when it breaks)
- [ ] Kill switch available? (stop trading if needed)

### Red Flags

🚩 If you hear:
- "We optimized and found THE parameters"
- "Trust the algorithm to find optimal"
- "No need to test further"
- "These parameters work on all markets"

→ Stop. Get second opinion.

---

## References

- [[live-trading-safety-constraints]] — No real capital
- [[ecs-audit-findings-20260830]] — Finding #5 (Bayesian claims)
- CRITICAL_AUDIT_RESPONSE_20260830.md — Remediation roadmap
- STAGE2_CALIBRATION_33PARAMS_24HOURS.py — Current implementation (random search)
