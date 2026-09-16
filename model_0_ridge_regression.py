"""
Model 0: Ridge Regression Harness
Item 3b Preregistration - FROZEN Configuration

Date: 2026-08-28
Status: Phase 2A Package 2A-1 - HARNESS BUILD
Configuration: Ridge L2 (λ=0.01) - IMMUTABLE

This harness implements regularized linear regression for Model 0.
No execution in this package - code structure only.
"""

import pandas as pd
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
import json
from pathlib import Path


class Model0RidgeHarness:
    """Ridge regression harness for Item 3b preregistration."""
    
    def __init__(self, config_path: str = "model_0_ridge_config.json"):
        """Initialize harness with frozen configuration."""
        self.config = self._load_config(config_path)
        self.scaler = StandardScaler()
        self.model = None
        self.feature_names = None
        
    def _load_config(self, config_path: str) -> dict:
        """Load frozen configuration from JSON."""
        if Path(config_path).exists():
            with open(config_path, 'r') as f:
                return json.load(f)
        else:
            # Default frozen configuration
            return {
                "algorithm": "Ridge",
                "regularization_type": "L2",
                "lambda": 0.01,
                "fit_intercept": True,
                "normalize": False,
                "solver": "auto",
                "max_iter": 1000,
                "tol": 1e-3
            }
    
    def load_data(self, df: pd.DataFrame, feature_cols: list, target_col: str) -> tuple:
        """Load and return features and targets."""
        X = df[feature_cols].copy()
        y = df[target_col].copy()
        self.feature_names = feature_cols
        return X, y
    
    def preprocess(self, X: pd.DataFrame, fit_scaler: bool = False) -> pd.DataFrame:
        """
        Standardize features to mean=0, std=1.
        
        If fit_scaler=True: fit on training data
        If fit_scaler=False: transform using training statistics
        """
        if fit_scaler:
            X_scaled = self.scaler.fit_transform(X)
        else:
            X_scaled = self.scaler.transform(X)
        
        return pd.DataFrame(X_scaled, columns=X.columns, index=X.index)
    
    def train_model(self, X_train: pd.DataFrame, y_train: pd.Series) -> 'Ridge':
        """
        Train Ridge regression on training set.
        
        Args:
            X_train: Training features (already preprocessed)
            y_train: Training targets
        
        Returns:
            Fitted Ridge model object
        """
        self.model = Ridge(
            alpha=self.config['lambda'],
            fit_intercept=self.config['fit_intercept'],
            max_iter=self.config['max_iter'],
            tol=self.config['tol']
        )
        self.model.fit(X_train, y_train)
        return self.model
    
    def predict(self, model: Ridge, X_val: pd.DataFrame) -> np.ndarray:
        """
        Generate predictions on validation/holdout set.
        
        Args:
            model: Fitted Ridge model
            X_val: Features (already preprocessed)
        
        Returns:
            Predictions as numpy array
        """
        return model.predict(X_val)
    
    def compute_rank_ic(self, predictions: np.ndarray, actuals: np.ndarray) -> float:
        """
        Compute Rank Information Coefficient (Spearman correlation).
        
        Args:
            predictions: Model predictions
            actuals: Actual target values
        
        Returns:
            Rank IC as float
        """
        from scipy.stats import spearmanr
        rank_ic, _ = spearmanr(predictions, actuals)
        return rank_ic
    
    def save_model(self, model: Ridge, filepath: str):
        """Save trained model to pickle file."""
        import pickle
        with open(filepath, 'wb') as f:
            pickle.dump(model, f)
    
    def load_model(self, filepath: str) -> Ridge:
        """Load trained model from pickle file."""
        import pickle
        with open(filepath, 'rb') as f:
            return pickle.load(f)


# ============================================================================
# USAGE PATTERN (Phase 2B execution only - NOT in Phase 2A)
# ============================================================================
# 
# harness = Model0RidgeHarness(config_path="model_0_ridge_config.json")
# 
# # Load data
# X_train, y_train = harness.load_data(df_train, feature_cols, target_col)
# X_val, y_val = harness.load_data(df_val, feature_cols, target_col)
# 
# # Preprocess (fit scaler on training data only)
# X_train_scaled = harness.preprocess(X_train, fit_scaler=True)
# X_val_scaled = harness.preprocess(X_val, fit_scaler=False)
# 
# # Train model
# model = harness.train_model(X_train_scaled, y_train)
# 
# # Predict
# predictions = harness.predict(model, X_val_scaled)
# 
# # Evaluate
# rank_ic = harness.compute_rank_ic(predictions, y_val.values)
# 
# # Save
# harness.save_model(model, "model_0_trained.pkl")
#
# ============================================================================

if __name__ == "__main__":
    print("Model 0 Ridge Regression Harness")
    print("Status: Code structure ready (Phase 2A)")
    print("Configuration: FROZEN (λ=0.01, L2 regularization)")
    print("Execution: Phase 2B (training begins then)")
