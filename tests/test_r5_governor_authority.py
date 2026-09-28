"""Governor authority: per-position inner loop, side symmetry, market telemetry, Mark V gate,
MiCOM protection and the orchestrator's governor decision path."""
import math

import pandas as pd
import pytest

from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor
from revision5.topology import BAY_IDS


def _governor():
    return BayTurbineClosedLoopGovernor(BAY_GOVERNOR_SPECS[BAY_IDS[0]])


def _control(gov, position_id, measured, mfe, bars=3):
    return gov.evaluate_position_control(
        measured_r=measured, reference_r=0.2, max_favorable_r=mfe, elapsed_bars=bars,
        min_hold_bars=1, max_hold_bars=60, hard_stop_r=-1.0, trade_target_r=3.0,
        position_id=position_id)


# ------------------------------------------------------------- per-position inner loop

def test_each_open_position_has_its_own_inner_loop_and_ratchet():
    gov = _governor()
    for _ in range(40):
        _control(gov, "INFY", measured=1.5, mfe=2.0)       # winning position ratchets up
    _control(gov, "TCS", measured=-0.1, mfe=0.0)           # fresh position in the same bay
    assert gov.inner_state("INFY").protected_r_floor > 0.5
    assert gov.inner_state("TCS").protected_r_floor < -0.9      # its own first step only
    assert gov.inner_state("TCS").integral_error != gov.inner_state("INFY").integral_error
    assert set(gov.open_position_ids) == {"INFY", "TCS"}


def test_closing_one_position_leaves_the_others_untouched():
    gov = _governor()
    _control(gov, "INFY", measured=0.5, mfe=0.5)
    _control(gov, "TCS", measured=0.5, mfe=0.5)
    before = gov.inner_state("TCS")
    gov.confirm_position_closed("INFY")
    assert gov.open_position_ids == ("TCS",)
    assert gov.inner_state("TCS") == before
    assert gov.inner_state("INFY").active is False


def test_a_new_position_never_inherits_a_closed_positions_ratchet():
    gov = _governor()
    for _ in range(40):
        _control(gov, "INFY", measured=1.5, mfe=2.0)
    gov.confirm_position_closed("INFY")
    result = _control(gov, "INFY", measured=0.1, mfe=0.1)
    assert result["protected_r_floor"] < 0.0 and result["action"] == "HOLD"


def test_legacy_single_position_api_is_unchanged():
    gov = _governor()
    gov.begin_position(hard_stop_r=-1.0)
    assert gov.position_active is True and gov.protected_r_floor == -1.0
    gov.confirm_position_closed()
    assert gov.position_active is False


# ------------------------------------------------------------------ side symmetry

def test_entry_is_symmetric_for_buy_and_sell():
    gov = _governor()
    base = gov.dynamic_z(0.0)
    assert gov.evaluate_entry_request(z_score=base - 0.5, side="BUY")["action"] == "ENTRY"
    assert gov.evaluate_entry_request(z_score=base - 0.5, side="SELL")["action"] == "NO_ACTION"
    assert gov.evaluate_entry_request(z_score=-(base - 0.5), side="SELL")["action"] == "ENTRY"
    assert gov.evaluate_entry_request(z_score=-(base - 0.5), side="BUY")["action"] == "NO_ACTION"


def test_adverse_grid_tightens_both_sides_symmetrically():
    gov = _governor()
    falling = gov.evaluate_entry_request(z_score=-5.0, grid_return_fraction=-0.2, side="BUY")
    rising = gov.evaluate_entry_request(z_score=+5.0, grid_return_fraction=+0.2, side="SELL")
    assert falling["dynamic_z"] == pytest.approx(rising["dynamic_z"])
    assert falling["dynamic_z"] < gov.dynamic_z(0.0)


def test_entry_rejects_invalid_side_and_inputs():
    gov = _governor()
    with pytest.raises(ValueError):
        gov.evaluate_entry_request(z_score=-3.0, side="LONG")
    with pytest.raises(ValueError):
        gov.evaluate_entry_request(z_score=float("nan"))
    with pytest.raises(ValueError):
        gov.evaluate_entry_request(z_score=-3.0, grid_return_fraction=math.inf)


# ------------------------------------------------------------ telemetry and Mark V gate

