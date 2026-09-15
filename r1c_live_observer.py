"""R1-C live observer — the actual entry point. Connects to Kite
read-only, feeds real 1-minute bars into all three live evaluators, runs
the composite state machine once per trading day, and writes the
Evidence Ledger to disk. This is the script the owner runs for real.

**Read this before running it.** This is the OWNER-AUTHORIZED EXCEPTION
scoped in `P01D_R0_POST_CLOSURE_LIVE_OBSERVATION_20260819.md` and the
R1-C candidate concept `P01D_RRME_R1C_PROPOSAL_20260819.md`, run against
the frozen D0 (`P01D_RRME_R1C_D0_DRAFT_20260819.md`). It is:

  - **Read-only.** No write-capable Kite method is imported or callable
    from this file or any module it imports for market data
    (`r1c_live_kite_client.py` only exposes `instruments()` and
    `historical_data()`). `LIVE_TRADING_ENABLED` is not read anywhere in
    this file because there is no execution path here for it to gate -
    grep this file yourself: no `place_order`, no `request_entry()`, no
    broker-write import of any kind.
  - **Not a validation.** Per D0 §5's firewall (inherited from the
    concept document), nothing computed by this run may be treated as
    confirmatory evidence for anything already exposed - this only
    matters for the historical-data path, which this file never touches
    at all; it only ever reads forward, live, from the moment it starts.
  - **Not a rescue and cannot promote anything.** COMPOSITE_ENTRY
    decisions logged here are Evidence Ledger records, not orders and
    not qualification for Stage 3/4 - see the concept document's own
    §1/§5 for what this run can and cannot become.

USAGE (in your own terminal, after setting KITE_API_KEY/KITE_ACCESS_TOKEN
per the token-exchange steps already walked through):

    python r1c_live_observer.py --once
        # one poll cycle: fetch new bars, ingest, print status, exit.
        # Safe to run repeatedly to inspect state without committing to
        # a long-running process.

    python r1c_live_observer.py
        # continuous: polls every --interval seconds (default 60),
        # Ctrl+C to stop. Calls the state machine's process_day() once,
        # the first time a poll is observed to have crossed past the
        # session's market-close, per trading day.

    python r1c_live_observer.py --ledger-out PATH.jsonl
        # where the Evidence Ledger is appended, one JSON object per
        # line, per decision - default: r1c_live_evidence_ledger.jsonl
        # in the project root.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import traceback
from dataclasses import asdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))


def _ist_now_naive() -> datetime:
    """Real wall-clock IST time, as a NAIVE datetime - anchored to IST
    regardless of the machine's own local timezone setting (never
    trusted), but naive from this point on so it is always safely
    comparable against the naive timestamps
    `r1c_live_kite_client.get_minute_bars` already normalizes every
    Kite-sourced bar to. See that module's own comment for the real bug
    this pairs with (a live `TypeError` on the very first continuous run,
    aware Kite timestamps compared against naive `datetime.now()`)."""
    return datetime.now(IST).replace(tzinfo=None)

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from r1c_live_kite_client import R1CLiveKiteClient, KiteResponseMalformedError  # noqa: E402
from r1c_live_v2c_evaluator import LiveV2CEvaluator, LIVE_UNIVERSE  # noqa: E402
from r1c_live_pillar2_evaluator import LivePillar2Evaluator  # noqa: E402
from r1c_live_pillar1_evaluator import LivePillar1Evaluator  # noqa: E402
from r1c_state_machine import CompositeStateMachine  # noqa: E402

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)
DEFAULT_POLL_SECONDS = 60
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "r1c_live_evidence_ledger.jsonl"
DEFAULT_SCRATCH_DIR = PROJECT_ROOT / "r1c_live_v2c_scratch"
DEFAULT_BARS_DIR = PROJECT_ROOT / "r1c_live_bars"


def _require_credentials() -> tuple[str, str]:
    api_key = os.environ.get("KITE_API_KEY", "")
    access_token = os.environ.get("KITE_ACCESS_TOKEN", "")
    if not api_key or not access_token:
        print("[BLOCK] KITE_API_KEY or KITE_ACCESS_TOKEN is not set in this terminal's "
              "environment. Set both, in your own terminal, before running this script - "
              "see P01D_R0_POST_CLOSURE_LIVE_OBSERVATION_20260819.md for the exact steps.")
        sys.exit(2)
    return api_key, access_token


def _append_ledger(entries: list, ledger_path: Path) -> None:
    if not entries:
        return
    with ledger_path.open("a", encoding="utf-8") as h:
        for e in entries:
            h.write(json.dumps(asdict(e)) + "\n")


def _append_bars_csv(symbol: str, bars: list[dict], bars_dir: Path) -> None:
    """Durable, append-only, one CSV per symbol - the raw price history
    behind every Evidence Ledger decision. Without this, the only record
    of what the market actually did lives in the evaluators' in-memory
    buffers and disappears the moment the process stops - needed for any
    later charting/review of where a trade's entry, confirmation, and
    exit actually happened relative to the real price curve."""
    if not bars:
        return
    bars_dir.mkdir(parents=True, exist_ok=True)
    path = bars_dir / f"{symbol.replace(' ', '_')}_1min.csv"
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as h:
        writer = csv.writer(h)
        if write_header:
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for b in bars:
            writer.writerow([b["timestamp"], b["open"], b["high"], b["low"], b["close"], b.get("volume", 0.0)])


class LiveObserverState:
    """Tracks what's already been ingested/processed so a poll only ever
    fetches NEW bars, and `process_day` runs at most once per trading
    day, not once per poll."""

    def __init__(self):
        self.last_fetched_until: dict[str, datetime] = {}
        self.processed_days: set[str] = set()

    def fetch_from(self, symbol: str, default_start: datetime) -> datetime:
        return self.last_fetched_until.get(symbol, default_start)


def run_one_poll(
    *, client: R1CLiveKiteClient, universe: tuple, state: LiveObserverState,
    v2c_ev: LiveV2CEvaluator, p2_ev: LivePillar2Evaluator, p1_ev: LivePillar1Evaluator,
    machine: CompositeStateMachine, ledger_path: Path, scratch_dir: Path,
    bars_dir: Path = DEFAULT_BARS_DIR, now: datetime | None = None,
) -> None:
    now = now if now is not None else _ist_now_naive()
    today = now.date()
    session_start = datetime.combine(today, MARKET_OPEN)
    default_start = session_start - timedelta(days=45)  # first-ever poll: enough
    # history for every evaluator's own trailing-window needs (25+ days
    # for V2-C's event_z20/ATR14, 10+ for Pillar I/II's slot-volume
    # baseline), same margin as each evaluator's own retention_days.

    fetched_any = False
    for symbol in list(universe) + ["NIFTY 50"]:
        start = state.fetch_from(symbol, default_start)
        if start >= now:
            continue
        # 2026-08-23: found live - this loop previously printed NOTHING
        # between the startup banner and "poll complete", so a hang
        # anywhere in it (the first call's instruments() dump, or any
        # individual historical_data() call - kiteconnect's SDK sets no
        # client-side timeout on either) was indistinguishable from a
        # frozen process. flush=True because stdout may be line-buffered
        # differently than expected when redirected/piped - this print
        # must appear immediately, not batched with the next one.
        print(f"  fetching {symbol}...", end="", flush=True)
        try:
            bars = client.get_minute_bars(symbol, start, now)
        except KiteResponseMalformedError as exc:
            print()  # close the in-progress line before the warning
            print(f"[WARN] {symbol}: malformed response, skipping this symbol this poll: {exc}")
            continue
        print(f" {len(bars)} bar(s)")
        for bar in bars:
            v2c_ev.ingest_1m_bar(symbol, bar)
            if symbol != "NIFTY 50":
                p2_ev.ingest_1m_bar(symbol, bar)
                p1_ev.ingest_1m_bar(symbol, bar)
        _append_bars_csv(symbol, bars, bars_dir)
        if bars:
            fetched_any = True
            last_ts = bars[-1]["timestamp"]
            state.last_fetched_until[symbol] = datetime.fromisoformat(last_ts) + timedelta(minutes=1)

    print(f"[{now.isoformat()}] poll complete - fetched_new_bars={fetched_any}")

    past_close = now.time() >= MARKET_CLOSE
    if past_close and today.isoformat() not in state.processed_days:
        print(f"[{now.isoformat()}] past market close for {today} - running composite evaluation")
        v2c_scores = v2c_ev.evaluate(today, out_dir=scratch_dir)
        p2_trades = p2_ev.evaluate(today)
        p1_trades = p1_ev.evaluate(today)
        entries = machine.process_day(
            today, v2c_scores=v2c_scores, pillar2_trades=p2_trades, pillar1_trades=p1_trades,
        )
        _append_ledger(entries, ledger_path)
        state.processed_days.add(today.isoformat())

        composite_entries = [e for e in entries if e.decision == "COMPOSITE_ENTRY"]
        print(f"  V2-C events scored: {len(v2c_scores)} | Pillar II setups: {len(p2_trades)} | "
              f"Pillar I confirmations: {len(p1_trades)}")
        print(f"  Composite entries today: {len(composite_entries)}")
        for e in composite_entries:
            print(f"    [COMPOSITE_ENTRY] {e.symbol} @ {e.detail.get('confirmation_entry_price')}")
        print(f"  Active permissions: {list(machine.active_permissions().keys())}")
        print(f"  Evidence Ledger -> {ledger_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Single poll cycle, then exit.")
    parser.add_argument("--interval", type=int, default=DEFAULT_POLL_SECONDS, help="Poll interval in seconds.")
    parser.add_argument("--ledger-out", type=Path, default=DEFAULT_LEDGER_PATH,
                         help="Evidence Ledger append-only JSONL output path.")
    parser.add_argument("--bars-out", type=Path, default=DEFAULT_BARS_DIR,
                         help="Directory for durable per-symbol 1-minute bar CSVs (used for later charting).")
    args = parser.parse_args()

    api_key, access_token = _require_credentials()

    governor_dir = os.environ.get("KITE_RATE_GOVERNOR_DIR")
    if not governor_dir:
        print("[BLOCK] KITE_RATE_GOVERNOR_DIR is not set (EA1-R1) - same shared directory every "
              "other process using this Kite account uses, so historical-data requests are "
              "coordinated across V11's terminals and P02's scan. See kite_request_governor.py.")
        return 2
    from kite_request_governor import KiteRequestGovernor
    governor = KiteRequestGovernor(state_dir=Path(governor_dir))

    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    client = R1CLiveKiteClient(kite, governor=governor)

    print("=" * 80)
    print("R1-C LIVE OBSERVER — read-only, no order-placement code exists in this module or")
    print("any module it imports for market data. LIVE_TRADING_ENABLED is not referenced")
    print("anywhere in this path because there is no execution path here to gate.")
    print(f"Universe: {len(LIVE_UNIVERSE)} symbols + NIFTY 50. Evidence Ledger -> {args.ledger_out}")
    print(f"Poll interval: {'single-shot (--once)' if args.once else f'{args.interval}s continuous'}")
    print("=" * 80)

    state = LiveObserverState()
    v2c_ev = LiveV2CEvaluator()
    p2_ev = LivePillar2Evaluator()
    p1_ev = LivePillar1Evaluator()
    machine = CompositeStateMachine()

    try:
        while True:
            try:
                run_one_poll(
                    client=client, universe=LIVE_UNIVERSE, state=state,
                    v2c_ev=v2c_ev, p2_ev=p2_ev, p1_ev=p1_ev, machine=machine,
                    ledger_path=args.ledger_out, scratch_dir=DEFAULT_SCRATCH_DIR,
                    bars_dir=args.bars_out,
                )
            except Exception:
                print("[WARN] poll cycle failed, will retry next cycle:")
                traceback.print_exc()

            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[SYSTEM] Live observer stopped by user.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
