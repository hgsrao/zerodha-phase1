"""Pins BLAS/LAPACK threading before any numpy-dependent import, for the
whole tests_external/ suite -- not just the calibration scripts.

Real, observed nondeterminism, not hypothetical: running the exact same
test suite twice in a row gave different results for
test_regime_id_box.py::test_calm_regime_can_approve_a_strong_signal (one
run passed, the very next run of the identical code failed), while every
individual test passes reliably in isolation. This is the same class of
issue already found and mitigated for the real calibration scripts this
session (see scripts/run_external_engine_48symbol_1month_calibration.py's
module docstring) -- GaussianHMM's own linear algebra (regime_hmm.py) is
real numpy/BLAS computation, and multi-threaded BLAS can give different
floating-point summation order run to run, which is enough to flip a
borderline regime classification near its own significance threshold.
Pinning single-threaded BLAS here makes the whole suite's results
reproducible, not just faster/slower.
"""
import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
