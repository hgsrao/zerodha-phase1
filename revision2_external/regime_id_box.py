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

import json
import math
import zlib
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from revision2.contracts import EffectiveConfig, IDDecision, ParameterUse, PASignal
from revision2_external.regime_hmm import GaussianHMM
from revision2_external.bb05_bb06_parameters import default_config, require

_HMM_STATES = 2  # structural calm/stressed architecture, not an optimizer choice
_HMM_FEATURES = 2  # (return, rolling volatility)

_STATE_VERSION = 1
_STATE_FIELDS = {"version", "config_hash", "universe", "symbols"}
_SYMBOL_FIELDS = {"history", "bars_since_refit", "fit"}
_HISTORY_FIELDS = {"maxlen", "values"}
_FIT_FIELDS = {"model", "stressed_state", "posterior", "observation"}
_MODEL_FIELDS = {"n_states", "n_iter", "tol", "random_state", "min_state_occupancy_fraction",
                 "variance_floor", "initial_variance_regularizer", "means", "vars", "transmat",
                 "startprob", "state_occupancy", "valid_state_mask", "monitor"}
_PROBABILITY_TOL = 1e-9
_OCCUPANCY_TOL = float(np.sqrt(np.finfo(float).eps))  # numerical serialization guard, not a tuning value


