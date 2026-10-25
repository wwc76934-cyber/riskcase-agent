"""Train-fitted tabular residual network with bounded, event-time feature transforms."""

from copy import deepcopy
from dataclasses import asdict, dataclass
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from torch import nn

from app.risk.ml_models import MODEL_COLUMNS
from app.risk.neural_model import seed_training


@dataclass(frozen=True)
class NeuralConfig:
    architecture: str = "residual"
    width: int = 64
    blocks: int = 2
    dropout: float = 0.15
    learning_rate: float = 0.001
    weight_decay: float = 0.01
    positive_weight: float = 20.0
    batch_size: int = 4096
    include_channel: bool = False
    engineered: bool = True
    threads: int = 4

    def __post_init__(self):
        if self.architecture not in ("residual", "mlp") or self.width < 4 or not 1 <= self.blocks <= 4:
            raise ValueError("Bounded supported architecture required")
        if not 0 <= self.dropout < 1 or not 0 < self.learning_rate <= 0.1 or not 0 <= self.weight_decay <= 1:
            raise ValueError("Finite bounded optimizer settings required")
        if not 0 < self.positive_weight <= 1000 or not 1 <= self.batch_size <= 65536 or not 1 <= self.threads <= 4:
            raise ValueError("Bounded weight/batch/CPU settings required")


def numeric_frame(frame, engineered=True):
    """No fitted population/target statistics and no identifier columns."""
    if not set(MODEL_COLUMNS) <= set(frame): raise ValueError("Required event-time features absent")
    names = [c for c in MODEL_COLUMNS if c not in ("channel", "mcc")]
    result = frame[names].astype(float).copy()
    if np.isinf(result.to_numpy()).any(): raise ValueError("Infinite raw values rejected")
    if engineered:
        amount = result["amount_cents"]
        count30 = result["count_30d"].clip(lower=1)
        signed_mean = result["sum_signed_cents_30d"] / count30
        # Signed means can cancel; explicit denominator floor makes this a feature,
        # not a claim that the signed sum equals gross consumer spending.
        result["amount_to_abs_signed_mean_30d"] = amount / signed_mean.abs().clip(lower=100)
        result["amount_to_abs_signed_mean_24h"] = amount / (result["sum_signed_cents_24h"] / result["count_24h"].clip(lower=1)).abs().clip(lower=100)
        result["velocity_1h_to_24h"] = result["count_1h"] / result["count_24h"].clip(lower=1)
        result["velocity_24h_to_30d"] = result["count_24h"] / count30
        result["velocity_7d_to_30d"] = result["count_7d"] / count30
        result["signed_sum_24h_to_30d"] = result["sum_signed_cents_24h"] / result["sum_signed_cents_30d"].abs().clip(lower=100)
        result["amount_is_nonpositive"] = (amount <= 0).astype(float)
        result["amount_by_novel_merchant"] = amount.abs() * (1 - result["merchant_seen_30d"])
        for name, period in (("hour", 24), ("weekday", 7)):
            result[name + "_sin"] = np.sin(2 * np.pi * result[name] / period)
            result[name + "_cos"] = np.cos(2 * np.pi * result[name] / period)
    return result


class NeuralPreprocessor:
    """Robust train-only median/IQR scaling and unknown-safe categorical vocabularies."""
    def __init__(self, config): self.config = config

    def fit(self, frame):
        values = numeric_frame(frame, self.config.engineered)
        self.columns = list(values)
        raw = values.to_numpy(dtype=np.float64)
        transformed = np.sign(raw) * np.log1p(np.abs(raw))
        self.median = np.nanmedian(transformed, axis=0)
        self.median = np.nan_to_num(self.median)
        q25, q75 = np.nanpercentile(transformed, [25, 75], axis=0)
        self.scale = np.maximum(np.nan_to_num(q75 - q25), 0.25)
        self.categories = ("mcc", "channel") if self.config.include_channel else ("mcc",)
        self.vocabularies = {c: {v: i + 1 for i, v in enumerate(sorted(set(frame[c].fillna("<MISSING>").astype(str))))} for c in self.categories}
        return self

    def transform(self, frame):
        values = numeric_frame(frame, self.config.engineered)
        if list(values) != self.columns: raise ValueError("Feature schema changed")
        raw = values.to_numpy(dtype=np.float64)
        transformed = np.sign(raw) * np.log1p(np.abs(raw))
        missing = np.isnan(transformed)
        numeric = np.concatenate((np.clip((np.where(missing, self.median, transformed) - self.median) / self.scale, -8, 8), missing), axis=1).astype(np.float32)
        cats = np.column_stack([frame[c].fillna("<MISSING>").astype(str).map(self.vocabularies[c]).fillna(0).to_numpy(dtype=np.int64) for c in self.categories])
        if not np.isfinite(numeric).all(): raise ValueError("Finite preprocessed features required")
        return torch.from_numpy(np.ascontiguousarray(numeric)), torch.from_numpy(np.ascontiguousarray(cats))

    @property
    def categorical_sizes(self): return [len(self.vocabularies[c]) + 1 for c in self.categories]


