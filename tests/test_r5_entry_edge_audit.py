"""R5 entry-edge audit: forward edge in R, matched controls and the money decomposition."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from revision2.transaction_costs import leg_cost, paper_fill_price

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("r5_entry_edge_audit", ROOT / "scripts/diagnostics/r5_entry_edge_audit.py")
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

SLIP = 0.0005


def _frame(closes, day="2024-03-01", start="09:30"):
    ts = pd.date_range(f"{day} {start}", periods=len(closes), freq="min", tz="Asia/Kolkata")
    c = np.asarray(closes, float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, c) + 0.1,
                         "low": np.minimum(o, c) - 0.1, "close": c, "volume": 1.0})


def _trade(side, entry_ts, market_entry, market_exit, stop, qty=10, reason="stop", tid="t1"):
    close_side = "SELL" if side == "BUY" else "BUY"
    fill_in = paper_fill_price(market_entry, side, SLIP)
    fill_out = paper_fill_price(market_exit, close_side, SLIP)
    pnl = (fill_out - fill_in) * qty if side == "BUY" else (fill_in - fill_out) * qty
    costs = leg_cost(fill_in, qty, side) + leg_cost(fill_out, qty, close_side)
    return {"trade_id": tid, "symbol": "INFY", "side": side, "entry_timestamp": entry_ts,
            "exit_timestamp": entry_ts, "entry_price": fill_in, "exit_price": fill_out, "quantity": qty,
            "planned_stop_price": stop, "reason": reason, "pnl": pnl, "costs": costs,
            "net_pnl": pnl - costs, "bars_held": 3}


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_money_decomposition_identity_and_slippage_recovery(side):
    t = _trade(side, "2024-03-01 09:35:00+05:30", 1000.0, 1004.0 if side == "BUY" else 996.0,
               stop=990.0 if side == "BUY" else 1010.0)
    d = audit.decompose(t, SLIP)
    assert d["market"] - d["slippage"] - d["brokerage"] - d["exchange"] - d["stt"] == pytest.approx(d["net"])
    assert d["market"] == pytest.approx(4.0 * 10, abs=1e-2)            # the pre-slippage move: 4 points x 10
    assert d["slippage"] == pytest.approx(SLIP * (1000.0 + (1004.0 if side == "BUY" else 996.0)) * 10, rel=1e-3)
    assert d["costs_model"] == pytest.approx(d["costs_recorded"])


def test_forward_edge_buy_and_sell_and_session_cap():
    # 40 bars; +1 per bar from bar 10.  Entry at bar 10 (09:40).
    closes = [100.0] * 10 + [100.0 + k for k in range(30)]
    bars = audit.SymbolBars(_frame(closes))
    idx = bars.index_of("2024-03-01 09:40:00+05:30")
    assert idx == 10
    f = bars.forward(idx, 100.0, +1.0, 2.0)
    assert f[1]["r"] == pytest.approx(0.5) and f[5]["r"] == pytest.approx(2.5)
    assert f[15]["r"] == pytest.approx(7.5) and not f[15]["truncated"]
    assert f[30]["truncated"] and f[30]["r"] == pytest.approx(29 / 2.0)  # capped at the last bar
    assert f[5]["mfe_r"] == pytest.approx((105.1 - 100.0) / 2.0)
    s = bars.forward(idx, 100.0, -1.0, 2.0)                             # a short into the rally
    assert s[5]["r"] == pytest.approx(-2.5) and s[5]["mae_r"] < 0
    pre = bars.pre_run(idx, 100.0, +1.0, 2.0)
    assert pre[5] == pytest.approx(0.0)
    assert bars.pre_run(3, 100.0, +1.0, 2.0)[5] is None                # not enough same-session bars


def test_sessions_are_not_crossed():
    two_days = pd.concat([_frame([100.0] * 20), _frame([200.0] * 20, day="2024-03-04")], ignore_index=True)
    bars = audit.SymbolBars(two_days)
    idx = bars.index_of("2024-03-01 09:45:00+05:30")
    f = bars.forward(idx, 100.0, +1.0, 1.0)
    assert f[30]["r"] == pytest.approx(0.0) and f[30]["truncated"]    # never reaches the 200 session
    c = bars.controls(idx, +1.0, 1.0, 20, seed=7)
    assert all(abs(v) < 1e-12 for v in c.values())
    assert bars.controls(idx, +1.0, 1.0, 20, seed=7) == c               # deterministic


def _days(*sessions, start="09:15"):
    days = ["2024-03-01", "2024-03-04", "2024-03-05", "2024-03-06"]
    return pd.concat([_frame(c, day=days[i], start=start) for i, c in enumerate(sessions)], ignore_index=True)


def test_entry_edge_is_trade_minus_matched_control():
    drift = [100.0 + 0.5 * k for k in range(60)]                          # the same steady drift every day
    bars = audit.SymbolBars(_days(drift, drift, start="09:30"))
    t = _trade("BUY", "2024-03-01 09:50:00+05:30", float(drift[20]), float(drift[35]), stop=float(drift[20]) - 2)
    row = audit.trade_row(t, bars, SLIP, controls=50)
    # Drift is identical on the other session and timing is measured from the pre-slippage entry,
    # so a random entry does exactly as well: zero edge (slippage is not mistaken for bad timing).
    assert row["forward"][5]["r"] - row["control_r"][5] == pytest.approx(0.0, abs=1e-9)
    assert row["entry_bar_close_r"] == pytest.approx(0.0, abs=1e-6)
    assert row["friction_r"] > 0
    assert row["forward"][5]["r"] - row["forward_net_r"][5] == pytest.approx(row["friction_r"], rel=0.05)  # charges at the actual exit price
    s = audit.summarize([row])
    json.dumps(audit._sanitize(s), allow_nan=False)


def test_controls_never_ride_the_same_session_move_that_triggered_the_entry():
    # Day 1 runs up 30 bars and the trade enters after the run; day 2 is flat.  A same-day control
    # window would include the run (look-ahead); other-session controls must see nothing.
    run_day = [100.0 + k for k in range(30)] + [129.0] * 90
    flat_day = [100.0] * 120
    bars = audit.SymbolBars(_days(run_day, flat_day))
    idx = 35
    c = bars.controls(idx, +1.0, 1.0, 50, seed=11)
    assert all(v is not None and abs(v) < 1e-12 for v in c.values())
    assert audit.SymbolBars(_frame(run_day)).controls(idx, +1.0, 1.0, 50, seed=11)[5] is None   # no other session


def test_cli_end_to_end_and_quarantine(tmp_path, monkeypatch):
    frame = _frame([100.0 + 0.1 * k for k in range(120)])
    protocol = {"sampling_plan": {"stage_a": [{"block": 1, "sessions": ["2024-03-01"]}], "stage_b": []}}
    trades = [_trade("BUY", "2024-03-01 09:40:00+05:30", 101.0, 101.5, 99.0, tid="a"),
              _trade("SELL", "2024-03-01 10:00:00+05:30", 103.0, 103.4, 105.0, reason="governor_exit:FSR_BELOW_EXIT:FSRN", tid="b")]
    result = {"params": {"stop_loss_atr_mult": 1.2}, "blocks": [{"block": 1, "sessions": ["2024-03-01"], "trades": trades}]}
    (tmp_path / "p.json").write_text(json.dumps(protocol))
    (tmp_path / "trial_000.json").write_text(json.dumps(result))
    monkeypatch.setattr(audit, "OUTPUT_ROOT", tmp_path / "out")
    monkeypatch.setattr(audit, "_worker", lambda: SimpleNamespace(prepare_block=lambda root, p, b: ({"INFY": frame}, {}, {})))
    assert audit.main(["--results", str(tmp_path / "trial_000.json"), "--protocol", str(tmp_path / "p.json"),
                       "--label", "t", "--slippage-fraction", str(SLIP)]) == 0
    doc = json.loads((tmp_path / "out/t/report.json").read_text())
    assert doc["pooled"]["measured"] == 2 and doc["pooled"]["by_exit_class"]["FSR_EXIT"]["trades"] == 1
    total = doc["pooled"]["decomposition_total"]
    assert total["market"] - total["slippage"] - total["brokerage"] - total["exchange"] - total["stt"] == pytest.approx(total["net"])
    bad = tmp_path / "r5_step6_verification_state"
    bad.mkdir()
    (bad / "x.json").write_text(json.dumps(result))
    with pytest.raises(SystemExit):
        audit.main(["--results", str(bad / "x.json"), "--label", "q"])


def test_exit_regret_mfe_ladder_and_fsr_channel_attribution():
    # Flat to bar 20, then +1 per bar.  A BUY entered at bar 10 and cut by FSRN at bar 20 (before
    # the move) shows large positive regret; a stop-out has the same path by construction.
    closes = [100.0] * 21 + [100.0 + k for k in range(1, 40)]
    bars = audit.SymbolBars(_frame(closes))
    t = _trade("BUY", "2024-03-01 09:40:00+05:30", 100.0, 100.0, 98.0,
               reason="governor_exit:FSR_BELOW_EXIT:FSRN", tid="f")
    t["exit_timestamp"] = "2024-03-01 09:50:00+05:30"
    row = audit.trade_row(t, bars, SLIP, controls=5)
    assert row["fsr_channel"] == "FSRN" and row["exit_class"] == "FSR_EXIT"
    reg = row["exit_regret"]
    risk = t["entry_price"] - 98.0
    assert reg[5]["close_r"] == pytest.approx(5.0 / risk, rel=1e-3)     # +5 points five bars after the exit
    assert reg[10]["best_r"] == pytest.approx(10.1 / risk, rel=1e-3)    # high = close + 0.1
    assert reg[1]["worst_r"] >= -0.2 / risk
    assert row["mfe_r_market"] == pytest.approx(0.1 / risk, rel=1e-3) and row["mae_r_market"] < 0
    s = audit.summarize([row])
    assert s["fsr_channels"] == {"FSRN": 1}
    assert s["exit_authority"]["governor_exit:FSR_BELOW_EXIT:FSRN"]["k5"]["share_best_ge_0.30R"] == 1.0
    assert s["mfe_ladder_market"][">=0.10R"] == 0.0 and s["mfe_ladder_market"]["n"] == 1
    json.dumps(audit._sanitize(s), allow_nan=False)


def test_controls_are_time_matched_and_ladders_are_exit_independent():
    # Every morning rallies +1/bar for 60 bars, then flat.  Controls come from the other sessions at
    # the trade's own time of day: a late entry's controls see the flat afternoon, an early one's
    # the rally.
    day = [100.0 + k for k in range(60)] + [159.0] * 200
    bars = audit.SymbolBars(_days(day, day, day))
    c = bars.controls(200, +1.0, 1.0, 50, seed=3)
    assert all(abs(v) < 1e-12 for v in c.values())                     # flat near 12:35 on other days
    early = bars.controls(20, +1.0, 1.0, 50, seed=3)
    assert early[5] == pytest.approx(5.0, abs=1.0)                      # rally near 09:35 on other days

    # A trade cut after 1 bar still gets its full fixed-horizon opportunity measured.
    t = _trade("BUY", str(bars.ts.iloc[10]), float(day[10]), float(day[11]), float(day[10]) - 2.0,
               reason="governor_exit:FSR_BELOW_EXIT:FSRN")
    t["exit_timestamp"] = str(bars.ts.iloc[11])
    row = audit.trade_row(t, bars, SLIP, controls=5)
    s = audit.summarize([row])
    risk = t["entry_price"] - t["planned_stop_price"]
    assert s["mfe_ladder_market"]["median"] < 1.0                        # to the historical exit only
    assert s["mfe_ladder_fixed"]["30"][">=1.00R"] == 1.0                 # 30 bars of rally ahead
    # Its controls sit in the same morning rally on the other days: same rungs, no edge.
    assert s["mfe_ladder_fixed_control"]["30"][">=1.00R"] == pytest.approx(1.0, abs=0.2)
    assert row["forward"][375]["truncated"] and row["forward"][375]["r"] == pytest.approx(
        (159.0 - t["entry_price"] / (1 + SLIP)) / risk, rel=1e-6)        # session-close horizon
    assert row["entry_bar_close_r"] == pytest.approx(0.0, abs=1e-6)
    assert row["next_bar_open_r"] == pytest.approx(0.0, abs=1e-6)      # open(t+1) = close(t) here


def test_pulse_train_width_interval_polarity_and_energy_vs_switching_loss():
    closes = [100.0 + 0.2 * k for k in range(120)]
    bars = audit.SymbolBars(_frame(closes))
    a = _trade("SELL", str(bars.ts.iloc[10]), float(closes[10]), float(closes[12]), float(closes[10]) + 2.0, tid="a")
    a["exit_timestamp"], a["bars_held"] = str(bars.ts.iloc[12]), 2
    b = _trade("BUY", str(bars.ts.iloc[15]), float(closes[15]), float(closes[55]), float(closes[15]) - 2.0, tid="b")
    b["exit_timestamp"], b["bars_held"] = str(bars.ts.iloc[55]), 40
    rows = [audit.trade_row(t, bars, SLIP, controls=5) for t in (a, b)]
    pt = audit.summarize(rows)["pulse_train"]
    assert pt["pulses"] == 2 and pt["polarity_share_sell"] == 0.5 and pt["polarity_flips_within_session"] == 1
    assert pt["off_interval_minutes"]["median"] == pytest.approx(3.0)          # exit 09:42 -> entry 09:45
    assert pt["width_bars"]["median"] == pytest.approx(21.0) and pt["width_bars"]["share_le_3"] == 0.5
    assert set(pt["by_width_bars"]) == {"1-3", "31-max"}
    for r, t in zip(rows, (a, b)):                                            # energy - loss = net, in R
        risk = abs(t["entry_price"] - t["planned_stop_price"])
        assert r["market_r"] - r["friction_r"] == pytest.approx(t["net_pnl"] / (t["quantity"] * risk), rel=1e-6)
    assert pt["by_width_bars"]["31-max"]["energy_r"] > pt["by_width_bars"]["1-3"]["energy_r"]   # wide pulse rode the trend


def test_session_cluster_bootstrap_is_wider_than_trade_bootstrap_for_correlated_days():
    # Three sessions, 50 trades each, identical within a day: really 3 observations, not 150.
    values = [1.0] * 50 + [-1.0] * 50 + [0.5] * 50
    days = ["d1"] * 50 + ["d2"] * 50 + ["d3"] * 50
    s = audit._stats(values, days)
    trade_width = s["ci95_trade"][1] - s["ci95_trade"][0]
    cluster_width = s["ci95"][1] - s["ci95"][0]
    assert s["clusters"] == 3 and cluster_width > 3 * trade_width
    assert audit._stats(values)["ci95"] == audit._stats(values)["ci95_trade"]   # unclustered: unchanged


def test_friction_sweep_reproduces_engine_net_at_model_slippage_and_scales_with_bps():
    closes = [100.0 + 0.1 * k for k in range(120)]
    bars = audit.SymbolBars(_days(closes, closes))
    t = _trade("BUY", str(bars.ts.iloc[10]), float(closes[10]), float(closes[20]), float(closes[10]) - 1.0)
    t["exit_timestamp"] = str(bars.ts.iloc[20])
    row = audit.trade_row(t, bars, SLIP, controls=5)
    sweep = audit.friction_sweep([row])
    risk = t["entry_price"] - t["planned_stop_price"]
    # Synthesized fills at the sealed 5 bps reproduce the engine's fills, charges and net.
    assert sweep["5"]["realized_net_rupees"] == pytest.approx(t["net_pnl"], abs=2e-3)
    assert sweep["5"]["realized_net_r"]["mean"] == pytest.approx(t["net_pnl"] / (t["quantity"] * risk), rel=1e-3)
    # At 0 bps: the pre-slippage move minus charges recomputed on the slippage-free fills.
    mkt_in, mkt_out, q = row["market_entry_price"], row["market_exit_price"], t["quantity"]
    expected0 = (mkt_out - mkt_in) * q - (sum(audit.leg_cost_parts(mkt_in, q, "BUY").values())
                                          + sum(audit.leg_cost_parts(mkt_out, q, "SELL").values()))
    assert sweep["0"]["realized_net_rupees"] == pytest.approx(expected0, rel=1e-9)
    nets = [sweep[f"{b:g}"]["realized_net_rupees"] for b in audit.SLIPPAGE_SWEEP_BPS]
    assert nets == sorted(nets, reverse=True)                                               # monotone in slippage
    assert set(sweep["0"]["fixed_hold_net_r"]) == {"15", "30", "60", "375"}
    assert sweep["first_fill_eod_trades"] == 1


def test_first_fill_competitor_keeps_one_entry_per_symbol_session():
    rows = [{"source": "s", "symbol": "INFY", "entry_timestamp": "2024-03-01 10:05:00+05:30"},
            {"source": "s", "symbol": "INFY", "entry_timestamp": "2024-03-01 09:40:00+05:30"},
            {"source": "s", "symbol": "INFY", "entry_timestamp": "2024-03-04 09:40:00+05:30"},
            {"source": "s", "symbol": "TCS", "entry_timestamp": "2024-03-01 11:00:00+05:30"}]
    firsts = audit.first_fill_per_symbol_session(rows)
    assert sorted((r["symbol"], r["entry_timestamp"][:16]) for r in firsts) == [
        ("INFY", "2024-03-01 09:40"), ("INFY", "2024-03-04 09:40"), ("TCS", "2024-03-01 11:00")]