from revision2_external.bb05_bb06_parameters import default_config
from revision5.governor_authority import (
    LIMITER_NAMES, PARAMETER_NAMES, BarTelemetry, GovernorAuthorityConfig, GovernorInputError,
    bar_telemetry, entry_decision, exhaust_spread, limiters, minimum_value_gate,
    position_decision, session_bar_index, side_aligned_conviction)


def _cfg():
    return GovernorAuthorityConfig.from_config(default_config())


def _bars(closes, *, wick=0.0, start="2024-03-01 09:15"):
    times = pd.date_range(start, periods=len(closes), freq="1min", tz="Asia/Kolkata")
    closes = [float(c) for c in closes]
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame({
        "timestamp": times, "open": opens, "close": closes,
        "high": [max(o, c) + wick for o, c in zip(opens, closes)],
        "low": [min(o, c) - wick for o, c in zip(opens, closes)],
    })


def _telemetry(**overrides):
    base = dict(available=True, reason="TELEMETRY_AVAILABLE", z_score=-5.0, atr=1.0,
                atr_fraction=0.01, vibration=0.0, velocity=0.5, one_bar_return=0.0)
    base.update(overrides)
    return BarTelemetry(**base)


def test_every_governor_authority_value_is_registry_owned():
    cfg = _cfg()
    registry = default_config()
    for name in PARAMETER_NAMES:
        registry.require(name)
    assert cfg.fsr_min_floor < cfg.fsr_exit_threshold < cfg.fsr_entry_threshold <= 1.0


def test_config_rejects_inverted_mark_v_thresholds():
    cfg = _cfg()
    with pytest.raises(ValueError):
        GovernorAuthorityConfig(**{**cfg.__dict__, "fsr_exit_threshold": cfg.fsr_entry_threshold}).validate()
    with pytest.raises(ValueError):
        GovernorAuthorityConfig(**{**cfg.__dict__, "exhaust_spread_trip": cfg.exhaust_spread_hold}).validate()


def test_telemetry_is_causal_and_measures_the_last_completed_bar():
    cfg = _cfg()
    closes = [100 + (i % 5) * 0.2 for i in range(60)]
    bars = _bars(closes, wick=0.3)
    prefix = bar_telemetry(bars.iloc[:40], cfg)
    appended = bars.iloc[:40].copy()
    later = bars.iloc[40:].copy()
    later["close"] = later["close"] * 3.0          # a wild future must not change the past value
    assert bar_telemetry(pd.concat([appended, later]).iloc[:40], cfg) == prefix
    assert prefix.available and prefix.vibration > 0.0 and prefix.velocity >= 0.0
    assert prefix.atr_fraction == pytest.approx(prefix.atr / closes[39])


def test_telemetry_fails_closed_on_warmup_flat_and_invalid_bars():
    cfg = _cfg()
    assert bar_telemetry(_bars([100.0] * 5), cfg).reason == "TELEMETRY_WARMUP_INSUFFICIENT"
    assert bar_telemetry(_bars([100.0] * 60), cfg).reason == "TELEMETRY_ZERO_DISPERSION"
    broken = _bars([100 + (i % 3) for i in range(60)])
    broken.loc[broken.index[-1], "high"] = float("nan")
    assert bar_telemetry(broken, cfg).reason.startswith("TELEMETRY_INVALID_BAR")


def test_vibration_is_wick_range_over_atr_and_zero_for_a_full_body_bar():
    cfg = _cfg()
    trend = [100 + i * 0.5 for i in range(60)]
    assert bar_telemetry(_bars(trend), cfg).vibration == pytest.approx(0.0)
    assert bar_telemetry(_bars(trend, wick=1.0), cfg).vibration > 0.0


def test_exhaust_spread_needs_two_members_and_scales_by_atr():
    a = _telemetry(one_bar_return=0.01, atr_fraction=0.01)
    b = _telemetry(one_bar_return=-0.01, atr_fraction=0.01)
    assert exhaust_spread([a]) is None
    assert exhaust_spread([a, BarTelemetry(False, "X")]) is None
    assert exhaust_spread([a, b]) == pytest.approx(1.0)


def test_session_bar_index_counts_only_the_current_session():
    ts = pd.Series(list(pd.date_range("2024-03-01 15:25", periods=3, freq="1min", tz="Asia/Kolkata"))
                   + list(pd.date_range("2024-03-04 09:15", periods=4, freq="1min", tz="Asia/Kolkata")))
    assert session_bar_index(ts) == 3
    assert session_bar_index(ts.iloc[:4]) == 0


