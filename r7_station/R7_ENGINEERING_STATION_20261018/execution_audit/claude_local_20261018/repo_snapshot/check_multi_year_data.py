from pathlib import Path

base_dir = Path('/home/shrinivas/ECS_Project_external_engine/mean_reversion')
print("Searching for feature directories under:", base_dir)
for p in base_dir.glob('**/features_*'):
    print(f"Found: {p}")
    files = list(p.glob('**/*.parquet'))
    print(f"  -> Parquet files count: {len(files)}")
    if files:
        print(f"  -> Sample: {files[0].name}")

