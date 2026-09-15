"""R1-C trade chart generator. LOCAL FILES ONLY - reads the durable
Evidence Ledger (`r1c_live_evidence_ledger.jsonl`) and the durable
per-symbol 1-minute bar CSVs (`r1c_live_bars/*.csv`) the live observer
already writes, and renders one price chart per `COMPOSITE_ENTRY`,
marking every stage: V2-C's permission grant, Pillar II's setup, Pillar
I's confirmation (= the actual entry), and the exit.

**Reuse, not reimplement, for the one thing not already stored**: the
Evidence Ledger's `COMPOSITE_ENTRY` detail carries entry/stop/target and
the setup/confirmation timestamps, but not the exact exit TIMESTAMP
(only `exit_price`/`exit_reason`, inherited from Pillar I's own frozen
trade-record schema, which was never designed to report one). This
module recovers it by calling Pillar I's own real, frozen
`fixed_exit_generic` function again, against the same day's real bars
and the same entry/stop/target already on record - a pure, deterministic
recomputation of a display-only detail, never a re-derivation of the
trade's actual economics (which are already frozen in the ledger).

No network access, no broker calls, no credentials - this module only
ever reads local files the observer already wrote.
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from r1c_live_pillar1_evaluator import pillar1  # noqa: E402  (reuses fixed_exit_generic)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as h:
        for line in h:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_symbol_bars(symbol: str, bars_dir: Path) -> list[dict]:
    path = bars_dir / f"{symbol.replace(' ', '_')}_1min.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as h:
        rows = list(csv.DictReader(h))
    for r in rows:
        r["open"] = float(r["open"]); r["high"] = float(r["high"])
        r["low"] = float(r["low"]); r["close"] = float(r["close"])
        r["volume"] = float(r.get("volume") or 0.0)
        r["dt"] = datetime.fromisoformat(r["timestamp"])
    rows.sort(key=lambda r: r["dt"])
    return rows


def find_composite_entries(ledger: list[dict], *, symbol: str | None = None) -> list[dict]:
    out = [e for e in ledger if e.get("decision") == "COMPOSITE_ENTRY"]
    if symbol:
        out = [e for e in out if e.get("symbol") == symbol]
    return out


# ---------------------------------------------------------------------------
# Recovering the exit timestamp - the one thing not already on record
# ---------------------------------------------------------------------------

def _pillar1_bar_from_row(row: dict) -> dict:
    """Matches the exact dict shape `fixed_exit_generic` expects
    (`dt`/`low`/`high`/`close`), reusing the real function unmodified."""
    return {"dt": row["dt"], "low": row["low"], "high": row["high"], "close": row["close"]}


def recover_exit_time(day_bars: list[dict], *, entry_time: str, entry_price: float,
                       stop: float, target: float) -> tuple[str | None, float | None, str | None]:
    """Calls Pillar I's own real, frozen `fixed_exit_generic` again -
    pure and deterministic given the same inputs already on record, so
    this recovers the display-only exit timestamp without re-deriving
    or second-guessing the trade's already-frozen economics."""
    entry_dt = datetime.fromisoformat(entry_time)
    day1 = [_pillar1_bar_from_row(r) for r in day_bars]
    if not day1:
        return None, None, None
    exit_dt, exit_px, reason = pillar1.fixed_exit_generic(day1, entry_dt, entry_price, stop, target)
    return exit_dt.isoformat(), exit_px, reason


# ---------------------------------------------------------------------------
# SVG chart rendering
# ---------------------------------------------------------------------------

@dataclass
class ChartMarkers:
    setup_time: str
    setup_price: float
    entry_time: str
    entry_price: float
    stop: float
    target: float
    exit_time: str | None
    exit_price: float | None
    exit_reason: str | None
    p_reverting: float | None
    granted_on: str | None


