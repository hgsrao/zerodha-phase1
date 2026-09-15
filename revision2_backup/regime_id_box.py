"""Box 5 (Intelligent Discrimination) -- HMM regime-gated signal filter.

This is the in-house engine's hybrid ID box, combining:
1. All existing ID checks (confidence/red-band/slippage/risk-reward thresholds)
2. NEW: 2-state Gaussian HMM regime detection (revision2.regime_hmm)
   - Fitted once from warmup window on (return, rolling volatility) features
   - Classifies each bar into "calm" or "stressed"
   - Stressed regime veto blocks entry the same way red quality band does
   - Mathematically detected rather than fixed heuristic thresholds

Architecture:
- This box does GATING only (approve/reject), not SCALING
- Scaling by volatility regime happens in PredictiveAnalyticsBox (PA box)
- Separation of concerns: PA scales magnitude, ID gates approval

Determinism guardrail: Uses zlib.crc32(symbol) for HMM random_state seed,
NOT Python's randomized hash() — ensures reproducible HMM fits across
all process invocations (critical for calibration runs).
"""

from __future__ import annotations

import zlib
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from revision2.contracts import EffectiveConfig, IDDecision, ParameterUse, PASignal
from revision2.regime_hmm import GaussianHMM

_FEATURE_WINDOW = 200  # bars of trailing (return, rolling_vol) features used to classify the current bar
_REFIT_EVERY_BARS = 20  # Baum-Welch EM is refit this often per symbol, not every single bar evaluation


