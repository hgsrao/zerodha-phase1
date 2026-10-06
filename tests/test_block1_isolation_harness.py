"""Unit tests for the Block 1 isolation harness helpers (no sealed market data is read)."""
import importlib.util
import sys
from pathlib import Path

import pytest

DIAG = Path(__file__).resolve().parents[1] / "scripts" / "diagnostics"
sys.path.insert(0, str(DIAG))
import isolation_lib as lib  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(name, DIAG / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker = _load("block1_arm_worker")


def bar(high, low):
    return {"high": high, "low": low, "open": (high + low) / 2, "close": (high + low) / 2}


# ----------------------------------------------------------------- exit quality

def test_buy_stop_breach_within_horizon_is_defensive_save():
    r = lib.classify_exit("BUY", stop=99.0, target=102.0, forward_bars=[bar(100.5, 99.5), bar(100, 98.9)])
    assert r["exit_class"] == "DEFENSIVE_SAVE" and r["bars_to_event"] == 2


def test_sell_target_reached_is_premature_choke():
    r = lib.classify_exit("SELL", stop=101.0, target=98.0, forward_bars=[bar(100.2, 99.0), bar(99.0, 97.9)])
    assert r["exit_class"] == "PREMATURE_CHOKE" and r["bars_to_event"] == 2


def test_same_bar_touching_both_levels_counts_as_stop_first():
    r = lib.classify_exit("BUY", stop=99.0, target=102.0, forward_bars=[bar(102.5, 98.5)])
    assert r["exit_class"] == "DEFENSIVE_SAVE" and r["same_bar_ambiguous"] is True


def test_full_horizon_inside_band_is_noise_churn_and_short_horizon_is_censored():
    inside = [bar(100.5, 99.5)] * lib.FORWARD_BARS
    assert lib.classify_exit("BUY", 99.0, 102.0, inside)["exit_class"] == "NOISE_CHURN"
    assert lib.classify_exit("BUY", 99.0, 102.0, inside[:7])["exit_class"] == "CENSORED"


def test_events_after_the_horizon_are_ignored():
    bars = [bar(100.5, 99.5)] * lib.FORWARD_BARS + [bar(110, 90)]
    assert lib.classify_exit("BUY", 99.0, 102.0, bars)["exit_class"] == "NOISE_CHURN"


def test_exit_kind_separates_conviction_from_mechanical():
    assert lib.exit_kind("governor_exit:FSR_BELOW_EXIT:FSRN") == "CONVICTION_FSRN"
    assert lib.exit_kind("governor_exit:FSR_BELOW_EXIT:FSRD") == "LIMITER"
    assert lib.exit_kind("force_close_time").startswith("MECHANICAL")


# ----------------------------------------------------------------- friction ledger

def _trade(side="BUY", entry=1000.0, exit_=1010.0, qty=100):
    from revision2.transaction_costs import leg_cost
    exit_side = "SELL" if side == "BUY" else "BUY"
    costs = leg_cost(entry, qty, side) + leg_cost(exit_, qty, exit_side)
    sign = 1 if side == "BUY" else -1
    stop = entry - sign * 10
    return {"trade_id": "t", "symbol": "X", "side": side, "quantity": qty, "entry_price": entry,
            "exit_price": exit_, "planned_entry_price": entry, "planned_stop_price": stop,
            "planned_target_price": entry + sign * 20, "entry_timestamp": "a", "exit_timestamp": "b",
            "costs": costs, "net_pnl": sign * (exit_ - entry) * qty - costs}


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_itemized_fees_reconcile_with_the_engines_booked_cost(side):
    row = lib.trade_ledger_row(_trade(side), 0.0005)
    assert row["fee_reconciliation_error_inr"] == pytest.approx(0.0, abs=1e-9)
    assert row["stt_inr"] > 0
    assert row["total_friction_inr"] == pytest.approx(row["fees_inr"] + row["slippage_inr"])


def test_net_is_frictionless_gross_minus_all_friction_and_stt_is_sell_leg_only():
    row = lib.trade_ledger_row(_trade("BUY"), 0.0005)
    assert row["net_pnl_inr"] == pytest.approx(row["gross_pnl_frictionless_inr"] - row["total_friction_inr"])
    # BUY entry has no STT; only the SELL exit leg carries it.
    assert row["stt_inr"] == pytest.approx(0.00025 * 1010.0 * 100)
    assert row["gross_pnl_frictionless_inr"] > row["gross_pnl_fill_inr"]


def test_slippage_recovers_the_market_price_for_both_sides():
    from revision2.transaction_costs import paper_fill_price
    for side in ("BUY", "SELL"):
        fill = paper_fill_price(1000.0, side, 0.0005)
        assert lib.slippage_per_share(fill, side, 0.0005) == pytest.approx(0.5, rel=1e-3)


# ----------------------------------------------------------------- null baseline

def _session(n=120, drift=0.0):
    bars, price = [], 1000.0
    for i in range(n):
        price += drift + (1.0 if i % 2 else -1.0)
        bars.append({"timestamp": f"2024-02-13 09:{i:03d}", "open": price, "high": price + 1.5,
                     "low": price - 1.5, "close": price})
    return bars


NULL_KW = dict(n_entries=30, seed=7, stop_atr_mult=1.2, target_atr_mult=1.2, max_hold_bars=40,
               risk_budget_inr=1000.0, slippage_fraction=0.0005, entry_window=(15, 100))


def test_null_is_deterministic_for_a_seed_and_differs_across_seeds():
    sess = {"X": [_session(), _session(drift=0.01)]}
    a = lib.random_entry_null(sess, **NULL_KW)
    b = lib.random_entry_null(sess, **NULL_KW)
    c = lib.random_entry_null(sess, **{**NULL_KW, "seed": 8})
    assert [r["net_pnl_inr"] for r in a] == [r["net_pnl_inr"] for r in b]
    assert [r["net_pnl_inr"] for r in a] != [r["net_pnl_inr"] for r in c]


def test_null_on_a_flat_tape_loses_exactly_its_friction():
    flat = [{"timestamp": f"t{i}", "open": 1000.0, "high": 1000.5, "low": 999.5, "close": 1000.0} for i in range(120)]
    rows = lib.random_entry_null({"X": [flat]}, **{**NULL_KW, "n_entries": 5})
    assert rows
    for r in rows:
        assert r["net_pnl_inr"] < 0
        assert r["fee_reconciliation_error_inr"] == pytest.approx(0.0, abs=1e-9)


def test_null_positions_never_overlap_per_symbol():
    rows = lib.random_entry_null({"X": [_session()]}, **{**NULL_KW, "n_entries": 200})
    spans = sorted((int(r["entry_timestamp"][-3:]), int(r["exit_timestamp"][-3:])) for r in rows)
    assert all(a_end < b_start for (_, a_end), (b_start, _) in zip(spans, spans[1:]))


# ----------------------------------------------------------------- arm wiring and zero state

def test_arm_00_and_10_differ_only_by_min_hold_fsrn_deferral():
    from revision2_external.bb05_bb06_parameters import default_config
    from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor
    from revision5.governor_authority import GovernorAuthorityConfig, position_decision
    from revision5.topology import BAY_IDS
    cfg = GovernorAuthorityConfig.from_config(default_config())
    control = worker.baseline_position_decision()

    def decide(fn, **kw):
        gov = BayTurbineClosedLoopGovernor(BAY_GOVERNOR_SPECS[BAY_IDS[0]])
        base = dict(position_id="P", measured_r=0.3, reference_r=0.2, max_favorable_r=0.3, elapsed_bars=2,
                    min_hold_bars=8, max_hold_bars=60, trade_target_r=3.0, conviction=0.0, drawdown=0.0,
                    velocity=0.2, session_bar=10_000, path_noise_r=0.8)
        return fn(gov, cfg, **{**base, **kw})

    assert decide(control, elapsed_bars=2)["reason"] == "FSR_BELOW_EXIT:FSRN"
    assert decide(position_decision, elapsed_bars=2)["action"] == "HOLD"
    for kw in ({"elapsed_bars": 9}, {"measured_r": -1.5, "max_favorable_r": 0.0}, {"conviction": 0.9}):
        a, b = decide(control, **kw), decide(position_decision, **kw)
        assert (a["action"], a["reason"]) == (b["action"], b["reason"])


def test_pa_symmetry_patch_applies_to_the_current_indicator_module():
    cls = worker.symmetric_pa_class()
    assert cls(symmetric_direction=True).symmetric_direction is True
    assert cls().symmetric_direction is False


class _Gov:
    integral_error = last_error = last_control_u = 0.0
    _inner_states = {}


class _Bay:
    governor = _Gov()
    consecutive_stops = 0


class _Plant:
    bays = {"B": _Bay()}


class _Orch:
    open_trades, symbol_consecutive_losses = {}, {}
    _equity_curve, _mtm_peak, _mtm_max_drawdown_fraction = [1000.0], 1000.0, 0.0


def test_zero_state_check_accepts_a_fresh_controller_and_rejects_carried_state():
    assert worker.verify_zero_state(_Orch(), _Plant(), 1000.0)
    dirty = _Orch()
    dirty._mtm_max_drawdown_fraction = 0.01
    with pytest.raises(RuntimeError, match="not zero"):
        worker.verify_zero_state(dirty, _Plant(), 1000.0)
    dirty_gov = _Plant()
    dirty_gov.bays = {"B": type("B", (), {"governor": type("G", (), {
        "integral_error": 0.2, "last_error": 0.0, "last_control_u": 0.0, "_inner_states": {}})(),
        "consecutive_stops": 0})()}
    with pytest.raises(RuntimeError, match="not zero"):
        worker.verify_zero_state(_Orch(), dirty_gov, 1000.0)


def test_forward_bars_stay_inside_the_exit_session():
    days = {"X": [[{"timestamp": f"2024-02-13 10:{i:02d}"} for i in range(5)],
                  [{"timestamp": f"2024-02-14 10:{i:02d}"} for i in range(5)]]}
    got = worker.forward_bars_after(days, "X", "2024-02-13 10:03", 15)
    assert [b["timestamp"] for b in got] == ["2024-02-13 10:04"]