def render_candlestick_svg(day_bars: list[dict], markers: ChartMarkers, *, symbol: str, session_date: str) -> str:
    if not day_bars:
        return "<p>No bar data available for this session.</p>"

    W, H = 980, 420
    pad_left, pad_right, pad_top, pad_bottom = 56, 20, 24, 36
    plot_w = W - pad_left - pad_right
    plot_h = H - pad_top - pad_bottom

    lo = min(min(b["low"] for b in day_bars), markers.stop)
    hi = max(max(b["high"] for b in day_bars), markers.target)
    span = (hi - lo) or 1.0
    lo -= span * 0.05
    hi += span * 0.05
    span = hi - lo

    n = len(day_bars)
    slot_w = plot_w / max(n, 1)

    def x_of(i: float) -> float:
        return pad_left + i * slot_w

    def y_of(price: float) -> float:
        return pad_top + (hi - price) / span * plot_h

    def x_of_time(ts: str) -> float:
        target_dt = datetime.fromisoformat(ts)
        for i, b in enumerate(day_bars):
            if b["dt"] >= target_dt:
                return x_of(i + 0.5)
        return x_of(n - 0.5)

    parts: list[str] = []
    parts.append(
        f'<svg viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="1-minute price chart for {symbol} on {session_date}, with V2-C permission, '
        f'Pillar II setup, Pillar I entry, stop, target, and exit marked.">'
    )

    # Gridlines + y-axis labels (5 levels)
    for k in range(5):
        price = lo + span * k / 4
        y = y_of(price)
        parts.append(f'<line x1="{pad_left}" y1="{y:.1f}" x2="{W - pad_right}" y2="{y:.1f}" '
                      f'stroke="currentColor" stroke-width="0.5" opacity="0.15"/>')
        parts.append(f'<text x="{pad_left - 6}" y="{y + 3:.1f}" text-anchor="end" '
                      f'font-size="10.5" fill="currentColor" opacity="0.7">{price:.2f}</text>')

    # Candlesticks
    for i, b in enumerate(day_bars):
        cx = x_of(i + 0.5)
        up = b["close"] >= b["open"]
        color = "var(--chart-up)" if up else "var(--chart-down)"
        parts.append(f'<line x1="{cx:.1f}" y1="{y_of(b["high"]):.1f}" x2="{cx:.1f}" y2="{y_of(b["low"]):.1f}" '
                      f'stroke="{color}" stroke-width="1"/>')
        body_top = y_of(max(b["open"], b["close"]))
        body_bot = y_of(min(b["open"], b["close"]))
        body_h = max(body_bot - body_top, 0.6)
        body_w = max(slot_w * 0.6, 1.0)
        parts.append(f'<rect x="{cx - body_w/2:.1f}" y="{body_top:.1f}" width="{body_w:.1f}" '
                      f'height="{body_h:.1f}" fill="{color}"/>')

    # Stop / target horizontal lines
    for price, label, color in ((markers.stop, "STOP", "var(--chart-stop)"),
                                 (markers.target, "TARGET", "var(--chart-target)")):
        y = y_of(price)
        parts.append(f'<line x1="{pad_left}" y1="{y:.1f}" x2="{W - pad_right}" y2="{y:.1f}" '
                      f'stroke="{color}" stroke-width="1.2" stroke-dasharray="5 4"/>')
        parts.append(f'<text x="{W - pad_right - 4}" y="{y - 4:.1f}" text-anchor="end" '
                      f'font-size="10" fill="{color}" font-weight="700">{label} {price:.2f}</text>')

    # Setup / entry / exit vertical markers
    def marker(ts: str, price: float, label: str, color: str, dy: int) -> None:
        x = x_of_time(ts)
        y = y_of(price)
        parts.append(f'<line x1="{x:.1f}" y1="{pad_top}" x2="{x:.1f}" y2="{H - pad_bottom}" '
                      f'stroke="{color}" stroke-width="1" stroke-dasharray="2 3" opacity="0.55"/>')
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{color}" stroke="var(--chart-bg)" stroke-width="1.5"/>')
        parts.append(f'<text x="{x:.1f}" y="{pad_top - 8 + dy}" text-anchor="middle" font-size="10.5" '
                      f'fill="{color}" font-weight="700">{label}</text>')

    marker(markers.setup_time, markers.setup_price, "PILLAR II SETUP", "var(--chart-setup)", 0)
    marker(markers.entry_time, markers.entry_price, "ENTRY (PILLAR I CONFIRM)", "var(--chart-entry)", 14)
    if markers.exit_time and markers.exit_price is not None:
        marker(markers.exit_time, markers.exit_price, f"EXIT ({markers.exit_reason})", "var(--chart-exit)", 28)

    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------

