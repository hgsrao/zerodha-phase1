"""Trade-by-trade comparison of an R8 shadow replay and an R8 paper replay of the same data.

    python revision8_avr/compare_shadow_paper.py <shadow_out_dir> <paper_out_dir> [--md report.md]

Both directories are ``run_r8_replay.py --out`` directories.  Trades are matched by (symbol, side,
entry time).  For every difference the report shows the tighten-only limits the paper run had in
force at the entry (and exit), read from the paper run's ``r8_adaptation.jsonl``, next to the
contract values, and names the limit that can explain it:

  size changed      -> per-symbol exposure / gross exposure / dispatch ceiling at entry
  exit changed      -> max hold (reason contains "max_hold"), drawdown halt, a target moved by the
                       raised minimum reward:risk, or a different path
  entry only shadow -> blocked in paper: max positions, entry confidence, min reward:risk, gross cap
  entry only paper  -> capacity or timing freed by an earlier difference

It reads files only; it runs nothing.
"""
import argparse
import bisect
import json
from pathlib import Path

import pandas as pd


def load(out_dir):
    out_dir = Path(out_dir)
    summary = json.loads((out_dir / 'summary.json').read_text())
    if summary.get('mode') != 'r8':
        raise SystemExit(f'{out_dir}: not an R8 replay')
    log = out_dir / 'r8_adaptation.jsonl'
    entries = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return summary, entries


def applied_timeline(entries):
    """[(timestamp, applied profile)] in log order; the profile holds until the next change."""
    out, current = [], None
    for e in entries:
        if e.get('applied'):
            current = e['applied']['new']
        if current is not None and (not out or out[-1][1] is not current):
            out.append((pd.Timestamp(e['timestamp']), current))
    return out


def limits_at(timeline, ts):
    if not timeline or ts is None:
        return None
    times = [t for t, _ in timeline]
    i = bisect.bisect_right(times, pd.Timestamp(ts)) - 1
    return None if i < 0 else timeline[i][1]


def key(t):
    return (t['symbol'], t['side'], str(pd.Timestamp(t['entry_timestamp'])))


def explain(kind, s, p, at_entry, at_exit, contract):
    if at_entry is None:
        return 'no paper profile in force yet'
    se, st = at_entry['safety_and_execution'], at_entry['strategy']
    gross = f"gross {contract['gross']:.3f}->{se['max_gross_exposure_fraction']:.3f}"
    symbol = f"per-symbol {contract['symbol']:.3f}->{se['max_exposure_per_symbol_fraction']:.3f}"
    if kind == 'size':
        return f"sizing: {symbol}, {gross}, dispatch ceiling {at_entry['dispatch_ceiling']:.3f}"
    if kind == 'exit':
        reason = str(p.get('reason'))
        prof = at_exit or at_entry
        if 'max_hold' in reason:
            return f"max hold {prof['strategy']['max_hold_bars']} bars (base {contract['max_hold']})"
        if 'drawdown' in reason:
            return f"drawdown halt {prof['safety_and_execution']['drawdown_halt_threshold']:.3f}"
        if s.get('reason') == p.get('reason') and 'target' in reason and s.get('exit_price') != p.get('exit_price'):
            return (f"target moved {s.get('exit_price'):.2f} -> {p.get('exit_price'):.2f}: the entry plan enforces "
                    f"min R:R {st['min_risk_reward_ratio']:.2f} (paper) on the target distance")
        return f"exit path changed ({s.get('reason')} -> {p.get('reason')})"
    if kind == 'blocked':
        return (f"max positions {se['max_concurrent_positions']}, entry confidence {st['entry_confidence_threshold']:.3f}, "
                f"min R:R {st['min_risk_reward_ratio']:.2f}, {gross}")
    return 'capacity / timing freed by an earlier difference'


