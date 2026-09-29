#!/usr/bin/env python3
"""Compare baseline/refactored full-authority five-bay traces; assert stop telemetry."""
import argparse
import json
from collections import Counter
from pathlib import Path

BAYS = ('GTG1_HEAVY_INDUSTRY', 'GTG2_TECH_TELECOM', 'CSTG1_BFSI', 'CSTG2_CONSUMER_AUTO', 'BPSTG_HEALTHCARE')


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarize(root, bay, block):
    directory = root/f'block{block}'/bay/'full'
    summary = json.loads((directory/'summary.json').read_text())
    trades = json.loads((directory/'trades.json').read_text())
    mfe = [t['mfe_r'] for t in trades if t.get('mfe_r') is not None]
    inner = read_rows(directory/'inner.jsonl')
    events = read_rows(directory/'events.jsonl')
    controlled = [r for r in inner if 'effective_gap_r' in r]
    stops = [e for e in events if e['event_type'] == 'GOVERNOR_STOP_UPDATE']
    pid_moves = []
    for event in stops:
        before, after = event['stop_before'], event['stop_after']
        assert after >= before if event['side'] == 'BUY' else after <= before
        assert event['effective_from'] == 'NEXT_BAR'
        detail = event.get('inner') or {}
        if after != before and detail.get('pid_incremental_floor_r', 0) > 1e-12:
            pid_moves.append(event)
    policy = summary.get('position_policy')
    if policy:
        for r in controlled:
            expected_gap = max(policy['minimum_gap_r'], policy['base_gap_r'] -
                               policy['pid_alpha_r']*max(0., min(r['control_u'], policy['pid_u_max'])))
            assert abs(r['effective_gap_r'] - expected_gap) < 1e-12
            assert r['floor'] >= r['floor_before']
            if not r['trailing_active']:
                assert r['floor'] == -1., 'pre-activation tightening'
    return dict(trades=summary['metrics']['completed_trades'],
                net_pnl=summary['metrics']['net_pnl'], hold=summary['bars_held'],
                mfe_r=dict(n=len(mfe), mean=sum(mfe)/len(mfe) if mfe else None, max=max(mfe) if mfe else None),
                exit_reasons=summary['exit_reason_detail'],
                position_evaluations=len(inner), activated_evaluations=sum(r['trailing_active'] for r in controlled),
                gap_modulated_evaluations=sum(r['effective_gap_r'] < r['base_gap_r'] for r in controlled),
                pid_incremental_floor_evaluations=sum(r['pid_incremental_floor_r'] > 1e-12 for r in controlled),
                applied_pid_incremental_stop_updates=len(pid_moves),
                effective_gap_values=sorted({r['effective_gap_r'] for r in controlled}),
                pid_stop_examples=pid_moves[:3], position_policy=policy)


def compare(before, after, block=1, bays=BAYS, require_parity=False):
    bmeta = json.loads((before/'overview.json').read_text())
    ameta = json.loads((after/'overview.json').read_text())
    assert bmeta['params'] == ameta['params'], 'parameter mismatch'
    assert bmeta['protocol_id'] == ameta['protocol_id'], 'protocol mismatch'
    result = {}
    for bay in bays:
        select = lambda meta: next(r for r in meta['runs'] if r['block'] == block and r['bay'] == bay and r['authority'] == 'full')
        b, a = select(bmeta), select(ameta)
        for key in ('sessions','symbols','slice_sha256'):
            assert b[key] == a[key], f'{bay}: {key} mismatch'
        if require_parity:
            for root in (before, after):
                summary = json.loads((root/f'block{block}'/bay/'full/summary.json').read_text())
                if summary.get('position_policy') is not None:
                    raise ValueError('parity check requires unpolicied traces')
            left = json.loads((before/f'block{block}'/bay/'full/trades.json').read_text())
            right = json.loads((after/f'block{block}'/bay/'full/trades.json').read_text())
            if left != right:
                raise ValueError(f'{bay}: legacy trade-list parity failed')
        result[bay] = dict(before=summarize(before,bay,block), after=summarize(after,bay,block))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('before', type=Path)
    p.add_argument('after', type=Path)
    p.add_argument('--block', type=int, default=1)
    p.add_argument('--bays', default=','.join(BAYS), help='Comma-separated canonical bay IDs')
    p.add_argument('--output', type=Path)
    p.add_argument('--require-parity', action='store_true', help='Require identical unpolicied trade lists')
    args = p.parse_args()
    bays = args.bays.split(',')
    if len(set(bays)) != len(bays) or not set(bays) <= set(BAYS):
        p.error('unknown or duplicate bay')
    result = compare(args.before,args.after,args.block,bays,args.require_parity)
    payload = json.dumps(result,indent=2)
    if args.output:
        args.output.write_text(payload+'\n')
    print(payload)

if __name__ == '__main__':
    main()
