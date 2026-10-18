# External-library calibration engine

A second, additive Revision 2 pipeline (`revision2_external/`) that replaces
8 of the 10 black boxes with real, installed external libraries, agreed on
after two rounds of critique on which libraries actually fit a single-
process Python backtester for a REST/WebSocket broker (Zerodha), not a
distributed system or a FIX-speaking one.

Does not modify or replace `revision2/` -- both exist side by side, the
same cross-checking principle as the Backtrader parity branch
(`codex/backtrader-parity`).

## The mapping

| # | Box | Library | Module |
|---|---|---|---|
| 1 | StartupCapabilityLock | **Pydantic** | `startup_validation.py` |
| 2 | DataIngestion (loader) | **ArcticDB** | `data_loader_arctic.py` |
| 3 | L2DataCertifier | **Pandera** | `data_certification_pandera.py` |
| 4 | Predictive Analytics | **TA-Lib** | `indicators_talib.py` |
| 5 | Intelligent Discrimination | **Gaussian HMM** (from scratch -- see below) | `regime_hmm.py`, `regime_id_box.py` |
| 6 | Model Predictive Control | **simple-pid** | `pid_controller.py` |
| 7 | SafetyGates (18-gate) | unchanged, in-house | `gates_framework.py` (reused) |
| 8 | PositionManager | **PyPortfolioOpt** | `position_sizing_pyportfolioopt.py` |
| 9 | P01D | unchanged, in-house (stdlib `hmac` already covers signing) | `revision2.boxes.P01DBox` (reused) |
| 10 | UnifiedExecution / broker | **kiteconnect + tenacity** | `broker_adapter_kite.py` (NOT used during calibration) |

## hmmlearn is not installed -- and can't be, yet

`hmmlearn`'s hard dependency, `numba`, has no release supporting Python
3.14 as of this build (`pip install numba` -> "Cannot install on Python
version 3.14.4; only versions >=3.10,<3.14 are supported" -- verified
directly, latest numba is 0.67.0). This is a real, current environment
constraint.

Rather than skip the swap or claim hmmlearn is installed, `regime_hmm.py`
implements a real Gaussian HMM (Baum-Welch EM, Viterbi decoding, diagonal-
covariance emissions -- textbook Rabiner-1989 HMM machinery) from scratch
in NumPy, matching this project's own established precedent:
`revision2/optimizer.py` did the same thing for RandomSearch/TPE/CMA-ES
when `optuna`/`cma` weren't available, for the identical reason.

If numba adds Python 3.14 support later, `hmmlearn.hmm.GaussianHMM` is a
drop-in replacement -- `tests_external/test_regime_hmm.py` is what to
rerun to confirm equivalence before swapping.

## Real bugs found and fixed while building this

1. `startup_validation.py`: the registry uses `minimum == maximum == 0` as
   a documented sentinel for "no real range" on every safety/fixed
   parameter -- naively enforcing it as a literal `[0, 0]` bound rejected
   every valid safety default. Fixed by skipping the range constraint when
   both bounds are exactly zero.
2. `regime_hmm.py`: a state with near-zero occupancy in the EM M-step left
   its transition-matrix row summing to ~0, and the row-normalization step
   then divided ~0/~0 -> NaN, corrupting the model for every later
   iteration. Fixed with an explicit degenerate-row fallback to a uniform
   distribution.
3. `regime_id_box.py`: fitting the HMM once on a single (often
   homogeneous) warmup window gave it no real second regime to learn --
   proven by a calm-then-shock synthetic test that initially failed both
   directions (calm data classified stressed, and vice versa). Fixed by
   refitting periodically (every 20 bars, not every single bar -- an
   earlier every-bar version made a real 3-symbol/1500-bar run time out at
   2+ minutes) on the trailing window, with a variance-ratio significance
   threshold so genuinely homogeneous data reports "calm" honestly instead
   of a coin-flip label.
4. `orchestrator.py`: `Gate08SymbolConcentration` (unchanged, in-house)
   correctly rejected every single trade once real data was used --
   PyPortfolioOpt's max-Sharpe solution can concentrate ~100% of weight
   into one symbol, exceeding the hard `max_exposure_per_symbol_fraction`
   safety cap. Not a bug in the gate (it's doing exactly its job); fixed by
   pre-clipping `PyPortfolioOptPositionManagerBox.size()`'s output to that
   same cap, so proposals aren't wasted on sizes guaranteed to be rejected.
   The gate still runs downstream as the final, authoritative check.
5. `orchestrator.py`: `trading_hours_start`/`trading_hours_end` were read
   via a bare `config.require()` call instead of each box's own trace-
   emitting `req()` pattern, so they showed up as "unconsumed" in the
   parameter-coverage report despite genuinely gating control flow. Fixed
   by tracking them explicitly.

## Verified state

- 38 tests across 9 new modules, all passing (`tests_external/`).
- A real end-to-end run (ADANIENT/INFY/TCS, 1500 bars each, 60-bar warmup)
  produces 9 real trades, net_pnl = -1568.44 (an honest negative result,
  not massaged -- consistent with this project's established finding that
  the underlying strategy hasn't yet cleared profitability on any real
  window tried so far).
- Parameter coverage: 62/68, with the 6 gaps all documented and intentional
  (`data_validation_mode` has no Pandera equivalent to consume it;
  `learning_rate_exploration_factor`/`phase1_exploration_intensity`/
  `phase2_optimization_intensity` are optimizer meta-parameters, already an
  established exclusion elsewhere in this project;
  `max_sector_exposure_fraction`/`max_symbol_concentration` are
  deliberately replaced by PyPortfolioOpt's own optimized weights).
- Reproducible dependency set frozen in `requirements_external_engine.txt`.

## Not done / open

- Not yet run at full 48-symbol scale (only spot-checked on 3 real symbols
  so far -- the HMM refit cadence and PyPortfolioOpt reweight cadence were
  tuned for that scale and would need re-timing at 48 symbols).
- No calibration-supervisor wiring yet (Random/TPE/CMA-ES search over this
  engine specifically) -- `revision2/calibration_supervisor.py` targets
  `Revision2PortfolioOrchestrator`'s interface; pointing it at this engine
  instead is a natural next step, not yet done.
- No comparison run against `revision2/portfolio_orchestrator.py` on
  identical data yet (the obvious next milestone, mirroring the Backtrader
  parity branch's own methodology).
