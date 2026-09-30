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


def test_entry_edge_is_trade_minus_matched_control():
    closes = [100.0 + 0.5 * k for k in range(60)]                        # steady drift up all session
    bars = audit.SymbolBars(_frame(closes))
    t = _trade("BUY", "2024-03-01 09:50:00+05:30", float(closes[20]), float(closes[35]), stop=float(closes[20]) - 2)
    row = audit.trade_row(t, bars, SLIP, controls=50)
    # Drift is identical everywhere and timing is measured from the pre-slippage entry, so a random
    # entry does exactly as well as this one: zero edge (slippage is not mistaken for bad timing).
    assert row["forward"][5]["r"] - row["control_r"][5] == pytest.approx(0.0, abs=1e-9)
    assert row["entry_bar_close_r"] == pytest.approx(0.0, abs=1e-6)
    assert row["friction_r"] > 0
    assert row["forward"][5]["r"] - row["forward_net_r"][5] == pytest.approx(row["friction_r"], rel=0.05)  # charges at the actual exit price
    s = audit.summarize([row])
    json.dumps(audit._sanitize(s), allow_nan=False)


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
