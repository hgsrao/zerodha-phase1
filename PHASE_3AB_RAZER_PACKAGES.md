# PHASE 3A & 3B: RAZER LAPTOP PACKAGES
## Parallel Feature Analysis + Universe Planning
**Machine:** Razer Laptop (lightweight analysis only)  
**Status:** Ready to deploy (Aug 29, parallel with Phase 4 on desktop)  
**Total Duration:** 8-10 hours (can run overnight)

---

## PHASE 3A: FEATURE ANALYSIS & PROVENANCE (4 packages, 2.5-3h)

### Package 3A-1: Feature Inventory & Provenance (45 min)
**What:** Document source and computation of every feature

**Input:**
- `daily_multi_timescale_fusion_panel_20260825.csv` (55 columns)
- `daily_multi_timescale_fusion_panel.py` (source code)

**Output:**
- `feature_provenance_catalog.md` (detailed documentation)

**Steps:**
1. List all 55 columns
2. Categorize by set (A=P1/P2, B=7D+ORB+Map, C=A+B)
3. For each feature: source data → computation method → output
4. Verify no circular references or look-ahead
5. Document data availability timestamps

**Acceptance Criteria:**
- All features documented with source
- Each feature has computation method
- No missing features
- No look-ahead detected

---

### Package 3A-2: Causality Verification (45 min)
**What:** Verify features use only data available at feature time

**Input:**
- `feature_provenance_catalog.md` (from 3A-1)
- Data CSV (check timestamps)

**Output:**
- `causality_verification_report.md`

**Steps:**
1. Check P1/P2 features: Are they from D's close? YES/NO
2. Check 7D features: Do they use only D-6 to D? YES/NO
3. Check ORB features: Do they use only D's bars? YES/NO
4. Check Map features: Do they use only D's data? YES/NO
5. Verify targets: Are they D+1 onward? YES/NO
6. Flag any suspicious timings

**Acceptance Criteria:**
- All features use only D or earlier data
- No same-day leakage
- Targets use D+1 onward
- Clear causality chain verified

---

### Package 3A-3: Feature Stability Over Time (45 min)
**What:** Analyze if features are stable or drifting

**Input:**
- `daily_multi_timescale_fusion_panel_20260825.csv`

**Output:**
- `feature_stability_analysis.json` (statistics by year/symbol)
- `feature_stability_charts.md` (trends and observations)

**Steps:**
1. Split data by year (2023, 2024, 2025, 2026)
2. For each feature: compute mean, std, min, max per year
3. Check for >50% drift year-over-year
4. Split data by symbol (8 stocks)
5. For each feature: compute mean, std per symbol
6. Flag any anomalies or suspicious drifts
7. Document findings

**Acceptance Criteria:**
- Statistics computed for all features × years
- Statistics computed for all features × symbols
- >50% drift flagged with explanations
- No unexplained anomalies

---

### Package 3A-4: Feature Importance Skeleton (30 min)
**What:** Create placeholder for feature importance (to fill after holdout)

**Input:**
- `feature_provenance_catalog.md` (all feature names)

**Output:**
- `feature_importance_skeleton.json` (template structure)

**Steps:**
1. List all 55 features from provenance catalog
2. Create JSON with each feature and 0 importance
3. Include metadata section (source, model, date)
4. Ready for population after Phase 2C

**Acceptance Criteria:**
- All 55 features listed
- Valid JSON structure
- Template ready for data population

---

## PHASE 3B: UNIVERSE EXPANSION PLANNING (4 packages, 2.5-3h)

### Package 3B-1: Symbol & Data Availability Check (45 min)
**What:** Verify 108-symbol dataset completeness

**Input:**
- Kite data directories (local or API)
- List of 108 symbols (48 NIFTY + 60 extended)

**Output:**
- `universe_symbols.json` (symbol list with status)
- `data_availability_report.md` (human-readable summary)

**Steps:**
1. List all 108 symbols
2. For each symbol:
   - Check if data exists for 2023-08-25 to 2026-08-24
   - Count missing dates
   - Assess data quality
3. Categorize: ✓ Complete | ⚠ Gaps | ✗ Insufficient
4. Document any issues

**Acceptance Criteria:**
- All 108 symbols checked
- Status assigned (✓/⚠/✗)
- Missing dates documented
- Quality assessment complete

