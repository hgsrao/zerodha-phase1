import sys
from pathlib import Path
import pandas as pd
import numpy as np

def evaluate_real_features(symbol: str = "MARUTI"):
    print("=" * 60)
    print(f"REAL FEATURE EXPECTANCY SCAN: {symbol}")
    print("=" * 60)
    parquet_path = Path(f"/home/shrinivas/ECS_Project_external_engine/mean_reversion/features_2026/train/{symbol}_mr_2026.parquet")
    df = pd.read_parquet(parquet_path)
    print(f"Loaded DataFrame Shape: {df.shape}")
    print(f"Available Columns: {list(df.columns[:8])} ... (total {len(df.columns)} cols)")
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if "close" in df.columns:
        df["forward_return"] = df["close"].shift(-5) - df["close"]
        df["target_binary"] = (df["forward_return"] > 0).astype(int)
        print("\nTop feature correlation with 5-bar forward direction:")
        corrs = df[numeric_cols].corrwith(df["target_binary"]).abs().sort_values(ascending=False)
        for col, val in corrs.head(5).items():
            print(f"  - {col}: {val:.4f}")
    print("=" * 60)

if __name__ == "__main__":
    evaluate_real_features("MARUTI")
