"""Pins BLAS/LAPACK threading before any numpy-dependent import.

Real, observed bug, not hypothetical: tests_external/conftest.py already
pins these same five env vars for the pytest suite, because running the
identical test suite twice gave different results for one GaussianHMM
regime test. That fix only covers pytest (conftest.py is imported by
pytest itself before any test module, hence before numpy/hmmlearn) -- it
does nothing for a plain script run via `python3 scripts/foo.py`.

Confirmed this gap is real, not just theoretical, by tracing the SAME
external-engine 6-month INFY backtest twice with no code change between
runs: 181 completed trades (stop 142 / target 20 / stop_gap 19) the first
time, 176 the second (stop 136 / stop_gap 24 / target 15 /
regime_stressed_exit 1). Same registry defaults, same market data, same
git commit -- the only thing that can explain a different trade COUNT
(not just different P&L on the same trades) is a different sequence of
regime classifications from HMMIntelligentDiscriminationBox's own
GaussianHMM fit, which is exactly what tests_external/conftest.py's own
docstring already diagnosed: multi-threaded BLAS can sum floats in a
different order run to run, flipping a borderline regime classification
near its own significance threshold, which changes id_approvals, which
changes which trades even exist.

Every real, non-pytest entrypoint (scripts/run_*.py, calibration
scripts) needs this SAME guard, imported as the very first thing --
before pandas, before anything that transitively pulls in numpy -- or
setting the env vars afterward has no effect (OpenBLAS reads these at
its own initialization, not per-call). This module exists so that fix is
one shared, tested place instead of five duplicated env var blocks.

Known real gap, disclosed rather than silently left: as of this
commit, only the scripts this session actively re-verified
(run_infy_6month_real.py, diagnose_saturation_streaks_real_infy.py) have
been updated to import this first. Every other real script that touches
the external engine's HMM regime box (48-symbol calibration runs, the
3-year INFY/Maruti runs, etc.) has NOT been re-audited for this same gap
-- any "real" number this project has reported from THOSE scripts should
be treated as unverified for exact reproducibility until they are.
"""
import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
