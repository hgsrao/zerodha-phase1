"""The operator's decision rules, checked on every bar of a realistic random stream.

R1  no weights take part in any decision
R2  four independent evidence controllers (governor, AVR, dispatch, studies) each vote
R3  a BUY pulse appears only on a bar where all four vote BULL
R4  a SELL pulse appears only on a bar where all four vote BEAR
R5  otherwise HOLD, with the reason recorded
"""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import random
import pytest

from .r8_state import Bar, Context, ControlConfig
from .r8_coordinated_controller import SymbolController

T0 = datetime(2026, 8, 13, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))


def stream(seed=5, n=1500):
    """Balanced up / flat / down spells with realistic candles, studies and NIFTY."""
    rng = random.Random(seed)
    px, study, nifty = 100.0, 0.0, 0.0
    for i in range(n):
        regime = [1, 0, -1, 0.5, 0, -0.5][(i // 60) % 6] * 0.0015
        o = px
        px = max(1.0, px * (1 + regime + rng.gauss(0, 0.0015)))
        hi = max(o, px) * (1 + abs(rng.gauss(0, 0.0005)))
        lo = min(o, px) * (1 - abs(rng.gauss(0, 0.0005)))
        vol = rng.lognormvariate(0, 0.5) * 1000
        study = 0.8 * study + 0.2 * max(-1, min(1, regime / 0.0015 + rng.gauss(0, 0.4)))
        nifty = 0.9 * nifty + 0.1 * max(-1, min(1, regime / 0.0015 + rng.gauss(0, 0.5)))
        yield (Bar(i, (T0 + timedelta(minutes=i)).isoformat(), o, hi, lo, px, vol),
               max(-1.0, min(1.0, study)), max(-1.0, min(1.0, nifty)))


def run(config, seed=5):
    c, actual, out = SymbolController(config), 0.0, []
    for bar, study, nifty in stream(seed):
        d = c.step(bar, Context(actual, study, nifty))
        out.append(d)
        # Simple position model: enter from flat on a pulse, exit when it turns against us.
        if actual == 0 and d.pulse in ('BUY', 'SELL'):
            actual = 0.5 if d.pulse == 'BUY' else -0.5
        elif (actual > 0 and d.pulse == 'SELL') or (actual < 0 and d.pulse == 'BUY'):
            actual = 0.0
    return out


@pytest.fixture
def config():
    return ControlConfig('INFY', 'GTG2_TECH_TELECOM', 0.02)


def test_r1_no_weights_in_config_or_decision(config):
    assert not {'avr_weight', 'study_weight', 'dispatch_weight', 'strict_four_way_consensus'} & set(asdict(config))


def test_r2_four_sources_vote_and_every_one_can_vote_both_ways(config):
    decisions = run(config)
    assert all(set(d.consensus_votes) == {'governor', 'avr', 'dispatch', 'study'} for d in decisions)
    for source in ('governor', 'avr', 'dispatch', 'study'):
        votes = {d.consensus_votes[source] for d in decisions}
        assert {'BULL', 'BEAR'} <= votes, f'{source} must be able to support and oppose'


def test_r3_r4_pulse_only_on_same_bar_four_way_agreement(config):
    decisions = run(config)
    buys = [d for d in decisions if d.pulse == 'BUY']
    sells = [d for d in decisions if d.pulse == 'SELL']
    assert buys and sells
    assert all(all(v == 'BULL' for v in d.consensus_votes.values()) for d in buys)
    assert all(all(v == 'BEAR' for v in d.consensus_votes.values()) for d in sells)


def test_r5_disagreement_is_hold_with_a_reason(config):
    for d in run(config):
        agree = len(set(d.consensus_votes.values())) == 1 and 'FLAT' not in d.consensus_votes.values()
        if d.eligible and not agree:
            assert d.pulse == 'HOLD' and d.consensus_reason


def test_entry_cooldown_after_release(config):
    # A release starts a cooldown: no BUY -> SELL (or SELL -> BUY) flip without a HOLD bar.
    pulses = [d.pulse for d in run(config)]
    assert not any(a in ('BUY', 'SELL') and b in ('BUY', 'SELL') and a != b for a, b in zip(pulses, pulses[1:]))


def test_vote_thresholds_come_from_config(config):
    strict = replace(config, governor_threshold=0.9, avr_threshold=0.9, dispatch_threshold=0.9, studies_threshold=0.9)
    assert sum(d.pulse != 'HOLD' for d in run(strict)) < sum(d.pulse != 'HOLD' for d in run(config))


def test_checkpoint_mid_stream_reproduces_future_decisions(config):
    items = list(stream(11))
    reference = SymbolController(config)
    expected = [reference.step(b, Context(0.0, s, n)) for b, s, n in items]
    for cut in (97, 401, 777):
        source = SymbolController(config)
        for b, s, n in items[:cut]:
            source.step(b, Context(0.0, s, n))
        restored = SymbolController(config)
        restored.restore(json.loads(json.dumps(source.snapshot())))
        assert restored.pid_gov.stagnant_bars == source.pid_gov.stagnant_bars
        after = [restored.step(b, Context(0.0, s, n)) for b, s, n in items[cut:]]
        assert [(d.pulse, d.consensus_votes) for d in after] == [(d.pulse, d.consensus_votes) for d in expected[cut:]]


def test_rejected_parallel_pid_checkpoint_leaves_state_untouched(config):
    c = SymbolController(config)
    for b, s, n in list(stream(3, 120)):
        c.step(b, Context(0.0, s, n))
    payload = json.loads(json.dumps(c.snapshot()))
    before = json.dumps(c.snapshot(), sort_keys=True)
    payload['parallel_pids']['governor']['integral'] = 9.0          # beyond the integral limit
    with pytest.raises(ValueError):
        c.restore(payload)
    assert json.dumps(c.snapshot(), sort_keys=True) == before


def _bear_bar(i, start=100.0):
    """Falling price, each candle opening high and closing near its low."""
    p = start - 0.08 * i
    return Bar(i, (T0 + timedelta(minutes=i)).isoformat(), p + 0.06, p + 0.08, p - 0.01, p, 1000.0)


def test_flat_four_way_bear_opens_short_only_when_shorts_are_allowed(config):
    for allow, expected in ((True, 'SELL'), (False, 'HOLD')):
        c = SymbolController(replace(config, allow_shorts=allow))
        for i in range(20):
            d = c.step(_bear_bar(i), Context(0.0, -0.8, -0.6))
        assert all(v == 'BEAR' for v in d.consensus_votes.values())
        assert d.pulse == expected
        if not allow:
            assert d.consensus_reason == 'FOUR_WAY_BEAR_CONSENSUS_SHORT_BLOCKED_BY_POLICY'


def test_short_policy_never_blocks_exiting_a_long(config):
    c = SymbolController(replace(config, allow_shorts=False))
    for i in range(20):
        d = c.step(_bear_bar(i), Context(0.5, -0.8, -0.6))
    assert d.pulse == 'SELL' and d.action_intent == 'REDUCE_OR_EXIT_LONG'


def test_engine_short_policy_reaches_the_controllers(config):
    from .test_r8_engine_integration import TestEngine
    assert TestEngine(['INFY'], r8_configs=[config]).r8_symbol_controllers['INFY'].config.allow_shorts is True
    e = TestEngine(['INFY'], r8_configs=[config], r8_allow_native_shorts=False)
    assert e.r8_symbol_controllers['INFY'].config.allow_shorts is False


def _reversal_lag(config, up_bars=60):
    c, px, buy, sell = SymbolController(config), 100.0, None, None
    for i in range(up_bars + 120):
        up = i < up_bars
        o = px
        px *= 1.0015 if up else 0.9985
        d = c.step(Bar(i, (T0 + timedelta(minutes=i)).isoformat(), o, max(o, px) * 1.0003, min(o, px) * 0.9997, px, 1000.0),
                   Context(0.0, 0.8 if up else -0.8, 0.6 if up else -0.6))
        if up and buy is None and d.pulse == 'BUY':
            buy = i + 1
        if not up and sell is None and d.pulse == 'SELL':
            sell = i - up_bars + 1
    return buy, sell


def test_entry_and_exit_response_are_symmetric(config):
    # With the evidence integral limited to 0.25 a reversal is answered as fast as a trend start.
    buy, sell = _reversal_lag(config)
    assert buy is not None and sell is not None and abs(sell - buy) <= 3
    slow_buy, slow_sell = _reversal_lag(replace(config, evidence_integral_limit=0.5))
    assert slow_sell > sell + 10            # the old 0.5 limit: exits lag by ~25 bars


def test_governor_stagnation_and_avr_droop_read_their_own_inputs(config):
    c = SymbolController(config)
    for b, s, n in list(stream(9, 300)):
        before = c.pid_gov.stagnant_bars
        c.step(b, Context(0.0, s, n))
        inputs = c.last_consensus['inputs']
        assert c.pid_avr.participation == pytest.approx(inputs['avr'])
        if c.last_result.eligible:
            moved = abs(inputs['governor']) > 0.005
            assert c.pid_gov.stagnant_bars == (0 if moved else min(50, before + 1))
