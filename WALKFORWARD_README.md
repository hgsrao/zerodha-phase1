# Multi-Month Walk-Forward Validation Protocol

## OVERVIEW

Automated testing framework comparing **dynamic targets (peer-pooled)** vs **baseline (1.5R/60 bars)** across 5 out-of-sample months (Sep 2023 - Jan 2024).

---

## WALK-FORWARD SCHEDULE

| Test Month | Seed Period | Regime | Available Data |
|---|---|---|---|
| **September 2023** | Jul 3 - Aug 31 | Post-rally consolidation | 5 MARUTI paths + ~18 peer paths = 23 total |
| **October 2023** | Jul 3 - Sep 30 | Sector rotation, vol compression | Growing evidence base |
| **November 2023** | Jul 3 - Oct 31 | Trend re-acceleration | Full Q3 + Oct data |
| **December 2023** | Jul 3 - Nov 30 | Year-end liquidity thinning | 5 months of history |
| **January 2024** | Jul 3 - Dec 31 | New year vol expansion | Full 6 months foundation |

---

## KEY PROTOCOL FEATURES

### 1. **No Future Data Leakage**
Each month's seed is frozen at test start:
- Sep test sees only Jul-Aug seed
- Oct test sees only Jul-Sep seed (adds Oct training data)
- Nov test sees only Jul-Oct seed (adds Nov training data)
- Etc.

This prevents the dynamic provider from "seeing" future prices while learning.

### 2. **Expanding Evidence Window**
As we move forward in time, the provider sees more history:
```
Sep: 2 months of seed → proposal for Sep
Oct: 3 months of seed → updated proposal for Oct
Nov: 4 months of seed → updated proposal for Nov
Dec: 5 months of seed → updated proposal for Dec
Jan: 6 months of seed → final proposal for Jan
```

### 3. **Peer Pooling Evolution**
Evidence base grows across tests:
- Sep: 5 MARUTI + ~18 peers = 23 total
- Oct: 6 MARUTI + ~22 peers = 28 total (more history available)
- Nov: 7 MARUTI + ~26 peers = 33 total
- Dec: 8 MARUTI + ~30 peers = 38 total
- Jan: 10 MARUTI + ~35 peers = 45 total (strongest evidence)

---

## EXPECTED OUTCOMES

### Baseline (Fixed 1.5R / 60 bars)
```
September:  ??? (running now)
October:    ??? (to be tested)
November:   ??? (to be tested)
December:   ??? (to be tested)
January:    ??? (to be tested)

5-Month Total: ??? cumulative P&L
```

### Dynamic (Peer-Pooled, ~1.35R / ~52 bars)
```
September:  ??? (running now)
October:    ??? (to be tested)
November:   ??? (to be tested)
December:   ??? (to be tested)
January:    ??? (to be tested)

5-Month Total: ??? cumulative P&L
```

### Comparison Metrics
- Total P&L difference (Dynamic - Baseline)
- Win rate by month (how many months dynamic wins)
- Improvement percentage
- Max drawdown across months
- Sharpe ratio (if P&L tracking works)

---

## HOW TO LAUNCH

### Prerequisites
```bash
✅ Configuration fixed (stop_loss_atr_mult = 1.0)
✅ Sep 1 baseline validated (+₹108.01)
✅ Sep 1 dynamic test running (results in 5-10 min)
```

### Launch Walk-Forward Once Sep 1 Dynamic Completes

```bash
cd /home/shrinivas/ECS_Project_external_engine
python3 run_walkforward_validation.py
```

**Runtime estimate:** 30-45 minutes (5 months × ~6-8 min each)

---

## WHAT THE SCRIPT DOES

### For Each of 5 Months:
1. Launch orchestrator with **baseline config** (1.5R/60 bars)
   - Seed frozen at month start
   - Test entire month
   - Extract P&L from trace

2. Launch orchestrator with **dynamic config** (peer-pooled)
   - Same seed, same test period
   - But with dynamic targets proposal
   - Extract P&L from trace

3. Compare and log results

### Final Report
```
Month              Baseline P&L     Dynamic P&L      Δ P&L        Winner
═════════════════════════════════════════════════════════════════════════
September 2023     +₹108.01         ±₹100-110        -/0/+        TBD
October 2023       ????             ????             ????         TBD
November 2023      ????             ????             ????         TBD
December 2023      ????             ????             ????         TBD
January 2024       ????             ????             ????         TBD
─────────────────────────────────────────────────────────────────────────
TOTAL              ????             ????             ????         TBD

VERDICT: Dynamic ✅ or Baseline ✅
```

---

## SUCCESS CRITERIA

**Dynamic Targets Promoted if:**
- ✅ Dynamic total > Baseline total (positive delta)
- ✅ Win at least 3 of 5 months
- ✅ No catastrophic drawdown in any month

**Baseline Retained if:**
- ❌ Dynamic total ≤ Baseline total
- ❌ Win fewer than 3 months
- ❌ Any single month shows large loss

---

## OUTPUT FILES

**Results ledger:**
```
/home/shrinivas/ECS_Project_external_engine/diagnostic_output/walkforward_results.json
```

**Contains:**
```json
{
  "run_timestamp": "2026-09-13T...",
  "baseline_results": [...],
  "dynamic_results": [...],
  "comparison": {
    "baseline_total": 0,
    "dynamic_total": 0,
    "delta_total": 0,
    "improvement_pct": 0,
    "dynamic_wins": 0
  }
}
```

---

## NEXT STEPS AFTER WALK-FORWARD

**If Dynamic Wins:**
1. Promote peer-pooled dynamic targets to active paper mode
2. Run live paper trading (bounded position size)
3. Monitor for regime shifts that invalidate pooling
4. Quarterly re-validation with newest evidence

**If Dynamic Loses:**
1. Debug peer pooling weights (try 30% MARUTI / 70% peers)
2. Try sector-specific peers only (filter EICHERMOT as different)
3. Extend seed period (go back to Jan 2023)
4. Try regime-based targets (bull vs bear market proposals)
5. Retry walk-forward with adjusted approach

---

## CRITICAL ASSUMPTIONS FOR WALK-FORWARD

1. **Data continuity:** No gaps in MARUTI, M&M, BAJAJ-AUTO, EICHERMOT data
2. **Regime stability:** Jul 2023 - Jan 2024 reflects similar market structure
3. **Peer comparability:** Automotive sector maintains similar dynamics
4. **Walk-forward timing:** Each month tested immediately after seed frozen
5. **No rebalancing:** Target-R fixed once proposal made (not re-optimized mid-month)

---

## TIMELINE

```
Now:                Sep 1 dynamic test running (5 min remaining)
         ↓
+5 min:              Sep 1 dynamic results → Decide: proceed with walk-forward?
         ↓
+10 min:             Launch run_walkforward_validation.py
         ↓
+45 min:             Walk-forward complete
         ↓
+50 min:             Unified verdict: Dynamic ✅ or Baseline ✅
         ↓
Decision:            Promote dynamic OR iterate & retry
```

---

**Status:** Walk-forward runner ready, awaiting Sep 1 dynamic results.  
**Next:** Once Sep 1 completes, execute: `python3 run_walkforward_validation.py`

