"""Map + Context Observer (2026-08-25 addendum).

Standalone, read-only, informational. NOT wired into any live engine's
entry/exit logic - a new observer to look at ALONSIDE the live Chart
Studies Monitor, not a replacement for it or a change to it.

Built from a price-action video review (see MAP_CONTEXT_OBSERVER_NOTE_20260825.md
if present): "the map" (support/resistance levels the market has proven it
respects, from daily-bar swing highs/lows) and "context" (NIFTY 50 index
regime + how extended a symbol's recent move already is, used only to
suggest a sizing tier - FULL/HALF/PASS - never to fire a trade).

Data sources, all already on disk, ZERO new Kite calls:
  - Daily OHLCV for BAJFINANCE/SBIN/SUNPHARMA/BRITANNIA:
    P02_QUANT_LAB_20260816/kite_nifty50_data/<SYMBOL>_daily.csv
  - Daily OHLCV for LAURUSLABS (not in the NIFTY-50 set):
    P02_QUANT_LAB_20260816/kite_midsmallcap_data/LAURUSLABS_daily.csv
  - NIFTY 50 index daily history (2014-2026):
    P02_QUANT_LAB_20260816/nifty50_index_historical_2014_2026.csv
  - Current price per symbol: chart_studies_live_state.json (the live
    Chart Studies Monitor's own snapshot, reused unchanged - not a fresh
    quote fetch).

Explicit gap, disclosed not hidden: catalyst/event-calendar awareness
(the third context check in the source material - earnings, macro prints)
is NOT implemented. No earnings/macro calendar exists anywhere in this
project. Every symbol's context section says so plainly rather than
silently omitting it.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from map_context_indicators import (
    build_map, check_fib_confluence, check_ma_confluence, classify_index_regime,
    compute_recent_swing_fib_levels, consecutive_directional_days,
    detect_recent_breakout, sizing_guidance,
)

# 2026-08-25 addendum: same disclosed lookback as detect_recent_breakout's
# own default - not swept/optimized.
BREAKOUT_LOOKBACK_DAYS = 10

ROOT = Path(__file__).parent
P02_ROOT = ROOT / "P02_QUANT_LAB_20260816"
LIVE_STATE_PATH = ROOT / "chart_studies_live_state.json"
OUT_PATH = ROOT / "local_dashboard" / "map_context_observer_report.html"

SYMBOLS = ["BAJFINANCE", "LAURUSLABS", "SBIN", "SUNPHARMA", "BRITANNIA"]
DAILY_PATH_OVERRIDES = {
    "LAURUSLABS": P02_ROOT / "kite_midsmallcap_data" / "LAURUSLABS_daily.csv",
}
NIFTY_INDEX_PATH = P02_ROOT / "nifty50_index_historical_2014_2026.csv"

# Only levels/history from before today count as "the map" - using bars
# formed intraday today would be look-ahead-shaped for a level supposedly
# already proven. Same discipline as chart_studies' own "decide off a
# fully closed bar" rule.
LOOKBACK_TRADING_DAYS = 250  # roughly one year - a first-cut window, not swept


def _load_daily(symbol: str) -> pd.DataFrame:
    path = DAILY_PATH_OVERRIDES.get(symbol, P02_ROOT / "kite_nifty50_data" / f"{symbol}_daily.csv")
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = df.sort_values("date").reset_index(drop=True)
    return df.tail(LOOKBACK_TRADING_DAYS).reset_index(drop=True)


def _current_prices() -> dict:
    """Reads the same chart_studies_bars_<SYMBOL>.csv files the live
    monitor itself writes each poll - NOT chart_studies_live_state.json,
    which never carries a priceHistory field (confirmed by inspection:
    its per-symbol keys are only state/score/projection/reads/
    recent_events/trades). Matches local_dashboard_refresh_daemon.py's
    own established source for the same data."""
    prices = {}
    for sym in SYMBOLS:
        bars_path = ROOT / f"chart_studies_bars_{sym}.csv"
        if not bars_path.is_file():
            continue
        bars = pd.read_csv(bars_path)
        if not bars.empty:
            prices[sym] = float(bars["close"].iloc[-1])
    return prices


def _fmt_level(level: dict | None, confluence: dict | None = None) -> str:
    if level is None:
        return "none found in lookback window"
    base = f"Rs.{level['level']:.2f} ({level['classification']}, {level['touches']} touches)"
    if confluence and confluence["has_confluence"]:
        which = []
        if confluence.get("sma_short_confluence"):
            which.append("SMA20")
        if confluence.get("sma_long_confluence"):
            which.append("SMA50")
        if confluence.get("fib_50_confluence"):
            which.append("Fib50%")
        if confluence.get("fib_618_confluence"):
            which.append("Fib61.8%")
        base += f" + {'/'.join(which)} confluence"
    return base


def render_symbol_section(symbol: str, daily: pd.DataFrame, current_price: float,
                           index_regime: dict) -> str:
    map_result = build_map(daily, current_price)
    extension = consecutive_directional_days(daily)
    long_guidance = sizing_guidance(index_regime["regime"], "LONG", extension)
    short_guidance = sizing_guidance(index_regime["regime"], "SHORT", extension)

    tier_class = {"FULL": "pnl-pos", "HALF": "", "PASS": "pnl-neg"}
    long_reasons = "; ".join(long_guidance.reasons) if long_guidance.reasons else "no context flags"
    short_reasons = "; ".join(short_guidance.reasons) if short_guidance.reasons else "no context flags"

    # 2026-08-25 addendum: MA confluence, fib confluence, and breakout -
    # all three verified against real data before building (see
    # map_context_indicators.py docstrings for each).
    sma20 = float(daily["close"].tail(20).mean())
    sma50 = float(daily["close"].tail(50).mean())
    fib_levels = compute_recent_swing_fib_levels(daily)

    def _combined_confluence(level: dict | None) -> dict | None:
        if level is None:
            return None
        ma = check_ma_confluence(level["level"], sma20, sma50)
        fib = check_fib_confluence(level["level"], fib_levels)
        return {
            "sma_short_confluence": ma["sma_short_confluence"],
            "sma_long_confluence": ma["sma_long_confluence"],
            "fib_50_confluence": fib["fib_50_confluence"],
            "fib_618_confluence": fib["fib_618_confluence"],
            "has_confluence": ma["has_confluence"] or fib["has_confluence"],
        }

    resistance_confluence = _combined_confluence(map_result["nearest_resistance"])
    support_confluence = _combined_confluence(map_result["nearest_support"])
    all_levels = map_result["all_support"] + map_result["all_resistance"]
    breakouts = detect_recent_breakout(daily, all_levels, lookback_days=BREAKOUT_LOOKBACK_DAYS)
    if breakouts:
        breakout_text = "; ".join(
            f"Rs.{b['level']:.2f} broken {b['direction']} ({b['touches']} prior touches)" for b in breakouts
        )
    else:
        breakout_text = f"none in the last {BREAKOUT_LOOKBACK_DAYS} sessions"

    return f"""
