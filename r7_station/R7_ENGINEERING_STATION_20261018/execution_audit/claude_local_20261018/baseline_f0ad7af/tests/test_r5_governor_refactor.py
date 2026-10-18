"""Opt-in experiment invariants; no Stage-A registry changes."""
from dataclasses import replace
from types import SimpleNamespace
import math
import pytest

from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor
from revision5.topology import GTG1_HEAVY_INDUSTRY
from revision5.governor_position_policy import GovernorPositionPolicy, absolute_conviction, update_conviction
from revision2_external.closed_loop_control import TradeReferencePath


def governor(policy=None):
    g = BayTurbineClosedLoopGovernor(BAY_GOVERNOR_SPECS[GTG1_HEAVY_INDUSTRY])
    g.position_policy = policy or GovernorPositionPolicy()
    return g


def step(g, **kwargs):
    args = dict(position_id='a', measured_r=0.1, reference_r=0.5, max_favorable_r=0.4,
                elapsed_bars=1, min_hold_bars=0, max_hold_bars=30,
                hard_stop_r=-1., trade_target_r=2., path_noise_r=.2, path_error_sigma=2.)
    args.update(kwargs)
    return g.evaluate_position_control(**args)


def test_no_preprofit_tightening_and_continuous_pid_actuation():
    g = governor()
    for i in range(5):
        out = step(g, max_favorable_r=.299, elapsed_bars=i, reference_r=2.)
        assert out['protected_r_floor'] == -1.
        assert out['reason'] == 'GOVERNOR_TRACKING'  # no binary path-error exit
    low = step(governor(), reference_r=.1)
    high = step(governor(), reference_r=.8)
    assert high['effective_gap_r'] < low['effective_gap_r']
    assert high['protected_r_floor'] > low['protected_r_floor']
    assert high['pid_incremental_floor_r'] > 0


def test_gap_bounds_latch_one_way_and_state_reset():
    g = governor()
    out = step(g, max_favorable_r=.3, reference_r=100.)
    assert out['trailing_active']
    assert out['effective_gap_r'] == pytest.approx(.25)
    floor = out['protected_r_floor']
    out = step(g, reference_r=-100., max_favorable_r=.3)
    assert out['effective_gap_r'] == .45
    assert out['protected_r_floor'] == floor
    assert step(g, position_id='b', max_favorable_r=0)['protected_r_floor'] == -1.
    g.confirm_position_closed('a')
    assert step(g, max_favorable_r=0)['protected_r_floor'] == -1.


def test_conviction_baseline_same_signal_masks_and_persistence():
    policy = GovernorPositionPolicy()
    signal = SimpleNamespace(confidence=.99, exit_confidence=.5, direction=1)
    studies = dict(confidence=.6, direction=1)
    baseline = absolute_conviction('BUY', signal, studies)
    assert baseline['conviction'] == .5
    trade = {'governor_entry_conviction': baseline}
    studies['direction'] = -1
    current = absolute_conviction('BUY', signal, studies)
    assert current['conviction'] == 0
    assert not update_conviction(trade,current,'1',policy)['fuel_cut_confirmed']
    assert not update_conviction(trade,current,'1',policy)['fuel_cut_confirmed']
    assert update_conviction(trade,current,'2',policy)['fuel_cut_confirmed']
    assert update_conviction(trade,baseline,'3',policy)['consecutive_bars'] == 0
    assert not update_conviction(trade,current,'4',policy)['fuel_cut_confirmed']
    with pytest.raises(ValueError):
        absolute_conviction('BUY', signal, dict(confidence=math.nan, direction=-1))


@pytest.mark.parametrize('gamma', [.5,1.,2.])
def test_gamma_once(gamma):
    p = TradeReferencePath('X','BUY',100,2,1.5,20,gamma,7)
    for held in (0,1,10,20,25):
        raw = (1-math.exp(-min(held,20)/7))/(1-math.exp(-20/7))
        assert p.progress(held) == pytest.approx(raw**gamma)
        assert p.expected_r(held) == pytest.approx(1.5*raw**gamma)
        assert p.lower_bound_r(held) == pytest.approx(-1+2.5*raw**gamma)


