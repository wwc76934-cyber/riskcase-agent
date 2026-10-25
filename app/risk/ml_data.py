"""Read only the allowed train/validation split, checking join and feature scope."""

import numpy as np
import pandas as pd

from app.risk.ml_models import MODEL_COLUMNS


def load_split(connection, split, *, expected_rows, expected_positives):
    if split not in {"train", "validation"}:
        raise ValueError("Final test/warmup are not permitted in this experiment")
    columns = ",".join(f"f.{name}" for name in MODEL_COLUMNS)
    frame = pd.read_sql_query(f"""SELECT f.transaction_id,f.user_id,f.event_time,f.split,
        f.history_30d_complete,{columns},l.is_fraud FROM features f JOIN evaluation.labels l
        ON f.transaction_id=l.transaction_id AND f.split=l.split WHERE f.split=? ORDER BY f.transaction_id""",
        connection, params=(split,))
    if len(frame) != expected_rows or frame["transaction_id"].duplicated().any():
        raise ValueError("Missing/duplicate split records or mismatched label join")
    if not frame["is_fraud"].isin([0, 1]).all() or int(frame["is_fraud"].sum()) != expected_positives:
        raise ValueError("Invalid/mismatched outcome counts")
    if not frame["history_30d_complete"].eq(1).all():
        raise ValueError("Incomplete history coverage")
    y = frame.pop("is_fraud").to_numpy(dtype=np.int8)
    metadata = frame[["transaction_id", "user_id", "event_time", "split"]].copy()
    X = frame[list(MODEL_COLUMNS)].copy()
    numeric = X.select_dtypes(include="number").to_numpy(dtype=np.float64)
    if np.isinf(numeric).any():
        raise ValueError("Infinite features rejected")
    return X, y, metadata
