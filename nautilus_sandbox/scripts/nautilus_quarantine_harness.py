from pathlib import Path
import pandas as pd

class StrictQuarantineHarness:
    def __init__(self, test_month_start: str = '2023-09-01'):
        self.cutoff_date = pd.Timestamp(test_month_start)

    def validate_data_shard(self, file_path: str, max_allowed_date: str):
        limit_ts = pd.Timestamp(max_allowed_date)
        df = pd.read_parquet(file_path)
        
        last_data_ts = pd.Timestamp(df.index.max())
        # Strip timezone if present to ensure clean scalar comparison
        if last_data_ts.tzinfo is not None:
            last_data_ts = last_data_ts.tz_localize(None)
        if limit_ts.tzinfo is not None:
            limit_ts = limit_ts.tz_localize(None)
        
        if last_data_ts >= limit_ts:
            raise PermissionError(
                f"🚨 QUARANTINE BREACH: Shard {file_path} contains data up to {last_data_ts}, "
            )
        print(f"✅ Quarantine Verified: Shard safe. Max timestamp {last_data_ts} < cutoff {limit_ts}")

if __name__ == '__main__':
    harness = StrictQuarantineHarness('2023-09-01')
    test_file = '/home/shrinivas/ECS_Project_external_engine/mean_reversion/features_2026/train/RELIANCE_mr_2026.parquet'
    try:
        harness.validate_data_shard(test_file, '2023-09-01')
    except Exception as e:
        print(f"Caught expected quarantine check: {e}")
