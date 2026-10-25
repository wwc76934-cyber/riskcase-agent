"""Two train-fitted pipelines; no identifiers, labels or partition markers in X."""

import numpy as np
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, OrdinalEncoder, StandardScaler


CONTINUOUS = ("amount_cents", "count_1h", "count_24h", "count_7d", "count_30d",
              "sum_signed_cents_24h", "sum_signed_cents_30d", "seconds_since_previous_in_snapshot")
BOUNDED = ("hour", "weekday", "merchant_seen_30d", "has_history_30d")
CATEGORICAL = ("channel", "mcc")
MODEL_COLUMNS = CONTINUOUS + BOUNDED + CATEGORICAL


def signed_log1p(values):
    values = np.asarray(values, dtype=np.float64)
    return np.sign(values) * np.log1p(np.abs(values))


def make_model(name, policy):
    if policy["class_weight"] != "balanced":
        raise ValueError("First experiment uses a frozen balanced-weight policy")
    if name == "logistic_regression":
        continuous = Pipeline([
            ("log", FunctionTransformer(signed_log1p, feature_names_out="one-to-one")),
            ("impute", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
            ("scale", StandardScaler()),
        ])
        preprocess = ColumnTransformer([
            ("continuous", continuous, list(CONTINUOUS)),
            ("bounded", StandardScaler(), list(BOUNDED)),
            ("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=True), list(CATEGORICAL)),
        ], sparse_threshold=1.0)
        estimator = LogisticRegression(**policy[name], class_weight="balanced", random_state=policy["random_seed"])
    elif name == "hist_gradient_boosting":
        preprocess = ColumnTransformer([
            ("numeric", "passthrough", list(CONTINUOUS + BOUNDED)),
            ("categorical", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1,
                                            encoded_missing_value=-1), list(CATEGORICAL)),
        ])
        estimator = HistGradientBoostingClassifier(**policy[name], class_weight="balanced",
            categorical_features=[False] * len(CONTINUOUS + BOUNDED) + [True] * len(CATEGORICAL),
            random_state=policy["random_seed"])
    else:
        raise ValueError("Unsupported model")
    return Pipeline([("preprocess", preprocess), ("model", estimator)])