class HMMIntelligentDiscriminationBox:
    """Hybrid ID box: standard ID checks + HMM regime veto."""

    def __init__(self, hmm_states: int = 2) -> None:
        self.hmm_states = hmm_states
        self._bar_history: Dict[str, deque] = {}
        self._cached_model: Dict[str, GaussianHMM] = {}
        self._cached_stressed_state: Dict[str, Optional[int]] = {}
        self._bars_since_refit: Dict[str, int] = {}

    def calibrate(self, symbol: str, warmup_bars: pd.DataFrame) -> None:
        """Initialize per-symbol HMM state from warmup window.

        Deliberately defers fitting: a warmup window alone is usually
        homogeneous (no real regime contrast to learn from yet), which
        made the 2-state split arbitrary/noise-driven. Fitting is deferred
        to _current_regime(), refit on the actual trailing window once
        real regime contrast can appear in it during live trading.
        """
        closes = warmup_bars["close"].to_numpy(dtype=float)
        self._bar_history[symbol] = deque(list(closes[-_FEATURE_WINDOW:]), maxlen=_FEATURE_WINDOW)
        self._bars_since_refit[symbol] = _REFIT_EVERY_BARS  # force a refit on the first real evaluation

    def _refit(self, symbol: str, features: np.ndarray) -> None:
        """Fit (or refit) the HMM model on current trailing features.

        CRITICAL: Uses zlib.crc32(symbol) % (2**31) for random_state,
        NOT Python's hash(symbol). Python's hash is randomized by default
        (PYTHONHASHSEED), producing different values every process
        invocation. This caused the external engine's HMM to produce
        nondeterministic regime classifications (proven: identical 6-month
        INFY backtest produced 181 trades one run, 176 the next).

        zlib.crc32 is deterministic and stable across process invocations,
        process invocations, and machines—the same symbol always produces
        the same seed value.
        """
        random_state = zlib.crc32(symbol.encode("utf-8")) % (2 ** 31)
        model = GaussianHMM(n_states=self.hmm_states, n_iter=20, random_state=random_state)
        model.fit(features)
        variances = model.vars_.sum(axis=1)
        stressed, calm = int(np.argmax(variances)), int(np.argmin(variances))

        # On genuinely homogeneous data (no real second regime present),
        # the model still has to split into n_states clusters -- it fits
        # two arbitrary sub-clusters of noise, and roughly half the time
        # the current bar lands in whichever one is arbitrarily labeled
        # "stressed", with no real regime shift behind it. Requiring
        # the two states' variances to differ by a real margin before
        # acting on the split rejects that degenerate case; the model then
        # honestly reports "no evidence of a distinct regime" (None),
        # rather than a coin-flip label.
        self._cached_model[symbol] = model
        self._cached_stressed_state[symbol] = (
            stressed if variances[calm] > 0 and variances[stressed] / variances[calm] >= 2.5 else None
        )

    def _current_regime(self, symbol: str, latest_close: float) -> str:
        """Classify the current bar's market regime: 'calm', 'stressed', or 'unknown'.

        Computes (return, rolling_vol) features from trailing close history,
        evaluates via HMM Viterbi decoder. Refits model every _REFIT_EVERY_BARS
        bars (expensive EM step) and uses cached Viterbi between refits (cheap).
        """
        history = self._bar_history.setdefault(symbol, deque(maxlen=_FEATURE_WINDOW))
        history.append(latest_close)
        closes = np.array(history)

        # Need enough data to compute returns and rolling vol
        if len(closes) < 60:
            return "unknown"

        # Compute (return, rolling_vol) features
        returns = pd.Series(closes).pct_change().dropna().to_numpy() * 100
        rolling_vol = pd.Series(returns).rolling(10, min_periods=3).std().bfill().to_numpy()
        features = np.column_stack([returns, rolling_vol])

        # Refit model every _REFIT_EVERY_BARS (Baum-Welch is expensive)
        due = self._bars_since_refit.get(symbol, _REFIT_EVERY_BARS) >= _REFIT_EVERY_BARS
        if due or symbol not in self._cached_model:
            self._refit(symbol, features)
            self._bars_since_refit[symbol] = 0
        else:
            self._bars_since_refit[symbol] = self._bars_since_refit.get(symbol, 0) + 1

        # Viterbi decode: which state is the current bar in?
        stressed_state = self._cached_stressed_state.get(symbol)
        if stressed_state is None:
            return "calm"  # No significant regime split detected
        model = self._cached_model[symbol]
        state = int(model.predict(features)[-1])
        return "stressed" if state == stressed_state else "calm"

    def evaluate(self, signal: PASignal, config: EffectiveConfig, latest_close: float) -> Tuple[IDDecision, List[ParameterUse]]:
        """Hybrid evaluation: standard ID checks + HMM regime veto.

        Execution order matters:
        1. No directional signal → reject immediately
        2. HMM stressed regime → reject immediately (new veto)
        3. Red quality band → reject immediately (existing veto)
        4. Confidence threshold check (existing)
        5. Slippage limit check (existing)
        6. Risk:reward ratio check (existing)
        7. Approve (existing)

        All checks return the same IDDecision structure; early returns
        preserve the decision chain for tracing.
        """
        trace: List[ParameterUse] = []

        def req(name: str, calculation: str, output_field: str) -> Any:
            value = config.require(name)
            trace.append(ParameterUse(name, "ID", value, calculation, output_field))
            return value

        # Read all ID thresholds from config
        entry_threshold = float(req("entry_confidence_threshold", "minimum confidence to approve entry", "approved"))
        exit_threshold = float(req("exit_confidence_threshold", "carried into MPC as the exit-quality bar", "timing_quality"))
        slippage_limit = float(req("slippage_guard_threshold", "maximum tolerated estimated slippage", "approved"))
        min_rr = float(req("min_risk_reward_ratio", "minimum acceptable reward:risk", "risk_reward_ratio"))

        estimated_slippage = min(0.20, signal.volatility * 2.0)

        # Check 1: Directional signal required
        if signal.direction == 0:
            return IDDecision(False, "no directional signal", signal.confidence, 0.0, exit_threshold), trace

        # Check 2: HMM regime veto (NEW—this is the architectural upgrade)
        regime = self._current_regime(signal.symbol, latest_close)
        if regime == "stressed":
            return IDDecision(False, "HMM regime: stressed", signal.confidence, 0.0, exit_threshold), trace

        # Check 3: Quality band veto (existing)
        if signal.quality_band == "red":
            return IDDecision(False, "PA quality band is red", signal.confidence, 0.0, exit_threshold), trace

        # Check 4: Confidence threshold (existing)
        if signal.confidence < entry_threshold:
            return IDDecision(False, f"confidence {signal.confidence:.4f} below entry threshold {entry_threshold:.4f}", signal.confidence, 0.0, exit_threshold), trace

        # Check 5: Slippage limit (existing)
        if estimated_slippage > slippage_limit:
            return IDDecision(False, f"estimated slippage {estimated_slippage:.4f} exceeds guard {slippage_limit:.4f}", signal.confidence, 0.0, exit_threshold), trace

        # Check 6: Risk:reward ratio (existing)
        assumed_reward = max(signal.confidence, 0.05) * 4.0
        assumed_risk = max(1.0 - signal.confidence, 0.10) * 2.0
        risk_reward = assumed_reward / assumed_risk if assumed_risk else 0.0
        if risk_reward < min_rr:
            return IDDecision(False, f"risk:reward {risk_reward:.2f} below minimum {min_rr:.2f}", signal.confidence, risk_reward, exit_threshold), trace

        # Check 7: Approve
        return IDDecision(True, "approved", signal.confidence, risk_reward, exit_threshold), trace
