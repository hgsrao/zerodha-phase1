# Zerodha Live Bot 3.4 - Comprehensive Project Plan
## Date: 2026-08-28
## Status: Item 3b Preregistration Phase

---

## EXECUTIVE SUMMARY

**Current Phase:** Study Layer V2 - Model Development & Validation (Item 3b)

**Infrastructure Status:** ✓ Operational
- Desktop: Ollama 0.33.1 + Qwen 30B running
- Razer Laptop: Ollama 0.33.1 + Qwen 30B + CodeLlama 13B running
- Dual-backend review infrastructure verified
- Git repository initialized with proper exclusions

**Completed Deliverables:** Items 1, 2, 3a frozen and documented
**Current Work:** Item 3b governance & provenance analysis complete (PACKAGES 7, 8A, 8B)
**Next Critical Gate:** Preregistration parameter freeze (PACKAGES 9-10)

---

## PART 1: WHERE WE ARE (Current Status)

### A. Study Layer V2 Roadmap Progress

| Item | Status | Completion Date | Notes |
|------|--------|-----------------|-------|
| 1. ORB/Map Risk Filter | ✅ COMPLETE | 2026-08-25 | FDR-corrected, real effects documented |
| 2. P1/P2 State Decomposition | ✅ COMPLETE | 2026-08-25 | 5,944 rows × 31 columns, 8 symbols |
| 3a. Daily Fusion Panel | ✅ COMPLETE | 2026-08-25 | 5,928 rows × 55 columns, causality-verified |
| 3b. Model 0/1 Development | 🔄 IN PROGRESS | — | Governance + Provenance analyzed |
| 4. Universe Expansion | ⏳ BLOCKED | — | Waiting for 3b methodology freeze |
| 5. Three-Head System | ⏳ BLOCKED | — | Depends on 3b/4 completion |
| MPC Optimization | ⏳ HOLD | — | Awaiting validated outcome model |

### B. PACKAGES Completed (2026-08-28)

**PACKAGE 7 - Item 3b Orientation (18:41:54)**
- ✅ Analyzed fusion panel contract and leakage safeguards
- ✅ Identified frozen vs. missing preregistration decisions
- ✅ Mapped three source files for inspection
- Output: `review_report_20260828_184154.md`

**PACKAGE 8A - Governance Lane (18:44:22)**
- ✅ Extracted model admission gate requirements
- ✅ Defined reusable generic rules vs. 3b-specific conditions
- ✅ Mapped rejection conditions and authority restrictions
- Output: `review_report_20260828_184422.md`
- Backend: Desktop Qwen 30B

**PACKAGE 8B - Provenance Lane (19:03:44)**
- ✅ Produced feature family inventory (P1/P2, 7D, ORB, Map)
- ✅ Documented targets/horizons (D+1 to D+20 at 1,3,5,10,20 day intervals)
- ✅ Verified causality protections and leakage safeguards
- ✅ Mapped data exclusions and universe boundaries
- Output: `review_report_20260828_190344.md`
- Backend: Razer Qwen 30B (103.11 seconds, 9,747 prompt tokens)

### C. Frozen Artifacts (Cannot be Modified)

**Frozen Feature Sets:**
- A: P1/P2 only (10 features)
- B: 7D + ORB + Map daily summaries (9+4+4 = 17 features)
- C: A + B combined (27 features)
- D: Provenance-aware/regularized subset (TBD in preregistration)

**Frozen Data:**
- Panel: `daily_multi_timescale_fusion_panel_20260825.csv` (5,928 rows × 55 columns)
- P1/P2: `p1_p2_state_decomposition_20260825.csv` (5,944 rows × 31 columns)
- Symbols: 8 (BAJFINANCE, SBIN, SUNPHARMA, RELIANCE, INFY, TCS, HDFCBANK, ICICIBANK)
- Date range: 2023-08-25 to 2026-08-24 (3 years)
- Targets: D+1, D+3, D+5, D+10, D+20 (5 horizons)

