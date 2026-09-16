import os, sys, logging, argparse
from pathlib import Path
import pandas as pd
import numpy as np

from revision2.mtf_causal_aligner import CausalMTFAligner

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

ALL_SYMBOLS = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", 
    "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK", 
    "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", 
    "LT", "M&M", "MARUTI", "MAXHEALTH", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", 
    "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"
]

class MeanReversionGate:
    def __init__(self, vwap_window=30, max_trend_z=1.0):
        self.vwap_window = vwap_window
        self.max_trend_z = max_trend_z

    def compute_gate(self, df_synthetic_index, df_universe_closes):
        idx_close = df_synthetic_index['close']
        sma_30 = idx_close.rolling(self.vwap_window, min_periods=5).mean()
        std_30 = idx_close.rolling(self.vwap_window, min_periods=5).std().replace(0, 1e-6)
        trend_z = (idx_close - sma_30) / std_30
        chop_pass = trend_z.abs() <= self.max_trend_z

        ret_15m = df_universe_closes.pct_change(15, fill_method=None).fillna(0)
        breadth_15m = (ret_15m > 0).mean(axis=1)
        breadth_pass = (breadth_15m >= 0.35) & (breadth_15m <= 0.65)

        return (chop_pass & breadth_pass).astype(int)

class MeanReversionFeatureEngine:
    def __init__(self, vwap_window=30, atr_window=14):
        self.vwap_window = vwap_window
        self.atr_window = atr_window

    def compute_features(self, df_symbol):
        result = pd.DataFrame(index=df_symbol.index)
        
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

        result['vwap_zscore'] = (df_symbol['close'] - vwap) / atr
        std_20 = df_symbol['close'].rolling(20, min_periods=5).std()
        result['bb_width'] = (std_20 / vwap).fillna(0)

        delta = df_symbol['close'].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean().replace(0, 1e-6)
        rs = gain / loss
        result['rsi_14'] = 100 - (100 / (1 + rs))

        return result

class MeanReversionExtractor:
    def __init__(self, data_source_dir, output_base_dir):
        self.data_source_dir = Path(data_source_dir)
        self.output_base_dir = Path(output_base_dir)
        self.mr_gate = MeanReversionGate()
        self.mr_engine = MeanReversionFeatureEngine()

    def _build_benchmark(self, raw_dfs):
        df_closes = pd.DataFrame({sym: df['close'] for sym, df in raw_dfs.items()})
        idx_close = (1 + df_closes.pct_change(fill_method=None).fillna(0).mean(axis=1)).cumprod() * 100.0
        df_market = pd.DataFrame({'close': idx_close}, index=idx_close.index)
        return df_market, df_closes

    def run_split(self, split):
        split_dir, out_dir = self.data_source_dir / split, self.output_base_dir / split
        out_dir.mkdir(parents=True, exist_ok=True)
        raw_dfs = {sym: pd.read_parquet(split_dir / f"{sym}_features.parquet") for sym in ALL_SYMBOLS}
        df_market, df_closes = self._build_benchmark(raw_dfs)
        gate = self.mr_gate.compute_gate(df_market, df_closes)

        for sym, df in raw_dfs.items():
            feats = self.mr_engine.compute_features(df)
            merged = pd.concat([df[['open', 'high', 'low', 'close', 'volume']], feats], axis=1)
            merged['mean_rev_gate'] = gate
            merged.to_parquet(out_dir / f"{sym}_mr_2026.parquet")
            
        logger.info(f"✅ Saved 48 Mean-Reversion feature files to {out_dir} (Gate Open Rate: {gate.mean()*100:.1f}%)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", required=True)
    parser.add_argument("--data-source-dir", default="/home/shrinivas/ECS_Project_external_engine_quarantine_20260912/extracted_features_2026")
    parser.add_argument("--output-base-dir", default="mean_reversion/features_2026")
    args = parser.parse_args()
    MeanReversionExtractor(args.data_source_dir, args.output_base_dir).run_split(args.split)
