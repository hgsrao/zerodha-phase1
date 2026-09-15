"""
P01D ENTRY GATE OBSERVATORY — read-only report generator. 2026-08-24.

Same purpose as P02_QUANT_LAB_20260816/p02_native_shadow_observatory.py,
for entry_gate_dry_run.py's own recorded history instead: reads the REAL
state that tool has already built (entry_gate_dry_run_position_log.json -
every candidate that reached ALL_CONDITIONS_PASSED today) and marks each
one against a current price, reusing compute_realized_hypothetical_pnl()
UNCHANGED - this script computes nothing itself, it only reads, calls that
one real function, and renders.

No order-placement code exists here or in anything it imports.
LIVE_TRADING_ENABLED is not referenced - there is no execution path here
to gate. Needs NO Kite credentials of its own: current prices come from
shadow_strategy_telemetry.json / orb_shadow_telemetry.json, both already
being freshly written by the two already-running, already-credentialed
collector processes - same zero-new-credentials design as
p02_native_shadow_observatory.py.

Honest scope note: unlike P02's native shadow (which has a real SL/TP
persisted per open position), this dry-run only computes a hypothetical
exit/take-profit at the MOMENT a candidate is evaluated - it isn't
persisted forward once a symbol drops out of the momentum/ORB top
candidates. Where the latest telemetry cycle still shows a matching
evaluation for a logged symbol, its stop/target are shown; otherwise the
card says so plainly rather than inventing a level.

Run: python p01d_entry_gate_observatory.py
Writes: p01d_entry_gate_observatory_report.html
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from entry_gate_dry_run import POSITION_LOG_FILE, OUTPUT_FILE, compute_realized_hypothetical_pnl

ROOT = Path(__file__).parent
NIFTY_DATA_DIR = ROOT / "P02_QUANT_LAB_20260816" / "kite_nifty50_data"
OUT_PATH = ROOT / "p01d_entry_gate_observatory_report.html"


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _current_price_map() -> dict:
    """Same two files entry_gate_dry_run.read_latest_candidates() itself
    reads from - reused here purely as a read, no re-derivation of price."""
    prices: dict[str, float] = {}
    momentum = _read_json(ROOT / "shadow_strategy_telemetry.json")
    external = momentum.get("external_variant") if isinstance(momentum, dict) else None
    if isinstance(external, dict) and external.get("status") == "MARKED":
        for row in external.get("selected") or []:
            symbol, price = row.get("symbol"), row.get("live_price")
            if isinstance(symbol, str) and isinstance(price, (int, float)) and price > 0:
                prices[symbol] = float(price)

    orb = _read_json(ROOT / "orb_shadow_telemetry.json")
    if isinstance(orb, dict):
        for row in orb.get("candidates") or []:
            symbol, price = row.get("symbol"), row.get("last_price")
            if isinstance(symbol, str) and isinstance(price, (int, float)) and price > 0:
                prices.setdefault(symbol, float(price))
    return prices


def _latest_hypothetical_levels(symbol: str) -> dict | None:
    """Best-effort: if the most recent entry_gate_dry_run poll cycle still
    has an evaluation for this exact symbol, surface its hypothetical
    exit/take-profit. Returns None (disclosed, not fabricated) otherwise."""
    telemetry = _read_json(ROOT / OUTPUT_FILE)
    if not isinstance(telemetry, dict):
        return None
    for row in telemetry.get("evaluations") or []:
        payload = row.get("order_payload")
        if payload and payload.get("tradingsymbol") == symbol:
            return {
                "exit": row.get("hypothetical_exit"),
                "take_profit": row.get("hypothetical_take_profit"),
            }
    return None


def load_daily_bars(symbol: str, lookback: int = 20) -> pd.DataFrame | None:
    path = NIFTY_DATA_DIR / f"{symbol}_daily.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df.sort_values("date").tail(lookback).reset_index(drop=True)


def render_candles_svg(bars, entry_date_iso, entry_price, current_price,
                        sl=None, tp=None) -> str:
    W, H, PAD_L, PAD_R, PAD_T, PAD_B = 980, 420, 56, 20, 24, 36
    lows = list(bars["low"]) + [entry_price, current_price] + ([float(sl)] if sl else [])
    highs = list(bars["high"]) + [entry_price, current_price] + ([float(tp)] if tp else [])
    p_min, p_max = min(lows), max(highs)
    p_span = (p_max - p_min) or 1
    n = len(bars)
    slot_w = (W - PAD_L - PAD_R) / max(n, 1)

    def y(price):
        return PAD_T + (H - PAD_T - PAD_B) * (1 - (price - p_min) / p_span)

    def x(i):
        return PAD_L + slot_w * i + slot_w / 2

    parts = []
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        price = p_min + p_span * frac
        yy = y(price)
        parts.append(f'<line x1="{PAD_L}" y1="{yy:.1f}" x2="{W - PAD_R}" y2="{yy:.1f}" stroke="currentColor" stroke-width="0.5" opacity="0.15"/>')
        parts.append(f'<text x="{PAD_L - 6}" y="{yy + 3:.1f}" text-anchor="end" font-size="10.5" fill="currentColor" opacity="0.7">{price:.2f}</text>')

    entry_idx = None
    for i, row in bars.iterrows():
        cx = x(i)
        up = row["close"] >= row["open"]
        color = "var(--chart-up)" if up else "var(--chart-down)"
        body_top = y(max(row["open"], row["close"]))
        body_bot = y(min(row["open"], row["close"]))
        body_h = max(body_bot - body_top, 1.2)
        parts.append(f'<line x1="{cx:.1f}" y1="{y(row["high"]):.1f}" x2="{cx:.1f}" y2="{y(row["low"]):.1f}" stroke="{color}" stroke-width="1"/>')
        parts.append(f'<rect x="{cx - slot_w * 0.32:.1f}" y="{body_top:.1f}" width="{slot_w * 0.64:.1f}" height="{body_h:.1f}" fill="{color}"/>')
        if entry_date_iso is not None and row["date"].date().isoformat() == entry_date_iso[:10]:
            entry_idx = i

    if sl:
        yy = y(float(sl))
        parts.append(f'<line x1="{PAD_L}" y1="{yy:.1f}" x2="{W - PAD_R}" y2="{yy:.1f}" stroke="var(--chart-stop)" stroke-width="1.2" stroke-dasharray="5 4"/>')
        parts.append(f'<text x="{W - PAD_R - 4}" y="{yy - 4:.1f}" text-anchor="end" font-size="10" fill="var(--chart-stop)" font-weight="700">EMERGENCY {float(sl):.2f}</text>')
    if tp:
        yy = y(float(tp))
        parts.append(f'<line x1="{PAD_L}" y1="{yy:.1f}" x2="{W - PAD_R}" y2="{yy:.1f}" stroke="var(--chart-target)" stroke-width="1.2" stroke-dasharray="5 4"/>')
        parts.append(f'<text x="{W - PAD_R - 4}" y="{yy - 4:.1f}" text-anchor="end" font-size="10" fill="var(--chart-target)" font-weight="700">HYPOTHETICAL TP {float(tp):.2f}</text>')

    if entry_idx is not None:
        ex, ey = x(entry_idx), y(entry_price)
        parts.append(f'<line x1="{ex:.1f}" y1="{PAD_T}" x2="{ex:.1f}" y2="{H - PAD_B}" stroke="var(--chart-entry)" stroke-width="1" stroke-dasharray="2 3" opacity="0.6"/>')
        parts.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4.5" fill="var(--chart-entry)" stroke="var(--chart-bg)" stroke-width="1.5"/>')
        parts.append(f'<text x="{ex:.1f}" y="{PAD_T - 8}" text-anchor="middle" font-size="10.5" fill="var(--chart-entry)" font-weight="700">ENTRY</text>')

    last_x = x(n - 1)
    parts.append(f'<circle cx="{last_x:.1f}" cy="{y(current_price):.1f}" r="4" fill="currentColor"/>')

    svg_inner = "\n".join(parts)
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Daily candles with entry marked">\n{svg_inner}\n</svg>'


def render_trade_card(entry: dict, marked: dict) -> str:
    symbol = entry["symbol"]
    entry_price = float(entry["entry_price"])
    entry_ts = entry["timestamp"]
    levels = _latest_hypothetical_levels(symbol)
    sl = tp = None
    if levels and levels.get("exit") and levels["exit"].get("valid"):
        sl = levels["exit"]["order_payload"]["trigger_price"]
    if levels and levels.get("take_profit") and levels["take_profit"].get("valid"):
        tp = levels["take_profit"]["order_payload"]["price"]

    if marked.get("status") == "MARKED":
        current_price = float(marked["current_price"])
        pnl = float(marked["net_pnl"])
        pnl_pct = (pnl / (entry_price * int(entry["quantity"]))) * 100
        pnl_class = "pnl-pos" if pnl >= 0 else "pnl-neg"
        pnl_sign = "+" if pnl >= 0 else ""
        pnl_row = (f'<span><strong>Hypothetical net P&amp;L</strong> (real Zerodha cost schedule, both legs) '
                    f'<span class="{pnl_class}">{pnl_sign}Rs.{pnl:.2f} ({pnl_sign}{pnl_pct:.2f}%)</span></span>')
    else:
        current_price = entry_price  # chart still needs a "now" point; disclosed below
        pnl_row = '<span class="price-unavailable">Current price unavailable this refresh (symbol has dropped out of both observers\' live candidate lists) - not marked to market.</span>'

    bars = load_daily_bars(symbol)
    if bars is None or bars.empty:
        chart_html = '<div class="chart-empty">No local daily price history for this symbol.</div>'
    else:
        svg = render_candles_svg(bars, entry_ts, entry_price, current_price, sl=sl, tp=tp)
        chart_html = svg

    sl_meta = f'<span><strong>Hypothetical emergency stop</strong> {float(sl):.2f}</span>' if sl else '<span class="price-unavailable">Emergency stop not available this refresh</span>'
    tp_meta = f'<span><strong>Hypothetical 2:1 take-profit</strong> {float(tp):.2f} (NOT native to V3.4)</span>' if tp else ''

    return f'''
<section class="trade-card">
  <div class="trade-head">
    <h2>{symbol}</h2>
    <span class="trade-date">{entry["source"]} &middot; entered {entry_ts[:16]} &middot; {int(entry["quantity"])} sh @ {entry_price:.2f}</span>
  </div>
  <div class="trade-meta">
    <span><strong>Entry</strong> {entry_price:.2f} &times; {int(entry["quantity"])} sh</span>
    {sl_meta}
    {tp_meta}
    {pnl_row}
  </div>
  <figure class="chart-fig">
    {chart_html}
  </figure>
  <div class="legend">
    <span><span class="swatch" style="background:var(--chart-up)"></span>Up day</span>
    <span><span class="swatch" style="background:var(--chart-down)"></span>Down day</span>
    <span><span class="swatch" style="background:var(--chart-entry)"></span>Entry</span>
    <span><span class="swatch" style="background:var(--chart-stop)"></span>Emergency stop (real V3.4 formula)</span>
    <span><span class="swatch" style="background:var(--chart-target)"></span>Hypothetical take-profit (not native)</span>
  </div>
</section>'''


def main():
    today = datetime.now().date().isoformat()
    log_path = ROOT / POSITION_LOG_FILE
    log = _read_json(log_path) or {}
    entries = log.get(today, [])

    current_prices = _current_price_map()
    pnl_result = compute_realized_hypothetical_pnl(log_path, trading_day=today, current_prices=current_prices)

    marked_by_symbol = {p["symbol"]: p for p in pnl_result["positions"]}
    cards = "\n".join(render_trade_card(e, marked_by_symbol.get(e["symbol"], {})) for e in entries)

    marked_count = sum(1 for p in pnl_result["positions"] if p["status"] == "MARKED")
    unavailable_count = len(pnl_result["positions"]) - marked_count

    generated_at = datetime.now().isoformat()
    total_pnl = float(pnl_result["total_net_pnl"])

    html = f'''<title>P01D Entry Gate Observatory</title>
<style>
  :root {{
    --bg: #f1f3f5; --surface: #ffffff; --surface-2: #e9ecef;
    --ink: #1a2130; --ink-muted: #5b6472; --ink-faint: #8993a3;
    --border: #d7dbe1; --border-strong: #b9c0cb;
    --accent: #2b3a67; --accent-soft: #eceef4;
    --chart-up: #2f8f5b; --chart-down: #c0453c; --chart-stop: #b3261e;
    --chart-target: #1f7a5c; --chart-entry: #8a4fd1; --chart-bg: var(--surface);
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
      --chart-up: #6fcb96; --chart-down: #e08a7c; --chart-stop: #e79287;
      --chart-target: #7fc9a8; --chart-entry: #c39ef2; --chart-bg: var(--surface);
      --pnl-pos: #6fcb96; --pnl-neg: #e08a7c;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #13161c; --surface: #191d24; --surface-2: #21262f;
    --ink: #e6e9ef; --ink-muted: #9aa3b3; --ink-faint: #6b7484;
    --border: #323945; --border-strong: #454e5d;
    --accent: #8b9dd6; --accent-soft: #232a42;
    --chart-up: #6fcb96; --chart-down: #e08a7c; --chart-stop: #e79287;
    --chart-target: #7fc9a8; --chart-entry: #c39ef2; --chart-bg: var(--surface);
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
  .chart-fig {{ margin: 0; }}
  .chart-fig svg {{ width: 100%; height: auto; display: block; background: var(--chart-bg); border-radius: 5px; }}
  .chart-empty {{ padding: 24px; text-align: center; color: var(--ink-faint); background: var(--surface-2); border-radius: 5px; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 10px 16px; font-size: 12px; color: var(--ink-muted); margin-top: 14px; padding-top: 14px; border-top: 1px dashed var(--border); }}
  .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .swatch {{ width: 10px; height: 10px; border-radius: 2px; display: inline-block; }}
  .empty-state {{ padding: 40px; text-align: center; color: var(--ink-faint); background: var(--surface); border: 1px dashed var(--border); border-radius: 7px; }}
  footer {{ font-size: 12px; color: var(--ink-faint); text-align: center; margin-top: 20px; }}
</style>

<div class="page">
  <div class="info-banner">
    <strong>P01D V3.4 ENTRY GATE - REAL AUTHORIZATION PIPELINE</strong> &mdash; every candidate below
    genuinely reached <code>ALL_CONDITIONS_PASSED</code> through RunnerEntryAuthorizer's real, unmodified
    production pipeline (capital ceiling, P0-3B-F policy layer, cooldowns, position limits) &mdash; the ONLY
    reason none of these became a real order is <code>LIVE_TRADING_ENABLED=False</code>. No order-placement
    code exists anywhere in this path. P&amp;L uses the real Zerodha delivery cost schedule on both legs,
    via <code>compute_realized_hypothetical_pnl()</code> (entry_gate_dry_run.py's own function, reused
    unchanged, not recomputed here).
  </div>

  <header class="masthead">
    <p class="kicker">P01D V3.4 &middot; Entry Gate Dry Run</p>
    <h1>P01D Entry Gate Observatory</h1>
    <p class="sub">Every candidate that passed the real risk gate today &mdash; generated {generated_at[:16]} IST.</p>
  </header>

  <div class="summary-strip">
    <div class="summary-cell"><div class="label">Trading Day</div><div class="val">{today}</div></div>
    <div class="summary-cell"><div class="label">Candidates Passed</div><div class="val">{len(entries)}</div></div>
    <div class="summary-cell"><div class="label">Marked / Unavailable</div><div class="val">{marked_count} / {unavailable_count}</div></div>
    <div class="summary-cell"><div class="label">Hypothetical Net P&amp;L</div><div class="val {"pnl-pos" if total_pnl >= 0 else "pnl-neg"}">{"+" if total_pnl >= 0 else ""}Rs.{total_pnl:,.2f}</div></div>
  </div>

  {cards if entries else '<div class="empty-state">No candidate has reached ALL_CONDITIONS_PASSED yet today.</div>'}

  <footer>Source: entry_gate_dry_run_position_log.json + compute_realized_hypothetical_pnl() (reused, not reimplemented) &middot; regenerate with <code>python p01d_entry_gate_observatory.py</code></footer>
</div>
'''
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(entries)} candidates today, {marked_count} marked, "
          f"hypothetical net P&L Rs.{total_pnl:,.2f})")


if __name__ == "__main__":
    main()
