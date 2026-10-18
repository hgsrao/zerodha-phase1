"""Engine B: shared R5 engine executed natively by NautilusTrader (offline backtest only)."""
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("nautilus_trader")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from engine_b.orchestrator import EngineBExecutionError, EngineBOrchestrator  # noqa: E402
from engine_b.runner import run_engine_b  # noqa: E402
from revision5.plant_control import PlantControlError  # noqa: E402
from run_engine_b_nautilus import load_synthetic  # noqa: E402

BARS = 500


@pytest.fixture(scope="module")
def smoke():
    data, nifty, vix, _ = load_synthetic(["INFY"], BARS)
    return data, nifty, vix, run_engine_b(data, nifty, vix)


def test_native_order_fill_lifecycle(smoke):
    report = smoke[3]
    orders = report["orders"]
    assert orders["entry_fills"] >= 1 and orders["positions_closed"] >= 1
    assert orders["submitted"] == orders["accepted"] == orders["fill_events"]
    assert orders["rejected"] == orders["canceled"] == orders["inflight_at_end"] == 0
    by_order = {}
    for row in report["order_audit"]:
        by_order.setdefault(row["client_order_id"], []).append(row["event"])
    assert by_order and all(events == ["SUBMIT", "ACCEPTED", "FILLED"] for events in by_order.values())
    # every fill R5 booked is a Nautilus fill: same order ids, prices and quantities
    fills = {f["client_order_id"]: f for f in report["nautilus_fills"]}
    assert set(fills) == set(by_order)
    for trade in report["trades"]:
        assert any(float(f["avg_px"]) == trade["entry_price"] for f in fills.values())
        assert any(float(f["avg_px"]) == trade["exit_price"] for f in fills.values())


def test_independent_nautilus_accounting_reconciles_with_r5_ledger(smoke):
    report = smoke[3]
    acct, ledger, rec = report["nautilus_accounting"], report["r5_ledger"], report["reconciliation"]
    assert rec["breaks"] == [] and rec["ledger_gross_error"] is None and rec["checks"] > 0
    assert acct["open_positions"] == {} and ledger["open_trades"] == []
    assert acct["gross_realized_closed_positions"] == pytest.approx(ledger["gross_pnl"], abs=1e-6)
    # Nautilus books commission in INR to the paisa; the R5 ledger keeps full precision.
    fills = report["orders"]["fill_events"]
    assert acct["commissions"] == pytest.approx(ledger["costs"], abs=0.005 * fills)
    assert acct["net_pnl"] == pytest.approx(ledger["net_pnl"], abs=0.005 * fills)
    pnl_total = acct["performance_stats_pnls"]["PnL (total)"]
    assert pnl_total is not None and pnl_total == pytest.approx(acct["net_pnl"], abs=0.01)


def test_event_chronology_and_causal_decision_clock(smoke):
    report = smoke[3]
    assert report["max_decision_lag_ns"] == 0                   # decided at the bar's close, never later
    assert report["decision_ticks"] == BARS - 60
    stamps = [row["ts_ns"] for row in report["order_audit"]]
    assert stamps == sorted(stamps)
    for row in report["order_audit"]:
        if row["event"] == "FILLED":
            assert row["ts_event"] == row["ts_ns"]              # filled at the decision bar's close
    assert report["reconciliation"]["chronology_errors"] == []


def test_option_a_post_fill_risk_stop_target_and_plant_capacity(smoke):
    rows = smoke[3]["option_a_fills"]
    assert rows
    for row in rows:
        sign = 1 if row["side"] == "BUY" else -1
        assert sign * (row["anchored_target"] - row["fill_price"]) == pytest.approx(
            abs(row["planned_target"] - row["reference_price"]))
        assert sign * (row["fill_price"] - row["anchored_stop"]) == pytest.approx(row["risk_per_share"])
        assert row["filled_quantity"] <= row["requested_quantity"]
        assert row["worst_case_loss_rupees"] <= row["max_loss_per_trade_rupees"]
        assert row["bay_notional_after_fill"] <= row["bay_effective_allocation"]
        assert row["breaches"] == []


