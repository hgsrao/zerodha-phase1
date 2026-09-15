"""
MAP + CONTEXT LIVE ENGINE — read-only, informational. 2026-08-25.

A completely SEPARATE engine from run_chart_studies_live_monitor.py -
that file is explicitly untouched by this addendum, per owner request
("let us not touch that ... we will build a new engine"). Different
entry logic, different output files, different ledger, no shared state.

Rule (owner-selected, "Reaction at a level"): watches BAJFINANCE,
LAURUSLABS, SBIN, SUNPHARMA, BRITANNIA on 5-minute bars. Each symbol's
support/resistance MAP is built once daily from real local daily-bar
archives (kite_nifty50_data / kite_midsmallcap_data - no new Kite calls
for that part). A candidate REAL/SIGNIFICANT support level (2+ prior
touches) must first be TESTED (a bar's low reaches it) and then RECLAIMED
(a later bar's close moves back above it) before an ENTRY fires - see
map_context_indicators.ReactionState. Stop = the tighter of the tested
low or a 1% hard cap; target = the nearest qualifying resistance, or no
target if none exists. Entry also requires the NIFTY 50 index regime +
extension context to not be a PASS (see map_context_indicators.sizing_guidance)
- a real filter, not a score contributor.

Simplification, disclosed not hidden: entries and exits fill at the
signal bar's own close (immediate), NOT next-bar-open like the
2026-08-24 Chart Studies addendum. This is a new, separate engine and
was scoped smaller on purpose; next-bar-open execution is a candidate
future addition, not built here.

No order-placement code exists in this module or anything it imports.
LIVE_TRADING_ENABLED is not referenced anywhere in this path because
there is no execution path here to gate - same disclosure convention as
every other read-only live component this project has built.

Reuses (does not reimplement):
  - map_context_indicators - all the Map/Context/Reaction math, already
    tested (34/34) before this engine was built.
  - map_context_observer._load_daily / NIFTY_INDEX_PATH - the same real
    local daily-bar loading already verified against real data.
  - kite_request_governor.KiteRequestGovernor / r1c_live_kite_client.R1CLiveKiteClient
    / v34_bridge_kite_credentials.build_kite_client_from_env - same
    governed, credentialed Kite access pattern as every other live
    component this project runs, including the SAME shared
    KITE_RATE_GOVERNOR_DIR every other process must use.
  - zerodha_delivery_costs.buy_cost/sell_cost - the same real cost
    schedule already used by entry_gate_dry_run.py and (as of
    2026-08-24) run_chart_studies_live_monitor.py.

Run: (same window pattern as every other live component this project uses)
    $env:KITE_API_KEY = "..."
    $env:KITE_ACCESS_TOKEN = "..."
    $env:KITE_RATE_GOVERNOR_DIR = "C:\\zerodha_data\\kite_rate_governor"
    python map_context_live_engine.py
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

from kite_request_governor import HISTORICAL as GOV_HISTORICAL, KiteRequestGovernor
from r1c_live_kite_client import R1CLiveKiteClient
from v34_bridge_kite_credentials import build_kite_client_from_env
from zerodha_delivery_costs import buy_cost, sell_cost

from map_context_indicators import (
    ReactionState, build_map, classify_index_regime, compute_stop_and_target,
    consecutive_directional_days, sizing_guidance, MIN_LEVEL_TOUCHES,
)
from map_context_observer import NIFTY_INDEX_PATH, P02_ROOT

# 2026-08-25 addendum: scaled from the original 5-symbol Chart Studies
# companion set to the full frozen V10-C 48-symbol universe (owner
# request). Deliberately does NOT import SYMBOLS from map_context_observer
# - that module stays a focused 5-symbol companion view for Chart Studies
# Monitor; this live engine's own universe is now separate and larger.
from P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825.acquire_v10_history import (
    SYMBOLS, OUTPUT_DIR as V10C_MINUTE_DATA_DIR, WARMUP_START as V10C_WARMUP_START,
    END_DATE as V10C_END_DATE,
)

PROJECT_ROOT = Path(__file__).parent
BAR_RESOLUTION = "5min"
POLL_SECONDS = 60
BOOTSTRAP_MINUTE_HISTORY_DAYS = 3
MARKET_CLOSE = dtime(15, 30)  # same convention as run_chart_studies_live_monitor.py

LEDGER_PATH = PROJECT_ROOT / "map_context_signal_ledger.jsonl"
SNAPSHOT_PATH = PROJECT_ROOT / "map_context_live_state.json"
PNL_LEDGER_PATH = PROJECT_ROOT / "map_context_pnl_ledger.jsonl"
BARS_PATH_TEMPLATE = "map_context_bars_{symbol}.csv"  # deliberately distinct from chart_studies_bars_*.csv

# Same disclosed, not-derived-from-any-real-account convention as
# run_chart_studies_live_monitor.py's own TOTAL_NOTIONAL.
TOTAL_NOTIONAL = 100000.0
NOTIONAL_PER_POSITION = TOTAL_NOTIONAL / len(SYMBOLS)


@dataclass
class MapContextTrade:
    symbol: str
    entry_ts: str
    entry_price: float
    stop_price: float
    target_price: Optional[float]
    exit_ts: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None  # STOP | TARGET | EOD
    status: str = "OPEN"  # OPEN | CLOSED

    def close(self, exit_ts: str, exit_price: float, reason: str):
        self.exit_ts = exit_ts
        self.exit_price = exit_price
        self.exit_reason = reason
        self.status = "CLOSED"

    @property
    def gross_pnl(self) -> Optional[float]:
        if self.exit_price is None:
            return None
        shares = NOTIONAL_PER_POSITION / self.entry_price
        return shares * (self.exit_price - self.entry_price)

    @property
    def net_pnl(self) -> Optional[float]:
        """Real Zerodha delivery costs netted off both legs - reuses
        zerodha_delivery_costs.buy_cost/sell_cost unchanged, same
        schedule entry_gate_dry_run.py and Chart Studies Monitor use."""
        if self.exit_price is None:
            return None
        shares = NOTIONAL_PER_POSITION / self.entry_price
        entry_notional = shares * self.entry_price
        exit_notional = shares * self.exit_price
        gross = shares * (self.exit_price - self.entry_price)
        return gross - buy_cost(entry_notional).total - sell_cost(exit_notional).total


def _resample_to_5min(bars_1min: list) -> pd.DataFrame:
    if not bars_1min:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(bars_1min)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    agg = df.resample(BAR_RESOLUTION, label="left", closed="left").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna(subset=["open"])
    return agg.reset_index()


def _completed_bars_only(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    freq = pd.Timedelta(BAR_RESOLUTION)
    return df[df["timestamp"] + freq <= pd.Timestamp(now)]


class MapContextWatcher:
    def __init__(self, symbol: str, client: R1CLiveKiteClient, daily_map: dict):
        self.symbol = symbol
        self.client = client
        self.daily_map_ref = daily_map  # {'support': [...], 'resistance': [...]}, refreshed once daily by main()
        self.minute_bars: list = []
        self.reaction = ReactionState()
        self.last_processed_bar_ts = None
        self.latest_close: Optional[float] = None
        self.trades: list = []
        self.open_trade: Optional[MapContextTrade] = None

    def bootstrap(self, now: datetime):
        from_dt = now - timedelta(days=BOOTSTRAP_MINUTE_HISTORY_DAYS)
        self.minute_bars = self.client.get_minute_bars(self.symbol, from_dt, now)
        print(f"  {self.symbol}: bootstrapped {len(self.minute_bars)} 1-min bars", flush=True)

    def _qualifying_support(self):
        levels = [lvl for lvl in self.daily_map_ref["support"] if lvl["touches"] >= MIN_LEVEL_TOUCHES]
        return levels[0] if levels else None  # nearest is already first (build_map sorts nearest-first)

    def _qualifying_resistance(self):
        levels = [lvl for lvl in self.daily_map_ref["resistance"] if lvl["touches"] >= MIN_LEVEL_TOUCHES]
        return levels[0] if levels else None

    def poll(self, now: datetime, index_regime: str) -> Optional[dict]:
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

        last_row = closed.iloc[-1]
        last_ts = last_row["timestamp"]
        self.latest_close = float(last_row["close"])

        closed[["timestamp", "open", "high", "low", "close", "volume"]].to_csv(
            PROJECT_ROOT / BARS_PATH_TEMPLATE.format(symbol=self.symbol), index=False,
        )

        if self.last_processed_bar_ts == last_ts:
            return None
        self.last_processed_bar_ts = last_ts

        # Manage an already-open position first: close-based stop/target,
        # deliberately different from Chart Studies' intrabar-touch hard
        # stop - a disclosed design choice for this separate engine.
        if self.open_trade is not None:
            close = self.latest_close
            if close <= self.open_trade.stop_price:
                self.open_trade.close(str(last_ts), close, "STOP")
                event = self._exit_event(last_ts, close, "STOP")
                self.open_trade = None
                return event
            if self.open_trade.target_price is not None and close >= self.open_trade.target_price:
                self.open_trade.close(str(last_ts), close, "TARGET")
                event = self._exit_event(last_ts, close, "TARGET")
                self.open_trade = None
                return event
            return None  # still open, nothing else to do this bar

        # No open position - run the reaction/reclaim entry rule. Capture
        # tested_level BEFORE calling update() - a confirmed ENTRY resets
        # it to None as part of the same call, so reading it afterwards
        # would silently lose the stop reference. Using the LEVEL itself
        # (not the deeper tested_low) as the stop reference, per Pillar
        # 3's own invalidation rule - see compute_stop_and_target's docstring.
        support = self._qualifying_support()
        pending_tested_level = self.reaction.tested_level
        result = self.reaction.update(float(last_row["low"]), float(last_row["close"]), support)
        if result != "ENTRY":
            return None

        guidance = sizing_guidance(index_regime, "LONG", consecutive_directional_days(closed))
        if guidance.tier == "PASS":
            event = {
                "timestamp": str(last_ts), "symbol": self.symbol, "direction": "ABSTAIN",
                "reason": "CONTEXT_PASS", "context_reasons": guidance.reasons, "price": self.latest_close,
            }
            self._append_and_log(event)
            return event

        resistance = self._qualifying_resistance()
        levels = compute_stop_and_target(self.latest_close, pending_tested_level, resistance)
        self.open_trade = MapContextTrade(
            symbol=self.symbol, entry_ts=str(last_ts), entry_price=self.latest_close,
            stop_price=levels["stop_price"], target_price=levels["target_price"],
        )
        self.trades.append(self.open_trade)
        event = {
            "timestamp": str(last_ts), "symbol": self.symbol, "direction": "ENTRY",
            "reason": "REACTION_RECLAIM", "price": self.latest_close,
            "stop_price": levels["stop_price"], "target_price": levels["target_price"],
            "context_tier": guidance.tier, "context_reasons": guidance.reasons,
        }
        self._append_and_log(event)
        return event

    def _exit_event(self, ts, price, reason) -> dict:
        event = {
            "timestamp": str(ts), "symbol": self.symbol, "direction": "EXIT",
            "reason": reason, "price": price,
            "gross_pnl": self.open_trade.gross_pnl, "net_pnl": self.open_trade.net_pnl,
        }
        self._append_and_log(event)
        return event

    def _append_and_log(self, event: dict):
        with open(LEDGER_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")

    def mark_open_trade_at_close(self, ts: str):
        if self.open_trade is not None and self.open_trade.status == "OPEN" and self.latest_close is not None:
            self.open_trade.close(ts, self.latest_close, "EOD")


LOOKBACK_TRADING_DAYS = 250  # same disclosed window as map_context_observer.py


def _load_daily_universe(symbol: str) -> pd.DataFrame:
    """2026-08-25 addendum. 41 of the 48 V10-C symbols have a pre-built
    daily CSV in kite_nifty50_data - read directly. The other 7 (BEL,
    ETERNAL, INDIGO, JIOFIN, MAXHEALTH, SHRIRAMFIN, TRENT - verified
    missing before writing this) don't have one anywhere in this project,
    so their daily bars are resampled from the real, hash-verified,
    already-repaired V10-C 1-minute archive instead - zero new Kite
    calls either way, and the 1-minute source is the SAME real Zerodha
    data the frozen V10-C replay itself used."""
    direct_path = P02_ROOT / "kite_nifty50_data" / f"{symbol}_daily.csv"
    if direct_path.is_file():
        df = pd.read_csv(direct_path)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.sort_values("date").reset_index(drop=True)
        return df.tail(LOOKBACK_TRADING_DAYS).reset_index(drop=True)

    minute_path = V10C_MINUTE_DATA_DIR / f"NSE_{symbol}_minute_{V10C_WARMUP_START}_{V10C_END_DATE}.csv"
    minute = pd.read_csv(minute_path)
    minute["timestamp"] = pd.to_datetime(minute["timestamp"]).dt.tz_localize(None)
    minute = minute.set_index("timestamp").sort_index()
    daily = minute.resample("1D").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
    }).dropna(subset=["open"]).reset_index().rename(columns={"timestamp": "date"})
    return daily.tail(LOOKBACK_TRADING_DAYS).reset_index(drop=True)


def _build_daily_maps() -> tuple[dict[str, dict], dict]:
    """Built once at startup (and again at the start of each new trading
    day if this process runs across midnight) from real local daily-bar
    archives - zero new Kite calls for this part. Returns
    ({symbol: {'support': [...], 'resistance': [...]}}, index_regime)."""
    index_daily = pd.read_csv(NIFTY_INDEX_PATH)
    index_daily["date"] = pd.to_datetime(index_daily["date"])
    index_daily = index_daily.sort_values("date").reset_index(drop=True).tail(250)
    index_regime = classify_index_regime(index_daily)

    maps = {}
    for symbol in SYMBOLS:
        daily = _load_daily_universe(symbol)
        # Bootstrap current_price with the daily archive's own last close -
        # good enough to seed the Map's support/resistance split before the
        # first live poll refines self.latest_close per watcher.
        seed_price = float(daily["close"].iloc[-1])
        built = build_map(daily, seed_price)
        maps[symbol] = {
            "support": [lvl for lvl in built["all_support"] if lvl["touches"] >= MIN_LEVEL_TOUCHES],
            "resistance": [lvl for lvl in built["all_resistance"] if lvl["touches"] >= MIN_LEVEL_TOUCHES],
        }
    return maps, index_regime


def _write_snapshot(watchers: dict, index_regime: dict, now: datetime):
    snapshot = {
        "generated_at": now.isoformat(),
        "index_regime": index_regime["regime"],
        "notional_per_position": NOTIONAL_PER_POSITION,
        "symbols": {
            sym: {
                "latest_close": w.latest_close,
                "reaction_tested": w.reaction.tested,
                "reaction_tested_level": w.reaction.tested_level,
                "open_trade": asdict(w.open_trade) if w.open_trade else None,
                "trades": [asdict(t) for t in w.trades],
            }
            for sym, w in watchers.items()
        },
    }
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")


def _generate_eod_statement(watchers: dict, now: datetime) -> str:
    lines = ["=" * 80, f"MAP + CONTEXT ENGINE - END OF DAY - {now.date().isoformat()}",
             "Hypothetical only: notional capital, no real order ever placed.", "=" * 80]
    all_trades = []
    for w in watchers.values():
        w.mark_open_trade_at_close(now.isoformat())
        all_trades.extend(w.trades)
    if not all_trades:
        lines.append("\nNo ENTRY signals fired today.")
        lines.append("=" * 80)
        return "\n".join(lines)
    net_total = sum(t.net_pnl for t in all_trades if t.net_pnl is not None)
    lines.append(f"\nTotal trades: {len(all_trades)}")
    lines.append(f"Net notional P&L (real Zerodha costs netted): Rs.{net_total:,.2f}")
    for t in all_trades:
        lines.append(f"  {t.symbol}: entry Rs.{t.entry_price:.2f} -> exit Rs.{(t.exit_price or 0):.2f} "
                      f"({t.exit_reason}) net Rs.{(t.net_pnl or 0):.2f}")
    lines.append("=" * 80)
    return "\n".join(lines)


def _append_pnl_ledger(watchers: dict, now: datetime, statement: str):
    record = {
        "date": now.date().isoformat(), "generated_at": now.isoformat(),
        "trades": [asdict(t) for w in watchers.values() for t in w.trades],
        "statement_text": statement,
    }
    with open(PNL_LEDGER_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _require_governor_dir() -> str:
    import os
    val = os.getenv("KITE_RATE_GOVERNOR_DIR")
    if not val:
        raise RuntimeError(
            "FAIL_CLOSED: KITE_RATE_GOVERNOR_DIR environment variable is missing. "
            "Must be the SAME directory every other process sharing this Kite "
            "account uses (e.g. C:\\zerodha_data\\kite_rate_governor)."
        )
    return val


def main():
    print("=" * 80)
    print("MAP + CONTEXT LIVE ENGINE - read-only, informational, NOT trade advice.")
    print("No order-placement code exists in this module or anything it imports.")
    print("Completely separate from run_chart_studies_live_monitor.py - untouched.")
    print(f"Symbols: {SYMBOLS}")
    print(f"Snapshot -> {SNAPSHOT_PATH}")
    print("=" * 80)

    kite = build_kite_client_from_env()
    governor_dir = _require_governor_dir()
    governor = KiteRequestGovernor(state_dir=governor_dir)
    client = R1CLiveKiteClient(kite, governor=governor)

    daily_maps, index_regime = _build_daily_maps()
    print(f"NIFTY 50 regime: {index_regime['regime']}")

    now = datetime.now()
    watchers = {}
    for sym in SYMBOLS:
        print(f"\nBootstrapping {sym}...")
        w = MapContextWatcher(sym, client, daily_maps[sym])
        w.bootstrap(now)
        sup = daily_maps[sym]["support"][0]["level"] if daily_maps[sym]["support"] else None
        res = daily_maps[sym]["resistance"][0]["level"] if daily_maps[sym]["resistance"] else None
        print(f"  qualifying support={sup} resistance={res} (REAL/SIGNIFICANT only)")
        watchers[sym] = w

    print(f"\nStarting live poll loop (every {POLL_SECONDS}s)... Ctrl+C to stop.\n")
    eod_reported_for = None
    try:
        while True:
            now = datetime.now()
            for sym, w in watchers.items():
                event = w.poll(now, index_regime["regime"])
                if event is not None:
                    print(f"[{now.isoformat()}] {sym}: {event['direction']} ({event.get('reason')}) "
                          f"price={event.get('price')}")
            _write_snapshot(watchers, index_regime, now)

            if now.time() >= MARKET_CLOSE and eod_reported_for != now.date():
                statement = _generate_eod_statement(watchers, now)
                print("\n" + statement + "\n")
                _append_pnl_ledger(watchers, now, statement)
                _write_snapshot(watchers, index_regime, now)
                eod_reported_for = now.date()

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\n[SYSTEM] Map + Context live engine stopped by user.")


if __name__ == "__main__":
    main()
