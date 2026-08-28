"""
Model 1: XGBoost Harness
Item 3b Preregistration - FROZEN Configuration

Date: 2026-08-28
Status: Phase 2A Package 2A-2 - HARNESS BUILD
Configuration: XGBoost (max_depth=5, learning_rate=0.05) - IMMUTABLE

This harness implements gradient boosting decision trees for Model 1.
No execution in this package - code structure only.
"""

import pandas as pd
import numpy as np
import xgboost as xgb
from sklearn.preprocessing import StandardScaler
import json
from pathlib import Path


class Model1XGBoostHarness:
    """XGBoost harness for Item 3b preregistration."""
    
    def __init__(self, config_path: str = "model_1_xgboost_config.json"):
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
                "algorithm": "XGBoost",
                "n_estimators": 500,
                "max_depth": 5,
                "learning_rate": 0.05,
                "subsample": 0.8,
                "colsample_bytree": 0.8,
                "reg_lambda": 1.0,
                "gamma": 0.1,
                "early_stopping_rounds": 50,
                "objective": "reg:squarederror",
                "random_state": 42
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
    
    def train_model(self, X_train: pd.DataFrame, y_train: pd.Series, 
                    X_val: pd.DataFrame = None, y_val: pd.Series = None) -> xgb.XGBRegressor:
        """
        Train XGBoost on training set with early stopping on validation set.
        
        Args:
            X_train: Training features (already preprocessed)
            y_train: Training targets
            X_val: Validation features (optional, for early stopping)
            y_val: Validation targets (optional, for early stopping)
        
        Returns:
            Fitted XGBoost model object
        """
        eval_set = None
        if X_val is not None and y_val is not None:
            eval_set = [(X_val, y_val)]
        
        self.model = xgb.XGBRegressor(
            n_estimators=self.config['n_estimators'],
            max_depth=self.config['max_depth'],
            learning_rate=self.config['learning_rate'],
            subsample=self.config['subsample'],
            colsample_bytree=self.config['colsample_bytree'],
            reg_lambda=self.config['reg_lambda'],
            gamma=self.config['gamma'],
            objective=self.config['objective'],
            random_state=self.config['random_state'],
            early_stopping_rounds=self.config['early_stopping_rounds'],
            verbose=False
        )
        
        self.model.fit(
            X_train, y_train,
            eval_set=eval_set,
            verbose=False
        )
        return self.model
    
    def predict(self, model: xgb.XGBRegressor, X_val: pd.DataFrame) -> np.ndarray:
        """
        Generate predictions on validation/holdout set.
        
        Args:
            model: Fitted XGBoost model
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
    
    def save_model(self, model: xgb.XGBRegressor, filepath: str):
        """Save trained model to JSON file."""
        model.save_model(filepath)
    
    def load_model(self, filepath: str) -> xgb.XGBRegressor:
        """Load trained model from JSON file."""
        model = xgb.XGBRegressor()
        model.load_model(filepath)
        return model


# ============================================================================
# USAGE PATTERN (Phase 2B execution only - NOT in Phase 2A)
# ============================================================================
# 
# harness = Model1XGBoostHarness(config_path="model_1_xgboost_config.json")
# 
# # Load data
# X_train, y_train = harness.load_data(df_train, feature_cols, target_col)
# X_val, y_val = harness.load_data(df_val, feature_cols, target_col)
# 
# # Preprocess (fit scaler on training data only)
# X_train_scaled = harness.preprocess(X_train, fit_scaler=True)
# X_val_scaled = harness.preprocess(X_val, fit_scaler=False)
# 
# # Train model (with early stopping on validation set)
# model = harness.train_model(X_train_scaled, y_train, X_val_scaled, y_val)
# 
# # Predict
# predictions = harness.predict(model, X_val_scaled)
# 
# # Evaluate
# rank_ic = harness.compute_rank_ic(predictions, y_val.values)
# 
# # Save
# harness.save_model(model, "model_1_trained.json")
#
# ============================================================================

if __name__ == "__main__":
    print("Model 1 XGBoost Harness")
    print("Status: Code structure ready (Phase 2A)")
    print("Configuration: FROZEN (max_depth=5, learning_rate=0.05)")
    print("Execution: Phase 2B (training begins then)")
