"""compare_shadow_paper: matching and attribution on small hand-made replay outputs."""
import json

import pytest

from .compare_shadow_paper import compare

PROFILE = {'version': 1, 'dispatch_ceiling': 0.3, 'dispatch_floor': 0.07,
           'strategy': {'entry_confidence_threshold': 0.18, 'min_risk_reward_ratio': 1.67, 'min_hold_bars': 2, 'max_hold_bars': 55},
           'safety_and_execution': {'max_concurrent_positions': 4, 'max_gross_exposure_fraction': 0.44,
                                    'max_exposure_per_symbol_fraction': 0.13, 'drawdown_halt_threshold': 0.23}}


def trade(symbol, entry, qty, exit_ts, reason, exit_price, pnl):
    return {'symbol': symbol, 'side': 'BUY', 'entry_timestamp': entry, 'exit_timestamp': exit_ts, 'quantity': qty,
            'entry_price': 100.0, 'exit_price': exit_price, 'reason': reason, 'net_pnl': pnl, 'costs': 1.0}


def write(path, mode, trades, log):
    path.mkdir()
    (path / 'summary.json').write_text(json.dumps({
        'mode': 'r8', 'trades': trades, 'r8': {'adaptation': {'mode': mode}},
        'effective_profile': {'max_gross_exposure_fraction': 0.5, 'max_exposure_per_symbol_fraction': 0.15}}))
    (path / 'r8_adaptation.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in log))


@pytest.fixture
def runs(tmp_path):
    t0 = '2024-03-04 09:16:00+05:30'
    shadow = [trade('A', '2024-03-04 10:00:00+05:30', 10, '2024-03-04 10:05:00+05:30', 'target', 101.0, 10.0),
              trade('B', '2024-03-04 10:10:00+05:30', 20, '2024-03-04 10:30:00+05:30', 'target', 102.0, 40.0),
              trade('C', '2024-03-04 10:20:00+05:30', 5, '2024-03-04 11:20:00+05:30', 'max_hold', 99.0, -5.0),
              trade('D', '2024-03-04 10:40:00+05:30', 8, '2024-03-04 10:50:00+05:30', 'stop', 98.0, -16.0)]
    paper = [trade('A', '2024-03-04 10:00:00+05:30', 10, '2024-03-04 10:06:00+05:30', 'target', 101.5, 15.0),
             trade('B', '2024-03-04 10:10:00+05:30', 15, '2024-03-04 10:30:00+05:30', 'target', 102.0, 30.0),
             trade('C', '2024-03-04 10:20:00+05:30', 5, '2024-03-04 11:15:00+05:30', 'max_hold', 99.5, -2.5)]
    write(tmp_path / 's', 'shadow', shadow, [{'timestamp': t0}])
    write(tmp_path / 'p', 'paper', paper, [{'timestamp': t0, 'applied': {'old': None, 'new': PROFILE}}])
    return tmp_path / 's', tmp_path / 'p'


def test_every_difference_is_matched_and_attributed(runs):
    rows, totals = compare(*runs)
    by = {r['symbol']: r for r in rows}
    assert set(by) == {'A', 'B', 'C', 'D'}
    assert by['A']['change'] == 'exit' and 'min R:R 1.67' in by['A']['cause']
    assert by['B']['change'] == 'size' and 'per-symbol 0.150->0.130' in by['B']['cause']
    assert by['C']['change'] == 'exit' and 'max hold 55' in by['C']['cause']
    assert by['D']['change'] == 'blocked in paper' and 'max positions 4' in by['D']['cause']
    assert totals['paper_net_pnl'] - totals['shadow_net_pnl'] == pytest.approx(sum(r['pnl_delta'] for r in rows))


def test_refuses_a_non_paper_run_as_the_paper_side(runs):
    with pytest.raises(SystemExit):
        compare(runs[0], runs[0])
