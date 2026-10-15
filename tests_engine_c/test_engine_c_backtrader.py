"""Engine C: shared R5 engine executed natively by Backtrader (offline backtest only).

Scenarios replay the seeded synthetic smoke feed shared with Engine B (P&L meaningless).  Each
targeted scenario changes only bars at or after the point it exercises; because Engine C is
causal, every decision before that point is identical to the base run.
"""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pandas as pd
import pytest

bt = pytest.importorskip("backtrader")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine_c.bt_market import NSEEquityIntradayCommission  # noqa: E402
from engine_c.config import EngineCConfig  # noqa: E402
from engine_c.data_contract import (SPECIAL_SESSIONS, DataContractError, audit_and_select, audit_bars,  # noqa: E402
                                   load_synthetic, require_clean)
from engine_c.orchestrator import EngineCExecutionError, EngineCOrchestrator  # noqa: E402
from engine_c.runner import run_engine_c  # noqa: E402
from revision2.transaction_costs import leg_cost  # noqa: E402
from revision5.plant_control import PlantControlError  # noqa: E402

BARS, WARMUP = 500, 60
BASE_COMMIT = "500ababc2cbf12a33902712ce8174ae5cca9322a"


def normalised(report):
    """Report minus Backtrader's process-global order refs (run-local order ids are kept)."""
    text = json.dumps(report, default=str, sort_keys=True)
    data = json.loads(text)
    for row in data["order_audit"]:
        row.pop("bt_ref", None)
    data["outstanding_orders_at_end"] = [{k: v for k, v in r.items() if k != "ref"} for r in data["outstanding_orders_at_end"]]
    data["backtrader_accounting"]["closed_trades"] = [{k: v for k, v in r.items() if k != "ref"}
                                                      for r in data["backtrader_accounting"]["closed_trades"]]
    return data


def fills(report, kind=None):
    return [r for r in report["order_audit"] if r["event"] == "FILLED" and (kind is None or r["kind"] == kind)]


@pytest.fixture(scope="module")
def feed():
    data, nifty, vix, _ = load_synthetic(["INFY"], BARS, WARMUP)
    return data, nifty, vix


@pytest.fixture(scope="module")
def base(feed):
    data, nifty, vix = feed
    return run_engine_c(data, nifty, vix, warmup=WARMUP)


@pytest.fixture(scope="module")
def thin(feed):
    data, nifty, vix = feed
    frame = data["INFY"]
    thin = {"INFY": frame.assign(volume=(frame["volume"] * 1e-3).round().clip(lower=1))}
    return run_engine_c(thin, nifty, vix, warmup=WARMUP, config=EngineCConfig(volume_fill_perc=50.0))


@pytest.fixture(scope="module")
def pair():
    data, nifty, vix, _ = load_synthetic(["INFY", "TCS", "HDFCBANK"], BARS, WARMUP)
    forward = run_engine_c(data, nifty, vix, warmup=WARMUP)
    backward = run_engine_c({s: data[s] for s in reversed(sorted(data))}, nifty, vix, warmup=WARMUP)
    return forward, backward


# 1. native lifecycle ---------------------------------------------------------------------
def test_native_backtrader_entry_fill_exit_lifecycle(base):
    orders = base["orders"]
    assert orders["entry_fills"] >= 1 and orders["positions_closed"] >= 1
    assert orders["submitted"] == orders["accepted"] == orders["fill_events"]
    assert orders["rejected"] == orders["margin"] == orders["canceled"] == orders["working_at_end"] == 0
    events = {}
    for row in base["order_audit"]:
        events.setdefault(row["order_id"], []).append(row["event"])
    assert events and all(e == ["SUBMIT", "SUBMITTED", "ACCEPTED", "FILLED"] for e in events.values())
    # Every R5 trade price is a Backtrader execution price, and Backtrader closed the same trades.
    entry, exit_ = fills(base, "entry"), fills(base, "exit")
    closed = base["backtrader_accounting"]["closed_trades"]
    assert len(closed) == len(base["trades"]) == len(entry) == len(exit_)
    for trade, e, x, bt_trade in zip(base["trades"], entry, exit_, closed):
        assert trade["entry_price"] == e["price"] and trade["exit_price"] == x["price"]
        assert trade["quantity"] == e["quantity"] == x["quantity"]
        assert pd.Timestamp(trade["entry_timestamp"]) == pd.Timestamp(e["fill_label"]) == pd.Timestamp(bt_trade["opened"])
        assert pd.Timestamp(trade["exit_timestamp"]) == pd.Timestamp(x["fill_label"]) == pd.Timestamp(bt_trade["closed"])
        assert bt_trade["pnl"] == pytest.approx(trade["pnl"], abs=1e-9)


