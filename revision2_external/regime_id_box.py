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

_FEATURE_WINDOW = 200  # bars of trailing (return, rolling_vol) features used to classify the current bar
_REFIT_EVERY_BARS = 20  # Baum-Welch EM is refit this often per symbol, not every single bar evaluation


class HMMIntelligentDiscriminationBox:
    def __init__(self, hmm_states: int = 2) -> None:
        self.hmm_states = hmm_states
        self._bar_history: Dict[str, deque] = {}
        self._cached_model: Dict[str, GaussianHMM] = {}
        self._cached_stressed_state: Dict[str, Optional[int]] = {}
        self._bars_since_refit: Dict[str, int] = {}

    def calibrate(self, symbol: str, warmup_bars: pd.DataFrame) -> None:
        closes = warmup_bars["close"].to_numpy(dtype=float)
        self._bar_history[symbol] = deque(list(closes[-_FEATURE_WINDOW:]), maxlen=_FEATURE_WINDOW)
        self._bars_since_refit[symbol] = _REFIT_EVERY_BARS  # force a refit on the first real evaluation
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
        model = GaussianHMM(n_states=self.hmm_states, n_iter=20, random_state=random_state)
        model.fit(features)
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
            stressed if variances[calm] > 0 and variances[stressed] / variances[calm] >= 2.5 else None
        )

    def _current_regime(self, symbol: str, latest_close: float) -> str:
        history = self._bar_history.setdefault(symbol, deque(maxlen=_FEATURE_WINDOW))
        history.append(latest_close)
        closes = np.array(history)
        if len(closes) < 60:
            return "unknown"
        returns = pd.Series(closes).pct_change().dropna().to_numpy() * 100
        rolling_vol = pd.Series(returns).rolling(10, min_periods=3).std().bfill().to_numpy()
        features = np.column_stack([returns, rolling_vol])

        # Baum-Welch EM (_refit) is real, iterative optimization -- too
        # expensive to rerun on every single bar at backtest scale (proven
        # by this module's own first full-orchestrator run timing out).
        # Reuse the cached model's cheap Viterbi predict() between refits;
        # the regime classification only needs to be approximately current,
        # not recomputed from scratch every bar.
        due = self._bars_since_refit.get(symbol, _REFIT_EVERY_BARS) >= _REFIT_EVERY_BARS
        if due or symbol not in self._cached_model:
            self._refit(symbol, features)
            self._bars_since_refit[symbol] = 0
        else:
            self._bars_since_refit[symbol] = self._bars_since_refit.get(symbol, 0) + 1

        stressed_state = self._cached_stressed_state.get(symbol)
        if stressed_state is None:
            return "calm"
        model = self._cached_model[symbol]
        state = int(model.predict(features)[-1])
        return "stressed" if state == stressed_state else "calm"

    def evaluate(self, signal: PASignal, config: EffectiveConfig, latest_close: float) -> Tuple[IDDecision, List[ParameterUse]]:
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "ID", value, calculation, output_field))
            return value

        entry_threshold = float(req("entry_confidence_threshold", "minimum confidence to approve entry", "approved"))
        exit_threshold = float(req("exit_confidence_threshold", "carried into MPC as the exit-quality bar", "timing_quality"))
        slippage_limit = float(req("slippage_guard_threshold", "maximum tolerated estimated slippage", "approved"))
        min_rr = float(req("min_risk_reward_ratio", "minimum acceptable reward:risk", "risk_reward_ratio"))

        estimated_slippage = min(0.20, signal.volatility * 2.0)

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

        assumed_reward = max(signal.confidence, 0.05) * 4.0
        assumed_risk = max(1.0 - signal.confidence, 0.10) * 2.0
        risk_reward = assumed_reward / assumed_risk if assumed_risk else 0.0
        if risk_reward < min_rr:
            return IDDecision(False, f"risk:reward {risk_reward:.2f} below minimum {min_rr:.2f}", signal.confidence, risk_reward, exit_threshold), trace

        return IDDecision(True, "approved", signal.confidence, risk_reward, exit_threshold), trace