def build_trade_section_html(entry: dict, day_bars: list[dict]) -> str:
    detail = entry["detail"]
    symbol = entry["symbol"]
    session_date = entry["date"]

    exit_time, exit_price, exit_reason = recover_exit_time(
        day_bars, entry_time=detail["confirmation_entry_time"],
        entry_price=detail["confirmation_entry_price"],
        stop=detail["confirmation_stop"], target=detail["confirmation_target"],
    )

    markers = ChartMarkers(
        setup_time=detail["setup_entry_time"], setup_price=detail["setup_entry_price"],
        entry_time=detail["confirmation_entry_time"], entry_price=detail["confirmation_entry_price"],
        stop=detail["confirmation_stop"], target=detail["confirmation_target"],
        exit_time=exit_time, exit_price=exit_price, exit_reason=exit_reason,
        p_reverting=detail.get("p_reverting"), granted_on=detail.get("granted_on"),
    )
    svg = render_candlestick_svg(day_bars, markers, symbol=symbol, session_date=session_date)

    return f"""
<section class="trade-card">
  <div class="trade-head">
    <h2>{symbol}</h2>
    <span class="trade-date">{session_date}</span>
  </div>
  <div class="trade-meta">
    <span><strong>V2-C permitted</strong> {markers.granted_on or '—'} (p={markers.p_reverting:.3f})</span>
    <span><strong>Pillar II setup</strong> {markers.setup_time} @ {markers.setup_price:.2f}</span>
    <span><strong>Pillar I entry</strong> {markers.entry_time} @ {markers.entry_price:.2f}</span>
    <span><strong>Stop</strong> {markers.stop:.2f}</span>
    <span><strong>Target</strong> {markers.target:.2f}</span>
    <span><strong>Exit</strong> {(markers.exit_time or '—')} @ {(f'{markers.exit_price:.2f}' if markers.exit_price is not None else '—')} ({markers.exit_reason or '—'})</span>
  </div>
  <figure class="chart-fig">
    {svg}
    <figcaption>1-minute price action for {symbol}, {session_date} — dashed vertical lines mark the setup, entry, and exit; dashed horizontal lines mark the frozen stop and target.</figcaption>
  </figure>
</section>
"""


def build_report_html(ledger_path: Path, bars_dir: Path, *, symbol: str | None = None) -> str:
    """Returns the BODY content only (trade cards, or an empty-state
    message) - `build_full_report_page` wraps this in the complete,
    styled, self-contained page the CLI actually writes to disk. Kept
    separate so tests can assert on the content itself without also
    re-parsing a full HTML document."""
    ledger = load_ledger(ledger_path)
    entries = find_composite_entries(ledger, symbol=symbol)

    sections = []
    for entry in entries:
        day_bars_all = load_symbol_bars(entry["symbol"], bars_dir)
        session_date = entry["date"]
        day_bars = [b for b in day_bars_all if b["timestamp"][:10] == session_date]
        sections.append(build_trade_section_html(entry, day_bars))

    body = "\n".join(sections) if sections else '<p class="empty">No COMPOSITE_ENTRY decisions in the ledger yet.</p>'
    return body


