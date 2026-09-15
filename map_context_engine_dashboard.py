"""Map + Context Engine Dashboard (2026-08-25).

Reads map_context_live_state.json (the REAL live 48-symbol engine's own
snapshot - entries, exits, stops, targets, P&L) and renders it as a
local, self-contained HTML report. This is a DIFFERENT thing from
map_context_observer.py's report: that one is the informational,
5-symbol Map+Context companion view for Chart Studies Monitor with no
trades of its own; this one shows what the new live engine actually did.

Read-only. No order-placement code exists in this path. Regenerate with
`python map_context_engine_dashboard.py`, or let
local_dashboard_refresh_daemon.py keep it current automatically.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
LIVE_STATE_PATH = ROOT / "map_context_live_state.json"
OUT_PATH = ROOT / "local_dashboard" / "map_context_engine_dashboard_report.html"


def _fmt_price(v):
    return f"Rs.{v:.2f}" if v is not None else "n/a"


def _fmt_pnl(v):
    if v is None:
        return "n/a"
    sign = "+" if v >= 0 else ""
    cls = "pnl-pos" if v >= 0 else "pnl-neg"
    return f'<span class="{cls}">{sign}Rs.{v:,.2f}</span>'


def render_trade_row(symbol: str, trade: dict, is_open: bool, mark_price) -> str:
    status = "OPEN" if is_open else trade["status"]
    exit_bit = (
        f'{_fmt_price(trade.get("exit_price"))} ({trade.get("exit_reason")})' if not is_open
        else f'mark {_fmt_price(mark_price)}'
    )
    net_pnl = trade.get("net_pnl") if not is_open else None
    gross_pnl = trade.get("gross_pnl") if not is_open else None
    return f"""
<tr>
  <td>{symbol}</td>
  <td>{status}</td>
  <td>{trade["entry_ts"][:16]}</td>
  <td>{_fmt_price(trade["entry_price"])}</td>
  <td>{_fmt_price(trade["stop_price"])}</td>
  <td>{_fmt_price(trade.get("target_price"))}</td>
  <td>{exit_bit}</td>
  <td>{_fmt_pnl(net_pnl)}</td>
  <td>{_fmt_pnl(gross_pnl) if gross_pnl is not None else ''}</td>
</tr>"""


def main() -> None:
    if not LIVE_STATE_PATH.is_file():
        print(f"[ERROR] {LIVE_STATE_PATH} not found - is map_context_live_engine.py running?")
        return
    with open(LIVE_STATE_PATH, encoding="utf-8") as f:
        state = json.load(f)

    symbols = state["symbols"]
    open_trades = [(sym, s["open_trade"], s.get("latest_close")) for sym, s in symbols.items() if s.get("open_trade")]
    closed_trades = [
        (sym, t, None) for sym, s in symbols.items() for t in s["trades"] if t["status"] != "OPEN"
    ]
    watching = [sym for sym, s in symbols.items() if s.get("reaction_tested") and not s.get("open_trade")]

    total_open = len(open_trades)
    total_closed = len(closed_trades)
    net_closed_pnl = sum(t.get("net_pnl") or 0 for _, t, _ in closed_trades)

    rows = "".join(
        render_trade_row(sym, t, True, mark) for sym, t, mark in open_trades
    ) + "".join(
        render_trade_row(sym, t, False, None) for sym, t, _ in closed_trades
    )
    if not rows:
        rows = '<tr><td colspan="9" class="price-unavailable">No trades yet today - the reaction/reclaim entry rule is selective by design.</td></tr>'

    watching_text = ", ".join(watching) if watching else "none currently"

    html = f"""<title>Map + Context Engine Dashboard</title>
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
  .page {{ max-width: 1200px; margin: 0 auto; padding: 32px 24px 56px; }}
  .info-banner {{
    background: var(--accent-soft); border: 1px solid var(--border-strong); color: var(--ink);
    border-radius: 5px; padding: 12px 16px; font-size: 13.5px; margin-bottom: 26px; line-height: 1.6;
  }}
  header.masthead {{ border-bottom: 1px solid var(--border); padding-bottom: 18px; margin-bottom: 22px; }}
  .kicker {{ font-family: var(--mono); font-size: 11.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-faint); margin: 0 0 8px; }}
  h1 {{ font-family: var(--serif); font-weight: 600; font-size: clamp(24px, 3.4vw, 32px); margin: 0 0 8px; text-wrap: balance; color: var(--ink); }}
  .sub {{ margin: 0; font-size: 14px; color: var(--ink-muted); }}
  .summary-strip {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1px;
    background: var(--border); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 26px;
  }}
  .summary-cell {{ background: var(--surface); padding: 14px 18px; }}
  .summary-cell .label {{ font-family: var(--mono); font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-faint); margin-bottom: 4px; }}
  .summary-cell .val {{ font-family: var(--mono); font-size: 17px; font-weight: 600; }}
  .pnl-pos {{ color: var(--pnl-pos); }}
  .pnl-neg {{ color: var(--pnl-neg); }}
  .price-unavailable {{ color: var(--ink-faint); font-style: italic; text-align: center; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--surface); border: 1px solid var(--border); border-radius: 7px; overflow: hidden; font-size: 13px; }}
  th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); font-family: var(--mono); }}
  th {{ background: var(--surface-2); font-size: 10.5px; letter-spacing: 0.05em; text-transform: uppercase; color: var(--ink-faint); }}
  .table-wrap {{ overflow-x: auto; margin-bottom: 24px; }}
  footer {{ font-size: 12px; color: var(--ink-faint); text-align: center; margin-top: 20px; }}
</style>

<div class="page">
  <div class="info-banner">
    <strong>MAP + CONTEXT LIVE ENGINE</strong> &mdash; the real, running 48-symbol paper engine (reaction-at-a-level
    entries, close-based invalidation, NIFTY-regime context gating). Completely separate from Chart Studies Monitor.
    No order-placement code exists anywhere in this path.
  </div>

  <header class="masthead">
    <p class="kicker">Map + Context &middot; Live Trades</p>
    <h1>Map + Context Engine Dashboard</h1>
    <p class="sub">Generated {state['generated_at'][:16]} IST &mdash; NIFTY 50 regime: {state['index_regime']}</p>
  </header>

  <div class="summary-strip">
    <div class="summary-cell"><div class="label">Symbols Tracked</div><div class="val">{len(symbols)}</div></div>
    <div class="summary-cell"><div class="label">Open Positions</div><div class="val">{total_open}</div></div>
    <div class="summary-cell"><div class="label">Closed Today</div><div class="val">{total_closed}</div></div>
    <div class="summary-cell"><div class="label">Net Closed P&amp;L</div><div class="val">{_fmt_pnl(net_closed_pnl) if total_closed else 'n/a'}</div></div>
    <div class="summary-cell"><div class="label">Currently Testing a Level</div><div class="val" style="font-size:12px">{watching_text}</div></div>
  </div>

  <div class="table-wrap">
    <table>
      <thead><tr>
        <th>Symbol</th><th>Status</th><th>Entry Time</th><th>Entry</th><th>Stop</th><th>Target</th>
        <th>Exit / Mark</th><th>Net P&amp;L</th><th>Gross P&amp;L</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

  <footer>Source: map_context_live_state.json &middot; regenerate with <code>python map_context_engine_dashboard.py</code></footer>
</div>
"""
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({total_open} open, {total_closed} closed, net closed P&L Rs.{net_closed_pnl:,.2f})")


if __name__ == "__main__":
    main()