def test_limiters_follow_their_physical_mappings():
    cfg = _cfg()
    calm = limiters(cfg, conviction=0.9, drawdown=0.0, velocity=0.0, session_bar=10_000)
    assert calm["FSRN"] == 0.9 and calm["FSRT"] == 1.0 and calm["FSRS"] == 1.0
    assert calm["FSRA"] == min(1.0, cfg.fsra_base)
    hot = limiters(cfg, conviction=0.9, drawdown=cfg.fsrt_drawdown_span / cfg.fsrt_slope,
                   velocity=100.0, session_bar=0)
    assert hot["FSRT"] == 0.0 and hot["FSRA"] == 0.0 and hot["FSRS"] == pytest.approx(cfg.fsrs_floor)
    for bad in (dict(conviction=1.5), dict(drawdown=-0.1), dict(velocity=float("nan")), dict(session_bar=-1),
                dict(session_bar=None)):
        kwargs = dict(conviction=0.5, drawdown=0.0, velocity=0.0, session_bar=5)
        kwargs.update(bad)
        with pytest.raises(GovernorInputError):
            limiters(cfg, **kwargs)


def test_minimum_value_gate_selects_the_minimum_and_applies_the_floor():
    values = {name: 1.0 for name in LIMITER_NAMES}
    values["FSRT"] = 0.05
    gate = minimum_value_gate(values, 0.15)
    assert gate["controlling_limiter"] == "FSRT"
    assert gate["fsr_selected"] == 0.05 and gate["fsr_effective"] == 0.15
    with pytest.raises(GovernorInputError):
        minimum_value_gate({**values, "FSRN": float("nan")}, 0.15)
    with pytest.raises(GovernorInputError):
        minimum_value_gate({k: v for k, v in values.items() if k != "FSRM"}, 0.15)


def test_side_aligned_conviction_zeroes_an_opposing_signal():
    assert side_aligned_conviction("BUY", pa_direction=1, pa_confidence=0.8,
                                   studies_direction=None, studies_confidence=0.7) == 0.7
    assert side_aligned_conviction("BUY", pa_direction=-1, pa_confidence=0.8,
                                   studies_direction=1, studies_confidence=0.7) == 0.0
    assert side_aligned_conviction("SELL", pa_direction=-1, pa_confidence=0.8,
                                   studies_direction=1, studies_confidence=0.7) == 0.0


def test_entry_decision_admits_and_only_derates_size():
    cfg = _cfg()
    result = entry_decision(_governor(), cfg, side="BUY", telemetry=_telemetry(z_score=-10.0),
                            conviction=0.9, drawdown=0.0, session_bar=10_000)
    assert result["action"] == "ENTRY"
    assert 0.0 < result["size_multiplier"] <= 1.0
    assert result["size_multiplier"] == result["fsr_selected"]


def test_entry_decision_fails_closed():
    cfg = _cfg()
    gov = _governor()
    common = dict(side="BUY", conviction=0.9, drawdown=0.0, session_bar=10_000)
    assert entry_decision(gov, cfg, telemetry=BarTelemetry(False, "W"), **common)["action"] == "NO_ACTION"
    assert entry_decision(gov, cfg, telemetry=_telemetry(z_score=+10.0), **common)["action"] == "NO_ACTION"
    low = entry_decision(gov, cfg, telemetry=_telemetry(z_score=-10.0), **{**common, "conviction": 0.1})
    assert low["reason"] == "FSR_BELOW_ENTRY_HURDLE:FSRN"
    held = entry_decision(gov, cfg, telemetry=_telemetry(z_score=-10.0),
                          bay_exhaust_spread=cfg.exhaust_spread_hold, **common)
    assert held["reason"] == "EXHAUST_SPREAD_HOLD"
    bad = entry_decision(gov, cfg, telemetry=_telemetry(z_score=-10.0), **{**common, "side": "LONG"})
    assert bad["action"] == "NO_ACTION" and bad["reason"].startswith("INVALID_GOVERNOR_INPUT")


def test_vibration_damper_raises_the_entry_hurdle():
    cfg = _cfg()
    common = dict(side="BUY", conviction=cfg.fsr_entry_threshold + 0.01, drawdown=0.0, session_bar=10_000)
    calm = entry_decision(_governor(), cfg, telemetry=_telemetry(z_score=-10.0, vibration=0.0), **common)
    rough = entry_decision(_governor(), cfg, telemetry=_telemetry(
        z_score=-10.0, vibration=cfg.vibration_damper_start + 5.0), **common)
    assert calm["action"] == "ENTRY"
    assert rough["action"] == "NO_ACTION" and rough["entry_hurdle"] > calm["entry_hurdle"]