**Frozen Rules (Standing Discipline):**
- Freeze definitions before testing (no retuning)
- Nested walk-forward + untouched holdout
- Joint-date block bootstrap + Benjamini-Hochberg FDR
- Real costs, real execution model
- Ablation required to overturn negative results
- No rescue adjustments to parameters or thresholds

**Known Constraints:**
- ORB: Real effect but WRONG SIGN (increases tail risk by 7-16pp, FDR-significant)
  - Use case: "reduce size / avoid mean-reversion entry" signal only
- Map+Context: Real but narrow effect (reduces 20bp adverse move risk by 1.6-2.3pp, 10-30min horizons only)
- Data quality issue fixed: Switched to clean 60-min NIFTY source (187 SBIN dates missing in original file)

---

## PART 2: WHAT WE NEED TO DO (Next Steps)

### Phase: Item 3b Preregistration (PACKAGES 9-10)

**Decisions That Must Be Frozen BEFORE Model Runs:**

#### 1. **Train/Validation/Holdout Partitioning** (CRITICAL)
   - [ ] Define exact chronological split dates
   - [ ] Ensure no look-ahead or survivor bias
   - [ ] Document rationale for boundaries
   - [ ] Lock dates (immutable once set)
   - **Timeline:** PACKAGE 9 (Desktop analysis)
   - **Owner Decision:** Split strategy? (e.g., 60% train / 20% validation / 20% holdout by time?)

#### 2. **Preprocessing Pipeline** (CRITICAL)
   - [ ] Missing value handling rules (imputation vs. exclusion)
   - [ ] Feature scaling method (standardization, normalization, etc.)
   - [ ] Categorical encoding (if any)
   - [ ] Outlier treatment (clipping, removal, or acceptance)
   - **Timeline:** PACKAGE 9
   - **Owner Decision:** Standardized to mean=0, std=1 (default) or custom?

#### 3. **Model 0 Configuration** (Regularized Linear Regression)
   - [ ] Regularization type (Ridge, Lasso, ElasticNet)
   - [ ] Regularization strength (λ/alpha value)
   - [ ] Solver and convergence tolerance
   - [ ] Feature interaction handling (none, or polynomial features?)
   - **Timeline:** PACKAGE 10 (Razer analysis)
   - **Recommended:** Ridge with α tuned on validation fold only

#### 4. **Model 1 Configuration** (Constrained Tree/Boosting)
   - [ ] Algorithm choice (XGBoost, LightGBM, or Catboost?)
   - [ ] Depth/complexity constraints (max_depth, min_samples_leaf)
   - [ ] Learning rate and number of boosting rounds
   - [ ] Regularization (lambda, gamma, subsample)
   - **Timeline:** PACKAGE 10
   - **Recommended:** XGBoost with depth ≤ 5, regularized

#### 5. **Evaluation Metrics (Three Tiers)**

   **Tier 1 — Statistical:**
   - [ ] Rank IC (Information Coefficient) with confidence bounds
   - [ ] Calibration test (predictions vs. actuals)
   - [ ] Uncertainty quantification (prediction intervals)
   - **Pass threshold:** Rank IC > 0.05 (minimum bar)

   **Tier 2 — Economic:**
   - [ ] Gross return (sum of daily forward returns × predictions)
   - [ ] Net return (after 2bp trading cost per day)
   - [ ] Turnover (proportion of portfolio changed per day)
   - [ ] Max drawdown (worst-case cumulative loss)
   - **Pass threshold:** Gross return > buy-and-hold by at least 200bp annually on 8-symbol universe

   **Tier 3 — Stability:**
   - [ ] Year-over-year consistency (each year > 0 gross return)
   - [ ] Symbol stability (each symbol individually profitable)
   - [ ] Regime stability (across bull/sideways/bear markets)
   - [ ] Fold stability (each walk-forward fold passes Tier 2 threshold independently)
   - **Pass threshold:** All four dimensions pass without exceptions

#### 6. **Multiple-Testing Correction**
   - [ ] Family definition (e.g., all A/B/C/D comparisons = 1 family)
   - [ ] FDR method (Benjamini-Hochberg)
   - [ ] p-value threshold (e.g., 0.05 before correction)
   - [ ] Correction level (0.05 family-wise)
   - **Timeline:** PACKAGE 10