def test_no_lookahead_future_bars_cannot_change_past_decisions(smoke):
    data, nifty, vix, report = smoke
    first_fill = next(r["ts_ns"] for r in report["order_audit"] if r["event"] == "FILLED")
    import pandas as pd
    cut = pd.Timestamp(first_fill, tz="UTC").tz_convert("Asia/Kolkata")
    frame = data["INFY"].copy()
    future = frame["timestamp"] >= cut       # the fill bar's successor onward
    frame.loc[future, ["open", "high", "low", "close"]] = frame.loc[future, ["open", "high", "low", "close"]][::-1].to_numpy()
    altered = run_engine_b({"INFY": frame}, nifty, vix)
    before = lambda rep: [r for r in rep["order_audit"] if r["ts_ns"] <= first_fill]
    assert before(altered) == before(report) and before(report)


def test_paper_broker_is_disabled_and_port_is_required(smoke):
    data, nifty, vix, _ = smoke
    from revision2_external.grid_context import SealedGridContextProvider
    engine = EngineBOrchestrator(["INFY"], grid_context_provider=SealedGridContextProvider(nifty, vix))
    with pytest.raises(EngineBExecutionError):
        engine.broker.place_order(symbol="INFY", side="BUY", quantity=1, order_type="MARKET", market_price=1.0)
    with pytest.raises(EngineBExecutionError):
        engine.broker.get_position("INFY")
    with pytest.raises(PlantControlError):
        engine._assert_paper_plant_broker()


def test_protected_engine_a_files_unchanged():
    protected = ["revision2", "revision2_external/paper_execution.py", "revision5/ccpp_unified_plant.py",
                 "runtime/operating_mode.py"]
    diff = subprocess.run(["git", "diff", "--name-only", "HEAD", "--", *protected], cwd=ROOT,
                          capture_output=True, text=True, check=True)
    assert diff.stdout.strip() == ""


def test_nautilus_denied_entry_is_disposed_and_nothing_is_booked(smoke):
    data, nifty, vix, _ = smoke
    report = run_engine_b(data, nifty, vix, max_order_quantity={"INFY": 10})   # native RiskEngine denial
    orders = report["orders"]
    assert orders["entry_submitted"] >= 1 and orders["rejected"] == orders["entry_submitted"]
    assert orders["fill_events"] == 0 and orders["inflight_at_end"] == 0
    assert any(r["event"] == "DENIED" and "maximum trade size" in r["reason"] for r in report["order_audit"])
    assert report["trades"] == [] and report["nautilus_accounting"]["open_positions"] == {}
    assert report["reconciliation"]["breaks"] == []
    assert report["nautilus_accounting"]["net_pnl"] == 0.0


def test_native_partial_entry_and_exit_fills_reconcile(smoke):
    data, nifty, vix, _ = smoke
    frame = data["INFY"]
    thin = {"INFY": frame.assign(volume=(frame["volume"] * 1e-3).round().clip(lower=1))}
    report = run_engine_b(thin, nifty, vix, venue_overrides={"liquidity_consumption": True})
    orders = report["orders"]
    assert orders["entry_partial_fills"] >= 1 and orders["exit_partial_fills"] >= 1
    assert orders["positions_closed"] >= 1 and orders["inflight_at_end"] == 0
    # R5 books exactly what Nautilus filled: partial entry quantity, VWAP across exit child orders
    entry_fills = [r for r in report["order_audit"] if r["event"] == "FILLED" and r["kind"] == "entry"]
    exit_fills = [r for r in report["order_audit"] if r["event"] == "FILLED" and r["kind"] == "exit"]
    trade = report["trades"][0]
    assert trade["quantity"] == entry_fills[0]["quantity"] < report["option_a_fills"][0]["requested_quantity"]
    closing = exit_fills[:[sum(f["quantity"] for f in exit_fills[:i + 1]) for i in range(len(exit_fills))].index(trade["quantity"]) + 1]
    assert trade["exit_price"] == pytest.approx(sum(f["quantity"] * f["price"] for f in closing) / trade["quantity"])
    assert report["reconciliation"]["breaks"] == [] and report["reconciliation"]["ledger_gross_error"] is None
    assert report["nautilus_accounting"]["gross_realized_closed_positions"] == pytest.approx(
        report["r5_ledger"]["gross_pnl"], abs=1e-6)