def _position(cfg, gov, **overrides):
    kwargs = dict(position_id="INFY", measured_r=0.3, reference_r=0.2, max_favorable_r=0.3,
                  elapsed_bars=3, min_hold_bars=1, max_hold_bars=60, trade_target_r=3.0,
                  conviction=0.9, drawdown=0.0, velocity=0.2, session_bar=10_000, path_noise_r=0.8)
    kwargs.update(overrides)
    return position_decision(gov, cfg, **kwargs)


def test_position_decision_holds_tracks_and_sheds_load():
    cfg = _cfg()
    assert _position(cfg, _governor())["reason"] == "GOVERNOR_TRACKING"
    shed = _position(cfg, _governor(), conviction=(cfg.fsr_exit_threshold + cfg.fsr_entry_threshold) / 2)
    assert shed["action"] == "HOLD" and shed["load_shed"] is True


def test_position_decision_exits_on_trip_low_fsr_invalid_input_and_inner_stop():
    cfg = _cfg()
    assert _position(cfg, _governor(), bay_exhaust_spread=cfg.exhaust_spread_trip)["reason"] == "EXHAUST_SPREAD_TRIP"
    assert _position(cfg, _governor(), conviction=0.0)["reason"] == "FSR_BELOW_EXIT:FSRN"
    assert _position(cfg, _governor(), velocity=None)["reason"].startswith("INVALID_GOVERNOR_INPUT")
    assert _position(cfg, _governor(), measured_r=float("nan"))["action"] == "EXIT"
    assert _position(cfg, _governor(), measured_r=-1.5, max_favorable_r=0.0)["action"] == "EXIT"


# ------------------------------------------------------- engine wiring (real orchestrator)

from revision5.ccpp_protection_cubicles import nifty_intertie_measurements
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
import revision2_external.orchestrator as orchestrator_module


def _engine(authority="full", nifty=None, closed_loop_mode="active_paper"):
    times = pd.date_range("2024-03-01 09:15", periods=100, freq="15min", tz="Asia/Kolkata")
    closes = [21000.0 * (1 + 0.001 * ((i % 7) - 3)) for i in range(100)] if nifty is None else nifty
    provider = SealedGridContextProvider(pd.DataFrame({"timestamp": times, "close": closes}),
                                         pd.DataFrame({"timestamp": times, "close": 15.0}))
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    orch = Engine(["INFY", "TCS"], grid_context_provider=provider, real_plant_dcs=plant,
                  plant_control_mode="PAPER_APPLY", closed_loop_mode=closed_loop_mode,
                  governor_authority=authority)
    ts = times[-1] + pd.Timedelta("1min")
    for _ in range(10):
        orch._plant_control_shadow_step(ts, 1.0)
    return orch, ts


def _pipeline(monkeypatch, authority, entry=None, position=None):
    from tests_external.test_audit_remediation import signal, long_bars
    from revision2.contracts import IDDecision
    orch, ts = _engine(authority, closed_loop_mode="active_paper" if authority == "full" else "shadow")
    monkeypatch.setattr(orch.pa, "evaluate", lambda snapshot, cfg: (signal(), []))
    monkeypatch.setattr(orch.id_box, "evaluate", lambda *a, **kw: (IDDecision(True, "fixture", .8, 2, .6), []))
    monkeypatch.setattr(orch.id_box, "_current_regime", lambda *a: "calm")
    if entry is not None:
        monkeypatch.setattr(orchestrator_module, "governor_entry_decision", entry)
    if position is not None:
        monkeypatch.setattr(orchestrator_module, "governor_position_decision", position)
    frame = long_bars()
    frame["timestamp"] = pd.date_range(ts - pd.Timedelta("30min"), periods=len(frame), freq="min")
    report = orch.run({"INFY": frame}, warmup=30)
    return orch, report


def _events(orch, kind):
    return [e for e in orch.controller_telemetry if e["event_type"] == kind]


