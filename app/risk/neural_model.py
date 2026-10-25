"""Small CPU MLP and explicit train/evaluate loops, without dataset permissions."""

from copy import deepcopy
import random

import numpy as np
from scipy import sparse
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


class RiskMLP(nn.Module):
    def __init__(self, input_width, hidden_widths=(64, 32), dropout=0.1):
        super().__init__()
        if input_width < 1 or any(width < 1 for width in hidden_widths) or not 0 <= dropout < 1:
            raise ValueError("Valid network dimensions and dropout required")
        layers, previous = [], input_width
        for width in hidden_widths:
            layers.extend([nn.Linear(previous, width), nn.ReLU(), nn.Dropout(dropout)])
            previous = width
        layers.append(nn.Linear(previous, 1))
        self.network = nn.Sequential(*layers)

    def forward(self, features):
        return self.network(features).squeeze(-1)  # [batch, D] -> [batch], raw logits


def seed_training(seed=42, threads=2):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    torch.use_deterministic_algorithms(True)


def dense_float32(values, *, max_bytes=512 * 1024 * 1024):
    if len(values.shape) != 2 or not values.shape[0] or not values.shape[1]:
        raise ValueError("Nonempty two-dimensional features required")
    needed = int(values.shape[0]) * int(values.shape[1]) * 4
    if needed > max_bytes:
        raise ValueError(f"Dense allocation {needed} exceeds declared limit {max_bytes}")
    result = values.astype(np.float32).toarray() if sparse.issparse(values) else np.asarray(values, dtype=np.float32)
    result = np.ascontiguousarray(result)
    if not np.isfinite(result).all():
        raise ValueError("Only finite preprocessed model inputs allowed")
    return result


def make_loader(X, y=None, *, batch_size=2048, shuffle=False, seed=42):
    if X.ndim != 2 or X.dtype != np.float32 or not len(X) or not np.isfinite(X).all():
        raise ValueError("Nonempty finite float32 features required")
    if batch_size < 1:
        raise ValueError("Positive batch size required")
    tensors = [torch.from_numpy(X)]
    if y is not None:
        y = np.asarray(y)
        if y.ndim != 1 or len(y) != len(X) or not np.isin(y, [0, 1]).all():
            raise ValueError("Matching binary targets required")
        tensors.append(torch.from_numpy(y.astype(np.float32)))
    generator = torch.Generator().manual_seed(seed)
    return DataLoader(TensorDataset(*tensors), batch_size=batch_size, shuffle=shuffle,
                      num_workers=0, generator=generator)


def make_loss(y):
    y = np.asarray(y)
    if y.ndim != 1 or not np.isin(y, [0, 1]).all() or len(np.unique(y)) != 2:
        raise ValueError("Training targets must contain both binary classes")
    ratio = float((y == 0).sum() / (y == 1).sum())
    return nn.BCEWithLogitsLoss(pos_weight=torch.tensor(ratio, dtype=torch.float32)), ratio


def train_one_epoch(model, loader, optimizer, criterion):
    model.train()
    loss_sum, rows, batches, maximum_norm = 0.0, 0, 0, 0.0
    for features, targets in loader:
        optimizer.zero_grad(set_to_none=True)
        logits = model(features)
        loss = criterion(logits, targets)
        if not torch.isfinite(loss):
            raise ValueError("Nonfinite training loss")
        loss.backward()
        squared_norm = 0.0
        for parameter in model.parameters():
            if parameter.grad is None or not torch.isfinite(parameter.grad).all():
                raise ValueError("Missing/nonfinite gradient")
            squared_norm += float(parameter.grad.square().sum())
        maximum_norm = max(maximum_norm, squared_norm ** 0.5)
        optimizer.step()
        if any(not torch.isfinite(parameter).all() for parameter in model.parameters()):
            raise ValueError("Nonfinite updated parameter")
        loss_sum += float(loss.detach()) * len(features)
        rows += len(features)
        batches += 1
    if not rows:
        raise ValueError("Training loader is empty")
    return {"weighted_loss": loss_sum / rows, "rows": rows, "batches": batches,
            "maximum_gradient_norm": maximum_norm}


def predict_batches(model, loader, criterion=None):
    model.eval()
    scores, loss_sum, rows = [], 0.0, 0
    with torch.no_grad():
        for batch in loader:
            logits = model(batch[0])
            if not torch.isfinite(logits).all():
                raise ValueError("Nonfinite evaluation logits")
            scores.append(torch.sigmoid(logits).cpu().numpy())
            if criterion is not None:
                loss = criterion(logits, batch[1])
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite evaluation loss")
                loss_sum += float(loss) * len(logits)
            rows += len(logits)
    if not rows:
        raise ValueError("Evaluation loader is empty")
    return np.concatenate(scores), loss_sum / rows if criterion is not None else None


class EarlyStop:
    """Predeclared maximum AP, earliest ties; checkpoint tensors are copied."""
    def __init__(self, patience=4, minimum_improvement=1e-6):
        if patience < 1 or minimum_improvement < 0:
            raise ValueError("Valid stopping parameters required")
        self.patience, self.minimum_improvement = patience, minimum_improvement
        self.best_score, self.best_epoch, self.bad_epochs, self.best_state = -np.inf, 0, 0, None

    def update(self, score, epoch, model):
        if not np.isfinite(score) or epoch < 1:
            raise ValueError("Finite stopping score and positive epoch required")
        improved = score > self.best_score + self.minimum_improvement
        if improved:
            self.best_score, self.best_epoch, self.bad_epochs = float(score), epoch, 0
            self.best_state = deepcopy(model.state_dict())
        else:
            self.bad_epochs += 1
        return improved, self.bad_epochs >= self.patience