def _state_real(value: Any, what: str) -> float:
    """Finite real number; bool/str/None/NaN/inf are never accepted as numeric ID state."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid ID state: {what} is not a number")
    try:
        number = float(value)  # an oversized JSON int raises OverflowError here
        finite = math.isfinite(number)
    except (OverflowError, TypeError, ValueError):
        raise ValueError(f"Invalid ID state: {what} is not finite") from None
    if not finite:
        raise ValueError(f"Invalid ID state: {what} is not finite")
    return number


def _state_int(value: Any, what: str, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"Invalid ID state: {what}")
    return value


def _state_dict(value: Any, keys: set, what: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"Invalid ID state: {what} schema")
    return value


def _state_array(value: Any, shape: Tuple[int, ...], what: str) -> np.ndarray:
    if not isinstance(value, list) or len(value) != shape[0]:
        raise ValueError(f"Invalid ID state: {what} shape")
    if len(shape) == 1:
        return np.array([_state_real(v, what) for v in value], dtype=float)
    return np.array([_state_array(row, shape[1:], what) for row in value], dtype=float)


def _state_universe(universe: Any) -> List[str]:
    if isinstance(universe, (str, bytes)) or not hasattr(universe, "__iter__"):
        raise ValueError("Invalid ID state: universe must be a collection of symbols")
    symbols = list(universe)
    if any(type(s) is not str or not s for s in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError("Invalid ID state: universe symbols must be unique non-empty text")
    return sorted(symbols)


def _stressed_state_from_model(model: GaussianHMM, variance_ratio: float) -> Optional[int]:
    """Same stressed/calm labelling as HMMIntelligentDiscriminationBox._refit, on saved arrays."""
    if model.valid_state_mask_ is None or model.valid_state_mask_.sum() != _HMM_STATES:
        return None
    variances = model.vars_.sum(axis=1)
    stressed, calm = int(np.argmax(variances)), int(np.argmin(variances))
    return stressed if variances[calm] > 0 and variances[stressed] / variances[calm] >= variance_ratio else None


def _expected_observation(model: GaussianHMM, stressed_state: Optional[int], posterior: np.ndarray) -> Dict[str, Any]:
    """Same record as _current_regime writes after the filter step."""
    if stressed_state is None:
        return {"available": False, "reason": "NO_DISTINCT_STRESSED_STATE",
                "stress_probability": None, "stressed_state": None}
    state = int(np.argmax(posterior))
    return {
        "available": True, "reason": "OK", "state": state,
        "stressed_state": stressed_state, "stress_probability": float(posterior[stressed_state]),
        "posterior": [float(value) for value in posterior],
        "regime": "stressed" if state == stressed_state else "calm",
        "state_variances": [float(value) for value in model.vars_.sum(axis=1)],
        "state_occupancy": [float(value) for value in model.state_occupancy_],
    }


def _check_distribution(values: np.ndarray, what: str, tol: float) -> None:
    if (values < -tol).any() or (values > 1 + tol).any() or abs(float(values.sum()) - 1.0) > tol:
        raise ValueError(f"Invalid ID state: {what} is not a normalized distribution")


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

    # ---- exact state continuity (id_v1) --------------------------------------------------------
    # Persisted per symbol: close history, refit counter, and (once evaluated) the cached fitted model
    # arrays/hyperparameters, stressed-state label, causal filter posterior and latest regime
    # observation.  Nothing is refit on restore: the saved model IS the cached model.  Config is bound
    # by config_hash and every model hyperparameter/derived label is re-validated against it, never
    # reapplied.  The saved universe must equal the live universe exactly (no missing/new symbol).

    @staticmethod
    def _config_bounds(config: EffectiveConfig) -> Dict[str, Any]:
        def positive_int(name: str) -> int:
            value = require(config, name)
            if int(value) != value or int(value) < 1:
                raise ValueError(f"Invalid ID state: configured {name}")
            return int(value)

        bounds: Dict[str, Any] = {name: positive_int(name) for name in
                                  ("id_feature_window", "id_refit_every_bars", "id_min_history_bars",
                                   "id_hmm_iterations")}
        for name in ("id_hmm_tolerance", "id_min_state_occupancy", "id_variance_floor",
                     "id_initial_variance_regularizer", "id_variance_ratio"):
            bounds[name] = _state_real(require(config, name), f"configured {name}")
        return bounds

    @staticmethod
    def _export_model(model: GaussianHMM) -> Dict[str, Any]:
        arrays = (model.means_, model.vars_, model.transmat_, model.startprob_,
                  model.state_occupancy_, model.valid_state_mask_)
        if any(a is None for a in arrays):
            raise ValueError("Invalid ID state: live model is not fitted")
        return dict(
            n_states=model.n_states, n_iter=model.n_iter, tol=model.tol, random_state=model.random_state,
            min_state_occupancy_fraction=model.min_state_occupancy_fraction,
            variance_floor=model.variance_floor,
            initial_variance_regularizer=model.initial_variance_regularizer,
            means=np.asarray(model.means_, dtype=float).tolist(), vars=np.asarray(model.vars_, dtype=float).tolist(),
            transmat=np.asarray(model.transmat_, dtype=float).tolist(),
            startprob=np.asarray(model.startprob_, dtype=float).tolist(),
            state_occupancy=np.asarray(model.state_occupancy_, dtype=float).tolist(),
            valid_state_mask=[bool(v) for v in model.valid_state_mask_],
            monitor=[float(v) for v in model.monitor_])

    def export_state(self, config: EffectiveConfig, universe: Any) -> Dict[str, Any]:
        """JSON-safe ``id_v1`` payload; validated here so an invalid state never reaches disk."""
        if self.config.config_hash != config.config_hash:
            raise ValueError("ID live state was configured under a different config")
        symbols_in_universe = _state_universe(universe)
        fit_keys = set(self._cached_model)
        if (set(self._bar_history) != set(symbols_in_universe)
                or not set(self._bars_since_refit).issubset(self._bar_history)
                or not (fit_keys == set(self._cached_stressed_state) == set(self._cached_posterior)
                        == set(self._last_regime_observation))
                or not fit_keys.issubset(self._bar_history)):
            raise ValueError("Invalid ID state: live maps are inconsistent with the universe")
        symbols: Dict[str, Any] = {}
        for symbol in symbols_in_universe:
            history = self._bar_history[symbol]
            fit = None
            if symbol in fit_keys:
                stressed = self._cached_stressed_state[symbol]
                fit = dict(
                    model=self._export_model(self._cached_model[symbol]),
                    stressed_state=None if stressed is None else int(stressed),
                    posterior=[float(v) for v in self._cached_posterior[symbol]],
                    observation=json.loads(json.dumps(self._last_regime_observation[symbol], allow_nan=False)))
            refit = self._bars_since_refit.get(symbol)
            symbols[symbol] = dict(
                history=dict(maxlen=history.maxlen, values=[_state_real(v, "close") for v in history]),
                bars_since_refit=None if refit is None else int(refit), fit=fit)
        payload = dict(version=_STATE_VERSION, config_hash=config.config_hash,
                       universe=symbols_in_universe, symbols=symbols)
        self._stage_state(payload, config, symbols_in_universe)
        return payload

    def _stage_model(self, raw: Any, symbol: str, bounds: Dict[str, Any]) -> GaussianHMM:
        raw = _state_dict(raw, _MODEL_FIELDS, "model")
        if _state_int(raw["n_states"], "n_states", 1) != self.hmm_states:
            raise ValueError("Invalid ID state: model state count")
        if _state_int(raw["n_iter"], "n_iter", 1) != bounds["id_hmm_iterations"]:
            raise ValueError("ID model n_iter differs from config")
        if _state_int(raw["random_state"], "random_state", 0) != zlib.crc32(symbol.encode("utf-8")) % (2 ** 31):
            raise ValueError("ID model random_state is not this symbol's seed")
        for field, name in (("tol", "id_hmm_tolerance"), ("min_state_occupancy_fraction", "id_min_state_occupancy"),
                            ("variance_floor", "id_variance_floor"),
                            ("initial_variance_regularizer", "id_initial_variance_regularizer")):
            if _state_real(raw[field], field) != bounds[name]:
                raise ValueError(f"ID model {field} differs from config")
        k = self.hmm_states
        means = _state_array(raw["means"], (k, _HMM_FEATURES), "means")
        variances = _state_array(raw["vars"], (k, _HMM_FEATURES), "vars")
        transmat = _state_array(raw["transmat"], (k, k), "transmat")
        startprob = _state_array(raw["startprob"], (k,), "startprob")
        occupancy = _state_array(raw["state_occupancy"], (k,), "state_occupancy")
        floor = bounds["id_variance_floor"]
        if (variances <= 0).any() or (variances < floor).any():
            raise ValueError("Invalid ID state: variances must be positive and at least the variance floor")
        for row in transmat:
            _check_distribution(row, "transmat row", _PROBABILITY_TOL)
        _check_distribution(startprob, "startprob", _PROBABILITY_TOL)
        _check_distribution(occupancy, "state_occupancy", _OCCUPANCY_TOL)
        mask = raw["valid_state_mask"]
        if not isinstance(mask, list) or len(mask) != k or any(type(v) is not bool for v in mask):
            raise ValueError("Invalid ID state: valid_state_mask")
        if mask != [bool(v) for v in (occupancy >= bounds["id_min_state_occupancy"])]:
            raise ValueError("Invalid ID state: valid_state_mask disagrees with occupancy")
        monitor = raw["monitor"]
        if not isinstance(monitor, list) or not 1 <= len(monitor) <= bounds["id_hmm_iterations"]:
            raise ValueError("Invalid ID state: monitor length")
        monitor = [_state_real(v, "monitor") for v in monitor]

        model = GaussianHMM(n_states=k, n_iter=bounds["id_hmm_iterations"],
                            random_state=raw["random_state"], tol=raw["tol"],
                            min_state_occupancy_fraction=raw["min_state_occupancy_fraction"],
                            variance_floor=raw["variance_floor"],
                            initial_variance_regularizer=raw["initial_variance_regularizer"])
        model.means_, model.vars_, model.transmat_, model.startprob_ = means, variances, transmat, startprob
        model.state_occupancy_ = occupancy
        model.valid_state_mask_ = np.array(mask, dtype=bool)
        model.monitor_ = monitor
        return model

    def _stage_state(self, payload: Any, config: EffectiveConfig, universe: Any):
        """Strictly validate and rebuild into NEW objects; touches no live attribute."""
        payload = _state_dict(payload, _STATE_FIELDS, "payload")
        if type(payload["version"]) is not int or payload["version"] != _STATE_VERSION:
            raise ValueError("Unsupported ID state version")
        if type(payload["config_hash"]) is not str or payload["config_hash"] != config.config_hash:
            raise ValueError("ID state was saved under a different config")
        expected_universe = _state_universe(universe)
        if payload["universe"] != expected_universe:
            raise ValueError("ID state universe differs from the live universe")
        if not isinstance(payload["symbols"], dict) or set(payload["symbols"]) != set(expected_universe):
            raise ValueError("ID state symbols differ from the live universe")
        bounds = self._config_bounds(config)
        window = bounds["id_feature_window"]

        histories: Dict[str, deque] = {}
        refits: Dict[str, int] = {}
        models: Dict[str, GaussianHMM] = {}
        stressed_map: Dict[str, Optional[int]] = {}
        posteriors: Dict[str, np.ndarray] = {}
        observations: Dict[str, Dict[str, Any]] = {}
        for symbol in expected_universe:
            entry = _state_dict(payload["symbols"][symbol], _SYMBOL_FIELDS, "symbol entry")
            history = _state_dict(entry["history"], _HISTORY_FIELDS, "history")
            if type(history["maxlen"]) is not int or history["maxlen"] != window:
                raise ValueError("Invalid ID state: history maxlen differs from config")
            if not isinstance(history["values"], list) or len(history["values"]) > window:
                raise ValueError("Invalid ID state: history length")
            closes = [_state_real(v, "close") for v in history["values"]]
            if any(v <= 0 for v in closes):
                raise ValueError("Invalid ID state: closes must be positive")
            histories[symbol] = deque(closes, maxlen=window)

            if entry["bars_since_refit"] is not None:
                refit = _state_int(entry["bars_since_refit"], "bars_since_refit", 0)
                if refit > bounds["id_refit_every_bars"]:
                    raise ValueError("Invalid ID state: bars_since_refit exceeds the refit cadence")
                refits[symbol] = refit

            if entry["fit"] is None:
                continue
            fit = _state_dict(entry["fit"], _FIT_FIELDS, "fit")
            if symbol not in refits or len(closes) < bounds["id_min_history_bars"]:
                raise ValueError("Invalid ID state: fitted model without a matching refit counter/history")
            model = self._stage_model(fit["model"], symbol, bounds)
            posterior = _state_array(fit["posterior"], (self.hmm_states,), "posterior")
            _check_distribution(posterior, "posterior", _PROBABILITY_TOL)
            saved_stressed = fit["stressed_state"]
            if saved_stressed is not None and (type(saved_stressed) is not int
                                               or not 0 <= saved_stressed < self.hmm_states):
                raise ValueError("Invalid ID state: stressed_state")
            if saved_stressed != _stressed_state_from_model(model, bounds["id_variance_ratio"]):
                raise ValueError("Invalid ID state: stressed_state disagrees with the saved model")
            expected = _expected_observation(model, saved_stressed, posterior)
            if (not isinstance(fit["observation"], dict)
                    or json.dumps(fit["observation"], sort_keys=True) != json.dumps(expected, sort_keys=True)):
                raise ValueError("Invalid ID state: observation disagrees with the saved model/posterior")
            models[symbol], stressed_map[symbol] = model, saved_stressed
            posteriors[symbol], observations[symbol] = posterior, expected
        return histories, refits, models, stressed_map, posteriors, observations

    def restore_state(self, payload: Any, config: EffectiveConfig, universe: Any) -> None:
        """Install a saved ``id_v1`` payload into this EMPTY box; all-or-nothing."""
        if (self._bar_history or self._cached_model or self._cached_stressed_state
                or self._bars_since_refit or self._cached_posterior or self._last_regime_observation):
            raise ValueError("ID state can only be restored into an empty box")
        histories, refits, models, stressed, posteriors, observations = self._stage_state(payload, config, universe)
        self.config = config
        self._bar_history, self._bars_since_refit = histories, refits
        self._cached_model, self._cached_stressed_state = models, stressed
        self._cached_posterior, self._last_regime_observation = posteriors, observations

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