#### 7. **Model Selection Rule**
   - [ ] NOT based on R² alone
   - [ ] Tier 1 pass required (Rank IC > threshold)
   - [ ] Tier 2 pass required (economic return test)
   - [ ] Tier 3 pass required (stability across all dimensions)
   - [ ] Tiebreaker (if both Model 0 and 1 pass all three tiers): prefer Model 0 (simpler)
   - **Timeline:** PACKAGE 10

#### 8. **Abort Conditions (Pre-defined Failure Triggers)**
   - [ ] If no model passes Tier 2, stop (do not try different hyperparameters)
   - [ ] If Rank IC < 0.03 even in best case, stop (signal too weak)
   - [ ] If max drawdown > 25% during validation, stop (too risky)
   - [ ] If turnover > 50% per day on average, stop (impractical execution)
   - **Timeline:** PACKAGE 10

#### 9. **Validation Procedure**
   - [ ] Nested walk-forward: train on [t0, t1), validate on [t1, t2), test on [t2, t3)
   - [ ] No data leakage between folds
   - [ ] Hyperparameter tuning ONLY on validation, never on test (holdout)
   - [ ] Holdout opened exactly once, after all decisions frozen
   - **Timeline:** POST-preregistration (Phase 2)

#### 10. **Artifact Hashes & Contracts**
   - [ ] Hash the frozen panel CSV
   - [ ] Hash the preregistration document itself
   - [ ] Hash model source code (before any run)
   - [ ] Sign contracts with owner approval
   - **Timeline:** PACKAGE 10 (end)

### Immediate Next Actions (Today/Tomorrow)

**PACKAGE 9 - Preregistration Part 1 (Desktop track)**
- Analyze train/val/holdout split strategy
- Review preprocessing pipeline options
- Draft Model 0 regularization parameters
- Verify causality protections in validation setup
- **Estimated duration:** 90-120 minutes
- **Owner input needed:** Split dates, preprocessing preference

**PACKAGE 10 - Preregistration Part 2 (Razer track)**
- Finalize Model 1 hyperparameter constraints
- Define all three evaluation tiers precisely
- Document abort conditions
- Hash preregistration document
- **Estimated duration:** 90-120 minutes
- **Parallel with PACKAGE 9**

**Preregistration Document Assembly**
- Consolidate PACKAGES 9 & 10 into single signed preregistration
- Obtain owner signature
- Commit to Git with frozen hash
- **Gate keeper:** No model run without signed preregistration

---

## PART 3: WHAT WE HAVE TO FINISH FOR PROJECT COMPLETION (Timeline)

### Phase 3A: Model Development & Validation (Weeks 1-3)

| Week | Task | Owner | Duration | Blocker? |
|------|------|-------|----------|----------|
| **Week 1 (Aug 29-Sep 4)** | | | | |
| | PACKAGE 9: Preregistration Part 1 | Desktop | 2h | ⚠️ CRITICAL |
| | PACKAGE 10: Preregistration Part 2 | Razer | 2h | ⚠️ CRITICAL |
| | Freeze preregistration document | Me | 1h | ⚠️ CRITICAL |
| | Build Model 0 harness | Me | 3h | Blocked until above done |
| | Build Model 1 harness | Me | 3h | Blocked until above done |
| | Dev-set training (A/B/C/D features × 2 models) | Me | 4h | Blocked until harness ready |
| | **Subtotal** | | **~15 hours** | |
|  | | | | |
| **Week 2 (Sep 5-11)** | | | | |
| | Open validation results (first pass) | Me | 2h | Blocked |
| | Tier 1 evaluation (Rank IC, calibration) | Me | 2h | Blocked |
| | Tier 2 evaluation (economic return) | Me | 2h | Blocked |
| | Tier 3 evaluation (stability) | Me | 3h | Blocked |
| | Model selection decision (pass/fail) | Me | 2h | Blocked |
| | **Subtotal** | | **~11 hours** | |
|  | | | | |
| **Week 3 (Sep 12-18)** | | | | |
| | Holdout testing (if models pass validation) | Me | 2h | Conditional |
| | OOS evidence report | Me | 2h | Conditional |
| | Item 3b closure & sign-off | Me + Owner | 1h | Conditional |
| | **Subtotal** | | **~5 hours** | Conditional |