def compare(shadow_dir, paper_dir):
    s_sum, _ = load(shadow_dir)
    p_sum, p_log = load(paper_dir)
    if p_sum.get('r8', {}).get('adaptation', {}).get('mode') != 'paper':
        raise SystemExit(f'{paper_dir}: not a paper-mode run')
    eff = p_sum.get('effective_profile', {})
    contract = {'gross': float(eff.get('max_gross_exposure_fraction') or 0.0),
                'symbol': float(eff.get('max_exposure_per_symbol_fraction') or 0.0),
                'max_hold': (p_log[0]['applied']['new']['strategy']['max_hold_bars'] if p_log and p_log[0].get('applied') else None)}
    timeline = applied_timeline(p_log)
    shadow = {key(t): t for t in s_sum['trades']}
    paper = {key(t): t for t in p_sum['trades']}
    rows = []
    for k in sorted(set(shadow) | set(paper), key=lambda x: (x[2], x[0])):
        s, p = shadow.get(k), paper.get(k)
        at_entry = limits_at(timeline, k[2])
        if s and p:
            kinds = []
            if s['quantity'] != p['quantity']:
                kinds.append('size')
            if s['exit_timestamp'] != p['exit_timestamp'] or s['reason'] != p['reason']:
                kinds.append('exit')
            if not kinds:
                continue
            at_exit = limits_at(timeline, p.get('exit_timestamp'))
            rows.append({'symbol': k[0], 'side': k[1], 'entry': k[2], 'change': '+'.join(kinds),
                         'qty': f"{s['quantity']} -> {p['quantity']}",
                         'exit': f"{s['reason']} @ {s['exit_timestamp']} -> {p['reason']} @ {p['exit_timestamp']}",
                         'pnl_delta': float(p['net_pnl']) - float(s['net_pnl']),
                         'cause': '; '.join(explain(x, s, p, at_entry, at_exit, contract) for x in kinds)})
        elif s:
            rows.append({'symbol': k[0], 'side': k[1], 'entry': k[2], 'change': 'blocked in paper',
                         'qty': f"{s['quantity']} -> 0", 'exit': f"{s['reason']} @ {s['exit_timestamp']} -> none",
                         'pnl_delta': -float(s['net_pnl']), 'cause': explain('blocked', s, None, at_entry, None, contract)})
        else:
            rows.append({'symbol': k[0], 'side': k[1], 'entry': k[2], 'change': 'new in paper',
                         'qty': f"0 -> {p['quantity']}", 'exit': f"none -> {p['reason']} @ {p['exit_timestamp']}",
                         'pnl_delta': float(p['net_pnl']), 'cause': explain('new', None, p, at_entry, None, contract)})
    totals = {'shadow_trades': len(shadow), 'paper_trades': len(paper),
              'shadow_net_pnl': sum(float(t['net_pnl']) for t in shadow.values()),
              'paper_net_pnl': sum(float(t['net_pnl']) for t in paper.values()),
              'unchanged_trades': len(set(shadow) & set(paper)) - sum(1 for r in rows if r['change'] not in ('blocked in paper', 'new in paper')),
              'by_change': {}}
    for r in rows:
        b = totals['by_change'].setdefault(r['change'], {'trades': 0, 'pnl_delta': 0.0})
        b['trades'] += 1
        b['pnl_delta'] += r['pnl_delta']
    return rows, totals


def markdown(rows, totals):
    lines = ['# R8 shadow vs paper: trade-by-trade', '',
             f"Shadow: {totals['shadow_trades']} trades, net P&L {totals['shadow_net_pnl']:.2f}.  "
             f"Paper: {totals['paper_trades']} trades, net P&L {totals['paper_net_pnl']:.2f}.  "
             f"Difference {totals['paper_net_pnl'] - totals['shadow_net_pnl']:+.2f}.", '',
             '| Change | Trades | P&L delta |', '|---|---|---|']
    lines += [f"| {k} | {v['trades']} | {v['pnl_delta']:+.2f} |" for k, v in sorted(totals['by_change'].items())]
    lines += ['', f"Trades identical in both runs: {totals['unchanged_trades']}", '',
              '| Symbol | Side | Entry | Change | Quantity | Exit | P&L delta | Limit in force (paper) |',
              '|---|---|---|---|---|---|---|---|']
    lines += [f"| {r['symbol']} | {r['side']} | {r['entry']} | {r['change']} | {r['qty']} | {r['exit']} | "
              f"{r['pnl_delta']:+.2f} | {r['cause']} |" for r in rows]
    if not rows:
        lines.append('| — | | | no differences | | | | |')
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('shadow_dir')
    ap.add_argument('paper_dir')
    ap.add_argument('--md', type=Path)
    a = ap.parse_args()
    rows, totals = compare(a.shadow_dir, a.paper_dir)
    text = markdown(rows, totals)
    if a.md:
        a.md.write_text(text)
    print(text)


if __name__ == '__main__':
    main()
