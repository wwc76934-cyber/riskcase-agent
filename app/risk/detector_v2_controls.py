"""Tree control with the neural model's same raw information/derived numeric features."""

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OrdinalEncoder

from app.risk.ml_models import MODEL_COLUMNS
from app.risk.neural_v2 import numeric_frame


class HistoricalFeatureView(TransformerMixin, BaseEstimator):
    def __init__(self, engineered=True, include_channel=False):
        self.engineered, self.include_channel = engineered, include_channel

    def fit(self, X, y=None):
        self.feature_names_in_ = np.asarray(list(X), dtype=object)
        return self

    def transform(self, X):
        values = numeric_frame(X, self.engineered)
        for category in (("mcc", "channel") if self.include_channel else ("mcc",)):
            values[category] = X[category].fillna("<MISSING>").astype(str)
        return values


def make_hgb_v2(*, seed=42, engineered=True):
    """Same 100 trees/15 leaves/L2=1 as old HGB; only input view changes."""
    numeric_names = list(numeric_frame(pd.DataFrame({c: [1] for c in MODEL_COLUMNS}), engineered))
    preprocess = ColumnTransformer([
        ("numeric", "passthrough", numeric_names),
        ("categorical", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1, encoded_missing_value=-1), ["mcc"]),
    ])
    estimator = HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=15, learning_rate=.1,
        min_samples_leaf=50, l2_regularization=1., early_stopping=False, class_weight="balanced",
        categorical_features=[False] * len(numeric_names) + [True], random_state=seed)
    return Pipeline([("derived", HistoricalFeatureView(engineered=engineered)), ("preprocess", preprocess), ("model", estimator)])