---

### Package 3B-2: Universe Expansion Specification (45 min)
**What:** Design the 108-symbol panel construction plan

**Input:**
- `universe_symbols.json` (symbol availability)
- Current 8-symbol panel spec

**Output:**
- `universe_expansion_spec.md` (design document)

**Steps:**
1. Decide construction logic (same as 8-symbol? any changes?)
2. Specify feature set application (A/B/C/D on all 108?)
3. Define target computation (same as 8-symbol?)
4. Estimate computational cost (time to build panel)
5. Identify any symbol-specific issues
6. Plan for data quality handling

**Acceptance Criteria:**
- Construction logic clear and documented
- Feature set strategy specified
- Target definition same/different documented
- Computational cost estimated
- Risk mitigation planned

---

### Package 3B-3: Build Panel Code Template (30 min)
**What:** Write skeleton code for 108-symbol panel construction

**Input:**
- `daily_multi_timescale_fusion_panel.py` (existing code)
- `universe_expansion_spec.md` (design)

**Output:**
- `build_108_symbol_panel_template.py` (parameterized code)

**Steps:**
1. Copy existing panel construction code
2. Parameterize for N symbols (not hardcoded 8)
3. Verify syntax (no execution)
4. Add comments explaining each section
5. Mark TODO sections for later implementation
6. Ensure imports are correct

**Acceptance Criteria:**
- Code has correct Python syntax
- Parameterized for 108 symbols
- No hardcoded 8-symbol assumptions
- Ready to execute after Item 3b PASS

---

### Package 3B-4: Expansion Risk Assessment (30 min)
**What:** Identify risks in expanding to 108 symbols

**Input:**
- `universe_symbols.json`
- `data_availability_report.md`

**Output:**
- `expansion_risk_assessment.json`

**Steps:**
1. Identify data quality risks (gaps, missing data)
2. Identify computation risks (time, memory)
3. Identify model risks (overfitting on larger universe?)
4. For each risk: severity, likelihood, mitigation
5. Create decision tree: proceed or reduce universe?
6. Recommend go/no-go decision

**Acceptance Criteria:**
- All risks identified
- Severity/likelihood assessed
- Mitigation strategies proposed
- Go/no-go recommendation clear

---

## DEPLOYMENT STRATEGY

### Timeline

**Aug 29 (Day 1, parallel with Phase 4 on desktop):**
```
Desktop:                  Razer (parallel):
09:00 - 11:00  Phase 4A   09:00 - 09:45  Package 3A-1
11:00 - 12:00  Phase 4B   09:45 - 10:30  Package 3A-2
12:00 - 13:00  Phase 4C   10:30 - 11:15  Package 3A-3
               [lunch]    11:15 - 11:45  Package 3A-4
14:00 - 16:00  Phase 4D   14:00 - 14:45  Package 3B-1
16:00 - 17:00  Phase 4E   14:45 - 15:30  Package 3B-2
               [review]   15:30 - 16:00  Package 3B-3
                          16:00 - 16:30  Package 3B-4
```

**Outputs converge on Aug 29 evening:**
- Desktop: 108-symbol panel built + validated
- Razer: Feature analysis + expansion plan ready

---

## PACKAGE SPECIFICATIONS

### Each Package Should Output:

**Type 1: Analysis Package (3A-1, 3A-2, 3A-3, 3B-1, 3B-2, 3B-4)**
- Markdown report (human-readable)
- JSON data (machine-readable)
- No code execution (read-only analysis)

**Type 2: Code Template Package (3B-3)**
- Python source file (.py)
- Valid syntax (verified with py_compile)
- Parameterized for reuse
- Ready to execute later

---

## SUCCESS CRITERIA

| Package | Success Look | Status |
|---------|--------------|--------|
| 3A-1 | All 55 features documented | ✓ Objective |
| 3A-2 | No causality issues found | ✓ Objective |
| 3A-3 | Stability analyzed, <50% drift flagged | ✓ Objective |
| 3A-4 | JSON template valid, ready for data | ✓ Objective |
| 3B-1 | All 108 symbols checked, status assigned | ✓ Objective |
| 3B-2 | Expansion plan clear and documented | ✓ Objective |
| 3B-3 | Code syntax valid, parameterized | ✓ Objective |
| 3B-4 | Risks assessed, go/no-go decided | ✓ Objective |

