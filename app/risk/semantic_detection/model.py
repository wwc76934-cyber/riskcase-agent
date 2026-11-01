"""Matched residual backbones; frozen semantic table never receives gradients."""
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from app.risk.neural_v2 import NeuralConfig, NeuralPreprocessor
from .data import validate_frame
from .semantics import UNKNOWN, category_key


@dataclass(frozen=True)
class DetectorConfig:
    variant: str = 'B1'
    width: int = 64
    blocks: int = 2
    dropout: float = .15
    learning_rate: float = .001
    weight_decay: float = .01
    positive_weight: float = 20.
    batch_size: int = 4096
    history: bool = False
    history_mode: str = 'attention'

    def __post_init__(self):
        if self.variant not in {'B0', 'B1', 'B2', 'B3', 'B3_shuffled'}:
            raise ValueError('Unknown variant')
        if not 4 <= self.width <= 256 or not 1 <= self.blocks <= 4 or not 0 <= self.dropout < 1:
            raise ValueError('Bounded backbone required')
        if not 0 < self.learning_rate <= .1 or not 0 <= self.weight_decay <= 1 or not 0 < self.positive_weight <= 1000:
            raise ValueError('Bounded optimizer required')
        if not 1 <= self.batch_size <= 65536:
            raise ValueError('Bounded batch required')
        if self.history_mode not in {'attention', 'mean'}:
            raise ValueError('Bounded historical aggregator required')


class Preprocessor:
    def fit(self, frame, keys):
        validate_frame(frame)
        self.numeric = NeuralPreprocessor(NeuralConfig(engineered=False, include_channel=True)).fit(frame)
        self.keys = [tuple(key) for key in keys]
        self.lookup = {key: i for i, key in enumerate(self.keys)}
        expected = {category_key(m, c) for m, c in zip(frame.mcc, frame.channel)}
        if not expected <= set(self.lookup) or self.keys[0] != (UNKNOWN, UNKNOWN):
            raise ValueError('Cache must cover training categories and explicit unknown')
        return self

    def transform(self, frame):
        validate_frame(frame)
        numeric, categories = self.numeric.transform(frame)
        pairs = torch.tensor([self.lookup.get(category_key(m, c), 0) for m, c in zip(frame.mcc, frame.channel)], dtype=torch.long)
        return numeric, categories, pairs


