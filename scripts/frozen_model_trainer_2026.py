#!/usr/bin/env python3
"""
Frozen Logistic Regression Model Training on 2026 MTF Features
================================================================

Trains a single frozen logistic regression model on TRAIN split (1.08M rows)
and evaluates on VALIDATION/TEST/SEALED splits with strict holdout protocol.

Features: 8 MTF features (5m_trend, 5m_efficiency, ..., 15m_realized_vol)
Labels: Binary classification (requires external label source or generation)
Model: sklearn LogisticRegression (frozen after TRAIN, no tuning on holdouts)

Causal Integrity: Feature extraction already enforces strict < alignment
Model Integrity: This module enforces frozen model (no hyperparameter tuning on holdouts)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import json
import logging
from datetime import datetime
from typing import Dict, Tuple, Optional

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('frozen_model_training_2026.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class LabelGenerator:
    """Generate binary labels from market data for supervised learning."""

    @staticmethod
    def generate_labels_from_returns(df: pd.DataFrame,
                                    future_periods: int = 5,
                                    threshold: float = 0.0) -> pd.Series:
        """
        Generate labels based on forward returns.

        Args:
            df: DataFrame with OHLCV columns
            future_periods: Number of periods ahead to measure return
            threshold: Return threshold for positive label (default: 0 = any positive return)

        Returns:
            Series of binary labels (0/1)
        """
        if 'close' not in df.columns:
            raise ValueError("DataFrame must have 'close' column")

        # Forward return
        forward_close = df['close'].shift(-future_periods)
        future_return = (forward_close - df['close']) / df['close']

        # Binary label: 1 if future return > threshold, else 0
        labels = (future_return > threshold).astype(int)

        logger.info(f"Generated labels: {labels.sum()} positive, {(1-labels).sum()} negative")
        return labels


class FrozenModelTrainer:
    """Train frozen logistic regression and evaluate on holdouts."""

    FEATURE_COLUMNS = [
        '5m_trend', '5m_efficiency', '5m_vwap_distance_atr', '5m_realized_vol',
        '15m_trend', '15m_efficiency', '15m_vwap_distance_atr', '15m_realized_vol'
    ]

    def __init__(self,
                 feature_dir: str = "revision2_external/extracted_features_2026",
                 output_dir: str = "revision2_external/frozen_models_2026"):
        """Initialize trainer."""
        self.feature_dir = Path(feature_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.model = None
        self.scaler = None
        self.train_stats = None

        logger.info("✅ FrozenModelTrainer initialized")

    def load_split(self, split: str) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Load all symbols for a split and combine.

        Args:
            split: 'train', 'validation', 'test', or 'sealed_confirmation'

        Returns:
            (X_combined, y_combined) - Combined features and labels for split
        """
        split_dir = self.feature_dir / split
        if not split_dir.exists():
            raise ValueError(f"Split directory not found: {split_dir}")

        logger.info(f"Loading {split} split...")

        X_list = []
        for parquet_file in sorted(split_dir.glob("*_features.parquet")):
            symbol = parquet_file.stem.replace("_features", "")
            df = pd.read_parquet(parquet_file)

            # Extract feature columns only
            X = df[self.FEATURE_COLUMNS].copy()
            X_list.append(X)

        X_combined = pd.concat(X_list, axis=0, ignore_index=False)
        logger.info(f"  Loaded {len(X_list)} symbols, shape: {X_combined.shape}")

        return X_combined

    def generate_labels(self, split: str, X: pd.DataFrame) -> pd.Series:
        """
        Generate labels for a split using returns-based method.

        Args:
            split: Split name (for logging)
            X: Features DataFrame (must have same index as original data)

        Returns:
            Binary labels (0/1)
        """
        logger.info(f"Generating labels for {split}...")

        # Load raw data to compute returns
        split_dir = self.feature_dir / split
        labels_list = []

        for parquet_file in sorted(split_dir.glob("*_features.parquet")):
            df = pd.read_parquet(parquet_file)

            # Generate labels from forward returns
            symbol_labels = LabelGenerator.generate_labels_from_returns(
                df, future_periods=5, threshold=0.0
            )
            labels_list.append(symbol_labels)

        y_combined = pd.concat(labels_list, axis=0, ignore_index=False)
        logger.info(f"  Labels shape: {y_combined.shape}, "
                   f"Positive: {y_combined.sum()}, Negative: {(1-y_combined).sum()}")

        return y_combined

    def train(self, X_train: pd.DataFrame, y_train: pd.Series) -> Dict:
        """
        Train frozen logistic regression on TRAIN split.

        Args:
            X_train: Training features (1,080,000 × 8)
            y_train: Training labels (1,080,000,)

        Returns:
            Training statistics dictionary
        """
        logger.info(f"\nTraining Frozen Logistic Regression")
        logger.info(f"  Input shape: {X_train.shape}")
        logger.info(f"  Label distribution: {y_train.value_counts().to_dict()}")

        # Remove NaN rows
        valid_mask = X_train.notna().all(axis=1) & y_train.notna()
        X_clean = X_train[valid_mask]
        y_clean = y_train[valid_mask]

        logger.info(f"  After NaN removal: {X_clean.shape} ({(~valid_mask).sum()} rows removed)")

        # Standardize features
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X_clean)

        logger.info(f"  Feature scaling: mean={X_scaled.mean():.4f}, std={X_scaled.std():.4f}")

        # Train model
        logger.info(f"  Training LogisticRegression (fit_intercept=True, solver='lbfgs')...")
        self.model = LogisticRegression(
            fit_intercept=True,
            solver='lbfgs',
            max_iter=1000,
            random_state=42,
            n_jobs=-1,
            verbose=0
        )
        self.model.fit(X_scaled, y_clean)

        # Training metrics
        y_pred = self.model.predict(X_scaled)
        y_pred_proba = self.model.predict_proba(X_scaled)[:, 1]

        train_stats = {
            "split": "train",
            "rows_processed": len(y_train),
            "rows_used": len(y_clean),
            "accuracy": float(accuracy_score(y_clean, y_pred)),
            "precision": float(precision_score(y_clean, y_pred)),
            "recall": float(recall_score(y_clean, y_pred)),
            "f1": float(f1_score(y_clean, y_pred)),
            "auc": float(roc_auc_score(y_clean, y_pred_proba)),
            "model_coefficients": self.model.coef_[0].tolist(),
            "model_intercept": float(self.model.intercept_[0]),
            "trained_at": datetime.utcnow().isoformat()
        }

        self.train_stats = train_stats

        logger.info(f"✅ Training complete:")
        logger.info(f"   Accuracy:  {train_stats['accuracy']:.4f}")
        logger.info(f"   Precision: {train_stats['precision']:.4f}")
        logger.info(f"   Recall:    {train_stats['recall']:.4f}")
        logger.info(f"   F1:        {train_stats['f1']:.4f}")
        logger.info(f"   AUC:       {train_stats['auc']:.4f}")

        return train_stats

    def evaluate(self, X_test: pd.DataFrame, y_test: pd.Series, split: str) -> Dict:
        """
        Evaluate frozen model on holdout split.

        Args:
            X_test: Test features
            y_test: Test labels
            split: Split name (for logging)

        Returns:
            Evaluation statistics dictionary
        """
        if self.model is None or self.scaler is None:
            raise ValueError("Model not trained. Call train() first.")

        logger.info(f"\nEvaluating on {split.upper()} split")
        logger.info(f"  Input shape: {X_test.shape}")

        # Remove NaN rows
        valid_mask = X_test.notna().all(axis=1) & y_test.notna()
        X_clean = X_test[valid_mask]
        y_clean = y_test[valid_mask]

        logger.info(f"  After NaN removal: {X_clean.shape} ({(~valid_mask).sum()} rows removed)")

        # Scale using training statistics
        X_scaled = self.scaler.transform(X_clean)

        # Predictions (FROZEN MODEL - no retraining)
        y_pred = self.model.predict(X_scaled)
        y_pred_proba = self.model.predict_proba(X_scaled)[:, 1]

        # Metrics
        eval_stats = {
            "split": split,
            "rows_processed": len(y_test),
            "rows_used": len(y_clean),
            "accuracy": float(accuracy_score(y_clean, y_pred)),
            "precision": float(precision_score(y_clean, y_pred)),
            "recall": float(recall_score(y_clean, y_pred)),
            "f1": float(f1_score(y_clean, y_pred)),
            "auc": float(roc_auc_score(y_clean, y_pred_proba)),
            "evaluated_at": datetime.utcnow().isoformat()
        }

        logger.info(f"✅ {split.upper()} Results:")
        logger.info(f"   Accuracy:  {eval_stats['accuracy']:.4f}")
        logger.info(f"   Precision: {eval_stats['precision']:.4f}")
        logger.info(f"   Recall:    {eval_stats['recall']:.4f}")
        logger.info(f"   F1:        {eval_stats['f1']:.4f}")
        logger.info(f"   AUC:       {eval_stats['auc']:.4f}")

        return eval_stats

    def save_model(self) -> Path:
        """Save trained model and scaler to disk."""
        if self.model is None:
            raise ValueError("No trained model to save")

        import pickle

        model_path = self.output_dir / "logistic_regression_frozen.pkl"
        scaler_path = self.output_dir / "feature_scaler.pkl"
        stats_path = self.output_dir / "training_stats.json"

        with open(model_path, 'wb') as f:
            pickle.dump(self.model, f)
        with open(scaler_path, 'wb') as f:
            pickle.dump(self.scaler, f)
        with open(stats_path, 'w') as f:
            json.dump(self.train_stats, f, indent=2)

        logger.info(f"✅ Model saved: {model_path}")
        logger.info(f"   Scaler: {scaler_path}")
        logger.info(f"   Stats: {stats_path}")

        return model_path

    def run_pipeline(self) -> Dict:
        """Execute full training and evaluation pipeline."""
        logger.info("\n" + "="*80)
        logger.info("FROZEN MODEL TRAINING PIPELINE - 2026 MTF FEATURES")
        logger.info("="*80 + "\n")

        results = {}

        # TRAIN SPLIT
        logger.info("PHASE 1: TRAINING")
        logger.info("-" * 80)
        X_train = self.load_split("train")
        y_train = self.generate_labels("train", X_train)
        train_stats = self.train(X_train, y_train)
        results["train"] = train_stats

        # VALIDATION SPLIT
        logger.info("\n\nPHASE 2: VALIDATION HOLDOUT")
        logger.info("-" * 80)
        X_val = self.load_split("validation")
        y_val = self.generate_labels("validation", X_val)
        val_stats = self.evaluate(X_val, y_val, "validation")
        results["validation"] = val_stats

        # TEST SPLIT
        logger.info("\n\nPHASE 3: TEST HOLDOUT")
        logger.info("-" * 80)
        X_test = self.load_split("test")
        y_test = self.generate_labels("test", X_test)
        test_stats = self.evaluate(X_test, y_test, "test")
        results["test"] = test_stats

        # SEALED CONFIRMATION
        logger.info("\n\nPHASE 4: SEALED CONFIRMATION")
        logger.info("-" * 80)
        X_sealed = self.load_split("sealed_confirmation")
        y_sealed = self.generate_labels("sealed_confirmation", X_sealed)
        sealed_stats = self.evaluate(X_sealed, y_sealed, "sealed_confirmation")
        results["sealed_confirmation"] = sealed_stats

        # Save model
        logger.info("\n\nPHASE 5: MODEL PERSISTENCE")
        logger.info("-" * 80)
        self.save_model()

        # Summary
        logger.info("\n\n" + "="*80)
        logger.info("FINAL RESULTS SUMMARY")
        logger.info("="*80)

        for split, stats in results.items():
            logger.info(f"\n{split.upper():20s}: "
                       f"Acc={stats['accuracy']:.4f}, "
                       f"Prec={stats['precision']:.4f}, "
                       f"Rec={stats['recall']:.4f}, "
                       f"F1={stats['f1']:.4f}, "
                       f"AUC={stats['auc']:.4f}")

        # Save results
        results_path = self.output_dir / "all_results.json"
        with open(results_path, 'w') as f:
            json.dump(results, f, indent=2)
        logger.info(f"\n✅ Results saved: {results_path}")

        return results


if __name__ == "__main__":
    trainer = FrozenModelTrainer()
    results = trainer.run_pipeline()

    print("\n" + "="*80)
    print("✅ FROZEN MODEL TRAINING COMPLETE")
    print("="*80)
    print(f"\nModel saved to: {trainer.output_dir}/")
    print(f"Results: {results}")