**Phase 3A Total: ~31 hours (4-5 calendar days of solid work)**

### Phase 3B: Universe Expansion (Weeks 4-6)

| Week | Task | Owner | Duration | Dependency |
|------|------|-------|----------|------------|
| **Week 4 (Sep 19-25)** | | | | |
| | Build 108-symbol daily panel (Item 4) | Me | 6h | Item 3b ✓ closed |
| | Verify no leakage on expanded universe | Me | 2h | Item 4 in progress |
| | Run Model 0/1 on 108-symbol validation | Me | 4h | Item 4 ready |
| | **Subtotal** | | **~12 hours** | |
|  | | | | |
| **Week 5 (Sep 26-Oct 2)** | | | | |
| | Evaluate Tier 1-3 on expanded universe | Me | 3h | Training complete |
| | Universe-level stability analysis | Me | 3h | Training complete |
| | Holdout test on 108-symbol | Me | 2h | Decision made |
| | **Subtotal** | | **~8 hours** | |

**Phase 3B Total: ~20 hours (3 calendar days)**

### Phase 4: Three-Head Assembly (Weeks 7-8)

| Week | Task | Owner | Duration | Dependency |
|------|------|-------|----------|------------|
| **Week 6-7 (Oct 3-16)** | | | | |
| | Alpha head: finalize Model 0/1 with Item 4 | Me | 2h | Item 4 ✓ |
| | Risk head: integrate P1/P2 state decomposition | Me | 3h | Item 4 ✓ |
| | Ranking head: cross-sectional rank integration | Me | 3h | Item 4 ✓ |
| | Self-learning system skeleton (Item 5) | Me | 4h | All heads ready |
| | Heads integration test | Me | 2h | Self-learning framework up |
| | **Subtotal** | | **~14 hours** | |

**Phase 4 Total: ~14 hours (2-3 calendar days)**

### Phase 5: MPC Build & Integration (Weeks 8-10)

| Week | Task | Owner | Duration | Dependency |
|------|------|-------|----------|------------|
| **Week 8-9 (Oct 17-30)** | | | | |
| | Implement MPC core (Item 5) | Me | 8h | Three-head assembly ✓ |
| | MPC to P01D integration test | Me | 2h | MPC core ready |
| | Shadow-mode MPC deployment | Me | 2h | MPC integration test ✓ |
| | Week 1 live monitoring (no orders) | Me | 3h | Shadow deployed |
| | **Subtotal** | | **~15 hours** | |

**Phase 5 Total: ~15 hours (2-3 calendar days)**

### Phase 6: Live Hardening & Deployment (Weeks 11-13)

| Week | Task | Owner | Duration | Dependency |
|------|------|-------|----------|------------|
| **Week 10 (Oct 31-Nov 6)** | | | | |
| | Operational safety verification | Me | 3h | Shadow mode running |
| | Risk controls audit | Me | 2h | Safety verification ✓ |
| | Broker integration final check | Me | 1h | Risk audit ✓ |
| | Set LIVE_TRADING_ENABLED = True (controlled) | Me | 1h | All checks pass |
| | Week 1 real paper trading (2% position size) | Me | 3h | Flag enabled |
| | **Subtotal** | | **~10 hours** | |

**Phase 6 Total: ~10 hours (1-2 calendar days)**

---

## TIMELINE: PROJECT COMPLETION FORECAST

### Critical Path Analysis

