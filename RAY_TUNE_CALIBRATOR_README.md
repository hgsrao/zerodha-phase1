# Ray Tune + Optuna Calibrator for Revision 4

**Massive parallel parameter calibration using distributed computing + intelligent Bayesian optimization.**

---

## Quick Start

### 1️⃣ Prepare Data

```bash
python prepare_calibration_data.py \
    --manifest revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json \
    --output symbol_bars.pkl \
    --days 30
```

**What it does:**
- Loads all 48 NSE symbols from manifest
- Extracts 30 days (1 month) of 1-minute bars
- Adds 1 day of warmup bars
- Serializes to `symbol_bars.pkl` for parallel calibration

**Output:**
```
[LOAD] Loading manifest: revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json
  ✓ Found 48 symbols

[PLAN] Data extraction:
  Days: 30 (calibration) + 1 (warmup) = 31 total
  Bars per day: 390
  Bars needed per symbol: ~12090

[LOAD] Loading symbol data...
  [ 5/48] ADANIENT         ✓ (12090 bars)
  [10/48] APOLLOHOSP       ✓ (12090 bars)
  ...

[RESULT]
  Symbols loaded: 48
  Symbols skipped: 0
  Total bars: 580,320

[SAVE] Serializing to symbol_bars.pkl...
  ✓ 12.5 MB
```

---

### 2️⃣ Run Ray Tune Calibration

```bash
python ray_tune_optuna_calibrator.py \
    --num-samples 200 \
    --num-workers 16 \
    --data-path symbol_bars.pkl \
    --output-dir ./calibration_results
```

**Parameters:**

| Flag | Default | Description |
|------|---------|-------------|
| `--num-samples` | 100 | Total parameter combinations to test |
| `--num-workers` | 16 | Parallel workers (use all CPU cores) |
| `--data-path` | *required* | Path to pickled symbol bars |
| `--output-dir` | `./calibration_results` | Output directory |
| `--warmup` | 60 | Warmup bars to skip |

**CPU/Memory:** Each worker uses ~1 CPU core + ~500MB RAM. With 16 workers and 200 samples:
- Total time: ~2-4 hours (depends on data size)
- Peak memory: ~8-12 GB
- CPU: Near 100% across all cores

---

## Architecture

### The 10x Calibration Stack

```
┌─────────────────────────────────────────────────────────┐
│  RAY TUNE (Distributed Computing Framework)             │
│  - Multi-core scheduling                                │
│  - Worker management                                    │
│  - Result aggregation                                   │
└────────────────────┬────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
   ┌────▼──────┐          ┌──────▼─────┐
   │ Worker 1  │  ......  │ Worker 16  │
   │ CPU Core  │          │ CPU Core   │
   └────┬──────┘          └──────┬─────┘
        │                         │
        │  ┌───────────────────┐  │
        └─►│   OPTUNA SAMPLER  │◄─┘
           │  (TPE Algorithm)  │
           │  - Smart sampling │
           │  - Learning       │
           └───────┬───────────┘
                   │
         ┌─────────▼──────────┐
         │ ORCHESTRATOR       │
         │ (Black Box)        │
         │ - Takes 45 params  │
         │ - Returns metrics  │
         └────────────────────┘
```

---

## How It Works

### Phase 1: Random Exploration + TPE
- **First 30 samples:** Random parameter exploration
- **Samples 31-60:** TPE learns from results
- **Goal:** Find promising parameter regions

### Phase 2: Bayesian Optimization
- **Samples 61-150:** CMA-ES refinement
- **TPE focuses:** On regions with high scores
- **Goal:** Converge on local optima

### Phase 3: Fine-Tuning
- **Samples 151-200:** Local optimization
- **Goal:** Polish best parameters

### Composite Score (Optimization Target)

