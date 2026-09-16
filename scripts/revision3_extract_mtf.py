import os, sys, json, logging, argparse
from pathlib import Path
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np

from revision2.mtf_causal_aligner import CausalMTFAligner
from revision2.cross_sectional_ranking import CrossSectionalRanking

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

ALL_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", 
    "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK", 
    "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", 
    "LT", "M&M", "MARUTI", "MAXHEALTH", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", 
    "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"
]

class MacroGateFilter:
    def __init__(self, vwap_window=30, min_breadth=0.45, min_vol_daily=0.0075):
        self.vwap_window = vwap_window
        self.min_breadth = min_breadth
        self.min_vol_daily = min_vol_daily

    def compute_gate(self, df_synthetic_index, df_universe_closes):
        idx_close = df_synthetic_index['close']
        sma_30 = idx_close.rolling(self.vwap_window, min_periods=5).mean()
        std_30 = idx_close.rolling(self.vwap_window, min_periods=5).std().replace(0, 1e-6)
        trend_z = (idx_close - sma_30) / std_30
        trend_pass = trend_z > 0.0

        ret_15m = df_universe_closes.pct_change(15, fill_method=None).fillna(0)
        breadth_15m = (ret_15m > 0).mean(axis=1)
        breadth_pass = breadth_15m >= self.min_breadth

        idx_1m_ret = idx_close.pct_change(fill_method=None).fillna(0)
        vol_60m_daily = idx_1m_ret.rolling(60, min_periods=10).std() * np.sqrt(375)
        vol_pass = vol_60m_daily >= self.min_vol_daily

        return (trend_pass & breadth_pass & vol_pass).astype(int)