class TabularRiskNetwork(nn.Module):
    def __init__(self, numeric_width, categorical_sizes, config):
        super().__init__()
        self.config = config
        dimensions = [min(12, max(2, int(math.ceil(math.sqrt(size))))) for size in categorical_sizes]
        self.embeddings = nn.ModuleList([nn.Embedding(size, dimension, padding_idx=0) for size, dimension in zip(categorical_sizes, dimensions)])
        self.input = nn.Sequential(nn.Linear(numeric_width + sum(dimensions), config.width), nn.GELU())
        self.blocks = nn.ModuleList([nn.Sequential(nn.LayerNorm(config.width), nn.Linear(config.width, config.width * 2), nn.GELU(), nn.Dropout(config.dropout), nn.Linear(config.width * 2, config.width), nn.Dropout(config.dropout)) for _ in range(config.blocks)])
        self.head = nn.Sequential(nn.LayerNorm(config.width), nn.Linear(config.width, 1))

    def forward(self, numeric, categories):
        embedded = [embedding(categories[:, i]) for i, embedding in enumerate(self.embeddings)]
        hidden = self.input(torch.cat([numeric, *embedded], dim=1))
        for block in self.blocks:
            update = block(hidden)
            hidden = hidden + update if self.config.architecture == "residual" else update
        return self.head(hidden).squeeze(-1)


def predict_tensors(model, tensors, batch_size=16384):
    model.eval()
    scores = []
    with torch.no_grad():
        for start in range(0, len(tensors[0]), batch_size):
            logits = model(tensors[0][start:start + batch_size], tensors[1][start:start + batch_size])
            if not torch.isfinite(logits).all(): raise ValueError("Nonfinite predictions")
            scores.append(torch.sigmoid(logits).numpy())
    return np.concatenate(scores)


class NeuralPredictor:
    def __init__(self, config, preprocessor, model): self.config, self.preprocessor, self.model = config, preprocessor, model

    def predict(self, frame): return predict_tensors(self.model, self.preprocessor.transform(frame))

    def save(self, directory):
        directory = Path(directory); directory.mkdir(parents=True, exist_ok=False)
        joblib.dump(self.preprocessor, directory / "preprocessor.joblib", compress=3)
        torch.save({"config": asdict(self.config), "numeric_width": len(self.preprocessor.columns) * 2,
                    "categorical_sizes": self.preprocessor.categorical_sizes, "state_dict": self.model.state_dict()}, directory / "model.pt")


def load_v2(directory):
    """Load only locally produced, trusted model artifacts; no arbitrary remote pickle."""
    directory = Path(directory)
    state = torch.load(directory / "model.pt", map_location="cpu", weights_only=True)
    config = NeuralConfig(**state["config"])
    preprocessor = joblib.load(directory / "preprocessor.joblib")
    if not isinstance(preprocessor, NeuralPreprocessor) or asdict(preprocessor.config) != asdict(config): raise ValueError("Preprocessor/config mismatch")
    model = TabularRiskNetwork(state["numeric_width"], state["categorical_sizes"], config)
    model.load_state_dict(state["state_dict"])
    return NeuralPredictor(config, preprocessor, model)


def predict_v2(model_path, feature_frame): return load_v2(model_path).predict(feature_frame)


def fit_v2(frame, y, config, *, seed=42, epochs=8, validation=None, on_epoch=None):
    """Explicit deterministic CPU loop; caller owns allowed periods and selection policy."""
    config = config if isinstance(config, NeuralConfig) else NeuralConfig(**config)
    if not 1 <= epochs <= 50: raise ValueError("Bounded positive epoch count required")
    y = np.asarray(y)
    if len(y) != len(frame) or not np.isin(y, [0, 1]).all() or len(np.unique(y)) != 2: raise ValueError("Both binary classes required")
    seed_training(seed, config.threads)
    prep = NeuralPreprocessor(config).fit(frame)
    tensors = prep.transform(frame)
    targets = torch.from_numpy(y.astype(np.float32))
    model = TabularRiskNetwork(tensors[0].shape[1], prep.categorical_sizes, config)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(config.positive_weight))
    generator = torch.Generator().manual_seed(seed)
    val_tensors = prep.transform(validation) if validation is not None else None
    for epoch in range(1, epochs + 1):
        model.train()
        indices = torch.randperm(len(y), generator=generator)
        loss_sum, batches, maximum_gradient_norm = 0., 0, 0.
        for start in range(0, len(y), config.batch_size):
            pos = indices[start:start + config.batch_size]
            optimizer.zero_grad(set_to_none=True)
            logits = model(tensors[0][pos], tensors[1][pos])
            loss = criterion(logits, targets[pos])
            if not torch.isfinite(loss): raise ValueError("Nonfinite loss")
            loss.backward()
            norm = nn.utils.clip_grad_norm_(model.parameters(), 5., error_if_nonfinite=True)
            maximum_gradient_norm = max(maximum_gradient_norm, float(norm))
            optimizer.step()
            loss_sum += float(loss.detach()) * len(pos); batches += 1
        row = {"epoch": epoch, "loss": loss_sum / len(y), "rows": len(y), "batches": batches, "max_preclip_gradient_norm": maximum_gradient_norm}
        if on_epoch: on_epoch(row, NeuralPredictor(config, prep, model), predict_tensors(model, val_tensors) if val_tensors is not None else None)
    return NeuralPredictor(config, prep, model)