```
SCORE = Sharpe Ratio + 0.5 × min(Profit Factor, 5.0) - 2.0 × Max Drawdown

Examples:
  Sharpe 1.5, PF 1.2, DD 0.10 → Score = 1.5 + 0.6 - 0.2 = 1.9 ✅ (good)
  Sharpe 0.8, PF 0.9, DD 0.05 → Score = 0.8 + 0.45 - 0.1 = 1.15 (OK)
  Sharpe 0.0, PF 0.3, DD 0.20 → Score = 0.0 + 0.15 - 0.4 = -0.25 (bad)
```

---

## All 45 Calibratable Parameters

### PA Signal Generation (19 parameters)

| Category | Parameters | Range |
|----------|-----------|-------|
| **Multipliers** | base_dp_dt_multiplier, base_dv_dt_multiplier | 0.5–2.0 |
| **Periods** | momentum, vwap, atr calculation periods | 10–30 |
| **Weights** | momentum, vwap, volatility, confirmation weights | 0.05–0.4 |
| **Quality Thresholds** | green, amber, red thresholds | 0.1–0.95 |
| **Regimes** | volatility, low/medium/high vol multipliers | 0.7–1.5 |
| **Smoothing** | entry/exit smoothing windows, persistence | 1–8 |

### ID Confidence (3 parameters)

| Parameter | Range | Default |
|-----------|-------|---------|
| entry_confidence_threshold | 0.30–0.80 | 0.50 |
| exit_confidence_threshold | 0.40–0.90 | 0.60 |
| slippage_guard_threshold | 0.01–0.15 | 0.05 |

### MPC / Position Sizing (15 parameters)

| Category | Parameters | Range |
|----------|-----------|-------|
| **Profit Targets** | profit_target_atr_mult, margin_buffer, min_profit_rupees | 0.0–2.5 |
| **Stop Loss** | stop_loss_atr_mult, min_risk_reward_ratio | 0.3–3.0 |
| **Holding** | min/max hold bars | 1–120 |
| **Slippage** | slippage_cost_multiplier | 0.8–1.5 |
| **PID Tuning** | 9 parameters (Kp, Ki, Kd for entry/exit + window/clamp) | 0.01–0.3 |

### Position Manager (6 parameters)

| Parameter | Range |
|-----------|-------|
| max_positions_live | 1–12 |
| max_positions_per_symbol | 1–3 |
| capital_per_trade_fraction | 0.005–0.10 |
| min_capital_buffer_fraction | 0.05–0.30 |
| max_sector_exposure_fraction | 0.10–0.60 |
| max_symbol_concentration | 0.01–0.15 |

### Safety Gates (6 parameters)

| Parameter | Range |
|-----------|-------|
| drawdown_normal_threshold | 0.05–0.20 |
| drawdown_derated_threshold | 0.10–0.25 |
| drawdown_halt_threshold | 0.15–0.35 |
| max_loss_per_trade_rupees | 1000–20000 |
| max_loss_per_day_rupees | 10000–150000 |
| portfolio_lambda_risk_limit | 0.05–0.30 |

### P01D / Execution (5 parameters)

| Parameter | Range |
|-----------|-------|
| limit_order_offset_percent | 0.00–0.05 |
| order_timeout_seconds | 5–120 |
| max_retry_attempts | 0–5 |
| retry_delay_seconds | 1–20 |
| slippage_tolerance_percent | 0.02–0.20 |

### Optimizer Meta (3 parameters)

| Parameter | Range |
|-----------|-------|
| phase1_exploration_intensity | 30–100 |
| phase2_optimization_intensity | 100–500 |
| learning_rate_exploration_factor | 0.01–0.10 |

### Position Manager Other (2 parameters)

| Parameter | Range |
|-----------|-------|
| rebalance_frequency_minutes | 15–240 |

---

## Output Files

### Primary Result
```
calibration_results/
└── calibration_winner.json
    ├── timestamp: "2026-09-09T14:23:45.123456"
    ├── best_score: 1.234
    ├── best_config: {all 45 parameters}
    ├── metrics: {
    │   "net_pnl": 50000.0,
    │   "sharpe": 1.25,
    │   "profit_factor": 1.15,
    │   "max_drawdown": 0.08,
    │   "trades": 145
    │ }
    └── calibration_params: {...}
```