# 2. independent broker accounting ----------------------------------------------------------
def test_independent_backtrader_accounting_reconciles(base):
    rec = base["reconciliation"]
    assert rec["breaks"] == [] and rec["ledger_gross_error"] is None and rec["chronology_errors"] == []
    assert rec["checks"] > base["decision_ticks"]               # at least one check per tick plus per event
    assert {"entry_execution", "exit_execution", "entry_booked", "exit_booked", "finish"} <= set(rec["checks_by_context"])
    rows = rec["final"]["rows"]
    assert all(row["status"] == "PASS" for row in rows.values()), rows
    acct, ledger = base["backtrader_accounting"], base["r5_ledger"]
    assert acct["open_positions"] == {} and ledger["open_trades"] == []
    assert acct["ending_value"] == pytest.approx(acct["ending_cash"])            # flat: value is all cash
    assert acct["net_pnl"] == pytest.approx(ledger["net_pnl"], abs=1e-6)
    assert acct["realized_gross"] == pytest.approx(ledger["gross_pnl"], abs=1e-9)
    assert acct["commissions"] == pytest.approx(ledger["costs"], abs=1e-9)


# 3. causal ordering, no lookahead, data contract -----------------------------------------------
def test_causal_clock_warmup_and_next_bar_fills(base, feed):
    labels = feed[0]["INFY"]["timestamp"]
    assert base["warmup_bars"] == WARMUP and base["decision_ticks"] == BARS - WARMUP
    assert pd.Timestamp(base["first_decision_label"]) == labels.iloc[WARMUP]
    assert all(pd.Timestamp(r["label"]) >= labels.iloc[WARMUP] for r in base["order_audit"])   # nothing during warm-up
    for row in base["entry_fills"]:
        decided, filled = pd.Timestamp(row["decision_timestamp"]), pd.Timestamp(row["fill_label"])
        assert filled > decided                                   # never on the decision bar
        assert filled == decided + pd.Timedelta(minutes=1) and row["fill_latency_seconds"] == 0.0
        frame = feed[0]["INFY"].set_index("timestamp")
        # Backtrader filled at the fill bar's open, slipped adversely, capped at the bar range.
        bar = frame.loc[filled]
        expected = min(bar["open"] * (1 + 0.0005), bar["high"]) if row["side"] == "BUY" else max(bar["open"] * (1 - 0.0005), bar["low"])
        assert row["fill_price"] == pytest.approx(expected, abs=1e-9)
    for trade in base["trades"]:
        assert pd.Timestamp(trade["exit_timestamp"]) == pd.Timestamp(trade["exit_decision_timestamp"]) + pd.Timedelta(minutes=1)


def test_no_lookahead_future_bars_cannot_change_past_decisions(base, feed):
    data, nifty, vix = feed
    cut = pd.Timestamp(fills(base)[0]["fill_label"])
    frame = data["INFY"].copy()
    future = frame["timestamp"] > cut
    cols = ["open", "high", "low", "close"]
    frame.loc[future, cols] = frame.loc[future, cols][::-1].to_numpy()
    altered = run_engine_c({"INFY": frame}, nifty, vix, warmup=WARMUP)
    upto = lambda rep: [{k: v for k, v in r.items() if k != "bt_ref"} for r in rep["order_audit"] if pd.Timestamp(r["label"]) <= cut]
    assert upto(base) and upto(altered) == upto(base)
    before = lambda rep: [d for d in rep["decisions"] if pd.Timestamp(d["decision_timestamp"]) < cut]
    assert before(altered) == before(base)
    assert altered["entry_fills"][0] == base["entry_fills"][0]


