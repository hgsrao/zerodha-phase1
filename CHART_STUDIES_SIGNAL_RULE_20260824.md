# Chart-studies composite entry/exit rule — 2026-08-24

Owner request: a live monitor over the 4 symbols in today's V11 shadow
plan (BAJFINANCE, LAURUSLABS, SBIN, SUNPHARMA), using the same 5 studies
visible on the owner's own Kite chart (Ichimoku Cloud, Bollinger Bands
20/2, Stochastic Momentum Index 10/3/3, session VWAP, Anchored VWAP from
2026-03-02), reporting when a composite of all 5 signals entry/exit.

**Extended 2026-08-24** to a 5th symbol, BRITANNIA — P02 Pillar I/II's
own `run_p02_live_paper.py --scan` flagged it live that day (Pillar II /
MEAN_REVERTING, close Rs.5,313.00, would fill at the next session's
open). Explicit owner request: not a one-shot paper scan, watched
continuously by this same 5-study composite engine instead of building a
separate tool. Consequence disclosed: `NOTIONAL_PER_POSITION` is
`TOTAL_NOTIONAL / len(SYMBOLS)`, so adding a 5th symbol re-splits the
notional from Rs.25,000 to Rs.20,000 per position for ALL five symbols
going forward (an equal-weight redistribution, not a per-symbol
override). Unlike the 4 V11 symbols (fixed for the day by V11's own
selection), Pillar I/II's flagged symbol is not re-synced automatically —
a different day's Pillar I/II signal would need to be added to `SYMBOLS`
by hand the same way.

**This is informational only — a mechanical read-out of what these
indicators are doing, not a trade recommendation, and not wired into the
V11 bridge's own entry/exit decisions in any way.** LIVE_TRADING_ENABLED
stays False; nothing here places or queues an order. Not licensed
investment advice — treat every signal as a data point to evaluate
yourself, not an instruction.

Rule is stated here, before looking at any live output, for the same
reason this project states every rule before looking at a result
(P02_MOMENTUM_PROCESS_DISCIPLINE_FREEZE_20260817.md) — so the read-out
isn't silently adjusted once it's running.

## Bar resolution

5-minute intraday bars, resampled from real Kite 1-minute bars
(`kite.historical_data(..., interval="minute")`, same governed fetch path
as every other live component this session — `KiteRequestGovernor`,
`KITE_RATE_GOVERNOR_DIR` required). 5-minute chosen as a reasonable
live-monitor cadence for these five studies; not swept/optimized.

## Per-indicator bullish/bearish read

1. **Ichimoku (9, 26, 52)** — Tenkan-sen (9-period mid), Kijun-sen
   (26-period mid), Senkou Span A/B (cloud, shifted forward 26 periods).
   BULLISH: close above both Senkou spans (above the cloud) AND Tenkan >
   Kijun. BEARISH: close below both spans AND Tenkan < Kijun. Otherwise
   NEUTRAL (inside the cloud, or Tenkan/Kijun disagree with price) —
   NEUTRAL counts toward neither side of the vote below.

2. **Bollinger Bands (20, 2σ)** — basis = 20-period SMA. BULLISH: close >
   basis. BEARISH: close < basis. (Reads position relative to the mean,
   not a breakout/mean-reversion call — the simplest, least ambiguous
   read given this is combined with 4 other studies, not used alone.)

3. **Stochastic Momentum Index (10, 3, 3)** — SMI main line vs. its
   signal line. BULLISH: SMI > signal. BEARISH: SMI < signal.

4. **Session VWAP** — resets every trading day. BULLISH: close > VWAP.
   BEARISH: close < VWAP.

5. **Anchored VWAP (from 2026-03-02)** — cumulative, never resets, matches
   the anchor date visible on the owner's own chart. BULLISH: close >
   anchored VWAP. BEARISH: close < anchored VWAP.

## Composite score and state machine

`score = (count BULLISH) - (count BEARISH)`, range -5..+5, recomputed on
every new 5-minute bar, independently per symbol.

Each symbol starts FLAT.

- **ENTRY** (FLAT -> IN): score crosses from below +3 up to >= +3 (at
  least 3 of 5 studies net-bullish, majority agreement).
- **EXIT** (IN -> FLAT): score subsequently crosses from above 0 down to
  <= 0 (majority lost — does not require a full bearish flip to +3
  bearish; asymmetric on purpose, a common hysteresis-band design to
  avoid flapping right at the +3 boundary).