```
Week 1 (Aug 29)  → PACKAGE 9/10 (preregistration)     [2 days]
                 → Model harness build                 [1 day]
                 → Dev training                        [1 day]
                 
Week 2 (Sep 5)   → Validation eval (Tier 1-3)         [3 days]
                 
Week 3 (Sep 12)  → Holdout test & Item 3b closure     [2 days]
                 
Week 4-5 (Sep 19)→ Universe expansion (Item 4)        [5 days]
                 
Week 6-7 (Oct 3) → Three-head assembly (Item 5)       [3 days]
                 
Week 8-9 (Oct 17)→ MPC build & shadow deploy         [3 days]
                 
Week 10 (Oct 31) → Live hardening & enable           [2 days]
                 
                 TOTAL: ~21 calendar days (Oct 31, 2026)
```

### Milestone Dates

| Milestone | Target Date | Status | Gate |
|-----------|-------------|--------|------|
| Preregistration frozen | **Aug 30, 2026** | 🔄 In Progress | Owner sign-off required |
| Model 0/1 validation complete | **Sep 5, 2026** | ⏳ Blocked | Preregistration ✓ |
| Item 3b closure | **Sep 18, 2026** | ⏳ Blocked | Validation ✓ |
| 108-symbol universe ready | **Sep 25, 2026** | ⏳ Blocked | Item 3b ✓ |
| Three-head assembly done | **Oct 9, 2026** | ⏳ Blocked | Item 4 ✓ |
| MPC shadow-mode live | **Oct 23, 2026** | ⏳ Blocked | Item 5 ✓ |
| **LIVE TRADING ENABLED** | **Oct 31, 2026** | ⏳ Blocked | All hardening ✓ |

### Contingency & Risks

**Risk: Model 0/1 both fail validation**
- Trigger: Neither model passes Tier 2 (economic return)
- Mitigation: Pre-abort conditions prevent endless retrying
- Recovery path: Return to Item 3a (feature engineering) with new features (out of scope for now)
- Buffer: +2 weeks for post-mortem and redesign

**Risk: Universe expansion (Item 4) shows instability**
- Trigger: 108-symbol Tier 3 (stability) fails while 8-symbol passed
- Mitigation: Document why (survivorship bias? regime change?)
- Recovery path: Fallback to 8-symbol model or redesign selection
- Buffer: +1 week for reanalysis

**Risk: Live trading produces losses in Week 1**
- Trigger: Real P&L < 0 despite all validation passing
- Mitigation: Position size capped at 2% initial; emergency stop procedures in place
- Recovery path: Disable trading, analyze live execution gap, retrain on newer data
- Buffer: Risk-limit stopping rule prevents catastrophic loss

---

## PART 4: CRITICAL SUCCESS FACTORS & CONSTRAINTS

### Non-Negotiable Constraints

1. **LIVE_TRADING_ENABLED must remain False until Item 6 complete**
   - Current state: Hardcoded False everywhere
   - Production traffic: Zero real orders allowed until sign-off

2. **No credentials on Razer laptop**
   - Kite API key/token: Desktop only
   - Broker authentication: Desktop only
   - Razer: Read-only analysis only

3. **Frozen preregistration is immutable**
   - Once signed, no parameter changes without new preregistration
   - No rescue thresholds after seeing results
   - Failed results → documented stop, not iteration

4. **Holdout dataset must not be peeked at**
   - Validation results only opened after all decisions frozen
   - Holdout opened exactly once (no re-tuning)

5. **Real costs must be included in economic evaluation**
   - 2bp per day trading cost assumed
   - No free-trading assumptions

### Owner Decisions Required (Immediate)

**By Aug 29 EOD:**
1. **Train/Val/Holdout split dates** — chronological boundaries
2. **Preprocessing preference** — standardization method, missing value handling
3. **Model 1 algorithm choice** — XGBoost vs. LightGBM vs. Catboost
4. **Pass thresholds** — acceptable Rank IC floor, drawdown limit

**By Sep 5 EOD:**
1. **Holdout opening decision** — proceed to Item 4, or iterate within Item 3b?

**By Sep 18 EOD:**
1. **Universe expansion signal** — ready to scale to 108 symbols?

**By Oct 31 EOD:**
1. **Live trading enablement** — green light for real orders?

---

## DELIVERABLES BY PHASE

