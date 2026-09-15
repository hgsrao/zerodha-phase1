#!/usr/bin/env python3
"""Production optimizer for the real frozen ADANIENT dataset.

This is the first optimizer pass. It keeps the canonical registry contract and the
hardcoded safety layer frozen, and only varies the 45 approved calibration values.
"""

from __future__ import annotations

import hashlib
import json
import random
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from production_trading_engine import ProductionTradingEngine

DATA_DIR = '/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824'


class ProductionOptimizer:
    def __init__(self, symbol: str = 'ADANIENT', data_dir: str = DATA_DIR, seed: int | None = None):
        self.symbol = symbol
        self.data_dir = data_dir
        self.registry = CanonicalParameterRegistry()
        self.rng = random.Random(seed)
        self.seed = seed
        self.windows = {
            'warmup': (pd.Timestamp('2023-07-03 09:15:00+05:30'), pd.Timestamp('2023-08-13 15:14:00+05:30')),
            'train': (pd.Timestamp('2023-08-14 09:15:00+05:30'), pd.Timestamp('2024-08-31 15:14:00+05:30')),
            'validation': (pd.Timestamp('2024-09-01 09:15:00+05:30'), pd.Timestamp('2025-08-31 15:14:00+05:30')),
            'test': (pd.Timestamp('2025-09-01 09:15:00+05:30'), pd.Timestamp('2026-08-24 15:14:00+05:30')),
        }

    def default_config(self) -> Dict[str, Any]:
        base = {name: spec.default for name, spec in self.registry.params.items()}
        base['symbols_to_trade'] = [self.symbol]
        base['exclude_symbols'] = []
        base['data_validation_mode'] = 'strict'
        base['order_type'] = 'MARKET'
        base['trading_hours_start'] = '09:15'
        base['trading_hours_end'] = '15:30'
        base['capital_allocation_mode'] = 'equal'
        return base

    def split_windows(self, df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        data = df.copy()
        data['timestamp'] = pd.to_datetime(data['timestamp'], utc=False)
        slices: Dict[str, pd.DataFrame] = {}
        for key, (start, end) in self.windows.items():
            mask = data['timestamp'].between(start, end)
            slices[key] = data.loc[mask].reset_index(drop=True)
        return slices

    def load_symbol_data(self) -> pd.DataFrame:
        path = f'{self.data_dir}/NSE_{self.symbol}_minute_2023-07-03_2026-08-24.csv'
        df = pd.read_csv(path)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=False)
        return df.sort_values('timestamp').reset_index(drop=True)

    def param_space(self) -> Dict[str, Tuple[float, float, str]]:
        specs = {}
        for name, spec in self.registry.params.items():
            if not spec.calibratable:
                continue
            if spec.param_type == 'int':
                specs[name] = (float(spec.minimum), float(spec.maximum), 'int')
            elif spec.param_type == 'float':
                specs[name] = (float(spec.minimum), float(spec.maximum), 'float')
            elif spec.param_type == 'str':
                specs[name] = ('', '', 'str')
        return specs

    def build_candidates(self, count: int = 10) -> List[Dict[str, Any]]:
        base = {name: spec.default for name, spec in self.registry.params.items()}
        candidates: List[Dict[str, Any]] = []
        for _ in range(count):
            cand = deepcopy(base)
            cand['symbols_to_trade'] = [self.symbol]
            cand['exclude_symbols'] = []
            cand['data_validation_mode'] = 'strict'
            cand['order_type'] = 'MARKET'
            cand['trading_hours_start'] = '09:15'
            cand['trading_hours_end'] = '15:30'
            cand['capital_allocation_mode'] = 'equal'
            for name, spec in self.registry.params.items():
                if not spec.calibratable:
                    continue
                if spec.param_type == 'int':
                    low, high, _ = self.param_space()[name]
                    cand[name] = self.rng.randint(int(low), int(high))
                elif spec.param_type == 'float':
                    low, high, _ = self.param_space()[name]
                    cand[name] = round(self.rng.uniform(low, high), 6)
            candidates.append(cand)
        return candidates

    def evaluate_candidate(self, candidate: Dict[str, Any], df: pd.DataFrame) -> Dict[str, Any]:
        engine = ProductionTradingEngine(symbol=self.symbol, data_dir=self.data_dir)
        result = engine.run_trading_cycle(df, effective_config=candidate)
        trade_count = int(result.get('trade_count', 0))
        total_pnl = float(result.get('total_pnl', 0.0))
        avg_pnl = float(result.get('avg_pnl', 0.0))
        win_rate = float(result.get('win_rate', 0.0))
        final_cash = float(result.get('final_cash', 0.0))

        score = total_pnl + 0.5 * final_cash + 50.0 * avg_pnl + 10.0 * win_rate
        return {
            'score': score,
            'candidate': candidate,
            'trade_count': trade_count,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'win_rate': win_rate,
            'final_cash': final_cash,
        }

    def freeze_config(self, candidate: Dict[str, Any], out_dir: str = 'output') -> str:
        Path(out_dir).mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        candidate_digest = hashlib.sha256(
            json.dumps(candidate, sort_keys=True, separators=(',', ':')).encode('utf-8')
        ).hexdigest()[:16]
        path = Path(out_dir) / f'production_config_{self.symbol}_seed{self.seed}_{candidate_digest}.json'
        payload = {
            'symbol': self.symbol,
            'seed': self.seed,
            'created_at_utc': stamp,
            'candidate': candidate,
            'registry_identity_sha256': self.registry.identity_sha256(),
            'windows': {k: [str(v[0]), str(v[1])] for k, v in self.windows.items()},
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
        return str(path)

    def run(self, candidate_count: int = 8) -> Dict[str, Any]:
        df = self.load_symbol_data()
        split = self.split_windows(df)
        train_df = split['train']
        validation_df = split['validation']
        candidates = self.build_candidates(count=candidate_count)

        train_results = [self.evaluate_candidate(c, train_df) for c in candidates]
        train_results.sort(key=lambda x: x['score'], reverse=True)
        top_train_candidates = [item['candidate'] for item in train_results[:min(5, len(train_results))]]

        validation_results = [self.evaluate_candidate(c, validation_df) for c in top_train_candidates]
        validation_results.sort(key=lambda x: x['score'], reverse=True)

        best = validation_results[0]
        winner = best['candidate']
        frozen_path = self.freeze_config(winner)
        return {
            'best_candidate': winner,
            'best_score': best['score'],
            'selected_on': 'validation',
            'frozen_config_path': frozen_path,
            'train_results': train_results,
            'validation_results': validation_results,
        }

    def save_results(self, result: List[Dict[str, Any]], out_dir: str = 'output') -> str:
        Path(out_dir).mkdir(exist_ok=True)
        stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        path = Path(out_dir) / f'optimizer_results_{self.symbol}_{stamp}.json'
        payload = {
            'symbol': self.symbol,
            'generated_at_utc': stamp,
            'results': [
                {'score': item['score'], 'candidate': item['candidate']} for item in result
            ],
            'windows': {k: [str(v[0]), str(v[1])] for k, v in self.windows.items()},
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
        return str(path)


def main() -> None:
    opt = ProductionOptimizer(symbol='ADANIENT', seed=123)
    result = opt.run(candidate_count=5)
    print('BEST_SCORE=', round(float(result['best_score']), 6))
    print('SELECTED_ON=', result['selected_on'])
    print('FROZEN_CONFIG=', result['frozen_config_path'])
    print('BEST_KEYS=', sorted(result['best_candidate'].keys())[:10])


if __name__ == '__main__':
    main()
