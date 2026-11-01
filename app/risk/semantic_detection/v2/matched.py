"""Matched original residual core with an explicitly normalized auxiliary branch."""
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from app.risk.neural_v2 import NeuralConfig, NeuralPreprocessor, TabularRiskNetwork
from app.risk.semantic_detection.data import validate_frame
from app.risk.semantic_detection.semantics import UNKNOWN, category_key


@dataclass(frozen=True)
class Config:
    representation: str = 'original'
    include_channel: bool = False
    dimensions: int = 32
    seed: int = 42
    engineered: bool = False
    width: int = 64
    blocks: int = 2
    dropout: float = .15
    learning_rate: float = .001
    weight_decay: float = .01
    positive_weight: float = 20.
    batch_size: int = 4096
    threads: int = 4

    def __post_init__(self):
        if self.representation not in {'original', 'learned', 'semantic', 'industry', 'description_shuffle', 'category_shuffle', 'random'}:
            raise ValueError('Approved auxiliary representation required')
        if self.dimensions not in (32, 128, 256) or type(self.include_channel) is not bool:
            raise ValueError('Frozen dimension and channel contract required')
        self.core_config()

    def core_config(self):
        return NeuralConfig(**{k: v for k, v in asdict(self).items() if k in NeuralConfig.__dataclass_fields__})


class Preprocessor:
    def __init__(self, config):
        self.config = config

    def key(self, mcc, channel):
        m, c = category_key(mcc, channel)
        return (m, c if self.config.include_channel else UNKNOWN)

    def fit(self, frame, keys):
        validate_frame(frame)
        self.numeric = NeuralPreprocessor(self.config.core_config()).fit(frame)
        self.keys = [tuple(k) for k in keys]
        if len(set(self.keys)) != len(self.keys) or self.keys[0] != (UNKNOWN, UNKNOWN):
            raise ValueError('Canonical unknown-safe category keys required')
        if not self.config.include_channel and any(c != UNKNOWN for _, c in self.keys):
            raise ValueError('Channel information forbidden in MCC-only auxiliary vocabulary')
        self.lookup = {k: i for i, k in enumerate(self.keys)}
        if not {self.key(m, c) for m, c in zip(frame.mcc, frame.channel)} <= set(self.lookup):
            raise ValueError('Training categories absent from auxiliary vocabulary')
        return self

    def transform(self, frame):
        validate_frame(frame)
        numeric, categories = self.numeric.transform(frame)
        pairs = torch.tensor([self.lookup.get(self.key(m, c), 0) for m, c in zip(frame.mcc, frame.channel)], dtype=torch.long)
        return numeric, categories, pairs


class Model(nn.Module):
    def __init__(self, config, table, numeric_width, categorical_sizes):
        super().__init__()
        self.config = config
        # Exactly the original core, initialized before any auxiliary operation.
        self.core = TabularRiskNetwork(numeric_width, categorical_sizes, config.core_config())
        self.table_shape = list(np.shape(table))
        if self.table_shape[1] != config.dimensions or not np.isfinite(table).all():
            raise ValueError('Finite representation with frozen dimensionality required')
        if config.representation != 'original':
            with torch.random.fork_rng(devices=[]):
                torch.manual_seed(config.seed + 101)
                if config.representation == 'learned':
                    self.aux = nn.Embedding(len(table), config.dimensions, padding_idx=0)
                else:
                    vectors = torch.as_tensor(table).clone().float()
                    vectors[0] = 0
                    if config.representation == 'category_shuffle':
                        p = torch.randperm(len(vectors) - 1, generator=torch.Generator().manual_seed(731)) + 1
                        vectors[1:] = vectors[p]
                    elif config.representation == 'random':
                        vectors = torch.randn(vectors.shape, generator=torch.Generator().manual_seed(731))
                        vectors[0] = 0
                    self.aux = nn.Embedding.from_pretrained(vectors, freeze=True, padding_idx=0)
                self.aux_input = nn.Linear(config.dimensions, config.width, bias=False)

    def encode(self, numeric, categories, pairs):
        base = torch.cat([numeric, *[layer(categories[:, i]) for i, layer in enumerate(self.core.embeddings)]], dim=1)
        h = self.core.input[0](base)
        if self.config.representation != 'original':
            h = h + self.aux_input(F.normalize(self.aux(pairs), dim=1))
        h = self.core.input[1](h)
        for block in self.core.blocks:
            h = h + block(h)
        return h

    def forward(self, numeric, categories, pairs):
        return self.core.head(self.encode(numeric, categories, pairs)).squeeze(-1)


class Predictor:
    def __init__(self, config, prep, model):
        self.config, self.prep, self.model = config, prep, model

    def predict(self, frame, batch_size=16384):
        tensors = self.prep.transform(frame)
        self.model.eval()
        scores = []
        with torch.inference_mode():
            for start in range(0, len(frame), batch_size):
                result = self.model(*(x[start:start + batch_size] for x in tensors)).sigmoid()
                if not torch.isfinite(result).all():
                    raise ValueError('Nonfinite prediction')
                scores.append(result.numpy())
        return np.concatenate(scores)

    def save(self, directory):
        directory = Path(directory); directory.mkdir(parents=True, exist_ok=False)
        joblib.dump(self.prep, directory / 'preprocessor.joblib')
        torch.save({'config': asdict(self.config), 'table_shape': self.model.table_shape,
                    'numeric_width': len(self.prep.numeric.columns) * 2,
                    'categorical_sizes': self.prep.numeric.categorical_sizes,
                    'state_dict': self.model.state_dict()}, directory / 'model.pt')


def load(directory):
    directory = Path(directory)
    state = torch.load(directory / 'model.pt', map_location='cpu', weights_only=True)
    config = Config(**state['config'])
    model = Model(config, np.zeros(state['table_shape'], dtype=np.float32), state['numeric_width'], state['categorical_sizes'])
    model.load_state_dict(state['state_dict'])
    return Predictor(config, joblib.load(directory / 'preprocessor.joblib'), model)