### Phase 3A (Model Development)
- ✅ Signed preregistration document (Aug 30)
- ✅ Model 0/1 source code frozen in Git (Aug 30)
- ✅ Validation evaluation report (Sep 12)
- ✅ Item 3b closure with OOS evidence (Sep 18)

### Phase 3B (Universe Expansion)
- ✅ 108-symbol daily panel (Sep 25)
- ✅ Universe-level stability analysis (Oct 2)
- ✅ Item 4 closure report (Oct 9)

### Phase 4 (Three-Head Assembly)
- ✅ Integrated Alpha/Risk/Ranking heads (Oct 9)
- ✅ Self-learning system architecture (Oct 16)
- ✅ Integration test results (Oct 16)

### Phase 5 (MPC Build)
- ✅ MPC core implementation (Oct 23)
- ✅ MPC to P01D integration (Oct 23)
- ✅ Shadow-mode monitoring log (Oct 30)

### Phase 6 (Live Hardening)
- ✅ Safety audit report (Oct 31)
- ✅ LIVE_TRADING_ENABLED = True (Oct 31)
- ✅ Week 1 real paper trading log (Nov 7)

---

## MONITORING & CHECKPOINTS

### Daily Checkpoint (6:00 PM IST)
- Review PACKAGE output reports
- Check for execution errors or blocks
- Confirm infrastructure health (both Ollama instances)
- Update memory files if decisions change

### Weekly Checkpoint (Friday EOD)
- Review progress against milestone dates
- Escalate any risk items or delays
- Update this plan if timeline shifts
- Confirm owner availability for next week's decisions

### Gate Review (Before each phase start)
- **Pre-Phase 3A:** Preregistration signed? ✓
- **Pre-Phase 3B:** Models passed validation? ✓
- **Pre-Phase 4:** Item 4 data ready? ✓
- **Pre-Phase 5:** Three heads integrated? ✓
- **Pre-Phase 6:** MPC shadow mode stable? ✓

---

## ASSUMPTIONS

1. **Infrastructure remains stable** — Ollama on both machines, network connectivity
2. **No major data issues** — NIFTY source clean, no surprise missing dates
3. **Model convergence** — Training harnesses produce valid Model 0/1 within 4 hours each
4. **No regulatory changes** — Zerodha API, market hours, margin rules unchanged
5. **Owner availability** — Decisions can be provided within 24 hours of request

---

## SUCCESS DEFINITION

**Project Complete When:**
1. ✅ Item 3b: Model 0/1 validated with OOS evidence passing all three tiers
2. ✅ Item 4: 108-symbol universe tested and stability proven
3. ✅ Item 5: Three-head system operational and integrated
4. ✅ MPC: Shadow-mode live deployment with 2+ weeks of clean trading
5. ✅ Live: LIVE_TRADING_ENABLED = True with real orders executing at 2% position size
6. ✅ Signed: All decision documents, preregistrations, and closure reports in Git

**Expected Completion Date: October 31, 2026**

---

## NEXT IMMEDIATE ACTION

**RIGHT NOW (Today, Aug 28, 2026):**
1. ✅ Git initialized and baseline committed
2. ✅ PACKAGES 7/8A/8B complete
3. ⏳ **YOU DECIDE:** Should we proceed with PACKAGES 9/10 today (preregistration), or pause for owner input first?

**If proceeding tonight:**
- PACKAGE 9 (Desktop): 2-3 hours for preregistration Part 1
- PACKAGE 10 (Razer): 2-3 hours parallel for preregistration Part 2
- Consolidation: 1 hour to freeze & sign-off

**Owner input needed before PACKAGES 9/10:**
- [ ] Train/Val/Holdout split dates (which dates split the 3-year sample?)
- [ ] Preprocessing: Standardize mean=0/std=1 (default) or custom?
- [ ] Model 1 algorithm: XGBoost / LightGBM / Catboost preference?

---

**Plan prepared by:** Claude Code (Haiku 4.5)  
**Approved by:** [Awaiting owner signature]  
**Last updated:** 2026-08-28 19:45 IST  
**Git commit:** c54548d (baseline + documentation)

