"""Opt-in offline governor experiment; independent of the sealed Stage-A registry."""
from dataclasses import asdict, dataclass
from math import isfinite


@dataclass(frozen=True)
class GovernorPositionPolicy:
    base_gap_r: float = 0.45
    pid_alpha_r: float = 0.10
    pid_u_max: float = 2.0
    minimum_gap_r: float = 0.15
    mfe_activation_r: float = 0.30
    conviction_absolute_threshold: float = 0.20
    conviction_drop_threshold: float = 0.10
    conviction_persistence_bars: int = 2

    def __post_init__(self):
        values = asdict(self)
        if any(isinstance(v, bool) or not isfinite(float(v)) for v in values.values()):
            raise ValueError('position policy values must be finite numbers')
        if not 0 < self.minimum_gap_r <= self.base_gap_r:
            raise ValueError('require 0 < minimum_gap_r <= base_gap_r')
        if self.pid_alpha_r < 0 or self.pid_u_max <= 0 or self.mfe_activation_r < 0:
            raise ValueError('invalid PID stop bounds or MFE activation')
        if not 0 < self.conviction_absolute_threshold <= 1:
            raise ValueError('absolute conviction threshold must be in (0, 1]')
        if not 0 < self.conviction_drop_threshold <= 1:
            raise ValueError('conviction drop threshold must be in (0, 1]')
        if (not isinstance(self.conviction_persistence_bars, int)
                or self.conviction_persistence_bars < 2):
            raise ValueError('conviction persistence must be an integer >= 2')


def absolute_conviction(side, signal, studies):
    """Same raw measures at entry-decision time and during the position; validate before masking."""
    values = (float(signal.exit_confidence), float(studies['confidence']))
    if not all(isfinite(x) and 0 <= x <= 1 for x in values):
        raise ValueError('invalid absolute conviction')
    directions = (signal.direction, studies['direction'])
    if any(isinstance(x, bool) or x not in (-1, 0, 1) for x in directions):
        raise ValueError('invalid conviction direction')
    sign = 1 if side == 'BUY' else -1 if side == 'SELL' else None
    if sign is None:
        raise ValueError('invalid position side')
    aligned = [0.0 if d == -sign else v for v, d in zip(values, directions)]
    return {'pa_exit_raw': values[0], 'studies_raw': values[1],
            'pa_direction': int(directions[0]), 'studies_direction': int(directions[1]),
            'conviction': min(aligned)}


def update_conviction(trade, current, timestamp, policy):
    """State belongs to one trade. Duplicate timestamps cannot advance persistence."""
    baseline = trade['governor_entry_conviction']
    entry = float(baseline['conviction'])
    value = float(current['conviction'])
    if not all(isfinite(x) and 0 <= x <= 1 for x in (entry, value)):
        raise ValueError('invalid entry/current conviction')
    drop = entry - value
    deteriorated = (value < policy.conviction_absolute_threshold
                    and drop >= policy.conviction_drop_threshold)
    state = trade.setdefault('governor_conviction_state', {'count': 0, 'timestamp': None})
    ts = str(timestamp)
    if state['timestamp'] != ts:
        state['count'] = state['count'] + 1 if deteriorated else 0
        state['timestamp'] = ts
    return {**current, 'entry': baseline, 'drop': drop,
            'deteriorated': deteriorated, 'consecutive_bars': state['count'],
            'fuel_cut_confirmed': state['count'] >= policy.conviction_persistence_bars}
