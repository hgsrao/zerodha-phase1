"""Box 5 (Intelligent Discrimination) -- HMM regime-gated signal filter.

Keeps ID's existing confidence/red-band/slippage/risk-reward checks
(revision2/boxes.py's IntelligentDiscriminationBox -- these aren't regime
detection, an HMM has nothing to say about them) and ADDS a real regime
veto: a per-symbol 2-state Gaussian HMM (revision2_external.regime_hmm),
fit once from the warmup window on (return, rolling volatility) features,
classifies each incoming bar into "calm" or "stressed" by comparing its
state to the fitted variances (whichever state has the higher variance is
"stressed"). A stressed-regime classification vetoes entry the same way a
red quality band already does -- mathematically detected rather than a
fixed heuristic threshold.
"""

from __future__ import annotations

import zlib
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from revision2.contracts import EffectiveConfig, IDDecision, ParameterUse, PASignal
from revision2_external.regime_hmm import GaussianHMM
from revision2_external.bb05_bb06_parameters import default_config, require

_HMM_STATES = 2  # structural calm/stressed architecture, not an optimizer choice


class HMMIntelligentDiscriminationBox:
    # Controls that change features or the fitted model; changing any of them
    # invalidates cached fits and the causal posterior.  Refit cadence, the
    # variance-ratio labelling and admission controls act on the next read.
    _MODEL_CONTROLS = (
        "id_feature_window", "id_volatility_window", "id_volatility_min_samples",
        "id_hmm_iterations", "id_hmm_tolerance", "id_min_state_occupancy",
        "id_variance_floor", "id_initial_variance_regularizer"
    )

    def __init__(self, hmm_states: int = _HMM_STATES, config: Optional[EffectiveConfig] = None) -> None:
        if hmm_states != _HMM_STATES:
            raise ValueError('BB05 requires the existing two-state architecture')
        self.config = config if config is not None else default_config()
        self.hmm_states = hmm_states
        self._bar_history: Dict[str, deque] = {}
        self._cached_model: Dict[str, GaussianHMM] = {}
        self._cached_stressed_state: Dict[str, Optional[int]] = {}
        self._bars_since_refit: Dict[str, int] = {}
        self._cached_posterior: Dict[str, np.ndarray] = {}
        self._last_regime_observation: Dict[str, Dict[str, Any]] = {}

    def configure(self, config: EffectiveConfig) -> None:
        # All BB05 controls must be present, even when called outside startup.
        expected = tuple(name for name in default_config().values if name.startswith("id_"))
        for name in expected:
            require(config, name)
        changed = any(config.require(name) != self.config.require(name)
                      for name in self._MODEL_CONTROLS)
        self.config = config
        if changed:
            # A feature/model change invalidates the model and causal posterior.
            self._cached_model.clear()
            self._cached_posterior.clear()
            self._cached_stressed_state.clear()
            self._last_regime_observation.clear()
        for symbol, history in self._bar_history.items():
            window = require(config, "id_feature_window")
            if history.maxlen != window:
                self._bar_history[symbol] = deque(history, maxlen=window)

    def calibrate(self, symbol: str, warmup_bars: pd.DataFrame, config: Optional[EffectiveConfig] = None) -> None:
        self.configure(config if config is not None else self.config)
        closes = warmup_bars["close"].to_numpy(dtype=float)
        self._bar_history[symbol] = deque(list(closes[-require(self.config, "id_feature_window"):]), maxlen=require(self.config, "id_feature_window"))
        self._bars_since_refit[symbol] = require(self.config, "id_refit_every_bars")  # force a refit on the first real evaluation
        # Deliberately no fit here: a warmup window alone is usually
        # homogeneous (no real regime contrast to learn from yet), which
        # made the 2-state split arbitrary/noise-driven -- proven wrong by
        # this module's own first test run against a calm-then-shock
        # fixture. Fitting is deferred to _current_regime(), refit on the
        # actual trailing window once real regime contrast can appear in it.

    def _refit(self, symbol: str, features: np.ndarray) -> None:
        # Real bug found and fixed this session: hash(symbol) is Python's
        # randomized string hash (PYTHONHASHSEED, on by default since
        # Python 3.3, is different every process invocation unless
        # explicitly pinned) -- NOT a stable, reproducible seed at all,
        # despite looking like one. Verified directly: `python3 -c
        # 'print(hash("INFY"))'` gives a different number every run.
        # Every real backtest that reaches this method therefore got a
        # genuinely different GaussianHMM random_state on every single
        # process invocation, which changed the EM fit's convergence,
        # which changed which bars classify as "stressed", which changed
        # id_approvals and therefore which trades even exist -- confirmed
        # by tracing the identical 6-month INFY backtest twice: 181
        # completed trades one run, 176 the next, same code, same data.
        # zlib.crc32 is a real, stable, process-independent hash (used
        # here purely as a deterministic seed derivation, not for any
        # cryptographic or collision-resistance property) -- the same
        # symbol string always produces the same seed, in this run, in
        # tomorrow's run, on a different machine.
        random_state = zlib.crc32(symbol.encode("utf-8")) % (2 ** 31)
        model = GaussianHMM(n_states=self.hmm_states, n_iter=require(self.config, "id_hmm_iterations"), random_state=random_state,
                            tol=require(self.config, "id_hmm_tolerance"),
                            min_state_occupancy_fraction=require(self.config, "id_min_state_occupancy"),
                            variance_floor=require(self.config, "id_variance_floor"),
                            initial_variance_regularizer=require(self.config, "id_initial_variance_regularizer"))
        model.fit(features)
        valid_states = model.valid_state_mask_
        # An apparent two-state fit with a state supported by only a handful
        # of bars is not evidence of a second market regime.  In particular,
        # it must not use the variance floor of an unoccupied state to pass
        # the 2.5x stressed/calm threshold and veto entries.
        if valid_states is None or valid_states.sum() != self.hmm_states:
            self._cached_model[symbol] = model
            self._cached_stressed_state[symbol] = None
            return
        variances = model.vars_.sum(axis=1)
        stressed, calm = int(np.argmax(variances)), int(np.argmin(variances))
        # On genuinely homogeneous data (no real second regime present),
        # the model still has to split into n_states clusters -- it fits
        # two arbitrary sub-clusters of noise, and roughly half the time
        # the current bar lands in whichever one is arbitrarily labeled
        # "stressed", with no real regime shift behind it. Proven wrong by
        # this module's own first test run on purely-calm data. Requiring
        # the two states' variances to differ by a real margin before
        # acting on the split rejects that degenerate case; the model then
        # honestly reports "no evidence of a distinct regime" (None),
        # rather than a coin-flip label.
        self._cached_model[symbol] = model
        self._cached_stressed_state[symbol] = (
            stressed if variances[calm] > 0 and variances[stressed] / variances[calm] >= require(self.config, "id_variance_ratio") else None
        )

    def _current_regime(self, symbol: str, latest_close: float) -> str:
        history = self._bar_history.setdefault(symbol, deque(maxlen=require(self.config, "id_feature_window")))
        history.append(latest_close)
        closes = np.array(history)
        if len(closes) < require(self.config, "id_min_history_bars"):
            return "unknown"
        returns = pd.Series(closes).pct_change().dropna().to_numpy() * 100
        rolling_vol = pd.Series(returns).rolling(require(self.config, "id_volatility_window"), min_periods=require(self.config, "id_volatility_min_samples")).std().bfill().to_numpy()
        features = np.column_stack([returns, rolling_vol])

        # Baum-Welch EM (_refit) is real, iterative optimization -- too
        # expensive to rerun on every single bar at backtest scale (proven
        # by this module's own first full-orchestrator run timing out).
        # Reuse the cached model's one-step causal filter between refits;
        # classification stays current without recomputing history each bar.
        due = self._bars_since_refit.get(symbol, require(self.config, "id_refit_every_bars")) >= require(self.config, "id_refit_every_bars")
        if due or symbol not in self._cached_model:
            self._refit(symbol, features)
            self._bars_since_refit[symbol] = 0
            # A refit changes emission and transition parameters, so restart
            # the causal filter from this model's completed history.
            posterior = self._cached_model[symbol].filter_proba(features)[-1]
            self._cached_posterior[symbol] = posterior
        else:
            self._bars_since_refit[symbol] = self._bars_since_refit.get(symbol, 0) + 1
            # Do not recompute Viterbi and forward filtering across the
            # entire 200-bar window on every minute. The one-step filter is
            # the exact causal recurrence for the fixed model between
            # scheduled refits and reduces this path from O(window) to O(1).
            model = self._cached_model[symbol]
            posterior = model.filter_step(features[-1], self._cached_posterior.get(symbol))
            self._cached_posterior[symbol] = posterior

        model = self._cached_model[symbol]
        stressed_state = self._cached_stressed_state.get(symbol)
        if stressed_state is None:
            self._last_regime_observation[symbol] = {
                "available": False, "reason": "NO_DISTINCT_STRESSED_STATE",
                "stress_probability": None, "stressed_state": None,
            }
            return "calm"
        # In live/replay operation the filtered MAP state is causal. Unlike
        # a full Viterbi path, it does not repeatedly recompute history and
        # cannot revise earlier state assignments.
        state = int(np.argmax(posterior))
        stress_probability = float(posterior[stressed_state])
        regime = "stressed" if state == stressed_state else "calm"
        self._last_regime_observation[symbol] = {
            "available": True, "reason": "OK", "state": state,
            "stressed_state": stressed_state, "stress_probability": stress_probability,
            "posterior": [float(value) for value in posterior], "regime": regime,
            "state_variances": [float(value) for value in model.vars_.sum(axis=1)],
            "state_occupancy": [float(value) for value in model.state_occupancy_],
        }
        return regime

    def latest_regime_observation(self, symbol: str) -> Dict[str, Any]:
        """Most recent causal HMM posterior; read-only and telemetry-safe."""
        return dict(self._last_regime_observation.get(symbol, {
            "available": False, "reason": "NOT_EVALUATED", "stress_probability": None,
        }))

    def evaluate(self, signal: PASignal, config: EffectiveConfig, latest_close: float) -> Tuple[IDDecision, List[ParameterUse]]:
        self.configure(config)
        trace: List[ParameterUse] = [ParameterUse(name, 'ID', require(config, name), 'BB05 effective regime/admission control', 'approved')
                                     for name in config.values if name.startswith('id_')]

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = require(config, name)
            trace.append(ParameterUse(name, "ID", value, calculation, output_field))
            return value

        entry_threshold = float(req("entry_confidence_threshold", "minimum confidence to approve entry", "approved"))
        exit_threshold = float(req("exit_confidence_threshold", "carried into MPC as the exit-quality bar", "timing_quality"))
        slippage_limit = float(req("slippage_guard_threshold", "maximum tolerated estimated slippage", "approved"))
        min_rr = float(req("min_risk_reward_ratio", "minimum acceptable reward:risk", "risk_reward_ratio"))

        estimated_slippage = min(require(config, "id_slippage_cap"), signal.volatility * require(config, "id_slippage_gain"))

        if signal.direction == 0:
            return IDDecision(False, "no directional signal", signal.confidence, 0.0, exit_threshold), trace

        regime = self._current_regime(signal.symbol, latest_close)
        if regime == "stressed":
            return IDDecision(False, "HMM regime: stressed", signal.confidence, 0.0, exit_threshold), trace
        if signal.quality_band == "red":
            return IDDecision(False, "PA quality band is red", signal.confidence, 0.0, exit_threshold), trace
        if signal.confidence < entry_threshold:
            return IDDecision(False, f"confidence {signal.confidence:.4f} below entry threshold {entry_threshold:.4f}", signal.confidence, 0.0, exit_threshold), trace
        if estimated_slippage > slippage_limit:
            return IDDecision(False, f"estimated slippage {estimated_slippage:.4f} exceeds guard {slippage_limit:.4f}", signal.confidence, 0.0, exit_threshold), trace

        assumed_reward = max(signal.confidence, require(config, "id_reward_floor")) * require(config, "id_reward_gain")
        assumed_risk = max(1.0 - signal.confidence, require(config, "id_risk_floor")) * require(config, "id_risk_gain")
        risk_reward = assumed_reward / assumed_risk if assumed_risk else 0.0
        if risk_reward < min_rr:
            return IDDecision(False, f"risk:reward {risk_reward:.2f} below minimum {min_rr:.2f}", signal.confidence, risk_reward, exit_threshold), trace

        return IDDecision(True, "approved", signal.confidence, risk_reward, exit_threshold), trace