def test_data_contract_detects_missing_duplicate_and_out_of_order_bars(feed):
    frame = feed[0]["INFY"].copy()
    clean = audit_bars("INFY", frame)
    assert clean["fatal"] == [] and clean["missing_minutes"] == 375 - 125   # second synthetic session is partial
    assert clean["sessions_ending_before_force_close"] == [str(frame["timestamp"].iloc[-1].date())]
    gap = audit_bars("INFY", frame.drop(index=[100, 101, 102]))
    assert gap["fatal"] == [] and gap["missing_minutes"] == clean["missing_minutes"] + 3   # reported, never filled
    dup = audit_bars("INFY", pd.concat([frame.iloc[:50], frame.iloc[[49]], frame.iloc[50:]]))
    assert dup["duplicated_timestamps"] == 1 and "duplicated_timestamps" in dup["fatal"]
    swapped = frame.copy()
    swapped.iloc[[70, 71]] = swapped.iloc[[71, 70]].to_numpy()
    assert audit_bars("INFY", swapped)["out_of_order_rows"] == 1
    off = frame.copy()
    off.loc[5, "timestamp"] = off.loc[5, "timestamp"] + pd.Timedelta(seconds=30)
    assert audit_bars("INFY", off)["off_minute_grid"] == 1
    bad = frame.copy()
    bad.loc[7, "high"] = bad.loc[7, "low"] - 1.0
    assert audit_bars("INFY", bad)["inconsistent_ohlc"] == 1
    naive = frame.assign(timestamp=frame["timestamp"].dt.tz_localize(None))
    assert audit_bars("INFY", naive)["first"] == clean["first"]          # naive = exchange-local IST
    utc_shifted = frame.assign(timestamp=frame["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None))
    assert audit_bars("INFY", utc_shifted)["outside_session"] > 0       # a UTC/IST mix-up is caught
    with pytest.raises(DataContractError):
        require_clean({"INFY": dup})



def _raw(stamps, price=100.0):
    """Raw CSV-shaped rows exactly as the frozen dataset stores them (string timestamps, +05:30)."""
    n = len(stamps)
    return pd.DataFrame({"timestamp": list(stamps), "open": [price] * n, "high": [price + 1] * n,
                         "low": [price - 1] * n, "close": [price + 0.5] * n, "volume": [10.0] * n})


def _minutes(start, count):
    return [t.strftime("%Y-%m-%dT%H:%M:%S+05:30") for t in pd.date_range(start, periods=count, freq="min")]


MUHURAT_2023 = _minutes("2023-11-12 18:15", 60)     # NSE/CMTR/59124: normal market 18:15-19:15
MUHURAT_2024 = _minutes("2024-11-01 18:00", 60)     # normal market 18:00-19:00


def test_special_session_calendar_is_explicit_and_sourced():
    assert sorted(map(str, SPECIAL_SESSIONS)) == ["2023-11-12", "2024-11-01"]
    for day, session in SPECIAL_SESSIONS.items():
        assert session.kind == "DIWALI_MUHURAT" and session.bars == 60 and not session.regular_session
        assert session.source
    assert (SPECIAL_SESSIONS[pd.Timestamp("2023-11-12").date()].first_bar,
            SPECIAL_SESSIONS[pd.Timestamp("2023-11-12").date()].last_bar) == ("18:15", "19:14")
    assert (SPECIAL_SESSIONS[pd.Timestamp("2024-11-01").date()].first_bar,
            SPECIAL_SESSIONS[pd.Timestamp("2024-11-01").date()].last_bar) == ("18:00", "18:59")


def test_muhurat_session_rows_are_legitimate():
    regular = _minutes("2023-11-10 09:15", 375)
    audit = audit_bars("INFY", _raw(regular + MUHURAT_2023 + _minutes("2023-11-13 09:15", 375) + MUHURAT_2024))
    assert audit["fatal"] == [] and audit["outside_session"] == 0
    assert audit["special_session_rows"] == 120 and audit["missing_minutes"] == 0     # 60 expected, not 375
    assert audit["special_sessions"] == {
        "2023-11-12": {"kind": "DIWALI_MUHURAT", "rows": 60, "expected": 60, "window": "18:15-19:14"},
        "2024-11-01": {"kind": "DIWALI_MUHURAT", "rows": 60, "expected": 60, "window": "18:00-18:59"}}
    assert audit["sessions_ending_before_force_close"] == []
    partial = audit_bars("INFY", _raw(MUHURAT_2023[:-5]))
    assert partial["fatal"] == [] and partial["missing_minutes"] == 5                   # reported, not filled


@pytest.mark.parametrize("stamp", [
    "2023-11-13T18:15:00+05:30",     # same evening minute, ordinary trading day
    "2024-11-04T18:00:00+05:30",     # same evening minute, ordinary trading day
    "2023-11-12T18:14:00+05:30",     # one minute before the 2023 normal market
    "2023-11-12T19:15:00+05:30",     # 19:15 is the end of the 2023 session, not a bar
    "2023-11-12T19:25:00+05:30",     # 2023 closing session, not the normal market
    "2023-11-12T18:05:00+05:30",     # 2023 pre-open
    "2024-11-01T17:59:00+05:30",     # 2024 pre-open
    "2024-11-01T19:00:00+05:30",     # 19:00 is the end of the 2024 session, not a bar
    "2023-11-12T18:00:00+05:30",     # 2023 pre-open start
    "2024-11-01T10:00:00+05:30",     # regular-hours row on a date whose regular session was a holiday
    "2023-11-12T09:15:00+05:30",     # Sunday regular hours
])
def test_evening_rows_outside_the_calendar_are_fatal(stamp):
    audit = audit_bars("INFY", _raw(sorted(MUHURAT_2023 + MUHURAT_2024 + [stamp])))
    assert audit["outside_session"] == 1 and "outside_session" in audit["fatal"]
    with pytest.raises(DataContractError, match="outside_session"):
        require_clean({"INFY": audit})


@pytest.mark.parametrize("bad", ["garbage", "", None, "2023-11-12T25:00:00+05:30", "2023-02-30T18:16:00+05:30",
                                 "2023-11-12T12:47:00+00:00"])
def test_malformed_timestamps_are_fatal(bad):
    audit = audit_bars("INFY", _raw(MUHURAT_2023[:10] + [bad] + MUHURAT_2023[10:]))
    assert {"unparseable_timestamps", "mixed_utc_offsets"} & set(audit["fatal"])
    with pytest.raises(DataContractError):
        require_clean({"INFY": audit})


def test_replay_window_is_invariant_to_muhurat_rows_and_audits_the_whole_file(feed):
    frame = feed[0]["INFY"]                                    # 2024-03-04 .. 2024-03-05, +05:30 labels
    body = _raw(frame["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S+05:30"))
    for column in ("open", "high", "low", "close", "volume"):
        body[column] = frame[column].to_numpy()
    end = frame["timestamp"].iloc[-1].date()
    plain, plain_audit = audit_and_select({"INFY": body.copy()}, end, 300, WARMUP)
    with_muhurat = pd.concat([_raw(MUHURAT_2023), body, _raw(MUHURAT_2024)], ignore_index=True)   # before and after
    chosen, audit = audit_and_select({"INFY": with_muhurat}, end, 300, WARMUP)
    pd.testing.assert_frame_equal(plain["INFY"], chosen["INFY"])
    assert audit["INFY"]["special_session_rows"] == 120 and audit["INFY"]["window"]["special_session_rows"] == 0
    assert audit["INFY"]["window"] == plain_audit["INFY"]["window"]
    # the whole raw file is audited before the window is chosen: a bad row far outside the window refuses the run
    poisoned = pd.concat([_raw(["2023-11-13T18:15:00+05:30"]), body], ignore_index=True)
    with pytest.raises(DataContractError, match="outside_session"):
        audit_and_select({"INFY": poisoned}, end, 300, WARMUP)
    # a window that reaches back into a special session keeps those bars as-is (no fill, no drop)
    reach, reach_audit = audit_and_select({"INFY": pd.concat([_raw(MUHURAT_2023), body], ignore_index=True)},
                                          end, len(body) + 10, WARMUP)
    assert len(reach["INFY"]) == len(body) + 10 and reach_audit["INFY"]["window"]["special_session_rows"] == 10

# 4. multi-symbol isolation and deterministic ordering -------------------------------------------
def test_multi_symbol_isolation_and_ordering(pair):
    forward, backward = pair
    assert forward["symbols"] == ["HDFCBANK", "INFY", "TCS"]
    assert forward["reconciliation"]["breaks"] == [] and forward["reconciliation"]["final"]["status"] == "PASS"
    by_symbol = {}
    for row in forward["order_audit"]:
        by_symbol.setdefault(row["order_id"], set()).add(row["symbol"])
    assert all(len(s) == 1 for s in by_symbol.values())          # an order never touches another symbol
    closed = forward["backtrader_accounting"]["closed_trades"]
    for symbol in forward["symbols"]:
        r5 = [t for t in forward["trades"] if t["symbol"] == symbol]
        native = [t for t in closed if t["symbol"] == symbol]
        assert len(r5) == len(native)
        assert sum(t["pnl"] for t in r5) == pytest.approx(sum(t["pnl"] for t in native), abs=1e-9)
    assert forward["bars_received"] == 3 * BARS and forward["decision_ticks"] == BARS - WARMUP
    traded = {t["symbol"] for t in forward["trades"]}
    assert {"INFY", "HDFCBANK"} <= traded
    # positions on different symbols overlapped in time and still reconciled per symbol
    spans = [(pd.Timestamp(t["entry_timestamp"]), pd.Timestamp(t["exit_timestamp"]), t["symbol"]) for t in forward["trades"]]
    assert any(a[2] != b[2] and a[0] < b[1] and b[0] < a[1] for a in spans for b in spans)
    for symbol in set(forward["symbols"]) - traded:
        assert not [r for r in forward["order_audit"] if r["symbol"] == symbol]
    assert normalised(backward) == normalised(forward)            # input order cannot change anything


# 5. risk stop / target / governor exits ------------------------------------------------------
def test_stop_target_anchoring_and_exit_semantics(base, feed):
    frame = feed[0]["INFY"].set_index("timestamp")
    for row in base["entry_fills"]:
        sign = 1 if row["side"] == "BUY" else -1
        assert sign * (row["anchored_target"] - row["fill_price"]) == pytest.approx(abs(row["planned_target"] - row["reference_price"]))
        assert sign * (row["fill_price"] - row["anchored_stop"]) == pytest.approx(row["risk_per_share"])
        assert row["worst_case_loss_rupees"] <= row["max_loss_per_trade_rupees"]
        assert row["breaches"] == [] and row["bay_notional_after_fill"] <= row["bay_effective_allocation"]
    targets = [t for t in base["trades"] if t["reason"] == "target"]
    assert targets
    fill_rows = {r["trade_id"]: r for r in base["entry_fills"]}
    for trade in targets:
        bar = frame.loc[pd.Timestamp(trade["exit_decision_timestamp"])]
        anchored = fill_rows[trade["trade_id"]]["anchored_target"]
        assert bar["high"] >= anchored if trade["side"] == "BUY" else bar["low"] <= anchored   # touched on the decision bar
        nxt = frame.loc[pd.Timestamp(trade["exit_timestamp"])]
        assert trade["exit_price"] == pytest.approx(max(nxt["open"] * (1 - 0.0005), nxt["low"]) if trade["side"] == "BUY"
                                                    else min(nxt["open"] * (1 + 0.0005), nxt["high"]))


def test_gap_through_planned_stop_arms_plant_trip_exit(base, feed):
    data, nifty, vix = feed
    first = base["entry_fills"][0]
    assert first["side"] == "BUY"
    gap = first["planned_stop"] - 2.0
    frame = data["INFY"].copy()
    at = frame["timestamp"] == pd.Timestamp(first["fill_label"])
    frame.loc[at, ["open", "high", "low", "close"]] = round(gap / 0.05) * 0.05
    report = run_engine_c({"INFY": frame}, nifty, vix, warmup=WARMUP)
    row = report["entry_fills"][0]
    assert row["decision_timestamp"] == first["decision_timestamp"] and row["fill_label"] == first["fill_label"]
    assert row["fill_price"] < row["planned_stop"]
    assert "ENGINE_C_FILL_BEYOND_PLANNED_STOP" in row["breaches"]
    trade = report["trades"][0]
    assert trade["reason"].startswith("plant_trip:ENGINE_C_FILL_BEYOND_PLANNED_STOP") or trade["reason"] in ("stop_gap", "target_gap")
    assert pd.Timestamp(trade["exit_decision_timestamp"]) == pd.Timestamp(first["fill_label"])   # first completed bar after the fill
    assert report["r5_ledger"]["funnel"]["execution_halted"] is True          # Gate16 slippage: R5 halts new entries
    assert report["reconciliation"]["breaks"] == []


def test_short_session_position_is_flattened_at_next_session(base, feed):
    data, nifty, vix = feed
    trade = base["trades"][0]
    keep_until = pd.Timestamp(trade["exit_decision_timestamp"]) - pd.Timedelta(minutes=1)
    frame = data["INFY"]
    day = keep_until.date()
    cut = frame[~((frame["timestamp"].dt.date == day) & (frame["timestamp"] > keep_until))].reset_index(drop=True)
    assert audit_bars("INFY", cut)["sessions_ending_before_force_close"][0] == str(day)
    report = run_engine_c({"INFY": cut}, nifty, vix, warmup=WARMUP)
    carried = report["session_carry_exits"]
    assert len(carried) == 1 and carried[0]["trade_id"] == trade["trade_id"]
    closed = report["trades"][0]
    assert closed["reason"] == "engine_c_session_carry_exit"
    next_session = cut[cut["timestamp"].dt.date > day]["timestamp"]
    assert pd.Timestamp(closed["exit_decision_timestamp"]) == next_session.iloc[0]
    assert pd.Timestamp(closed["exit_timestamp"]) == next_session.iloc[1]
    assert report["reconciliation"]["breaks"] == []


# 6. plant capacity and exposure ---------------------------------------------------------------
class _Port:
    offline = True

    def __init__(self, cash):
        self._cash = cash

    def cash(self):
        return self._cash

    def realized_gross_pnl(self):
        return 0.0

    def commissions_total(self):
        return 0.0

    def net_position(self, symbol):
        return 0

    def now_label(self):
        return None


def test_working_entries_reserve_concurrency_sector_and_bay_capacity(feed):
    data, nifty, vix = feed
    from revision2_external.grid_context import SealedGridContextProvider
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS
    from revision5.topology import SYMBOL_TO_BAY
    symbols = ["INFY", "TCS", "WIPRO", "HCLTECH", "TECHM", "HDFCBANK", "ICICIBANK"]
    r5 = EngineCOrchestrator(symbols, grid_context_provider=SealedGridContextProvider(nifty, vix),
                             real_plant_dcs=CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:"),
                             plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", governor_authority="full")
    r5.attach_port(_Port(1_000_000.0))
    warm = data["INFY"].iloc[:WARMUP]
    r5.begin({s: warm for s in symbols})
    working = lambda symbol, notional: {"kind": "entry", "symbol": symbol, "reserved_notional": notional}
    assert r5._capacity_breach("INFY", 10, 1000.0) is None                      # nothing working: R5's own checks rule
    r5._working = {i: working(s, 1_000.0) for i, s in enumerate(["HDFCBANK", "ICICIBANK", "WIPRO", "HCLTECH", "TECHM"])}
    assert r5._capacity_breach("TCS", 1, 100.0) == "max_concurrent_positions"   # 5 working entries = cap of 5
    sector_cap = r5._run.sector_cap_fraction * r5._equity()
    r5._working = {1: working("TCS", sector_cap - 500.0)}
    assert r5.sector_map["TCS"] == r5.sector_map["INFY"]
    assert r5._capacity_breach("INFY", 1, 1000.0) == "sector_exposure_cap"
    bay = r5.real_plant_dcs.bays[SYMBOL_TO_BAY["HDFCBANK"]]
    allocation = float(bay.effective_allocation)
    assert SYMBOL_TO_BAY["ICICIBANK"] == SYMBOL_TO_BAY["HDFCBANK"] and sector_cap > allocation + 1_000.0
    r5._working = {1: working("ICICIBANK", allocation - 100.0)}
    assert r5._capacity_breach("HDFCBANK", 1, 500.0) == "plant_bay_capacity"   # sector still has room
    assert r5._capacity_breach("HDFCBANK", 1, 50.0) is None                     # fits the remaining headroom
    # gross exposure: R5's own gross check now counts working entries
    r5._working = {1: working("TCS", 123_456.0)}
    assert r5._gross_exposure_notional() == pytest.approx(123_456.0)


# 7. duplicate-order prevention ---------------------------------------------------------------
def test_one_working_order_per_symbol_and_no_duplicate_submissions(base, thin, pair):
    for report in (base, thin, *pair):
        assert report["orders"]["max_working_orders_per_symbol"] == 1
        submitted = [d["candidate_id"] for d in report["decisions"] if d["disposition"] == "submitted"]
        assert len(submitted) == len(set(submitted))
        open_order = {}
        for row in report["order_audit"]:
            if row["event"] == "SUBMIT":
                prior = open_order.get(row["symbol"])
                assert prior is None, f"{row['symbol']}: {row['order_id']} submitted while {prior} working"
                open_order[row["symbol"]] = row["order_id"]
            elif row["event"] in ("CANCELED", "REJECTED", "MARGIN", "UNFILLED_AT_END") or (row["event"] == "FILLED" and not row.get("partial")):
                if open_order.get(row["symbol"]) == row["order_id"]:
                    open_order.pop(row["symbol"])
            elif row["event"] == "FILLED" and row.get("partial") and row["kind"] == "entry":
                open_order.pop(row["symbol"], None)             # remainder cancelled at once (IOC)


# 8. rejection, cancellation, margin -------------------------------------------------------------
def test_native_rejection_margin_and_unfilled_order_disposal(feed):
    data, nifty, vix = feed
    rejected = run_engine_c(data, nifty, vix, warmup=WARMUP, config=EngineCConfig(max_order_quantity={"INFY": 10}))
    assert rejected["orders"]["rejected"] == rejected["orders"]["entry_submitted"] >= 1
    assert any(r["event"] == "REJECTED" and "exceeds venue maximum 10" in r["reason"] for r in rejected["order_audit"])
    margin = run_engine_c(data, nifty, vix, warmup=WARMUP, config=EngineCConfig(starting_cash=20_000.0),
                          orchestrator_kwargs={"starting_equity": 1_000_000.0})
    assert margin["orders"]["margin"] == margin["orders"]["entry_submitted"] >= 1
    for report in (rejected, margin):
        assert report["orders"]["fill_events"] == 0 and report["trades"] == []
        assert report["backtrader_accounting"]["net_pnl"] == 0.0 and report["backtrader_accounting"]["open_positions"] == {}
        assert report["reconciliation"]["breaks"] == [] and report["orders"]["working_at_end"] == 0
        assert report["r5_ledger"]["funnel"]["fills"] == 0


def test_order_working_at_end_of_data_is_cancelled_not_filled(base, feed):
    data, nifty, vix = feed
    decided = pd.Timestamp(base["entry_fills"][0]["decision_timestamp"])
    frame = data["INFY"][data["INFY"]["timestamp"] <= decided].reset_index(drop=True)
    report = run_engine_c({"INFY": frame}, nifty, vix, warmup=WARMUP)
    assert report["orders"]["entry_submitted"] == 1 and report["orders"]["fill_events"] == 0
    # Submitted on the final bar: Backtrader never ran another broker cycle, so it stays SUBMITTED.
    assert [r["status_after_cancel"] for r in report["outstanding_orders_at_end"]] == ["Submitted"]
    ended = [r for r in report["order_audit"] if r["event"] == "UNFILLED_AT_END"]
    assert len(ended) == 1 and ended[0]["filled"] == 0 and ended[0]["status_at_end"] == "Submitted"
    assert report["r5_ledger"]["funnel"]["fills"] == 0
    assert report["trades"] == [] and report["backtrader_accounting"]["open_positions"] == {}
    assert report["reconciliation"]["breaks"] == []


# 9. partial fills (Backtrader's native volume filler) -------------------------------------------
def test_native_partial_entry_and_exit_fills_reconcile(thin):
    orders = thin["orders"]
    assert orders["entry_partial_fills"] >= 1 and orders["exit_partial_fill_events"] >= 1 and orders["canceled"] >= 1
    entry = thin["entry_fills"][0]
    assert entry["filled_quantity"] < entry["requested_quantity"]
    trade = thin["trades"][0]
    assert trade["quantity"] == entry["filled_quantity"] and trade["exit_fills"] >= 2
    events = [(r["order_id"], r["event"]) for r in thin["order_audit"]]
    first = events[0][0]
    assert [e for o, e in events if o == first] == ["SUBMIT", "SUBMITTED", "ACCEPTED", "FILLED", "CANCELED"]
    exit_fills = [r for r in fills(thin, "exit")][:trade["exit_fills"]]
    assert sum(f["quantity"] for f in exit_fills) == trade["quantity"]
    assert len({f["fill_label"] for f in exit_fills}) == len(exit_fills)        # worked across bars, never invented
    assert trade["exit_price"] == pytest.approx(sum(f["quantity"] * f["price"] for f in exit_fills) / trade["quantity"])
    rec = thin["reconciliation"]
    assert rec["breaks"] == [] and rec["final"]["status"] == "PASS"


# 10. commission and slippage ------------------------------------------------------------------
def test_commission_scheme_matches_r5_cost_model_and_cap_effect_is_explained(base, thin):
    scheme = NSEEquityIntradayCommission()
    for price in (99.95, 1375.45, 2400.0):
        for qty in (1, 29, 100, 2_000):
            assert scheme.getcommission(qty, price) == pytest.approx(leg_cost(price, qty, "BUY"), abs=1e-9)
            assert scheme.getcommission(-qty, price) == pytest.approx(leg_cost(price, qty, "SELL"), abs=1e-9)
    # Per-fill charging: two Rs 90k fills each hit the Rs 20 brokerage cap; one Rs 180k leg pays it once.
    assert leg_cost(1500.0, 60, "BUY") * 2 - leg_cost(1500.0, 120, "BUY") == pytest.approx(20.0, abs=1e-9)
    for report in (base, thin):
        assert fills(report)
        for row in fills(report):
            assert row["commission"] == pytest.approx(leg_cost(row["price"], row["quantity"], row["side"]), abs=1e-9)
        final = report["reconciliation"]["final"]["rows"]
        assert final["commission_vs_r5_fill_model"]["status"] == "PASS"
        assert final["commission_vs_r5_trade_ledger"]["status"] in ("PASS", "EXPLAINED")
    for row in base["entry_fills"]:
        assert row["slippage_vs_reference"] == pytest.approx(row["fill_price"] - row["reference_price"])


# 11. deterministic repeatability -------------------------------------------------------------
def test_deterministic_repeatability_in_process_and_across_hash_seeds(base, feed, tmp_path):
    data, nifty, vix = feed
    again = run_engine_c(data, nifty, vix, warmup=WARMUP)
    assert normalised(again) == normalised(base)
    out = tmp_path / "c.json"
    env = {**os.environ, "PYTHONHASHSEED": "7", "PYTHONPATH": str(ROOT)}
    subprocess.run([sys.executable, str(ROOT / "scripts/run_engine_c_backtrader.py"), "--synthetic-smoke",
                    "--bars", str(BARS), "--report", str(out)], cwd=ROOT, env=env, check=True, capture_output=True)
    cli = normalised(json.loads(out.read_text()))
    for key in ("orders", "trades", "decisions", "entry_fills", "order_audit", "backtrader_accounting", "data_identity"):
        assert cli[key] == normalised(base)[key], key


# 12. Engine A / Engine B protection ------------------------------------------------------------
PROTECTED = ["engine_b", "tests_engine_b", "scripts/run_engine_b_nautilus.py", "scripts/audit_single_symbol_infy.py",
             "scripts/capture_r5_golden_master.py", "golden_master", "revision2", "revision2_external", "revision5",
             "runtime", "market_data_loader.py", "gates_framework.py", "data", "tests", "tests_external"]


# feature/r5-parameter-authority deliberately changes the shared R5 code to move every native controller
# value into the registry (defaults identical; behaviour proven by scripts/r5_golden_master_local.py).
# The Engine C acceptance boundary still holds for everything else: the golden masters, the frozen
# data and the Engine A/B execution code may not change, and shared-code changes are confined to the
# declared parameter-authority surface.
PARAMETER_AUTHORITY_SURFACE = {
    "engine_b/runner.py", "scripts/run_engine_b_nautilus.py",
    "revision2_external/orchestrator.py", "revision5/ccpp_protection_cubicles.py", "revision5/ccpp_unified_plant.py",
    "revision5/controller_parameters.py", "revision5/dynamic_parameters.py", "revision5/governor.py",
    "revision5/governor_authority.py", "revision5/hrsg.py",
    "tests/r5_authority_harness.py", "tests/r5_remaining_literals.py", "tests/test_r5_parameter_authority.py",
    "tests/test_r5_parameter_authority_engines.py", "tests/test_canonical_parameter_registry.py",
    "tests/test_revision5_bb04_dynamic_parameterization.py", "tests/test_revision5_bb05_bb06_parameterization.py",
    "tests/test_revision5_bb07_bb08_parameterization.py", "tests/test_revision5_engine_applicability.py",
    "tests/test_revision5_three_controller_parameterization.py", "tests_engine_b/test_engine_b_parameter_authority.py",
}


def test_engine_a_and_b_code_golden_masters_and_data_untouched():
    base = subprocess.run(["git", "cat-file", "-t", BASE_COMMIT], cwd=ROOT, capture_output=True, text=True)
    if base.returncode != 0:
        pytest.skip("Engine B base commit not in this checkout")
    diff = subprocess.run(["git", "diff", "--name-only", BASE_COMMIT, "--", *PROTECTED], cwd=ROOT,
                          capture_output=True, text=True, check=True)
    changed = set(diff.stdout.split())
    assert not {p for p in changed if p.startswith(("golden_master/", "data/"))}
    assert changed <= PARAMETER_AUTHORITY_SURFACE, sorted(changed - PARAMETER_AUTHORITY_SURFACE)


def test_engine_c_is_independent_of_engine_a_and_b_execution_code():
    banned = ("engine_b", "nautilus_trader", "revision2_external.paper_execution", "kiteconnect",
              "revision2_external.broker_adapter_kite")
    for path in sorted((ROOT / "engine_c").glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                assert not name.startswith(banned), f"{path.name} imports {name}"
    from engine_c.orchestrator import _DisabledPaperBroker
    stub = _DisabledPaperBroker(0.0005)
    with pytest.raises(EngineCExecutionError):
        stub.place_order(symbol="INFY", side="BUY", quantity=1, order_type="MARKET", market_price=1.0)
    with pytest.raises(EngineCExecutionError):
        stub.get_position("INFY")
    from revision2_external.grid_context import SealedGridContextProvider
    data, nifty, vix, _ = load_synthetic(["INFY"], 120, WARMUP)
    engine = EngineCOrchestrator(["INFY"], grid_context_provider=SealedGridContextProvider(nifty, vix))
    with pytest.raises(PlantControlError):
        engine._assert_paper_plant_broker()


@pytest.mark.skipif(os.environ.get("ENGINE_C_SKIP_GOLDEN_MASTER") == "1", reason="golden master check skipped by env")
def test_engine_a_golden_master_still_passes():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/capture_r5_golden_master.py"), "--check"], cwd=ROOT,
                            capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert result.returncode == 0 and "GOLDEN MASTER: PASS" in result.stdout, result.stdout[-2000:]