<section class="trade-card">
  <div class="trade-head">
    <h2>{symbol}</h2>
    <span class="trade-date">Map from {daily['date'].iloc[0].date()} to {daily['date'].iloc[-1].date()} &middot; current Rs.{current_price:.2f}</span>
  </div>
  <div class="trade-meta">
    <span><strong>Nearest resistance</strong> {_fmt_level(map_result['nearest_resistance'], resistance_confluence)}</span>
    <span><strong>Nearest support</strong> {_fmt_level(map_result['nearest_support'], support_confluence)}</span>
    <span><strong>Extension</strong> {extension['streak']} consecutive {extension['direction'] or 'flat'} day(s)</span>
  </div>
  <div class="trade-meta">
    <span><strong>Significant-level breakout (last {BREAKOUT_LOOKBACK_DAYS}d)</strong> {breakout_text}</span>
  </div>
  <div class="trade-meta">
    <span class="{tier_class[long_guidance.tier]}"><strong>LONG sizing</strong> {long_guidance.tier} &mdash; {long_reasons}</span>
  </div>
  <div class="trade-meta">
    <span class="{tier_class[short_guidance.tier]}"><strong>SHORT sizing</strong> {short_guidance.tier} &mdash; {short_reasons}</span>
  </div>
  <div class="trade-meta">
    <span class="price-unavailable">Catalyst/event-calendar check: NOT AVAILABLE &mdash; no earnings/macro calendar wired in anywhere in this project. This context check is disclosed as missing, not silently skipped.</span>
  </div>
</section>
"""


def main() -> None:
    index_daily = pd.read_csv(NIFTY_INDEX_PATH)
    index_daily["date"] = pd.to_datetime(index_daily["date"])
    index_daily = index_daily.sort_values("date").reset_index(drop=True).tail(LOOKBACK_TRADING_DAYS)
    index_regime = classify_index_regime(index_daily)

    prices = _current_prices()
    sections = []
    marked = 0
    for symbol in SYMBOLS:
        daily = _load_daily(symbol)
        current_price = prices.get(symbol)
        if current_price is None:
            current_price = float(daily["close"].iloc[-1])
        else:
            marked += 1
        sections.append(render_symbol_section(symbol, daily, current_price, index_regime))

    generated_at = pd.Timestamp.now().strftime("%Y-%m-%dT%H:%M")
    html = f"""<title>Map + Context Observer</title>