class ReplacementPilotDetector(nn.Module):
    def __init__(self, config, table, sizes):
        super().__init__()
        self.config = config
        dimension = table.shape[1]
        if config.variant == 'B0':
            self.categories = nn.ModuleList([nn.Embedding(size, dimension // 2, padding_idx=0) for size in sizes])
        elif config.variant == 'B1':
            self.category = nn.Embedding(len(table), dimension, padding_idx=0)
        else:
            vectors = torch.as_tensor(table).clone().float()
            if config.variant == 'B3_shuffled':
                permutation = torch.randperm(len(vectors) - 1, generator=torch.Generator().manual_seed(731)) + 1
                vectors[1:] = vectors[permutation]
            self.category = nn.Embedding.from_pretrained(vectors, freeze=True)
        self.projection = nn.Linear(dimension, dimension) if config.variant in {'B3', 'B3_shuffled'} else nn.Identity()
        self.input = nn.Sequential(nn.Linear(24 + dimension, config.width), nn.GELU())
        self.blocks = nn.ModuleList([nn.Sequential(nn.LayerNorm(config.width), nn.Linear(config.width, config.width * 2),
                                    nn.GELU(), nn.Dropout(config.dropout), nn.Linear(config.width * 2, config.width),
                                    nn.Dropout(config.dropout)) for _ in range(config.blocks)])
        if config.history:
            if config.history_mode == 'attention':
                self.query, self.key = (nn.Linear(config.width, config.width) for _ in range(2))
            self.value = nn.Linear(config.width, config.width)
            self.gate = nn.Linear(config.width * 2, config.width)
        self.head = nn.Sequential(nn.LayerNorm(config.width), nn.Linear(config.width, 1))

    def encode(self, numeric, categories, pairs):
        categorical = torch.cat([layer(categories[:, i]) for i, layer in enumerate(self.categories)], dim=1) if self.config.variant == 'B0' else self.category(pairs)
        hidden = self.input(torch.cat([numeric, self.projection(categorical)], dim=1))
        for block in self.blocks:
            hidden = hidden + block(hidden)
        return hidden

    def forward(self, numeric, categories, pairs, history=None):
        hidden = self.encode(numeric, categories, pairs)
        if self.config.history:
            if history is None:
                raise ValueError('Explicit bounded historical neighborhood required')
            hn, hc, hp, mask, ages = history
            size, neighbors = hp.shape
            prior = self.encode(hn.flatten(0, 1), hc.flatten(0, 1), hp.flatten()).reshape(size, neighbors, -1)
            if self.config.history_mode == 'attention':
                attention = (self.query(hidden).unsqueeze(1) * self.key(prior)).sum(-1) / hidden.shape[-1] ** .5
                attention = attention - torch.log1p(ages / 3600)
                attention = attention.masked_fill(~mask, -1e9).softmax(1) * mask
            else:
                attention = mask.float()
            attention = attention / attention.sum(1, keepdim=True).clamp_min(1e-8)
            context = (attention.unsqueeze(-1) * self.value(prior)).sum(1)
            gate = self.gate(torch.cat([hidden, context], 1)).sigmoid()
            fused = gate * hidden + (1 - gate) * context
            hidden = torch.where(mask.any(1, keepdim=True), fused, hidden)
        return self.head(hidden).squeeze(-1)


class Detector(ReplacementPilotDetector):
    """PRD contract: original numerical + category branch ALWAYS retained.

    B1/B2/B3 add a matched-width auxiliary category representation. The pilot
    replacement architecture remains loadable solely to preserve its records.
    """
    encoding_contract = 'additive-v2'

    def __init__(self, config, table, sizes):
        super().__init__(config, table, sizes)
        if config.variant != 'B0':
            dimension = table.shape[1]
            self.base_categories = nn.ModuleList([nn.Embedding(size, dimension // 2, padding_idx=0) for size in sizes])
            self.input = nn.Sequential(nn.Linear(24 + dimension * 2, config.width), nn.GELU())

    def encode(self, numeric, categories, pairs):
        if self.config.variant == 'B0':
            return super().encode(numeric, categories, pairs)
        original_categories = torch.cat([layer(categories[:, i]) for i, layer in enumerate(self.base_categories)], dim=1)
        additional = self.projection(self.category(pairs))
        hidden = self.input(torch.cat([numeric, original_categories, additional], dim=1))
        for block in self.blocks:
            hidden = hidden + block(hidden)
        return hidden


class Predictor:
    def __init__(self, config, prep, model, table_shape):
        self.config, self.prep, self.model, self.table_shape = config, prep, model, table_shape

    def predict(self, frame, batch_size=16384):
        if self.config.history:
            raise ValueError('Use explicit causal history predictor for history models')
        tensors = self.prep.transform(frame)
        self.model.eval()
        scores = []
        with torch.inference_mode():
            for start in range(0, len(frame), batch_size):
                result = self.model(*(t[start:start + batch_size] for t in tensors)).sigmoid()
                if not torch.isfinite(result).all():
                    raise ValueError('Nonfinite prediction')
                scores.append(result.numpy())
        return np.concatenate(scores)

    def save(self, directory):
        directory = Path(directory); directory.mkdir(parents=True, exist_ok=False)
        joblib.dump(self.prep, directory / 'preprocessor.joblib')
        torch.save({'config': asdict(self.config), 'table_shape': self.table_shape,
                    'encoding_contract': getattr(self.model, 'encoding_contract', 'replacement-pilot-v1'),
                    'categorical_sizes': self.prep.numeric.categorical_sizes,
                    'state_dict': self.model.state_dict()}, directory / 'model.pt')


def load_model(directory):
    """Trusted local artifacts only; joblib must not be loaded from arbitrary sources."""
    directory = Path(directory)
    state = torch.load(directory / 'model.pt', map_location='cpu', weights_only=True)
    prep = joblib.load(directory / 'preprocessor.joblib')
    config = DetectorConfig(**state['config'])
    model_type = Detector if state.get('encoding_contract') == 'additive-v2' else ReplacementPilotDetector
    model = model_type(config, np.zeros(state['table_shape'], dtype=np.float32), state['categorical_sizes'])
    model.load_state_dict(state['state_dict'])
    return Predictor(config, prep, model, state['table_shape'])