# Same design tokens and trade-card/chart-fig markup as the published
# demo artifact - a real trade must look exactly like the demo already
# shown, not fall back to bare unstyled text. Self-contained (no
# external fonts/scripts), matches both light and dark viewers.
_PAGE_STYLE = """
  :root {
    --bg: #f1f3f5; --surface: #ffffff; --surface-2: #e9ecef;
    --ink: #1a2130; --ink-muted: #5b6472; --ink-faint: #8993a3;
    --border: #d7dbe1; --border-strong: #b9c0cb;
    --accent: #2b3a67; --accent-soft: #eceef4;
    --chart-up: #2f8f5b; --chart-down: #c0453c; --chart-stop: #b3261e;
    --chart-target: #1f7a5c; --chart-setup: #5b6fd6; --chart-entry: #8a4fd1;
    --chart-exit: #c77a1f; --chart-bg: var(--surface);
    --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    --mono: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #13161c; --surface: #191d24; --surface-2: #21262f;
      --ink: #e6e9ef; --ink-muted: #9aa3b3; --ink-faint: #6b7484;
      --border: #323945; --border-strong: #454e5d;
      --accent: #8b9dd6; --accent-soft: #232a42;
      --chart-up: #6fcb96; --chart-down: #e08a7c; --chart-stop: #e79287;
      --chart-target: #7fc9a8; --chart-setup: #9aa8f0; --chart-entry: #c39ef2;
      --chart-exit: #e0ab6b; --chart-bg: var(--surface);
    }
  }
  :root[data-theme="dark"] {
    --bg: #13161c; --surface: #191d24; --surface-2: #21262f;
    --ink: #e6e9ef; --ink-muted: #9aa3b3; --ink-faint: #6b7484;
    --border: #323945; --border-strong: #454e5d;
    --accent: #8b9dd6; --accent-soft: #232a42;
    --chart-up: #6fcb96; --chart-down: #e08a7c; --chart-stop: #e79287;
    --chart-target: #7fc9a8; --chart-setup: #9aa8f0; --chart-entry: #c39ef2;
    --chart-exit: #e0ab6b; --chart-bg: var(--surface);
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); font-family: var(--sans); font-size: 15px; line-height: 1.55; }
  .page { max-width: 1080px; margin: 0 auto; padding: 32px 24px 56px; }
  header.masthead { border-bottom: 1px solid var(--border); padding-bottom: 18px; margin-bottom: 26px; }
  .kicker { font-family: var(--mono); font-size: 11.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-faint); margin: 0 0 8px; }
  h1 { font-family: var(--serif); font-weight: 600; font-size: clamp(24px, 3.4vw, 32px); margin: 0 0 8px; color: var(--ink); }
  .sub { margin: 0; font-size: 14px; color: var(--ink-muted); }
  .trade-card { background: var(--surface); border: 1px solid var(--border); border-radius: 7px; padding: 20px 22px 22px; margin-bottom: 24px; }
  .trade-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
  .trade-head h2 { font-family: var(--serif); font-size: 20px; margin: 0; color: var(--ink); }
  .trade-date { font-family: var(--mono); font-size: 12.5px; color: var(--ink-faint); }
  .trade-meta { display: flex; flex-wrap: wrap; gap: 8px 18px; font-size: 12.5px; color: var(--ink-muted); background: var(--surface-2); border: 1px solid var(--border); border-radius: 5px; padding: 10px 14px; margin-bottom: 16px; }
  .trade-meta strong { color: var(--ink); font-weight: 600; margin-right: 4px; }
  .chart-fig { margin: 0; }
  .chart-fig svg { width: 100%; height: auto; display: block; background: var(--chart-bg); border-radius: 5px; }
  .chart-fig figcaption { font-size: 12px; color: var(--ink-faint); margin-top: 8px; }
  .empty { font-size: 14px; color: var(--ink-muted); background: var(--surface); border: 1px solid var(--border); border-radius: 7px; padding: 16px 20px; }
"""


def build_full_report_page(body_html: str, *, symbol: str | None = None) -> str:
    """Wraps `build_report_html`'s output in the complete, styled,
    self-contained page - the thing the CLI actually writes. This is
    what makes a real trade look exactly like the already-published demo,
    not bare unstyled text."""
    subtitle = (f"Filtered to {symbol}." if symbol else
                "Every COMPOSITE_ENTRY logged so far — V2-C permission, Pillar II setup, "
                "Pillar I entry, stop, target, and exit all marked on the real price curve.")
    return f"""<title>R1-C Trade Chart</title>
<style>{_PAGE_STYLE}</style>
<div class="page">
  <header class="masthead">
    <p class="kicker">R1-C · Composite Engine · Trade Review</p>
    <h1>Trade Chart</h1>
    <p class="sub">{subtitle}</p>
  </header>
{body_html}
</div>
"""


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=PROJECT_ROOT / "r1c_live_evidence_ledger.jsonl")
    parser.add_argument("--bars-dir", type=Path, default=PROJECT_ROOT / "r1c_live_bars")
    parser.add_argument("--symbol", type=str, default=None)
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "r1c_trade_chart_report.html")
    args = parser.parse_args()

    html_body = build_report_html(args.ledger, args.bars_dir, symbol=args.symbol)
    full_page = build_full_report_page(html_body, symbol=args.symbol)
    args.out.write_text(full_page, encoding="utf-8")
    print(f"Wrote {args.out}")
