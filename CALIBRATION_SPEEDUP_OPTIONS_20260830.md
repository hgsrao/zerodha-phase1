# Calibration Speedup Options — Hardware Constraints Analysis

**Date:** August 30, 2026 | **Problem:** 24-hour calibration is slow

---

## Current Bottleneck

```
Time per iteration:  ~2-3 minutes per parameter set
Iterations target:   300-700 total
Total time:          24 hours

Why slow?
- Loading 3 years × 48 symbols × 15min bars = ~500K data points
- Each iteration: backtest entire portfolio
- No parallelization
- Single-threaded
```

---

## Option 1: Parallel Local (FASTEST, NO CLOUD)

### How It Works
Run **4-8 independent calibration processes** on same machine, each testing different parameter ranges.

```python
# pseudo-code
Process 1: params_set_A (base_dp_dt: 0.8-0.95, ...)
Process 2: params_set_B (base_dp_dt: 0.95-1.1, ...)
Process 3: params_set_C (base_dp_dt: 1.1-1.25, ...)
... (4-8 parallel)

Then: Merge results, find best across all
```

### Speed Gain
- **4 parallel:** ~4x speedup → 6 hours
- **8 parallel:** ~6-7x speedup → 3-4 hours (hardware limit)

### Cost
- $0 (your machine)
- Uses existing CPU cores

### Trade-offs
- ✅ Fast
- ✅ No code rewrite
- ✅ Free
- ❌ Only works if you have multi-core CPU (most modern PCs do)
- ❌ Machine becomes unresponsive during run

### Implementation (TODAY)
1. Copy `STAGE2_CALIBRATION_33PARAMS_24HOURS.py` → `calibration_worker_1.py`, `calibration_worker_2.py`, etc.
2. Each modifies parameter ranges (different slice)
3. Run all in parallel PowerShell windows
4. Merge results in 1 hour

---

## Option 2: Cloud VM (FASTER + BETTER HARDWARE)

### Options

**AWS EC2 (Recommended)**
```
Instance:     c6i.4xlarge (16 CPU cores)
Cost:         ~$0.68/hour
Calibration:  ~2-3 hours (with parallelization)
Total cost:   ~$2-3

Other clouds:
- Google Cloud: similar pricing
- Azure: similar pricing
```

### Speed Gain
- **16 cores parallel:** ~12-16x speedup → 1.5-2 hours
- **+ better CPU:** Additional 1.5-2x faster per core
- **Total: 6 hours → 30-45 minutes**

### Cost
- EC2: $2-3 for this run
- Data transfer: ~$1-2
- **Total: ~$4-5**

### Trade-offs
- ✅ VERY fast (30-45 min)
- ✅ Machine stays responsive
- ✅ Can run anytime
- ✅ Scalable
- ❌ Small cost (~$5)
- ❌ Code needs minor port (pandas, numpy available on AWS)
- ❌ Data transfer (but dataset is only ~50-100 MB)

### Implementation (2-3 hours)
1. Launch EC2 instance (c6i.4xlarge, Ubuntu)
2. Install Python + dependencies
3. Upload data (~100 MB)
4. Run calibration (30-45 min actual)
5. Download results (~1 MB)
6. Terminate instance

---

## Option 3: Hybrid (BEST BALANCE)

### How It Works
1. **Local quick pass:** Run 4 parallel calibration on local machine (subset of data)
   - Use 1 year instead of 3 years
   - 48 symbols (same)
   - Take ~2-3 hours (instead of 24)
   - Get initial parameter hints

2. **Cloud validation:** Run best 20 parameter sets on AWS (full 3-year data)
   - Confirm they work on full dataset
   - Takes ~30 min on 16-core EC2
   - Cost: ~$0.50

3. **Result:** Best parameters in ~3-4 hours, cost ~$1

### Speed Gain
- Full: 24 hours → 3-4 hours (70% faster)
- Cost: $1

### Trade-offs
- ✅ Fast (3-4 hours)
- ✅ Cheap (~$1)
- ✅ Machine usable
- ✅ Validation is thorough
- ❌ Requires both local + cloud setup
- ❌ More complex

---

## Option 4: Algorithm Upgrade (MEDIUM EFFORT)

### Replace Random Search with Bayesian Optimization

Current: Random sampling (~12-30 iterations/hour)  
Bayesian: Intelligent sampling (~8-15 iterations/hour but 5-10x fewer needed)

```
Random:    300 iterations needed → 24 hours
Bayesian:  50 iterations needed → 3-4 hours (SAME SPEED, SMARTER)
```

