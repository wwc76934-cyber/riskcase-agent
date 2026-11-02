"""Explicit supervised masks and dropout-view consistency on TRAIN rows only."""
import math
import torch
from torch.nn import functional as F


def supervised_loss(logits, labels, *, kind='bce', positive_weight=20., gamma=2., beta=.999, class_weights=None):
    mask = labels >= 0
    if not mask.any():
        return logits.sum() * 0
    z, y = logits[mask], labels[mask].float()
    if kind == 'bce':
        return F.binary_cross_entropy_with_logits(z, y, pos_weight=z.new_tensor(positive_weight))
    if kind not in {'focal', 'cb_focal'} or gamma < 0 or not 0 <= beta < 1:
        raise ValueError('Supported focal configuration required')
    base = F.binary_cross_entropy_with_logits(z, y, reduction='none')
    pt = torch.where(y == 1, z.sigmoid(), (-z).sigmoid())
    weight = torch.ones_like(y)
    if kind == 'cb_focal':
        if class_weights is None or len(class_weights) != 2:
            raise ValueError('Explicit exposed-training-global class weights required')
        weight = torch.as_tensor(class_weights, device=y.device, dtype=y.dtype)[y.long()]
    return (weight * (1 - pt).pow(gamma) * base).mean()


def class_balanced_weights(labels, beta=.999):
    if not 0 <= beta < 1:
        raise ValueError('beta must be in [0,1)')
    counts = [int((labels == c).sum()) for c in (0, 1)]
    if min(counts) < 1:
        raise ValueError('Both exposed classes required')
    raw = [(1 - beta) / (-math.expm1(n * math.log(beta))) if beta else 1. for n in counts]
    return [2 * v / sum(raw) for v in raw]


def consistency_loss(first, second, labels):
    mask = labels < 0
    if not mask.any():
        return first.sum() * 0
    # Symmetric Bernoulli KL, differentiable in both legal dropout views.
    p, q = first[mask].sigmoid().clamp(1e-6, 1-1e-6), second[mask].sigmoid().clamp(1e-6, 1-1e-6)
    kl = lambda a, b: a * (a.log() - b.log()) + (1-a) * ((1-a).log() - (1-b).log())
    return .5 * (kl(p, q) + kl(q, p)).mean()
