"""Step-0 HMM observation through the real replay loop, with the real HMM.

Nothing here mocks the regime model: histories are appended, the Gaussian HMM is fitted and the
causal filter is stepped.  Only unrelated admission inputs are controlled (PA always proposes a
BUY, ID always approves after its real regime read, and one symbol's upstream admission and the
engine's new-entry halt are forced for a window) so that warm-up, held positions, upstream
rejection, halted entries and a session boundary all occur in one deterministic replay.  The
fixture is synthetic, so no market data is needed.

Refit convention (unchanged by step 0): with ``id_refit_every_bars`` = 20 the counter is reset to
0 on a fit and incremented on each later eligible observation, so consecutive fits are 21
eligible observations apart.
"""
import numpy as np
import pandas as pd
import pytest

from revision2.contracts import IDDecision
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision2_external.regime_id_box import HMMIntelligentDiscriminationBox
from tests_external.test_audit_remediation import signal

WARMUP = 30
BARS_PER_SESSION = 90
SYMBOLS = ("INFY", "TCS")
REJECT_TCS = (pd.Timestamp("2024-01-01 14:40"), pd.Timestamp("2024-01-01 14:55"))  # inclusive, local time
HALT_AT = pd.Timestamp("2024-01-02 10:00")  # new entries halted from here on


def _frame(seed, shock_after=None):
    """Two sessions of 1-minute bars: calm, then a volatility shock, so the HMM sees two regimes."""
    rng = np.random.default_rng(seed)
    n = 2 * BARS_PER_SESSION
    ts = list(pd.date_range("2024-01-01 14:00", periods=BARS_PER_SESSION, freq="min", tz="Asia/Kolkata")) + \
        list(pd.date_range("2024-01-02 09:15", periods=BARS_PER_SESSION, freq="min", tz="Asia/Kolkata"))
    returns = np.concatenate([rng.normal(0, .0008, 120), rng.normal(0, .006, n - 120)])
    if shock_after is not None:  # rewrite only the future after index shock_after
        returns[shock_after + 1:] = np.random.default_rng(seed + 1000).normal(0, .02, n - shock_after - 1)
    close = 1000. * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"timestamp": ts, "open": open_, "high": np.maximum(open_, close) * 1.0005,
                         "low": np.minimum(open_, close) * .9995, "close": close, "volume": 1000.})


def _state(box, symbol):
    posterior = box._cached_posterior.get(symbol)
    return (tuple(box._bar_history.get(symbol, ())), box._bars_since_refit.get(symbol),
            None if posterior is None else tuple(np.asarray(posterior, dtype=float)),
            box._latest_regime.get(symbol), box._last_observed_bar.get(symbol))


def _local(ts):
    return pd.Timestamp(ts).tz_localize(None) if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts)


def _replay(monkeypatch, frames):
    orch = Engine(list(SYMBOLS))
    box = orch.id_box
    log = {s: [] for s in SYMBOLS}
    refits = {s: [] for s in SYMBOLS}
    context = {"held": 0, "rejected": 0, "halted": 0, "entry_reads": 0, "exit_reads": 0}

    observe = HMMIntelligentDiscriminationBox.observe_bar
    refit = HMMIntelligentDiscriminationBox._refit

    def spy_observe(self, symbol, close, *, timestamp):
        if self is box and _local(timestamp) >= HALT_AT:
            orch._execution_halted = True
        regime = observe(self, symbol, close, timestamp=timestamp)
        if self is box:
            log[symbol].append((pd.Timestamp(timestamp), float(close), _state(self, symbol), regime))
        return regime

    def spy_refit(self, symbol, features):
        if self is box:
            refits[symbol].append(len(log[symbol]))  # index of the observation being processed
        return refit(self, symbol, features)

    monkeypatch.setattr(HMMIntelligentDiscriminationBox, "observe_bar", spy_observe)
    monkeypatch.setattr(HMMIntelligentDiscriminationBox, "_refit", spy_refit)

    monkeypatch.setattr(orch.pa, "evaluate", lambda snapshot, cfg: (signal(symbol=snapshot.symbol), []))
    real_evaluate = box.evaluate

    def entry_read(sig, cfg, latest_close=None):
        before = _state(box, sig.symbol)
        _, trace = real_evaluate(sig, cfg, latest_close=latest_close)
        assert _state(box, sig.symbol) == before, "entry read mutated the HMM"
        context["entry_reads"] += 1
        return IDDecision(True, "fixture", .8, 2., .6), trace

    monkeypatch.setattr(box, "evaluate", entry_read)

    real_exit = orch._maybe_exit

    def exit_read(symbol, *args, **kwargs):
        before = _state(box, symbol)
        context["held"] += int(symbol in orch.open_trades)
        context["halted"] += int(orch._execution_halted)
        result = real_exit(symbol, *args, **kwargs)
        assert _state(box, symbol) == before, "exit path mutated the HMM"
        context["exit_reads"] += 1
        return result

    monkeypatch.setattr(orch, "_maybe_exit", exit_read)

    real_admission = orch.supervisory_bridge.evaluate_upstream_admission

    def admission(*, symbol, runtime_parameters, safety_parameters):
        result = real_admission(symbol=symbol, runtime_parameters=runtime_parameters,
                                safety_parameters=safety_parameters)
        now = log[symbol][-1][0]
        if symbol == "TCS" and REJECT_TCS[0] <= _local(now) <= REJECT_TCS[1]:
            context["rejected"] += 1
            return type("Rejected", (), {"admitted": False,
                                         "consumed_parameters": result.consumed_parameters})()
        return result

    monkeypatch.setattr(orch.supervisory_bridge, "evaluate_upstream_admission", admission)

    report = orch.run(frames, warmup=WARMUP)
    return orch, report, log, refits, context