+3 entry / 0 exit is a disclosed design choice, not backtested or swept
against this data — same discipline as this project's other
not-optimized thresholds (e.g. p02_core.py's TRAIL_ATR_MULTIPLE). Treat
it as a reasonable first cut, adjustable if the owner wants a different
threshold after seeing real output.

## What gets logged

Every ENTRY/EXIT transition (not just the current state) is appended to
`chart_studies_signal_ledger.jsonl` — one JSON line per event, timestamp,
symbol, direction, score, fill price (the triggering bar's close), and
each of the 5 individual reads at that moment, matching this project's
existing evidence-ledger convention (`r1c_live_evidence_ledger.jsonl`).

## Addendum — 2026-08-24, after watching one live trading day

Owner asked "what can we make better" after reviewing today's real
SUNPHARMA trades against the owner's own Kite chart. Per this project's
"state the rule before results" discipline, this is written down before
the changes below produce any new live output. Four changes, all in
`chart_studies_indicators.py` / `run_chart_studies_live_monitor.py`:

1. **Anchored VWAP excluded from the vote.** Still computed, displayed,
   and logged per bar, but no longer counted toward the composite score.
   It stayed BULLISH the entire session (anchored ~6 months back) while
   the 4 faster studies genuinely flipped bearish, silently capping how
   bearish the composite could read. `VOTING_STUDIES` = the other 4;
   `ENTRY_THRESHOLD`/`EXIT_THRESHOLD` numeric values (3 / 0) are
   unchanged, now read as "of 4" rather than "of 5" — proportionally
   stricter (75% vs. 60% agreement to enter).
2. **2-consecutive-bar confirmation.** Previously fired the instant a
   single closed bar crossed the threshold. Now requires
   `CONFIRMATION_BARS = 2` consecutive closed bars meeting the condition
   before firing — a standard whipsaw filter; signals arrive one bar
   later but with fewer false starts. 2 of 3 SUNPHARMA round-trips today
   were single-bar-driven chop that reversed within the hour.
3. **Independent hard-stop backstop.** `HARD_STOP_PCT = 0.5%` from entry,
   checked every bar regardless of composite score, on top of (not
   instead of) the composite's own exit. A backstop for tail moves the
   composite's own debounce would otherwise wait out.
4. **Real transaction costs in the P&L.** The card's P&L was raw price
   delta only. Now reuses `zerodha_delivery_costs.buy_cost`/`sell_cost`
   (same real cost schedule `entry_gate_dry_run.py`'s own
   `compute_realized_hypothetical_pnl()` already uses) to report a
   net-of-costs figure alongside the existing gross one, for both closed
   trades and the still-open position's mark-to-market.

None of these are backtested or swept against this data — same
"disclosed first cut, adjustable" discipline as the original thresholds.
Owner's own framing: implement now, observe for about an hour, decide
from there.

**Same-day follow-up (still 2026-08-24):** three more pieces added directly
in these same files (owner explicitly opted for "on the fly," no separate
versioned folder this round):
- `project_state(score)` → GREEN (score >= ENTRY_THRESHOLD) / RED (score
  <= EXIT_THRESHOLD) / AMBER (between) - a fully numeric reduction of the
  report's qualitative GREEN/AMBER/RED language, no swing-detection or
  volatility-compression algorithm implemented (none existed to reuse).
- Next-bar-open execution: a SIGNAL (composite crossing, confirmed) no
  longer fills immediately - it queues, and fills at the very next closed
  bar's own `open`, honoring "enter/exit at the next bar's open" for real.
- `evaluate_fill()` / `ABSTAIN_SLIPPAGE_PCT = 0.3%`: if that next-bar open
  has moved more than 0.3% from the decision price, the system ABSTAINS
  (no trade) instead of chasing - the report's own rule, previously
  undisclosed as a number.

**Restart consequence, disclosed:** the live monitor's in-memory trade/
event history is not persisted across a restart — applying these changes
requires restarting the process, which resets the CURRENT snapshot's
`trades`/`recent_events` to empty (starts counting again from the
restart). Nothing is actually lost: every event that already fired today
remains permanently in `chart_studies_signal_ledger.jsonl` (append-only,
untouched by a restart) and indicators themselves are freshly
re-bootstrapped from real Kite history on startup, not degraded.

## Paper P&L (added 2026-08-24, owner request)

Every ENTRY opens a hypothetical position at that bar's close; the
matching EXIT closes it at ITS bar's close. Nothing here is a real order
or real capital — it exists purely so a signal has a concrete "would this
have made or lost money" answer, not just a state transition.

- **Notional convention**: Rs.1,00,000 total, split evenly across the 4
  symbols (Rs.25,000 each) — a disclosed assumption, not derived from any
  account balance, purely so the statement has a Rupee number alongside
  the percentage return.
- **End of day**, once the monitor's poll loop crosses 15:30 IST (same
  `MARKET_CLOSE` convention as `r1c_live_observer.py`), it automatically
  prints and logs a full statement: every closed round-trip trade (entry
  price/time, exit price/time, % return, notional P&L), any position
  still open at close marked-to-market at the last available price (never
  silently dropped), and a net total. Logged to
  `chart_studies_pnl_ledger.jsonl`, one record per day.