### Implementation
```python
# Current
params = {random values}  # 300+ times

# Bayesian (scikit-optimize)
from skopt import gp_minimize

best = gp_minimize(
    fun=backtest_objective,
    space=[...],  # parameter ranges
    n_calls=50,  # NOT 300+
    n_initial_points=10
)
```

### Speed Gain
- Same hardware: 24 hours → 3-4 hours
- + Better parameters (not just random)

### Cost
- $0
- 1-2 hours coding

### Trade-offs
- ✅ Legitimate optimization (not random)
- ✅ Free
- ✅ Fewer iterations needed
- ✅ Better results
- ❌ Need to code Bayesian logic
- ❌ Takes 1-2 hours setup
- ❌ Current calibration must finish/restart

---

## Recommendation (My Vote)

### For TODAY (Continue current run)
**Option 1: Parallel Local** (6-8 hours total)
- Start 4 parallel STAGE2 processes NOW
- Different parameter range slices each
- Finish by tomorrow morning
- Cost: $0

### For FUTURE (Next optimization cycle)
**Option 3: Hybrid** (3-4 hours, $1)
- Local quick pass (1-year data)
- Cloud validation (full data)
- Best of both worlds

### For PRODUCTION (If pursuing real calibration)
**Option 4: Bayesian Algorithm** (3-4 hours, $0, better results)
- Replace random search
- Upgrade to scikit-optimize
- Get 5-10x fewer iterations needed
- Better parameter quality

---

## Quick Start: Option 1 (Parallel Local) — TODAY

### Steps

**Step 1: Create 4 worker scripts**
```bash
copy STAGE2_CALIBRATION_33PARAMS_24HOURS.py calibration_worker_1.py
copy STAGE2_CALIBRATION_33PARAMS_24HOURS.py calibration_worker_2.py
copy STAGE2_CALIBRATION_33PARAMS_24HOURS.py calibration_worker_3.py
copy STAGE2_CALIBRATION_33PARAMS_24HOURS.py calibration_worker_4.py
```

**Step 2: Modify each to use different parameter range slice**
```python
# Worker 1
PARAM_SLICE = "A"  # base_dp_dt: 0.80-0.95, base_dv_dt: 0.80-0.95

# Worker 2
PARAM_SLICE = "B"  # base_dp_dt: 0.95-1.10, base_dv_dt: 0.95-1.10

# Worker 3
PARAM_SLICE = "C"  # base_dp_dt: 1.10-1.25, base_dv_dt: 1.10-1.25

# Worker 4
PARAM_SLICE = "D"  # base_dp_dt: 0.80-1.25, base_dv_dt: 0.80-1.25 (full range)
```

**Step 3: Run all 4 in parallel**
```powershell
# Terminal 1
python calibration_worker_1.py

# Terminal 2
python calibration_worker_2.py

# Terminal 3
python calibration_worker_3.py

# Terminal 4
python calibration_worker_4.py
```

**Step 4: Monitor all 4**
```powershell
Get-Process python | Select-Object Id, CPU, Memory, StartTime
```

**Step 5: Merge results**
```python
# Load all 4 results files
# Find best across all workers
# Report winner
```

### Time Estimate
- Setup: 30 min
- Execution: 6 hours (parallel)
- Merge: 15 min
- **Total: 6.5 hours** (vs. 24 hours now)

---

## Decision Tree

```
Do you need result TODAY?
├─ YES, ASAP → Option 1 (Parallel Local, 6 hours)
│
Do you have $5 budget and want FASTEST?
├─ YES → Option 2 (Cloud EC2, 45 min)
│
Do you want BALANCED (fast + cheap)?
├─ YES → Option 3 (Hybrid, 3-4 hours, $1)
│
Do you want to IMPROVE ALGORITHM?
└─ YES → Option 4 (Bayesian, 3-4 hours, $0, better results)
```

---

## My Honest Recommendation

**Right now:** Let current calibration (PID 28072) finish as-is
- Already 30+ min in
- Will have some data by tomorrow morning
- Useful for exploratory analysis

**Next calibration run:** Use **Option 1 (Parallel)** if pursuing more
- Zero cost
- 6-hour turnaround
- No cloud setup needed
- Your machine probably has 4+ cores

**If this becomes regular:** Upgrade to **Option 4 (Bayesian)**
- Better algorithm anyway
- Replaces random search
- Same time, better results
- One-time 2-hour code investment

---

## Bottom Line

Hardware is the constraint, yes. But you have **3 solid options:**

1. **Free + Fast:** Parallel local (6 hours, $0)
2. **Cheapest:** Cloud EC2 (45 min, $5)
3. **Smart:** Bayesian algorithm (3-4 hours, $0, better results)

Pick one and I'll help implement immediately. Which appeals most?