@pytest.mark.parametrize('kwargs', [dict(conviction_persistence_bars=1), dict(pid_alpha_r=-1),
                                     dict(pid_u_max=math.nan), dict(minimum_gap_r=.6)])
def test_policy_validation(kwargs):
    with pytest.raises(ValueError):
        GovernorPositionPolicy(**kwargs)


def test_fuel_cut_persistence_does_not_delay_other_protection():
    from tests.test_r5_governor_authority import _cfg, _position
    cfg = _cfg()
    assert _position(cfg, governor(), conviction=0., fuel_cut_confirmed=False)['action'] == 'HOLD'
    assert _position(cfg, governor(), conviction=0., fuel_cut_confirmed=True)['reason'] == 'FSRN_SUSTAINED_DETERIORATION'
    assert _position(cfg, governor(), conviction=0., fuel_cut_confirmed=False,
                     drawdown=cfg.fsrt_drawdown_span / cfg.fsrt_slope)['reason'] == 'FSR_BELOW_EXIT:FSRT'
    assert _position(cfg, governor(), conviction=math.nan, fuel_cut_confirmed=False)['reason'].startswith('INVALID_GOVERNOR_INPUT')


def test_experimental_trace_is_passthrough(monkeypatch):
    import tests.test_r5_governor_trace as fixture
    Original = fixture.Engine
    class ExperimentalEngine(Original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.governor_position_policy = GovernorPositionPolicy()
            for g in self._bay_governors.values():
                g.position_policy = self.governor_position_policy
    monkeypatch.setattr(fixture, 'Engine', ExperimentalEngine)
    tracer, traced = fixture._replay(monkeypatch, True)
    _, plain = fixture._replay(monkeypatch, False)
    assert fixture._fingerprint(traced) == fixture._fingerprint(plain)
    assert traced['governor_authority'] == plain['governor_authority']
    rows = tracer.rows['inner']
    assert rows and all('effective_gap_r' in r for r in rows)
    positions = [e for e in tracer.rows['events'] if e['event_type'] == 'GOVERNOR_POSITION_DECISION']
    assert positions and all(e['conviction_detail'] is not None for e in positions)
    assert not any(e['reason'].startswith('INVALID') for e in positions)
    stops = [e for e in tracer.rows['events'] if e['event_type'] == 'GOVERNOR_STOP_UPDATE']
    assert stops and all(e['effective_from'] == 'NEXT_BAR' for e in stops)


@pytest.mark.parametrize('side', ['BUY','SELL'])
def test_actual_stop_price_anchor_and_next_bar_activation(side):
    # Exercise the orchestrator's real stop application with a minimal controlled bar.
    from tests.test_r5_governor_authority import _engine
    from revision5.governor_authority import BarTelemetry
    orch, ts = _engine('full')
    orch.governor_position_policy = GovernorPositionPolicy()
    _, g = orch._governor_for('INFY')
    g.position_policy = orch.governor_position_policy
    sign = 1 if side == 'BUY' else -1
    signal = SimpleNamespace(exit_confidence=.8, direction=sign)
    studies = {'confidence': .8, 'direction': sign}
    trade = dict(side=side, entry_price=100., stop_price=100.-2*sign,
                 target_price=100.+4*sign, minimum_hold_bars=0, maximum_hold_bars=30,
                 trade_id='fixture', governor_entry_conviction=absolute_conviction(side,signal,studies))
    orch._governor_telemetry['INFY'] = BarTelemetry(True,'fixture',atr=.2,velocity=0.)
    orch._governor_session_bar = lambda *args: 100
    bar = {'open':100.,'high':100.8 if sign>0 else 100.,
           'low':100. if sign>0 else 99.2,'close':100.+.2*sign}
    result = orch._governor_position_step('INFY',ts,trade,bar,signal,1,studies,.8,
                                          SimpleNamespace(expected_r=.5))
    assert result['action'] == 'HOLD'
    expected = 100.+sign*2*result['protected_r_floor']
    assert trade['governor_stop_price'] == pytest.approx(expected)
    events = [e for e in orch.controller_telemetry if e['event_type']=='GOVERNOR_STOP_UPDATE']
    assert events[-1]['effective_from'] == 'NEXT_BAR'
