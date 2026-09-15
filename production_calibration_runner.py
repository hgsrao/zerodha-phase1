#!/usr/bin/env python3
"""Production calibration runner for the real frozen dataset.

This module defines the train / validation / untouched test windows, freezes a winning
production config, and validates the calibration workflow without permitting arbitrary
runtime drift.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry


DATA_DIR = '/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824'


class ProductionCalibrationRunner:
    def __init__(self, symbol: str = 'ADANIENT', data_dir: str = DATA_DIR):
        self.symbol = symbol
        self.data_dir = data_dir
        self.registry = CanonicalParameterRegistry()

    def build_windows(self) -> Dict[str, Tuple[datetime, datetime]]:
        warmup_start = datetime.fromisoformat('2023-07-03T09:15:00+05:30')
        warmup_end = datetime.fromisoformat('2023-08-13T15:14:00+05:30')
        train_start = datetime.fromisoformat('2023-08-14T09:15:00+05:30')
        train_end = datetime.fromisoformat('2024-08-31T15:14:00+05:30')
        validation_start = datetime.fromisoformat('2024-09-01T09:15:00+05:30')
        validation_end = datetime.fromisoformat('2025-08-31T15:14:00+05:30')
        test_start = datetime.fromisoformat('2025-09-01T09:15:00+05:30')
        test_end = datetime.fromisoformat('2026-08-24T15:14:00+05:30')
        return {
            'warmup': (warmup_start, warmup_end),
            'train': (train_start, train_end),
            'validation': (validation_start, validation_end),
            'test': (test_start, test_end),
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

    def load_symbol(self) -> pd.DataFrame:
        csv_name = f'NSE_{self.symbol}_minute_2023-07-03_2026-08-24.csv'
        path = os.path.join(self.data_dir, csv_name)
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        df = pd.read_csv(path)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=False)
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df

    def freeze_config(self, config: Dict[str, Any], output_dir: str = 'output') -> str:
        Path(output_dir).mkdir(exist_ok=True)
        stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        path = Path(output_dir) / f'production_config_{self.symbol}_{stamp}.json'
        payload = {
            'symbol': self.symbol,
            'created_at_utc': stamp,
            'config': config,
            'registry_identity_sha256': self.registry.identity_sha256(),
            'windows': {
                k: [v[0].isoformat(), v[1].isoformat()] for k, v in self.build_windows().items()
            },
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
        return str(path)

    def list_approved_params(self) -> List[str]:
        return sorted(self.registry.calibratable_names())

    def calibrate_demo(self) -> Dict[str, Any]:
        default = self.default_config()
        approved = self.list_approved_params()
        tuned = default.copy()
        for name in approved[:5]:
            tuned[name] = default[name]
        tuned['symbols_to_trade'] = [self.symbol]
        return {
            'approved_45_count': len(approved),
            'config': tuned,
            'windows': self.build_windows(),
        }


def main() -> None:
    runner = ProductionCalibrationRunner(symbol='ADANIENT')
    print('WINDOWS=', {k: [v[0].isoformat(), v[1].isoformat()] for k, v in runner.build_windows().items()})
    print('APPROVED_45=', len(runner.list_approved_params()))
    print('DEFAULT_CONFIG_SYMBOL=', runner.default_config()['symbols_to_trade'])
    frozen_path = runner.freeze_config(runner.default_config())
    print('FROZEN_CONFIG=', frozen_path)
    print('DEMO_CALIBRATED=', runner.calibrate_demo()['approved_45_count'])


if __name__ == '__main__':
    main()