<style>
  :root {{
    --bg: #f1f3f5; --surface: #ffffff; --surface-2: #e9ecef;
    --ink: #1a2130; --ink-muted: #5b6472; --ink-faint: #8993a3;
    --border: #d7dbe1; --border-strong: #b9c0cb;
    --accent: #2b3a67; --accent-soft: #eceef4;
    --pnl-pos: #2f8f5b; --pnl-neg: #c0453c;
    --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #13161c; --surface: #191d24; --surface-2: #21262f;
      --ink: #e6e9ef; --ink-muted: #9aa3b3; --ink-faint: #6b7484;
      --border: #323945; --border-strong: #454e5d;
      --accent: #8b9dd6; --accent-soft: #232a42;
      --pnl-pos: #6fcb96; --pnl-neg: #e08a7c;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #13161c; --surface: #191d24; --surface-2: #21262f;
    --ink: #e6e9ef; --ink-muted: #9aa3b3; --ink-faint: #6b7484;
    --border: #323945; --border-strong: #454e5d;
    --accent: #8b9dd6; --accent-soft: #232a42;
    --pnl-pos: #6fcb96; --pnl-neg: #e08a7c;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: var(--sans); font-size: 15px; line-height: 1.55; }}
  .page {{ max-width: 1080px; margin: 0 auto; padding: 32px 24px 56px; }}
  .info-banner {{
    background: var(--accent-soft); border: 1px solid var(--border-strong); color: var(--ink);
    border-radius: 5px; padding: 12px 16px; font-size: 13.5px; margin-bottom: 26px; line-height: 1.6;
  }}
  .info-banner strong {{ font-family: var(--mono); letter-spacing: 0.03em; }}
  header.masthead {{ border-bottom: 1px solid var(--border); padding-bottom: 18px; margin-bottom: 22px; }}
  .kicker {{ font-family: var(--mono); font-size: 11.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-faint); margin: 0 0 8px; }}
  h1 {{ font-family: var(--serif); font-weight: 600; font-size: clamp(24px, 3.4vw, 32px); margin: 0 0 8px; text-wrap: balance; color: var(--ink); }}
  .sub {{ margin: 0; font-size: 14px; color: var(--ink-muted); }}
  .summary-strip {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1px;
    background: var(--border); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 26px;
  }}
  .summary-cell {{ background: var(--surface); padding: 14px 18px; }}
  .summary-cell .label {{ font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-faint); margin-bottom: 4px; }}
  .summary-cell .val {{ font-family: var(--mono); font-size: 17px; font-weight: 600; }}
  .pnl-pos {{ color: var(--pnl-pos); }}
  .pnl-neg {{ color: var(--pnl-neg); }}
  .price-unavailable {{ color: var(--ink-faint); font-style: italic; }}
  .trade-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 7px; padding: 20px 22px 22px; margin-bottom: 24px; }}
  .trade-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }}
  .trade-head h2 {{ font-family: var(--serif); font-size: 20px; margin: 0; color: var(--ink); }}
  .trade-date {{ font-family: var(--mono); font-size: 12.5px; color: var(--ink-faint); }}
  .trade-meta {{
    display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 12.5px; color: var(--ink-muted);
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 5px; padding: 10px 14px; margin-bottom: 10px;
  }}
  .trade-meta strong {{ color: var(--ink); font-weight: 600; margin-right: 4px; }}
  footer {{ font-size: 12px; color: var(--ink-faint); text-align: center; margin-top: 20px; }}
</style>

<div class="page">
  <div class="info-banner">
    <strong>MAP + CONTEXT OBSERVER</strong> &mdash; NOT wired into any live engine's entry/exit logic.
    A standalone, read-only view built from a price-action video review. "Map" = daily-bar swing
    support/resistance, clustered from real closing history, classified by touch count. "Context" =
    NIFTY 50 index regime + how extended each symbol's recent move already is, expressed only as a
    suggested sizing tier (FULL/HALF/PASS) &mdash; never as a trade signal. No order-placement code
    exists anywhere in this path.
  </div>

  <header class="masthead">
    <p class="kicker">Map + Context &middot; Informational Only</p>
    <h1>Map + Context Observer</h1>
    <p class="sub">Generated {generated_at} IST &mdash; {marked}/{len(SYMBOLS)} symbols marked to today's live price.</p>
  </header>

  <div class="summary-strip">
    <div class="summary-cell"><div class="label">NIFTY 50 Regime</div><div class="val">{index_regime['regime']}</div></div>
    <div class="summary-cell"><div class="label">Index as-of</div><div class="val">{index_regime.get('as_of_date', 'n/a')}</div></div>
    <div class="summary-cell"><div class="label">Lookback window</div><div class="val">{LOOKBACK_TRADING_DAYS} sessions</div></div>
  </div>

  {''.join(sections)}

  <footer>Read-only, informational &middot; regenerate with <code>python map_context_observer.py</code></footer>
</div>
"""
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_PATH} (NIFTY regime: {index_regime['regime']}, {marked}/{len(SYMBOLS)} marked to live price)")


if __name__ == "__main__":
    main()