def test_full_authority_requires_paper_actuation_and_a_grid():
    with pytest.raises(ValueError):
        Engine(["INFY"], governor_authority="full")                     # shadow closed loop, no grid
    with pytest.raises(ValueError):
        Engine(["INFY"], governor_authority="partial")
    orch, _ = _engine("full")
    assert orch.governor_authority == "full"
    assert {"mv_fsr_entry_threshold", "micom_nifty_vol_z_window"} <= orch.consumed_parameters


def test_full_authority_governor_no_action_blocks_every_entry(monkeypatch):
    no_action = lambda *a, **kw: {"action": "NO_ACTION", "reason": "FIXTURE_NO_ACTION", "size_multiplier": 0.0}
    orch, report = _pipeline(monkeypatch, "full", entry=no_action)
    assert report["orders_submitted"] == 0
    decisions = _events(orch, "GOVERNOR_ENTRY_DECISION")
    assert decisions and all(e["authority"] == "full" for e in decisions)
    assert report["governor_authority"]["entry_decisions"]


def test_advisory_governor_records_but_does_not_block(monkeypatch):
    no_action = lambda *a, **kw: {"action": "NO_ACTION", "reason": "FIXTURE_NO_ACTION", "size_multiplier": 0.0}
    orch, report = _pipeline(monkeypatch, "advisory", entry=no_action)
    assert report["orders_submitted"] > 0
    assert _events(orch, "GOVERNOR_ENTRY_DECISION")


def test_full_authority_size_multiplier_only_derates(monkeypatch):
    seen = []
    original = orchestrator_module.PyPortfolioOptPositionManagerBox.size

    def spy(self, plan, equity, size_mult, *args, **kwargs):
        seen.append(size_mult)
        return original(self, plan, equity, size_mult, *args, **kwargs)
    monkeypatch.setattr(orchestrator_module.PyPortfolioOptPositionManagerBox, "size", spy)

    def admit(fraction):
        return lambda *a, **kw: {"action": "ENTRY", "reason": "FIXTURE", "size_multiplier": fraction}
    _pipeline(monkeypatch, "full", entry=admit(1.0))
    full, seen[:] = list(seen), []
    _pipeline(monkeypatch, "full", entry=admit(0.5))
    assert full and seen
    assert seen[0] == pytest.approx(0.5 * full[0])


def test_full_authority_governor_exit_executes_at_the_next_open(monkeypatch):
    admit = lambda *a, **kw: {"action": "ENTRY", "reason": "FIXTURE", "size_multiplier": 1.0}
    exit_now = lambda *a, **kw: {"action": "EXIT", "reason": "FIXTURE_EXIT", "load_shed": False}
    orch, report = _pipeline(monkeypatch, "full", entry=admit, position=exit_now)
    governed = [t for t in orch.completed_trades if t["reason"] == "governor_exit:FIXTURE_EXIT"]
    assert governed, [t["reason"] for t in orch.completed_trades]
    armed = _events(orch, "GOVERNOR_EXIT_ARMED")
    assert armed and all(pd.Timestamp(t["exit_timestamp"]) > pd.Timestamp(armed[0]["timestamp"])
                         for t in governed[:1])
    for governor in orch._bay_governors.values():
        assert governor.open_position_ids == ()


def test_full_authority_hold_moves_the_protective_stop_one_way(monkeypatch):
    admit = lambda *a, **kw: {"action": "ENTRY", "reason": "FIXTURE", "size_multiplier": 1.0}
    floors = iter([-0.5, -0.8, 0.2] + [0.2] * 1000)
    hold = lambda *a, **kw: {"action": "HOLD", "reason": "FIXTURE_HOLD", "load_shed": False,
                             "protected_r_floor": next(floors)}
    stops = {}
    original = Engine._governor_position_step

    def spy(self, symbol, timestamp, trade, *args):
        result = original(self, symbol, timestamp, trade, *args)
        stops.setdefault(trade["trade_id"], []).append(trade["governor_stop_price"])
        return result
    monkeypatch.setattr(Engine, "_governor_position_step", spy)
    _pipeline(monkeypatch, "full", entry=admit, position=hold)
    first = stops[min(stops)]
    assert len(first) >= 3 and first == sorted(first)          # a BUY stop only ever rises
    assert first[1] == first[0] and first[2] > first[1]        # a lower floor never loosens it