### Ray Tune Logs
```
calibration_results/
└── revision4_calibration/
    ├── events.out.tfevents.xxx    (TensorBoard events)
    ├── trial_1/                   (Worker 1 results)
    ├── trial_2/                   (Worker 2 results)
    └── ...
```

---

## Typical Results

### 200 Samples on 48 Symbols × 30 Days

| Metric | Expected |
|--------|----------|
| **Time** | 2.5–4 hours (16 workers) |
| **Best Sharpe** | 0.8–1.5 |
| **Best Profit Factor** | 0.95–1.20 |
| **Max Drawdown** | 8–15% |
| **Trades** | 100–300 |
| **Convergence** | After ~100 samples |

### Acceptance Thresholds (Hard Gates)

These are separate from calibration optimization:

| Gate | Threshold | Status |
|------|-----------|--------|
| Min trades | 50 | Hard requirement |
| Profit factor | ≥0.90 | Hard requirement |
| Max drawdown | ≤25% | Hard requirement |
| Min symbols traded | ≥2 | Hard requirement |

**Calibration score optimizes beyond these thresholds** (searches for best-of-best, not just gate-passing).

---

## Advanced Usage

### Custom Parameter Ranges

Edit `ray_tune_optuna_calibrator.py`:

```python
PARAMETER_RANGES = {
    "entry_confidence_threshold": (0.40, 0.75),  # Narrow from (0.3, 0.8)
    "profit_target_atr_mult": (1.0, 2.0),       # Tighten from (0.8, 2.5)
    # ... rest unchanged
}
```

### Using Fewer Samples

For quick testing:

```bash
python ray_tune_optuna_calibrator.py \
    --num-samples 50 \
    --num-workers 8 \
    --data-path symbol_bars.pkl
```

**Tradeoff:** Faster (20 min) but less thorough exploration

### Parallel Across Multiple Machines

Ray supports distributed clusters. For multi-machine setup:

```python
import ray

# Connect to existing Ray cluster
ray.init(address="ray://cluster-head:10001")

# Run calibration as normal
results = tune.run(...)
```

---

## Troubleshooting

### "CUDA memory exhausted" on GPU

Ray Tune doesn't use GPU by default. If you see this:
- Ensure no TensorFlow/PyTorch defaults to GPU
- The orchestrator should run on CPU

### Workers hanging or not starting

```bash
# Check Ray cluster status
ray status

# Kill and restart Ray
ray stop
ray start --head
```

### Out of memory with 16 workers

Reduce workers:
```bash
python ray_tune_optuna_calibrator.py \
    --num-workers 8 \
    --num-samples 200
```

### Scores all negative/flat

- Check orchestrator metrics are returning correctly
- Verify data was loaded properly
- Ensure warm-up bars don't overlap with calibration period

---

## Integration with Revision 4

The calibrator treats your Revision 4 orchestrator as a **pure black box**:

1. **Input:** 45 parameters as dict
2. **Orchestrator:** `Revision4PortfolioOrchestrator` initialization + run
3. **Output:** Metrics (sharpe, pnl, profit_factor, max_drawdown, trades)

**No assumptions about internal implementation** — orchestrator can be refactored without changing calibrator.

---

## Next Steps

1. **Run data preparation** → `symbol_bars.pkl`
2. **Start calibration** with 200 samples
3. **Monitor progress** in `calibration_results/`
4. **Extract best parameters** from `calibration_winner.json`
5. **Validate on out-of-sample** test data
6. **Deploy to production** with proven parameters

---

## References

- **Ray Tune:** https://docs.ray.io/en/latest/tune/ (distributed computing)
- **Optuna:** https://optuna.readthedocs.io/ (Bayesian optimization)
- **TPE Sampler:** https://optuna.readthedocs.io/en/stable/reference/samplers.html#tpe-sampler (algorithm)
- **ASHA Scheduler:** Early stopping for efficiency

---

**Status: Ready to calibrate. No calibration without explicit approval.**
