#!/usr/bin/env python3
"""
In-House Engine Model Training with Refined Cost-Aware Labels (2026)
======================================================================

Integrates:
1. In-house MTF features from revision2/features_mtf_2026/
2. Cost-aware refined labels from /home/shrinivas/ECS_ModelDevelopment_2026_MTF/labels_refined_2026/

Trains frozen logistic regression on TRAIN split, evaluates on VALIDATION/TEST/SEALED.

Pipeline:
- Load causally-aligned MTF features (in-house engine)
- Load cost-aware refined labels
- Train frozen model on TRAIN split
- Evaluate on holdout splits (no hyperparameter tuning)
- Save model + statistics
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
        logging.FileHandler('train_model_inhouse_refined_2026.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class InHouseRefinedModelTrainer:
    """Train frozen logistic regression using refined labels."""

    # Features extracted from in-house MTF pipeline
    FEATURE_COLUMNS = [
        '5m_trend', '5m_efficiency', '5m_vwap_dist_atr', '5m_realized_vol',
        '15m_trend', '15m_efficiency', '15m_vwap_dist_atr', '15m_realized_vol',
        # Cross-sectional features
        '15m_rs_percentile', '15m_rs_excess'
    ]

    def __init__(self,
                 feature_dir: str = "revision2/features_mtf_2026",
                 label_dir: str = "/home/shrinivas/ECS_ModelDevelopment_2026_MTF/labels_refined_2026",
                 output_dir: str = "revision2/frozen_models_inhouse_2026"):
        """Initialize trainer."""
        self.feature_dir = Path(feature_dir)
        self.label_dir = Path(label_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.model = None
        self.scaler = None
        self.train_stats = None

        logger.info("✅ InHouseRefinedModelTrainer initialized")
        logger.info(f"   Features: {self.feature_dir}")
        logger.info(f"   Labels: {self.label_dir}")
        logger.info(f"   Output: {self.output_dir}")

    def load_split(self, split: str) -> Tuple[pd.DataFrame, Optional[pd.Series]]:
        """
        Load features and labels for a split.

        In-house extraction completed only for TRAIN split.
        For other splits, labels are available but features must come from external extraction.

        Args:
            split: 'train', 'validation', 'test', or 'sealed_confirmation'

        Returns:
            (X_features, y_labels) - Features and labels for split
        """
        logger.info(f"\nLoading {split} split...")

        # For TRAIN: use in-house extracted features
        if split == "train":
            X_list = []
            if not self.feature_dir.exists():
                raise ValueError(f"Feature directory not found: {self.feature_dir}")

            # Load all symbol files from flat in-house directory
            for parquet_file in sorted(self.feature_dir.glob("*_mtf_2026.parquet")):
                symbol = parquet_file.stem.replace("_mtf_2026", "")
                df = pd.read_parquet(parquet_file)

                # Extract feature columns (use available, skip missing)
                available_cols = [col for col in self.FEATURE_COLUMNS if col in df.columns]
                X = df[available_cols].copy()
                X_list.append(X)
                logger.debug(f"  {symbol}: {X.shape}")

            X_combined = pd.concat(X_list, axis=0, ignore_index=False)
            logger.info(f"  ✅ Loaded features: {X_combined.shape} (IN-HOUSE extraction)")
        else:
            # For validation/test/sealed: features from external engine (if needed later)
            logger.warning(f"  Note: {split} uses external engine features (in-house extraction pending)")
            logger.warning(f"  Skipping evaluation on {split} for now")
            return None, None

        # Load refined labels
        labels_path = self.label_dir / f"{split}_labels.parquet"
        if not labels_path.exists():
            logger.warning(f"Labels not found: {labels_path}")
            return X_combined, None

        y = pd.read_parquet(labels_path)['label'].reset_index(drop=True)

        # Align indices
        X_combined = X_combined.reset_index(drop=True)

        logger.info(f"  ✅ Loaded labels: {y.shape}, Positive: {y.sum()}/{len(y)} ({y.mean()*100:.2f}%)")

        return X_combined, y

    def train(self, X_train: pd.DataFrame, y_train: pd.Series) -> Dict:
        """
        Train frozen logistic regression on TRAIN split.

        Args:
            X_train: Training features
            y_train: Training labels (cost-aware refined)

        Returns:
            Training statistics dictionary
        """
        logger.info(f"\n{'='*80}")
        logger.info(f"TRAINING FROZEN LOGISTIC REGRESSION")
        logger.info(f"{'='*80}")
        logger.info(f"Input shape: {X_train.shape}")
        logger.info(f"Label distribution: {y_train.value_counts().to_dict()}")

        # Remove NaN rows
        valid_mask = X_train.notna().all(axis=1) & y_train.notna()
        X_clean = X_train[valid_mask]
        y_clean = y_train[valid_mask]

        logger.info(f"After NaN removal: {X_clean.shape} ({(~valid_mask).sum()} rows removed)")
        logger.info(f"Positive label rate: {y_clean.mean()*100:.3f}%")

        # Standardize features
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X_clean)

        logger.info(f"Feature scaling: mean={X_scaled.mean():.4f}, std={X_scaled.std():.4f}")

        # Train model
        logger.info(f"Training LogisticRegression (fit_intercept=True, solver='lbfgs', max_iter=1000)...")
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
            "label_distribution": {
                "positive": int(y_clean.sum()),
                "negative": int((1 - y_clean).sum()),
                "positive_pct": float(y_clean.mean() * 100)
            },
            "accuracy": float(accuracy_score(y_clean, y_pred)),
            "precision": float(precision_score(y_clean, y_pred, zero_division=0)),
            "recall": float(recall_score(y_clean, y_pred, zero_division=0)),
            "f1": float(f1_score(y_clean, y_pred, zero_division=0)),
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

        logger.info(f"\n{'='*80}")
        logger.info(f"EVALUATING ON {split.upper()} SPLIT")
        logger.info(f"{'='*80}")
        logger.info(f"Input shape: {X_test.shape}")

        # Remove NaN rows
        valid_mask = X_test.notna().all(axis=1) & y_test.notna()
        X_clean = X_test[valid_mask]
        y_clean = y_test[valid_mask]

        logger.info(f"After NaN removal: {X_clean.shape} ({(~valid_mask).sum()} rows removed)")
        logger.info(f"Positive label rate: {y_clean.mean()*100:.3f}%")

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
            "label_distribution": {
                "positive": int(y_clean.sum()),
                "negative": int((1 - y_clean).sum()),
                "positive_pct": float(y_clean.mean() * 100)
            },
            "accuracy": float(accuracy_score(y_clean, y_pred)),
            "precision": float(precision_score(y_clean, y_pred, zero_division=0)),
            "recall": float(recall_score(y_clean, y_pred, zero_division=0)),
            "f1": float(f1_score(y_clean, y_pred, zero_division=0)),
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

        model_path = self.output_dir / "logistic_regression_inhouse_refined.pkl"
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
        logger.info("IN-HOUSE ENGINE MODEL TRAINING WITH REFINED LABELS")
        logger.info("="*80 + "\n")
        logger.info("Note: TRAIN split uses in-house extracted features")
        logger.info("      Validation/Test/Sealed require separate in-house extraction")
        logger.info("="*80 + "\n")

        results = {}

        # TRAIN SPLIT (in-house extraction available)
        logger.info("PHASE 1: TRAINING ON TRAIN SPLIT (IN-HOUSE FEATURES)")
        logger.info("-" * 80)
        X_train, y_train = self.load_split("train")
        if y_train is None or X_train is None:
            raise ValueError("Training labels or features not found")
        train_stats = self.train(X_train, y_train)
        results["train"] = train_stats

        # VALIDATION, TEST, SEALED splits (pending in-house extraction)
        logger.info("\n" + "="*80)
        logger.info("PENDING: Validation/Test/Sealed splits require in-house extraction")
        logger.info("="*80)
        logger.info("\nTo complete cross-split validation:")
        logger.info("1. Run in-house MTF extraction for VALIDATION split (Apr 2026)")
        logger.info("2. Run in-house MTF extraction for TEST split (May 2026)")
        logger.info("3. Run in-house MTF extraction for SEALED split (Jun 2026)")
        logger.info("4. Re-run this trainer to evaluate on all holdout splits")

        # Save model
        logger.info("\n\nPHASE 2: MODEL PERSISTENCE")
        logger.info("-" * 80)
        self.save_model()

        # Summary
        logger.info("\n\n" + "="*80)
        logger.info("TRAINING COMPLETE - TRAIN SPLIT RESULTS")
        logger.info("="*80)

        for split, stats in results.items():
            logger.info(f"\n{split.upper():25s}: "
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
    trainer = InHouseRefinedModelTrainer()
    results = trainer.run_pipeline()

    print("\n" + "="*80)
    print("✅ IN-HOUSE ENGINE MODEL TRAINING COMPLETE")
    print("="*80)
    print(f"\nModel saved to: {trainer.output_dir}/")
    print(f"\nFinal Results Summary:")
    for split, stats in results.items():
        print(f"  {split.upper():25s}: Acc={stats['accuracy']:.4f}, AUC={stats['auc']:.4f}")
