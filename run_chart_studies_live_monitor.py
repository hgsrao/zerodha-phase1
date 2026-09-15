"""
CHART STUDIES LIVE MONITOR — read-only, informational. 2026-08-24.

Watches the 4 symbols in today's V11 shadow plan (BAJFINANCE, LAURUSLABS,
SBIN, SUNPHARMA) using the 5-study composite rule documented in
CHART_STUDIES_SIGNAL_RULE_20260824.md (Ichimoku, Bollinger Bands 20/2,
Stochastic Momentum Index 10/3/3, session VWAP, Anchored VWAP from
2026-03-02), and prints/logs an event every time the composite crosses
into or out of majority agreement.

This is NOT wired to the V11 bridge in any way. No order-placement code
exists in this module or anything it imports. LIVE_TRADING_ENABLED is not
referenced anywhere in this path because there is no execution path here
to gate — same disclosure convention as r1c_live_observer.py.

Reuses (does not reimplement):
  - v34_bridge_kite_credentials.build_kite_client_from_env — same
    KITE_API_KEY/KITE_ACCESS_TOKEN convention as every other terminal.
  - kite_request_governor.KiteRequestGovernor — same cross-process rate
    coordination as the V11 bridge / R1-C observer / P02 live scan.
    KITE_RATE_GOVERNOR_DIR is required, fail-closed, and MUST be the same
    directory every other process sharing this Kite account uses.
  - r1c_live_kite_client.R1CLiveKiteClient — for instrument-token lookup
    and 1-minute bar fetches (its designed purpose). This module was NOT
    modified to add the daily-bar bootstrap fetch below — it is
    hash-frozen (R1C_LIVE_OBSERVER_HASH_MANIFEST_*), and this is an
    unrelated new tool, so the daily fetch is implemented standalone here
    instead of reopening that file for a tangential feature.
  - chart_studies_indicators — all the actual indicator math, plus (added
    2026-08-24) the confirmation-bar debounce and hard-stop threshold.
  - zerodha_delivery_costs.buy_cost/sell_cost (added 2026-08-24) — the same
    real Zerodha delivery cost schedule entry_gate_dry_run.py's
    compute_realized_hypothetical_pnl() already uses, not reimplemented.

Run: (same window pattern as every other live component this project uses)
    $env:KITE_API_KEY = "..."
    $env:KITE_ACCESS_TOKEN = "..."
    $env:KITE_RATE_GOVERNOR_DIR = "C:\\zerodha_data\\kite_rate_governor"
    python run_chart_studies_live_monitor.py
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

from kite_request_governor import HISTORICAL as GOV_HISTORICAL, KiteRequestGovernor
from r1c_live_kite_client import KiteResponseMalformedError, R1CLiveKiteClient
from v34_bridge_kite_credentials import build_kite_client_from_env
from zerodha_delivery_costs import buy_cost, sell_cost
import chart_studies_indicators as ind

PROJECT_ROOT = Path(__file__).parent
# BRITANNIA added 2026-08-24, owner request: P02 Pillar I/II's own
# `run_p02_live_paper.py --scan` flagged it live today (Pillar II /
# MEAN_REVERTING, close Rs.5313.00, would fill at next session's open) -
# the owner explicitly did not want a one-shot paper scan for Pillar I/II,
# wanted it watched continuously by the SAME 5-study composite engine as
# the V11 symbols below, reusing this monitor rather than building a
# parallel one. Unlike the 4 V11 symbols (fixed for the day by V11's own
# selection), Pillar I/II could flag a DIFFERENT symbol on a future day -
# this list is not auto-synced to that scan, so a future day's symbol
# would need to be added here by hand the same way.
SYMBOLS = ["BAJFINANCE", "LAURUSLABS", "SBIN", "SUNPHARMA", "BRITANNIA"]
ANCHOR_DATE = "2026-03-02"
BAR_RESOLUTION = "5min"
POLL_SECONDS = 60
BOOTSTRAP_MINUTE_HISTORY_DAYS = 3  # enough 1-min bars to warm up a 52-period Ichimoku at 5-min resolution
LEDGER_PATH = PROJECT_ROOT / "chart_studies_signal_ledger.jsonl"
SNAPSHOT_PATH = PROJECT_ROOT / "chart_studies_live_state.json"
PNL_LEDGER_PATH = PROJECT_ROOT / "chart_studies_pnl_ledger.jsonl"
MARKET_CLOSE = dtime(15, 30)  # same convention as r1c_live_observer.py's own MARKET_CLOSE

# Hypothetical notional per position, for the P&L statement ONLY - this
# monitor holds no real capital, places no real orders, and this number
# is not derived from any account balance. Split evenly across the 4
# symbols so there's a concrete Rupee number in the EOD statement instead
# of just a bare percentage - same INITIAL_CAPITAL-style convention
# P02_QUANT_LAB's own backtests use, disclosed here for the same reason.
TOTAL_NOTIONAL = 100000.0
NOTIONAL_PER_POSITION = TOTAL_NOTIONAL / len(SYMBOLS)


@dataclass
class PaperTrade:
    """One simulated round-trip - opened at an ENTRY event's bar close,
    closed at the matching EXIT event's bar close (or marked to the last
    available price if the market closes first). Entirely hypothetical -
    no real order, no real capital; see module docstring."""
    symbol: str
    entry_ts: str
    entry_price: float
    exit_ts: Optional[str] = None
    exit_price: Optional[float] = None
    status: str = "OPEN"  # OPEN | CLOSED | MARKED_AT_CLOSE

    def close(self, exit_ts: str, exit_price: float, status: str = "CLOSED"):
        self.exit_ts = exit_ts
        self.exit_price = exit_price
        self.status = status

    @property
    def return_pct(self) -> Optional[float]:
        if self.exit_price is None:
            return None
        return (self.exit_price / self.entry_price - 1.0) * 100.0

    @property
    def notional_pnl(self) -> Optional[float]:
        if self.exit_price is None:
            return None
        shares = NOTIONAL_PER_POSITION / self.entry_price
        return shares * (self.exit_price - self.entry_price)

    @property
    def net_notional_pnl(self) -> Optional[float]:
        """2026-08-24 addendum (see CHART_STUDIES_SIGNAL_RULE_20260824.md,
        "what can we make better"): notional_pnl above is raw price delta
        only. Real round-trips pay real Zerodha delivery costs on both
        legs - reuses zerodha_delivery_costs.buy_cost/sell_cost unchanged,
        the SAME real cost schedule entry_gate_dry_run.py's
        compute_realized_hypothetical_pnl() already uses, not
        reimplemented here."""
        if self.exit_price is None:
            return None
        return _net_of_costs(self.entry_price, self.exit_price, NOTIONAL_PER_POSITION)[1]


def _net_of_costs(entry_price: float, exit_price: float, notional_per_position: float):
    """Nets real Zerodha delivery buy+sell costs off a round-trip's gross
    notional P&L. Returns (gross, net, buy_fees, sell_fees). 2026-08-24
    addendum, see PaperTrade.net_notional_pnl above."""
    shares = notional_per_position / entry_price
    entry_notional = shares * entry_price
    exit_notional = shares * exit_price
    gross = shares * (exit_price - entry_price)
    buy_fees = buy_cost(entry_notional).total
    sell_fees = sell_cost(exit_notional).total
    net = gross - buy_fees - sell_fees
    return gross, net, buy_fees, sell_fees


def _trade_with_costs(t: "PaperTrade", mark_price: Optional[float]) -> dict:
    """Serializes a PaperTrade for the live snapshot/card, adding real-cost
    fields alongside the raw dataclass fields. For a CLOSED/MARKED_AT_CLOSE
    trade, costs use the actual exit_price; for a still-OPEN trade, costs
    are estimated against `mark_price` (the symbol's latest close) so the
    card can show a real-cost-aware UNREALIZED figure too, refreshed every
    poll - same idea as entry_gate_dry_run.py's compute_realized_hypothetical_pnl(),
    not reimplemented, just applied to this card's own trade records."""
    d = asdict(t)
    ref_price = t.exit_price if t.exit_price is not None else mark_price
    if ref_price is not None:
        gross, net, buy_fees, sell_fees = _net_of_costs(t.entry_price, ref_price, NOTIONAL_PER_POSITION)
        shares = NOTIONAL_PER_POSITION / t.entry_price
        entry_notional = shares * t.entry_price
        d["gross_notional_pnl"] = gross
        d["buy_cost"] = buy_fees
        d["sell_cost"] = sell_fees
        d["net_notional_pnl"] = net
        d["net_return_pct"] = (net / entry_notional) * 100.0
        d["cost_mark_price"] = ref_price
    else:
        d["gross_notional_pnl"] = None
        d["buy_cost"] = None
        d["sell_cost"] = None
        d["net_notional_pnl"] = None
        d["net_return_pct"] = None
        d["cost_mark_price"] = None
    return d


def _fetch_daily_bars_for_anchor(kite, governor, token, from_dt, to_dt):
    """One-time-at-startup daily-interval fetch to bootstrap Anchored
    VWAP's running_pv/running_vol without re-fetching months of 1-minute
    bars. Mirrors R1CLiveKiteClient.get_minute_bars's own governance and
    malformed-response-checking pattern exactly, kept standalone here
    (see module docstring) rather than editing that frozen file."""
    if governor is not None:
        governor.acquire(GOV_HISTORICAL)
    rows = kite.historical_data(token, from_dt, to_dt, "day")
    if not isinstance(rows, list):
        raise KiteResponseMalformedError(f"kite.historical_data(token={token}, interval=day): expected a list, got {rows!r}.")
    pv = 0.0
    vol = 0.0
    for row in rows:
        if not isinstance(row, dict) or not all(k in row for k in ("high", "low", "close", "volume")):
            raise KiteResponseMalformedError(f"kite.historical_data(token={token}, interval=day): malformed row {row!r}.")
        typical = (float(row["high"]) + float(row["low"]) + float(row["close"])) / 3
        v = float(row["volume"] or 0.0)
        pv += typical * v
        vol += v
    return pv, vol


def _resample_to_5min(bars_1min: list) -> pd.DataFrame:
    if not bars_1min:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "TradingDay"])
    df = pd.DataFrame(bars_1min)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    agg = df.resample(BAR_RESOLUTION, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna(subset=["open"])
    agg = agg.reset_index()
    agg["TradingDay"] = agg["timestamp"].dt.normalize()
    return agg


def _completed_bars_only(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """A 5-minute bucket is only "closed" once now has passed its end —
    evaluating a still-forming bar would be look-ahead-shaped (the bar's
    high/low/close can still change), same discipline as p02_core's own
    "decide off a fully closed bar" rule."""
    freq = pd.Timedelta(BAR_RESOLUTION)
    return df[df["timestamp"] + freq <= pd.Timestamp(now)]


class SymbolWatcher:
    def __init__(self, symbol, client: R1CLiveKiteClient, kite, governor, running_pv, running_vol):
        self.symbol = symbol
        self.client = client
        self.kite = kite
        self.governor = governor
        self.running_pv = running_pv
        self.running_vol = running_vol
        self.minute_bars: list = []
        self.state = ind.SymbolSignalState()
        self.last_processed_bar_ts = None
        self.latest_reads = {}
        self.latest_score = None
        self.latest_close: Optional[float] = None
        self.projection: Optional[str] = None  # GREEN | AMBER | RED, see ind.project_state
        self.pending_signal: Optional[dict] = None  # a SIGNAL awaiting next-bar-open execution
        self.trades: list = []
        self.open_trade: Optional[PaperTrade] = None

    def bootstrap(self, now: datetime):
        from_dt = now - timedelta(days=BOOTSTRAP_MINUTE_HISTORY_DAYS)
        self.minute_bars = self.client.get_minute_bars(self.symbol, from_dt, now)
        print(f"  {self.symbol}: bootstrapped {len(self.minute_bars)} 1-min bars "
              f"({BOOTSTRAP_MINUTE_HISTORY_DAYS}-day lookback for indicator warm-up)")

    def poll(self, now: datetime):
        from_dt = (pd.to_datetime(self.minute_bars[-1]["timestamp"]) if self.minute_bars else now - timedelta(minutes=10))
        new_bars = self.client.get_minute_bars(self.symbol, from_dt.to_pydatetime() if hasattr(from_dt, "to_pydatetime") else from_dt, now)
        if new_bars:
            existing_ts = {b["timestamp"] for b in self.minute_bars}
            self.minute_bars.extend(b for b in new_bars if b["timestamp"] not in existing_ts)

        bars5 = _resample_to_5min(self.minute_bars)
        if bars5.empty:
            return None
        closed = _completed_bars_only(bars5, now)
        if closed.empty:
            return None

        computed = ind.compute_all_indicators(
            closed, anchor_ts=ANCHOR_DATE, running_pv=self.running_pv, running_vol=self.running_vol,
        )
        last_row = computed.iloc[-1]
        last_ts = last_row["timestamp"]

        # Persist the full 5-min price history seen so far - overwritten
        # (not appended) every poll since `closed` already holds the whole
        # accumulated series from bootstrap onward; this is what makes a
        # real time-vs-price chart possible on the card, using the SAME
        # bars the signals themselves are computed from (not a different
        # process's separately-fetched data).
        closed[["timestamp", "open", "high", "low", "close", "volume"]].to_csv(
            PROJECT_ROOT / f"chart_studies_bars_{self.symbol}.csv", index=False,
        )

        reads = ind.classify_row(last_row)
        self.latest_reads = reads
        self.latest_score = reads["score"]
        self.latest_close = float(last_row["close"])
        self.projection = ind.project_state(reads["score"])  # 2026-08-24: GREEN/AMBER/RED, see chart_studies_indicators.py

        # 2026-08-24 addendum: resolve any SIGNAL still waiting for its
        # next-bar-open fill BEFORE anything else. Safe to check every
        # poll (idempotent - only resolves once a real new bar exists
        # after the decision bar) and independent of the dedup guard
        # below, since the decision bar and the fill bar are never the
        # same bar. See ind.evaluate_fill()/_try_execute_pending_fill().
        fill_event = self._try_execute_pending_fill(closed)
        if fill_event is not None:
            return fill_event

        if self.last_processed_bar_ts == last_ts:
            return None  # already evaluated this closed bar - avoid redundant state-machine calls
        self.last_processed_bar_ts = last_ts

        # 2026-08-24 addendum: hard stop checked BEFORE the composite state
        # machine, and independently of it - see ind.HARD_STOP_PCT for
        # rationale. Fills IMMEDIATELY (this bar's close, approximating a
        # stop TOUCH) rather than queueing for next-bar-open - a stop is a
        # protective trigger, not an ordinary projection-driven decision;
        # see CHART_STUDIES_SIGNAL_RULE_20260824.md's exit-priority list.
        if self.open_trade is not None and ind.is_hard_stop_breached(self.open_trade.entry_price, self.latest_close):
            self.state.state = "FLAT"
            self.state.streak = 0
            self.state.last_score = reads["score"]
            event = {
                "timestamp": str(last_ts), "direction": "EXIT", "score": reads["score"],
                "reads": dict(reads), "reason": "HARD_STOP", "stage": "FILL",
                "stop_price": ind.hard_stop_price(self.open_trade.entry_price),
                "symbol": self.symbol, "price": self.latest_close,
            }
            self.state.events.append(event)
            self.open_trade.close(str(last_ts), self.latest_close)
            self.open_trade = None
            return event

        # 2026-08-24 addendum: an ENTRY/EXIT firing here is now only a
        # SIGNAL (the composite's own read, frozen at this bar's close) -
        # NOT yet a fill. Queued in self.pending_signal and executed by
        # _try_execute_pending_fill() on a later poll, at the next closed
        # bar's own open. "Use only completed candles... enter at the next
        # eligible five-minute bar open" - report Section 4.
        decision_event = self.state.update(last_ts, reads["score"], reads)
        if decision_event is not None:
            decision_event["reason"] = "COMPOSITE"
            decision_event["stage"] = "SIGNAL"
            decision_event["symbol"] = self.symbol
            decision_event["price"] = self.latest_close  # the DECISION bar's close, not a fill
            self.pending_signal = dict(decision_event)
        return decision_event

    def _try_execute_pending_fill(self, closed: pd.DataFrame) -> Optional[dict]:
        """2026-08-24 addendum. Returns a FILL or ABSTAIN event once the
        first closed bar strictly after the queued signal's own bar
        exists, else None (still waiting). See
        ind.evaluate_fill()/ABSTAIN_SLIPPAGE_PCT for the ABSTAIN rule."""
        if self.pending_signal is None:
            return None
        decision_ts = pd.Timestamp(self.pending_signal["timestamp"])
        after = closed[closed["timestamp"] > decision_ts]
        if after.empty:
            return None  # the next bar hasn't closed yet - keep waiting
        fill_row = after.iloc[0]
        fill_open = float(fill_row["open"])
        decision_price = self.pending_signal["price"]
        verdict, slippage_pct = ind.evaluate_fill(decision_price, fill_open)
        signal = self.pending_signal
        self.pending_signal = None

        base = {
            "timestamp": str(fill_row["timestamp"]), "symbol": self.symbol,
            "score": signal["score"], "reads": signal["reads"],
            "reason": signal.get("reason", "COMPOSITE"),
            "decision_price": decision_price, "slippage_pct": slippage_pct,
        }
        if verdict == "ABSTAIN":
            base.update({
                "direction": "ABSTAIN", "stage": "ABSTAIN",
                "abstained_direction": signal["direction"],
                "price": fill_open,  # what it WOULD have filled at
            })
            self.state.events.append(base)  # 2026-08-24: the outcome, not just the signal, must be durable/visible
            return base

        base.update({"direction": signal["direction"], "stage": "FILL", "price": fill_open})
        if signal["direction"] == "ENTRY":
            self.open_trade = PaperTrade(symbol=self.symbol, entry_ts=str(fill_row["timestamp"]), entry_price=fill_open)
            self.trades.append(self.open_trade)
        elif signal["direction"] == "EXIT" and self.open_trade is not None:
            self.open_trade.close(str(fill_row["timestamp"]), fill_open)
            self.open_trade = None
        self.state.events.append(base)  # 2026-08-24: FILL recorded alongside the earlier SIGNAL, same list
        return base

    def mark_open_trade_at_close(self, ts: str):
        """Called once at end-of-day reporting - anything still OPEN gets
        marked to the last available price, not silently excluded from
        the P&L statement."""
        if self.open_trade is not None and self.open_trade.status == "OPEN" and self.latest_close is not None:
            self.open_trade.close(ts, self.latest_close, status="MARKED_AT_CLOSE")


def _append_ledger(event: dict):
    with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def _write_snapshot(watchers: dict, now: datetime):
    snapshot = {
        "generated_at": now.isoformat(),
        "rule_doc": "CHART_STUDIES_SIGNAL_RULE_20260824.md",
        "notional_per_position": NOTIONAL_PER_POSITION,
        "symbols": {
            sym: {
                "state": w.state.state,
                "score": w.latest_score,
                "projection": w.projection,  # 2026-08-24: GREEN | AMBER | RED
                "reads": {k: v for k, v in w.latest_reads.items() if k != "score"},
                "recent_events": w.state.events[-10:],
                "trades": [_trade_with_costs(t, w.latest_close) for t in w.trades],
            }
            for sym, w in watchers.items()
        },
    }
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")


def generate_eod_pnl_statement(watchers: dict, now: datetime) -> str:
    """Hypothetical, notional-based P&L across every trade the composite
    rule opened/closed today. NOT real capital, NOT a real account
    statement - see TOTAL_NOTIONAL/NOTIONAL_PER_POSITION above."""
    lines = []
    lines.append("=" * 80)
    lines.append(f"CHART STUDIES - END OF DAY P&L STATEMENT - {now.date().isoformat()}")
    lines.append("Hypothetical only: notional capital, no real order ever placed.")
    lines.append(f"Assumed notional: Rs.{TOTAL_NOTIONAL:,.0f} total, "
                  f"Rs.{NOTIONAL_PER_POSITION:,.0f} per position ({len(watchers)} symbols)")
    lines.append("=" * 80)

    all_trades = []
    for sym, w in watchers.items():
        w.mark_open_trade_at_close(now.isoformat())
        all_trades.extend(w.trades)

    if not all_trades:
        lines.append("\nNo ENTRY signals fired today - nothing to report.")
        lines.append("=" * 80)
        return "\n".join(lines)

    total_pnl = sum(t.notional_pnl for t in all_trades if t.notional_pnl is not None)
    closed = [t for t in all_trades if t.status == "CLOSED"]
    marked = [t for t in all_trades if t.status == "MARKED_AT_CLOSE"]

    lines.append(f"\nTotal trades: {len(all_trades)}  "
                 f"(closed on an EXIT signal: {len(closed)}, still open at close: {len(marked)})")
    lines.append(f"Gross notional P&L: Rs.{total_pnl:,.2f}  ({total_pnl / TOTAL_NOTIONAL * 100:+.2f}% of assumed notional)")
    if closed:
        win_rate = sum(1 for t in closed if t.notional_pnl > 0) / len(closed) * 100
        lines.append(f"Win rate (closed trades only, gross): {win_rate:.1f}%")

    # 2026-08-24 addendum: real Zerodha delivery costs netted off, alongside
    # (not replacing) the gross lines above - see PaperTrade.net_notional_pnl.
    net_total = sum(t.net_notional_pnl for t in all_trades if t.net_notional_pnl is not None)
    lines.append(f"Net of real Zerodha costs: Rs.{net_total:,.2f}  ({net_total / TOTAL_NOTIONAL * 100:+.2f}% of assumed notional)")
    if closed:
        net_win_rate = sum(1 for t in closed if (t.net_notional_pnl or 0) > 0) / len(closed) * 100
        lines.append(f"Win rate (closed trades only, net of costs): {net_win_rate:.1f}%")

    lines.append("\nPer-trade detail:")
    lines.append(f"{'Symbol':<12} {'Entry TS':<20} {'Entry Px':>10} {'Exit TS':<20} {'Exit Px':>10} {'Return %':>9} {'Notional PnL':>13} {'Status':<15}")
    for t in all_trades:
        lines.append(
            f"{t.symbol:<12} {t.entry_ts[:19]:<20} {t.entry_price:>10.2f} "
            f"{(t.exit_ts or '')[:19]:<20} {(t.exit_price or 0):>10.2f} "
            f"{(t.return_pct or 0):>8.2f}% {(t.notional_pnl or 0):>13.2f} {t.status:<15}"
        )
    lines.append("=" * 80)
    return "\n".join(lines)


def _append_pnl_ledger(watchers: dict, now: datetime, statement_text: str):
    record = {
        "date": now.date().isoformat(),
        "generated_at": now.isoformat(),
        "notional_per_position": NOTIONAL_PER_POSITION,
        "trades": [asdict(t) for w in watchers.values() for t in w.trades],
        "statement_text": statement_text,
    }
    with open(PNL_LEDGER_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def main():
    print("=" * 80)
    print("CHART STUDIES LIVE MONITOR - read-only, informational, NOT trade advice.")
    print("No order-placement code exists in this module or anything it imports.")
    print(f"Symbols: {SYMBOLS}")
    print(f"Rule: {ENTRY_THRESHOLD_DOC}")
    print(f"Ledger -> {LEDGER_PATH}")
    print(f"Snapshot -> {SNAPSHOT_PATH}")
    print("=" * 80)

    kite = build_kite_client_from_env()
    governor_dir = _require_governor_dir()
    governor = KiteRequestGovernor(state_dir=governor_dir)
    client = R1CLiveKiteClient(kite, governor=governor)

    now = datetime.now()
    watchers = {}
    for sym in SYMBOLS:
        print(f"\nBootstrapping {sym}...")
        token = client.instrument_token(sym)
        anchor_dt = datetime.fromisoformat(ANCHOR_DATE)
        # Bootstrap the Anchored VWAP base through YESTERDAY only - today's
        # own bars are added live below, never double-counted.
        yesterday_close = datetime.combine(now.date(), datetime.min.time()) - timedelta(seconds=1)
        running_pv, running_vol = _fetch_daily_bars_for_anchor(kite, governor, token, anchor_dt, yesterday_close)
        print(f"  Anchored VWAP base (from {ANCHOR_DATE} through yesterday): "
              f"cumulative volume={running_vol:,.0f}")
        w = SymbolWatcher(sym, client, kite, governor, running_pv, running_vol)
        w.bootstrap(now)
        watchers[sym] = w

    print(f"\nStarting live poll loop (every {POLL_SECONDS}s)... Ctrl+C to stop.\n")
    eod_reported_for = None  # date - guards against re-printing the statement every poll past close
    try:
        while True:
            now = datetime.now()
            for sym, w in watchers.items():
                event = w.poll(now)
                if event is not None:
                    print(f"[{now.isoformat()}] {sym}: {event['direction']} [{event.get('stage', 'FILL')}] "
                          f"(price={event['price']:.2f}, score={event['score']}) "
                          f"reads={ {k: v for k, v in event['reads'].items() if k != 'score'} }")
                    _append_ledger(event)
            _write_snapshot(watchers, now)
            status = "  ".join(f"{s}:{w.state.state}({w.latest_score})[{w.projection}]" for s, w in watchers.items())
            print(f"[{now.isoformat()}] poll complete - {status}")

            if now.time() >= MARKET_CLOSE and eod_reported_for != now.date():
                statement = generate_eod_pnl_statement(watchers, now)
                print("\n" + statement + "\n")
                _append_pnl_ledger(watchers, now, statement)
                _write_snapshot(watchers, now)  # re-write with trades now marked at close
                eod_reported_for = now.date()

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\n[SYSTEM] Chart studies monitor stopped by user.")
        if eod_reported_for is None:
            print("(Market hadn't closed yet - run generate_eod_pnl_statement() "
                  "again after 15:30 IST, or restart the monitor and let it run through close, "
                  "for a full end-of-day statement.)")


def _require_governor_dir():
    import os
    val = os.getenv("KITE_RATE_GOVERNOR_DIR")
    if not val:
        raise RuntimeError(
            "FAIL_CLOSED: KITE_RATE_GOVERNOR_DIR environment variable is missing. "
            "Must be set to the SAME directory every other process sharing this "
            "Kite account uses (e.g. C:\\zerodha_data\\kite_rate_governor)."
        )
    return val


ENTRY_THRESHOLD_DOC = (
    f"ENTRY when >= {ind.ENTRY_THRESHOLD} of {len(ind.VOTING_STUDIES)} voting studies net-bullish "
    f"(anchored_vwap displayed, not voted); EXIT when composite falls to <= {ind.EXIT_THRESHOLD}; "
    f"both require {ind.CONFIRMATION_BARS} consecutive confirming closed bars, THEN fill at the "
    f"NEXT closed bar's own open (ABSTAIN if slippage > {ind.ABSTAIN_SLIPPAGE_PCT:.1%}); "
    f"plus an independent {ind.HARD_STOP_PCT:.1%} hard-stop backstop, filled immediately; "
    f"GREEN/AMBER/RED projection per bar - see CHART_STUDIES_SIGNAL_RULE_20260824.md"
)


if __name__ == "__main__":
    main()