---

## DEPENDENCIES

- **3A-1 → 3A-2:** 3A-1 output used as input to 3A-2
- **3A-3:** Independent (can run anytime)
- **3A-4:** Independent (can run anytime)
- **3B-1 → 3B-2 → 3B-3:** Sequential (each output is input to next)
- **3B-1 → 3B-4:** Both depend on 3B-1

---

## MACHINE CONSTRAINTS

**Razer Laptop:**
- CPU: Mobile processor (constrained)
- RAM: 16GB
- GPU: GTX 1060 Max-Q (6GB VRAM)
- **Best for:** Read-only analysis, lightweight computation
- **Avoid:** Large model training, inference on >10k rows

**All Phase 3A/3B packages:**
- Read CSV files (no training)
- Compute statistics
- Write markdown/JSON
- **Estimated load:** 5-10% CPU utilization
- **Estimated duration:** 5-10 min per package (includes overhead)

---

## NOTES FOR RAZER EXECUTION

1. **Keep Ollama running:** Use existing qwen3-coder:30b if needed for code review
2. **Monitor disk space:** CSV files ~50MB each, output files negligible
3. **Network stable:** If pulling data from cloud, verify connection
4. **No credential risk:** Phase 3A/3B have zero order authority (read-only)
5. **Commit results:** All output files should be git-committed

---

## IF SOMETHING BREAKS

**Issue:** CSV file not found  
→ Verify file path in current directory

**Issue:** JSON parse error  
→ Use `python -m json.tool filename.json` to validate

**Issue:** Markdown formatting issue  
→ Check for unmatched quotes, backticks

**Issue:** Feature not found in catalog  
→ Check if feature exists in original CSV columns

---

## RECOMMENDED EXECUTION PATTERN

```bash
# On Razer laptop

# Package 3A-1
python phase_3a1_feature_provenance.py
# Check: feature_provenance_catalog.md exists, has 55 features

# Package 3A-2
python phase_3a2_causality_verification.py
# Check: causality_verification_report.md, no issues found

# Package 3A-3 (parallel OK)
python phase_3a3_feature_stability.py
# Check: feature_stability_analysis.json valid

# Package 3A-4 (parallel OK)
python phase_3a4_feature_importance.py
# Check: feature_importance_skeleton.json valid, all features listed

# Package 3B-1
python phase_3b1_data_availability.py
# Check: universe_symbols.json, 108 symbols listed

# Package 3B-2
python phase_3b2_expansion_spec.py
# Check: universe_expansion_spec.md, plan documented

# Package 3B-3
python phase_3b3_panel_template.py
# Check: build_108_symbol_panel_template.py syntax valid

# Package 3B-4
python phase_3b4_risk_assessment.py
# Check: expansion_risk_assessment.json, go/no-go decided

# Commit all
git add phase_3a*.* phase_3b*.*
git commit -m "Phase 3A/3B: Feature analysis + universe planning complete"
```

---

## EXPECTED OUTPUTS

**Phase 3A (Feature Analysis):**
- `feature_provenance_catalog.md` (1-2 pages)
- `causality_verification_report.md` (1-2 pages)
- `feature_stability_analysis.json` (data file)
- `feature_stability_charts.md` (charts/observations)
- `feature_importance_skeleton.json` (data file)

**Phase 3B (Universe Planning):**
- `universe_symbols.json` (data file, 108 entries)
- `data_availability_report.md` (1-2 pages)
- `universe_expansion_spec.md` (3-5 pages)
- `build_108_symbol_panel_template.py` (code file)
- `expansion_risk_assessment.json` (data file)

**Total:** 10 output files, ~15 pages of documentation

---

## NEXT STEPS AFTER PHASE 3A/3B

**After Aug 29 evening:**
1. Claude reviews all Phase 3A/3B outputs (10 min)
2. Consolidation: Feature analysis + expansion plan merged
3. **Decision point:** Go/no-go for Phase 4 continuation

If PASS: Proceed to Phase 5 (MPC assembly)  
If HOLD: Debug and rerun

---

Generated: 2026-08-28  
Status: Ready to deploy on Aug 29