def test_nifty_intertie_measurements():
    flat = [100.0] * 30
    assert nifty_intertie_measurements(flat, 20) == (0.0, 0.0)
    assert nifty_intertie_measurements(flat[:5], 20) is None
    wavy = [100 * (1 + 0.001 * ((i % 5) - 2)) for i in range(30)] + [95.0]
    ret, z = nifty_intertie_measurements(wavy, 20)
    assert ret < -0.04 and z < -3.0
    assert nifty_intertie_measurements(flat + [float("nan")], 20) is None


def test_micom_crash_trips_blocks_admission_and_unwinds_positions():
    from tests_external.test_audit_remediation import open_position
    closes = [21000.0 * (1 + 0.001 * ((i % 7) - 3)) for i in range(99)] + [21000.0 * 0.97]
    orch, ts = _engine("full", nifty=closes)
    assert orch._micom_trip["ansi_code"] == "ANSI 81U"
    assert not orch.real_plant_dcs.electrical_network.grid_connected
    assert orch._paper_plant_entry_limit("INFY", 10, 100, ts) == 0
    trade = open_position(orch)
    bar = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0}
    from tests_external.test_audit_remediation import signal
    orch._maybe_exit("INFY", ts, bar, signal(), 3, False, 0.5, {})
    assert orch.completed_trades[-1]["reason"] == "micom_trip:ANSI 81U"
    assert trade is not orch.open_trades.get("INFY")


def test_micom_recloses_when_healthy_and_daily_latch_resets_next_session():
    orch, ts = _engine("full")
    assert orch._micom_trip is None and orch.real_plant_dcs.electrical_network.grid_connected
    orch._day_start_equity = orch._mark_to_market_equity() * 1.05        # a 5% fleet drawdown today
    orch._plant_control_shadow_step(ts, 1.0)
    assert orch._micom_trip["ansi_code"] == "ANSI 67"
    assert orch.real_plant_dcs.grid_relay.master_breaker_open
    orch._day_start_equity = orch._mark_to_market_equity()
    orch._plant_control_shadow_step(ts, 1.0)
    assert orch._micom_trip["ansi_code"] == "ANSI 67"                    # latched for the session
    orch.real_plant_dcs.begin_bar((ts + pd.Timedelta(days=3)).to_pydatetime(), 0)
    assert not orch.real_plant_dcs.grid_relay.master_breaker_open


# ------------------------------------------- trend candidates: overspeed limit + ranked FSRN

from revision5.governor_authority import causal_percentile_rank


def test_trend_overspeed_admits_momentum_and_blocks_overextension_symmetrically():
    gov = _governor()
    limit = -gov.runtime_base_z                       # ~ +1.9 sigma in the trade direction (no feedback yet)
    for side, sign in (("BUY", 1.0), ("SELL", -1.0)):
        ok = gov.evaluate_entry_request(z_score=sign * (limit - 0.5), side=side, entry_mode="trend_overspeed")
        hot = gov.evaluate_entry_request(z_score=sign * (limit + 0.5), side=side, entry_mode="trend_overspeed")
        assert ok["action"] == "ENTRY"
        assert hot["action"] == "NO_ACTION" and hot["reason"] == "GOVERNOR_OVERSPEED_LIMIT"


def test_trend_overspeed_limit_tightens_under_adverse_grid():
    gov = _governor()
    calm = gov.evaluate_entry_request(z_score=1.0, side="BUY", entry_mode="trend_overspeed")
    stressed = gov.evaluate_entry_request(z_score=1.0, grid_return_fraction=-0.2, side="BUY",
                                          entry_mode="trend_overspeed")
    assert stressed["signed_z_limit"] < calm["signed_z_limit"]


def test_mean_reversion_remains_the_governor_default_and_unknown_modes_fail():
    gov = _governor()
    assert gov.evaluate_entry_request(z_score=-5.0)["entry_mode"] == "mean_reversion"
    with pytest.raises(ValueError):
        gov.evaluate_entry_request(z_score=0.0, entry_mode="breakout")


def test_causal_percentile_rank():
    assert causal_percentile_rank([0.1, 0.2, 0.3, 0.4], 0.35, 4) == 0.75
    assert causal_percentile_rank([0.2] * 4, 0.2, 4) == 1.0
    assert causal_percentile_rank([0.1, 0.2], 0.3, 4) is None          # warming up
    assert causal_percentile_rank([0.1, float("nan"), 0.2, 0.3], 0.3, 4) is None
    assert causal_percentile_rank([0.1, 0.2, 0.3, 0.4], float("nan"), 4) is None