class MomentumRSEngine:
    def __init__(self, vwap_window=30, atr_window=14):
        self.vwap_window = vwap_window
        self.atr_window = atr_window

    def compute_refined_rs(self, df_symbol, df_market):
        result = pd.DataFrame(index=df_symbol.index)
        sym_ret = df_symbol['close'].pct_change(fill_method=None).fillna(0)
        mkt_ret = df_market['close'].pct_change(fill_method=None).fillna(0)

        # 1. EMA-Weighted Excess Return
        excess_ret = sym_ret - mkt_ret
        weights = np.exp(np.linspace(-1, 0, 15))
        weights /= weights.sum()
        result['15m_ema_excess'] = excess_ret.rolling(15, min_periods=15).apply(lambda x: np.sum(weights * x), raw=True).fillna(0)

        # 2. Asymmetric Overextension Penalty
        typical_price = (df_symbol['high'] + df_symbol['low'] + df_symbol['close']) / 3.0
        roll_vol = df_symbol['volume'].rolling(self.vwap_window, min_periods=5).sum().replace(0, 1)
        roll_pv = (typical_price * df_symbol['volume']).rolling(self.vwap_window, min_periods=5).sum()
        vwap = roll_pv / roll_vol

        tr = pd.concat([
            df_symbol['high'] - df_symbol['low'],
            (df_symbol['high'] - df_symbol['close'].shift()).abs(),
            (df_symbol['low'] - df_symbol['close'].shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(self.atr_window, min_periods=5).mean().replace(0, 1e-6)

        vwap_dist = (df_symbol['close'] - vwap).abs() / atr
        penalty = np.where(vwap_dist <= 2.0, 1.0, np.maximum(0.0, 1.0 - ((vwap_dist - 2.0) / 1.5)))

        # 3. Volume Thrust
        vol_15m = df_symbol['volume'].rolling(15, min_periods=5).sum()
        avg_vol_15m = vol_15m.rolling(60, min_periods=15).mean().replace(0, 1)
        rel_vol = (vol_15m / avg_vol_15m).clip(0.5, 3.0)

        result['rs_momentum_raw'] = result['15m_ema_excess'] * penalty
        result['rs_thrust'] = result['rs_momentum_raw'] * rel_vol
        return result

class Revision3MTFExtractor:
    def __init__(self, data_source_dir, output_base_dir):
        self.data_source_dir = Path(data_source_dir)
        self.output_base_dir = Path(output_base_dir)
        self.aligner_5m = CausalMTFAligner(timeframe='5min')
        self.aligner_15m = CausalMTFAligner(timeframe='15min')
        self.cross_section = CrossSectionalRanking(min_coverage=45, lookback_bars=4)
        self.macro_gate = MacroGateFilter()
        self.rs_engine = MomentumRSEngine()

    def _build_benchmark(self, raw_dfs):
        df_closes = pd.DataFrame({sym: df['close'] for sym, df in raw_dfs.items()})
        idx_close = (1 + df_closes.pct_change(fill_method=None).fillna(0).mean(axis=1)).cumprod() * 100.0
        df_market = pd.DataFrame({'open': idx_close.shift(1).fillna(100.0), 'high': idx_close * 1.0005, 'low': idx_close * 0.9995, 'close': idx_close, 'volume': 1000000}, index=idx_close.index)
        return df_market, df_closes

    def _extract_base_mtf(self, df_1m):
        df_5m = self.aligner_5m.aggregate_completed_bars(df_1m)
        df_15m = self.aligner_15m.aggregate_completed_bars(df_1m)
        
        def calc_feats(df_htf, pfx):
            out = pd.DataFrame(index=df_htf.index)
            c = df_htf['close']
            out[f'{pfx}_trend'] = np.sign(c.diff(4)).fillna(0)
            typ = (df_htf['high'] + df_htf['low'] + df_htf['close']) / 3.0
            vwap = (typ * df_htf['volume']).rolling(20, min_periods=5).sum() / df_htf['volume'].rolling(20, min_periods=5).sum().replace(0, 1)
            tr = pd.concat([df_htf['high']-df_htf['low'], (df_htf['high']-c.shift()).abs(), (df_htf['low']-c.shift()).abs()], axis=1).max(axis=1)
            atr = tr.rolling(14, min_periods=1).mean().replace(0, 1)
            out[f'{pfx}_vwap_dist_atr'] = ((c - vwap) / atr).clip(-5, 5)
            out[f'{pfx}_realized_vol'] = np.log(c / c.shift(1)).rolling(20, min_periods=5).std().fillna(0)
            return out
            
        f_5m = calc_feats(df_5m, '5m')
        f_15m = calc_feats(df_15m, '15m')
        
        res = df_1m.copy()
        for col in f_5m.columns: res[col] = self.aligner_5m.align_features(df_1m, f_5m[[col]])[col]
        for col in f_15m.columns: res[col] = self.aligner_15m.align_features(df_1m, f_15m[[col]])[col]
        return res

    def run_split(self, split):
        split_dir, out_dir = self.data_source_dir / split, self.output_base_dir / split
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_dfs = {sym: pd.read_parquet(split_dir / f"{sym}_features.parquet") for sym in ALL_SYMBOLS}
        df_market, df_closes = self._build_benchmark(raw_dfs)
        gate = self.macro_gate.compute_gate(df_market, df_closes)

        extracted_dfs = {}
        for sym, df in raw_dfs.items():
            base = self._extract_base_mtf(df)
            rs = self.rs_engine.compute_refined_rs(df, df_market)
            merged = pd.concat([base, rs], axis=1)
            merged['macro_gate'] = gate
            extracted_dfs[sym] = merged

        ranked_dfs = self.cross_section.run(extracted_dfs)
        for sym, df in ranked_dfs.items():
            df.to_parquet(out_dir / f"{sym}_mtf_2026.parquet")
        logger.info(f"✅ Saved 48 feature files to {out_dir}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", required=True)
    parser.add_argument("--data-source-dir", default="/home/shrinivas/ECS_Project_external_engine_quarantine_20260912/extracted_features_2026")
    parser.add_argument("--output-base-dir", default="revision3/features_2026")
    args = parser.parse_args()
    Revision3MTFExtractor(args.data_source_dir, args.output_base_dir).run_split(args.split)
