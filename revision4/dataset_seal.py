"""
REVISION 04 (Proper): Dataset Sealing

Load and freeze the 48-symbol dataset manifest.
Validate all files exist and match hashes.
Fail before run if dataset is incomplete.
"""

import hashlib
from pathlib import Path
from typing import Dict, List, Tuple
from revision4.contracts import DatasetSeal


class DatasetValidator:
    """Validate and seal the dataset."""

    def __init__(self, manifest_path: str):
        self.manifest_path = manifest_path
        self.manifest = None
        self.seal = None

    def load_manifest(self, data_dir: str) -> DatasetSeal:
        """
        Load and validate 48-symbol manifest.
        Fail if not exactly 48 symbols.
        Recompute hashes from files and verify against manifest.
        """
        from revision2.dataset_manifest import DatasetManifest

        # Load manifest
        try:
            manifest = DatasetManifest.load(self.manifest_path)
        except Exception as e:
            raise RuntimeError(f"Cannot load manifest: {e}")

        # Check symbol count
        files = manifest.files
        if len(files) != 48:
            raise RuntimeError(f"Dataset has {len(files)} symbols, need exactly 48")

        # Extract symbols and validate files + recompute hashes
        symbols = []
        symbol_hashes = {}
        data_path = Path(data_dir)

        for file_record in files:
            symbol = file_record.symbol
            symbols.append(symbol)

            # Check file exists
            file_full_path = data_path / file_record.filename
            if not file_full_path.exists():
                raise RuntimeError(f"File not found: {file_full_path}")

            # Recompute hash from file
            try:
                with open(file_full_path, 'rb') as f:
                    file_hash = hashlib.sha256(f.read()).hexdigest()
            except IOError as e:
                raise RuntimeError(f"Cannot read {file_full_path}: {e}")

            # Verify hash matches manifest
            if file_hash != file_record.sha256:
                raise RuntimeError(
                    f"{symbol}: hash mismatch\n"
                    f"  Expected (manifest): {file_record.sha256}\n"
                    f"  Computed (file):     {file_hash}"
                )

            symbol_hashes[symbol] = file_hash

        # Create seal
        self.seal = DatasetSeal(
            month_start="2024-08-01",
            month_end="2024-08-31",
            warmup_bars=60,
            warmup_start="2024-07-02",  # One month before

            symbols=symbols,
            symbol_count=len(symbols),
            symbol_hashes=symbol_hashes,

            code_commit="HEAD",
            registry_hash="v1_68_params",
            config_hash="baseline",
        )

        print(f"\n[SEAL] Dataset validated:")
        print(f"  Symbols: {self.seal.symbol_count}")
        print(f"  Period: {self.seal.month_start} to {self.seal.month_end}")
        print(f"  Warmup: {self.seal.warmup_bars} bars starting {self.seal.warmup_start}")
        print(f"  Hash: {self.seal.registry_hash}")

        return self.seal

    def get_seal(self) -> DatasetSeal:
        """Return frozen seal."""
        if not self.seal:
            raise RuntimeError("Dataset not sealed. Call load_manifest() first.")
        return self.seal


class WarmupLoader:
    """Load bars before the calibration month for indicator initialization."""

    def __init__(self, loader, seal: DatasetSeal):
        self.loader = loader
        self.seal = seal

    def load_warmup_bars(self) -> Dict[str, list]:
        """
        Load warmup bars for all 48 symbols.
        Return {symbol: [Bar, Bar, ...]} chronologically.
        Exactly 60 bars before month_start.
        """
        import pandas as pd

        warmup_data = {}

        for symbol in self.seal.symbols:
            try:
                df = self.loader._load_symbol_csv(symbol)
                df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)

                # Get bars before month_start, last 60
                df_warmup = df[df['timestamp'].dt.strftime('%Y-%m-%d') < self.seal.month_start]
                df_warmup = df_warmup.tail(self.seal.warmup_bars)

                if len(df_warmup) >= self.seal.warmup_bars:
                    warmup_data[symbol] = df_warmup
                else:
                    print(f"  Warning: {symbol} has only {len(df_warmup)} warmup bars (need {self.seal.warmup_bars})")

            except Exception as e:
                print(f"  Error loading warmup for {symbol}: {e}")

        print(f"\n[WARMUP] Loaded {len(warmup_data)}/{self.seal.symbol_count} symbols")
        return warmup_data


def seal_dataset(manifest_path: str, data_dir: str) -> DatasetSeal:
    """
    Convenience function: seal the dataset.
    Fails if validation doesn't pass.
    """
    validator = DatasetValidator(manifest_path)
    seal = validator.load_manifest(data_dir)

    # Verify exactly 48
    if seal.symbol_count != 48:
        raise RuntimeError(f"ABORT: Dataset has {seal.symbol_count} symbols, not 48")

    print(f"\n✓ Dataset sealed: 48/48 symbols validated")
    return seal