def _trending_pipeline(monkeypatch):
    """Full-authority replay whose bars have real dispersion and whose grid covers every bar."""
    import numpy as np
    from tests_external.test_audit_remediation import signal
    from revision2.contracts import IDDecision
    grid_times = pd.date_range("2024-02-28 00:00", periods=5 * 96, freq="15min", tz="Asia/Kolkata")
    nifty = [21000.0 * (1 + 0.001 * ((i % 7) - 3)) for i in range(len(grid_times))]
    provider = SealedGridContextProvider(pd.DataFrame({"timestamp": grid_times, "close": nifty}),
                                         pd.DataFrame({"timestamp": grid_times, "close": 15.0}))
    orch = Engine(["INFY"], grid_context_provider=provider,
                  real_plant_dcs=CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:"),
                  plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
                  governor_authority="full")
    monkeypatch.setattr(orch.pa, "evaluate", lambda snapshot, cfg: (signal(), []))
    monkeypatch.setattr(orch.id_box, "evaluate", lambda *a, **kw: (IDDecision(True, "fixture", .8, 2, .6), []))
    monkeypatch.setattr(orch.id_box, "_current_regime", lambda *a: "calm")
    i = np.arange(90)
    close = 1000.0 + 0.05 * i + 0.8 * np.sin(i / 3.0)
    open_ = np.concatenate([[close[0]], close[:-1]])
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2024-03-01 10:00", periods=90, freq="min", tz="Asia/Kolkata"),
        "open": open_, "close": close, "high": np.maximum(open_, close) + 0.3,
        "low": np.minimum(open_, close) - 0.3, "volume": 1000.0})
    return orch, orch.run({"INFY": frame}, warmup=30)


def test_full_authority_with_the_real_governor_decision_admits_momentum_candidates(monkeypatch):
    """Regression: the mean-reversion comparator and raw-confidence FSRN vetoed every momentum
    candidate (0 of 3559 on real data).  The real decision path must be able to admit."""
    orch, report = _trending_pipeline(monkeypatch)
    decisions = report["governor_authority"]["entry_decisions"]
    assert decisions, "no governor entry decisions recorded"
    assert report["fills"] > 0, decisions
    assert not any(k.startswith("NO_ACTION:GOVERNOR_ENTRY_NOT_REACHED") for k in decisions)


# ------------------------------------------------ noise-scaled path-error tolerance

def test_path_error_tolerance_scales_with_bar_noise_and_time():
    """A 0.5R lag after 3 bars is noise when one bar moves ~0.8R; the legacy 0.30R tolerance
    (the outer loop's setpoint) exited on it -- 82 of 146 real trial-0 trades."""
    kwargs = dict(measured_r=-0.3, reference_r=0.2, max_favorable_r=0.0, elapsed_bars=3,
                  min_hold_bars=1, max_hold_bars=60, hard_stop_r=-1.0, trade_target_r=3.0)
    legacy = _governor().evaluate_position_control(**kwargs, position_id="A")
    scaled = _governor().evaluate_position_control(**kwargs, position_id="A",
                                                   path_noise_r=0.8, path_error_sigma=2.0)
    assert legacy["reason"] == "GOVERNOR_PATH_ERROR"
    assert scaled["action"] == "HOLD"
    quiet = _governor().evaluate_position_control(**{**kwargs, "measured_r": -0.9}, position_id="A",
                                                  path_noise_r=0.1, path_error_sigma=2.0)
    assert quiet["reason"] == "GOVERNOR_PATH_ERROR"          # far outside a quiet market's envelope


def test_path_noise_inputs_must_come_together_and_be_positive():
    base = dict(measured_r=0.0, reference_r=0.0, max_favorable_r=0.0, elapsed_bars=1,
                min_hold_bars=1, max_hold_bars=60, hard_stop_r=-1.0, trade_target_r=3.0)
    for bad in (dict(path_noise_r=0.5), dict(path_noise_r=0.0, path_error_sigma=2.0),
                dict(path_noise_r=float("nan"), path_error_sigma=2.0)):
        with pytest.raises(ValueError):
            _governor().evaluate_position_control(**base, **bad)


def test_position_decision_fails_closed_without_path_noise():
    cfg = _cfg()
    assert _position(cfg, _governor(), path_noise_r=None)["reason"].startswith("INVALID_GOVERNOR_INPUT")