def _reference(config, symbol, closes, timestamps):
    """An independent box fed the same completed-bar prefixes, one observation at a time.

    The symbol must match: the HMM seeds its fit from the symbol name."""
    ref = HMMIntelligentDiscriminationBox(config=config)
    ref.calibrate(symbol, pd.DataFrame({"close": []}), config)
    states, fits = [], []
    real_refit = ref._refit

    def counting(symbol, features):
        fits.append(len(states))
        return real_refit(symbol, features)

    ref._refit = counting
    for close, ts in zip(closes, timestamps):
        regime = ref.observe_bar(symbol, close, timestamp=str(ts))
        states.append((_state(ref, symbol)[:4], regime))
    return states, fits


@pytest.fixture(scope="module")
def base_run():
    mp = pytest.MonkeyPatch()
    try:
        yield _replay(mp, {s: _frame(i + 1) for i, s in enumerate(SYMBOLS)})
    finally:
        mp.undo()


def test_scenarios_actually_occur(base_run):
    _, report, _, _, context = base_run
    assert report["fills"] > 0 and context["held"] > 0
    assert context["rejected"] > 0 and context["halted"] > 0
    assert context["entry_reads"] > 0 and context["exit_reads"] > 0


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_every_processed_bar_is_observed_exactly_once_in_order(base_run, symbol):
    _, _, log, _, _ = base_run
    frame = _frame(SYMBOLS.index(symbol) + 1)
    observed = [row[0] for row in log[symbol]]
    expected = [pd.Timestamp(t) for t in frame["timestamp"].iloc[:-1]]
    assert observed == expected  # warm-up, both sessions, no duplicate, no omission
    assert len(observed) == len(frame) - 1
    # Warm-up hands over to the main loop without a gap or a repeat.
    assert observed.count(pd.Timestamp(frame["timestamp"].iloc[WARMUP - 1])) == 1
    assert observed.count(pd.Timestamp(frame["timestamp"].iloc[WARMUP])) == 1
    # The final fill/reconciliation sentinel stays outside the replay clock.
    assert pd.Timestamp(frame["timestamp"].iloc[-1]) not in observed
    # The session boundary is crossed without skipping either side.
    assert pd.Timestamp(frame["timestamp"].iloc[BARS_PER_SESSION - 1]) in observed
    assert pd.Timestamp(frame["timestamp"].iloc[BARS_PER_SESSION]) in observed


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_replay_matches_an_independent_box_fed_the_same_prefixes(base_run, symbol):
    orch, _, log, refits, _ = base_run
    frame = _frame(SYMBOLS.index(symbol) + 1)
    closes = list(frame["close"].iloc[:-1])
    states, fits = _reference(orch.config, symbol, closes, frame["timestamp"].iloc[:-1])
    assert len(states) == len(log[symbol])
    for i, ((_, close, state, regime), (ref_state, ref_regime)) in enumerate(zip(log[symbol], states)):
        assert close == closes[i]
        assert state[0] == ref_state[0], f"history differs at observation {i}"
        assert state[1] == ref_state[1], f"refit counter differs at observation {i}"
        if ref_state[2] is None:
            assert state[2] is None
        else:
            np.testing.assert_allclose(state[2], ref_state[2], rtol=0, atol=1e-12)
        assert regime == ref_regime
    assert refits[symbol] == fits
    assert any(row[2][2] is not None for row in log[symbol])  # a posterior really was computed


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_refit_cadence_is_21_eligible_observations(base_run, symbol):
    orch, _, _, refits, _ = base_run
    every = int(orch.config.require("id_refit_every_bars"))
    first = int(orch.config.require("id_min_history_bars")) - 1  # 0-based index of the first eligible bar
    assert refits[symbol][0] == first
    assert len(refits[symbol]) >= 3
    assert all(b - a == every + 1 for a, b in zip(refits[symbol], refits[symbol][1:]))


def test_future_closes_cannot_change_earlier_posteriors(monkeypatch, base_run):
    _, _, base_log, _, _ = base_run
    cut = 120  # main-loop index; closes after it are rewritten
    _, _, log, _, _ = _replay(monkeypatch, {"INFY": _frame(1, shock_after=cut), "TCS": _frame(2)})
    assert log["INFY"][cut + 1][1] != base_log["INFY"][cut + 1][1]  # the future really changed
    for i in range(cut + 1):
        assert log["INFY"][i][2] == base_log["INFY"][i][2], f"observation {i} changed"
        assert log["INFY"][i][3] == base_log["INFY"][i][3]
