"""Box 5 (Intelligent Discrimination) -- Gaussian HMM regime detection.

hmmlearn is unavailable in this environment: its hard dependency, numba,
has no released version supporting Python 3.14 as of this build (verified
directly -- `pip install numba` fails with "Cannot install on Python
version 3.14.4; only versions >=3.10,<3.14 are supported", and this is the
latest numba release, 0.67.0). This is a real, current environment
constraint, not a design choice.

Rather than silently drop the regime-detection swap or fabricate that
hmmlearn is installed, this implements the same class of algorithm --
a Gaussian Hidden Markov Model trained via Baum-Welch EM, decoded via
Viterbi -- from scratch in NumPy. This matches this project's own
established precedent: revision2/optimizer.py did exactly this for
RandomSearch/TPE/CMA-ES when optuna/cma weren't available, with the same
justification. All math here (forward-backward in log-space, Viterbi,
diagonal-covariance Gaussian emissions, EM parameter updates) is textbook
Rabiner-1989 HMM machinery, not a novel or unverified algorithm.

Revisit: if numba adds Python 3.14 support, hmmlearn.hmm.GaussianHMM is a
drop-in replacement for GaussianHMM below (same fit/predict shape) -- this
module's own test suite is the thing to re-run to confirm equivalence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np


def _log_gaussian_pdf(x: np.ndarray, mean: np.ndarray, var: np.ndarray) -> np.ndarray:
    """Log density of a diagonal-covariance multivariate Gaussian,
    evaluated for every row of x (T, D) against one (mean, var) state."""
    var = np.maximum(var, 1e-8)
    d = x.shape[1]
    diff = x - mean
    return -0.5 * (d * np.log(2 * np.pi) + np.sum(np.log(var)) + np.sum(diff * diff / var, axis=1))


@dataclass
class GaussianHMM:
    """A minimal, real Gaussian HMM: diagonal-covariance emissions, full
    transition matrix, trained by Baum-Welch EM. API shape mirrors
    hmmlearn.hmm.GaussianHMM's fit()/predict()/score() closely enough to
    swap in directly if hmmlearn becomes installable later."""

    n_states: int
    n_iter: int = 50
    tol: float = 1e-4
    random_state: int = 0

    def __post_init__(self) -> None:
        self.means_: Optional[np.ndarray] = None
        self.vars_: Optional[np.ndarray] = None
        self.transmat_: Optional[np.ndarray] = None
        self.startprob_: Optional[np.ndarray] = None
        self.monitor_: List[float] = []

    def _init_params(self, X: np.ndarray) -> None:
        rng = np.random.default_rng(self.random_state)
        n, d = X.shape
        idx = rng.choice(n, size=self.n_states, replace=False)
        self.means_ = X[idx].copy()
        overall_var = np.var(X, axis=0) + 1e-6
        self.vars_ = np.tile(overall_var, (self.n_states, 1))
        self.transmat_ = np.full((self.n_states, self.n_states), 1.0 / self.n_states)
        self.startprob_ = np.full(self.n_states, 1.0 / self.n_states)

    def _log_emission(self, X: np.ndarray) -> np.ndarray:
        return np.column_stack([_log_gaussian_pdf(X, self.means_[k], self.vars_[k]) for k in range(self.n_states)])

    @staticmethod
    def _logsumexp(a: np.ndarray, axis=None) -> np.ndarray:
        amax = np.max(a, axis=axis, keepdims=True)
        amax = np.where(np.isfinite(amax), amax, 0)
        result = amax + np.log(np.sum(np.exp(a - amax), axis=axis, keepdims=True))
        return np.squeeze(result, axis=axis) if axis is not None else result

    def _forward_backward(self, log_b: np.ndarray):
        n, k = log_b.shape
        log_pi = np.log(np.maximum(self.startprob_, 1e-300))
        log_A = np.log(np.maximum(self.transmat_, 1e-300))

        log_alpha = np.zeros((n, k))
        log_alpha[0] = log_pi + log_b[0]
        for t in range(1, n):
            log_alpha[t] = self._logsumexp(log_alpha[t - 1][:, None] + log_A, axis=0) + log_b[t]

        log_beta = np.zeros((n, k))
        for t in range(n - 2, -1, -1):
            log_beta[t] = self._logsumexp(log_A + log_b[t + 1][None, :] + log_beta[t + 1][None, :], axis=1)

        log_likelihood = self._logsumexp(log_alpha[-1], axis=0)
        log_gamma = log_alpha + log_beta - log_likelihood
        gamma = np.exp(log_gamma)

        log_xi = np.zeros((n - 1, k, k))
        for t in range(n - 1):
            log_xi[t] = (
                log_alpha[t][:, None] + log_A + log_b[t + 1][None, :] + log_beta[t + 1][None, :] - log_likelihood
            )
        xi = np.exp(log_xi)
        return gamma, xi, float(log_likelihood)

    def fit(self, X: np.ndarray) -> "GaussianHMM":
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X[:, None]
        self._init_params(X)
        prev_ll = -np.inf
        for _ in range(self.n_iter):
            log_b = self._log_emission(X)
            gamma, xi, ll = self._forward_backward(log_b)
            self.monitor_.append(ll)

            self.startprob_ = gamma[0] / gamma[0].sum()
            xi_sum = xi.sum(axis=0)
            gamma_sum_excl_last = gamma[:-1].sum(axis=0)
            self.transmat_ = xi_sum / np.maximum(gamma_sum_excl_last[:, None], 1e-300)
            # A state that's essentially unoccupied in this window (gamma
            # summing to ~0) leaves its whole transmat_ row ~0 too, and the
            # row-sum below then divides ~0/~0 -> NaN, corrupting the model
            # for every later iteration. Fall back to a uniform row for a
            # degenerate state instead of propagating NaN.
            row_sums = self.transmat_.sum(axis=1, keepdims=True)
            degenerate = (row_sums.flatten() < 1e-12)
            if degenerate.any():
                self.transmat_[degenerate] = 1.0 / self.n_states
                row_sums[degenerate] = 1.0
            self.transmat_ /= row_sums

            weights = gamma.sum(axis=0)
            for k in range(self.n_states):
                w = gamma[:, k][:, None]
                self.means_[k] = (w * X).sum(axis=0) / max(weights[k], 1e-300)
                diff = X - self.means_[k]
                self.vars_[k] = (w * diff * diff).sum(axis=0) / max(weights[k], 1e-300)
                self.vars_[k] = np.maximum(self.vars_[k], 1e-8)

            if abs(ll - prev_ll) < self.tol:
                break
            prev_ll = ll
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """Viterbi most-likely state sequence."""
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X[:, None]
        log_b = self._log_emission(X)
        n, k = log_b.shape
        log_pi = np.log(np.maximum(self.startprob_, 1e-300))
        log_A = np.log(np.maximum(self.transmat_, 1e-300))

        delta = np.zeros((n, k))
        psi = np.zeros((n, k), dtype=int)
        delta[0] = log_pi + log_b[0]
        for t in range(1, n):
            scores = delta[t - 1][:, None] + log_A
            psi[t] = np.argmax(scores, axis=0)
            delta[t] = scores[psi[t], np.arange(k)] + log_b[t]

        path = np.zeros(n, dtype=int)
        path[-1] = int(np.argmax(delta[-1]))
        for t in range(n - 2, -1, -1):
            path[t] = psi[t + 1, path[t + 1]]
        return path

    def score(self, X: np.ndarray) -> float:
        X = np.asarray(X, dtype=float)
        if X.ndim == 1:
            X = X[:, None]
        log_b = self._log_emission(X)
        _, _, ll = self._forward_backward(log_b)
        return ll
