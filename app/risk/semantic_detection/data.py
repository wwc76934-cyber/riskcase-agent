"""Training/development adapters. Outcomes never enter model frames or graphs."""
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing

import numpy as np

from app.cli.train_neural_v2 import load_development
from app.risk.ml_models import MODEL_COLUMNS
from .common import digest


def load_data(source_root, *, train_only=False):
    source_root = Path(source_root).resolve()
    if train_only:
        from app.risk.ml_data import load_split
        manifest_path = source_root / 'data/features/features-b9692149f1c1/manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        cohort_path = Path(manifest['cohort_report'])
        cohort = json.loads(cohort_path.read_text(encoding='utf-8'))
        features = manifest_path.parent / 'features.sqlite'
        labels = cohort_path.parent / 'dataset/evaluation-labels.sqlite'
        if (digest(cohort_path) != manifest['cohort_report_sha256'] or digest(features) != manifest['features_sha256']
                or digest(labels) != cohort['artifact_sha256']['dataset/evaluation-labels.sqlite']):
            raise ValueError('Data provenance changed')
        with closing(sqlite3.connect(features.resolve().as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('ATTACH DATABASE ? AS evaluation', (labels.resolve().as_uri() + '?mode=ro',))
            loaded = [load_split(db, 'train', expected_rows=cohort['partitions']['train']['rows'],
                                 expected_positives=cohort['partitions']['train']['fraud_rows'])]
        return loaded, {'manifest': str(manifest_path), 'manifest_sha256': digest(manifest_path),
                        'features_sha256': digest(features), 'labels_sha256': digest(labels),
                        'only_training_records_selected': True}
    loaded, provenance = load_development(source_root)
    for _, _, meta in loaded:
        if not meta['event_time'].ge('2016-01-01').all() or not meta['event_time'].lt('2019-01-01').all():
            raise ValueError('Research periods violated')
    if not loaded[0][2]['split'].eq('train').all() or not loaded[1][2]['split'].eq('validation').all():
        raise ValueError('Training/evaluation scope mismatch')
    return loaded, provenance


def validate_frame(frame):
    if set(frame) != set(MODEL_COLUMNS):
        raise ValueError('Exact feature allowlist required; metadata/outcomes forbidden')


def label_mask(ids, y, fraction, seed=1729):
    """Train-only controller stratification. Hidden labels are removed before optimization."""
    y = np.asarray(y)
    if not 0 < fraction <= 1 or len(ids) != len(y) or set(np.unique(y)) != {0, 1}:
        raise ValueError('Binary train labels and fraction in (0,1] required')
    if len(set(ids)) != len(ids):
        raise ValueError('Unique training IDs required')
    mask = np.zeros(len(y), dtype=bool)
    for label in (0, 1):
        positions = np.flatnonzero(y == label)
        ordered = sorted(positions, key=lambda i: hashlib.sha256(f'{seed}\0{ids[i]}'.encode()).digest())
        count = max(1, int(np.ceil(len(positions) * fraction)))
        mask[ordered[:count]] = True
    exposed = np.where(mask, y, -1).astype(np.int8)
    return mask, exposed


def audit_fields(X, metadata, glossary):
    from .semantics import category_key
    return {'scope': '2016-2017 training only', 'rows': len(X),
            'fields': {name: {'missing': int(X[name].isna().sum()), 'unique': int(X[name].nunique()),
                             'counts': {str(k): int(v) for k, v in X[name].fillna('<missing>').value_counts().items()}}
                       for name in ('mcc', 'channel')},
            'mcc_unmapped': sorted({category_key(m, c)[0] for m, c in zip(X.mcc, X.channel)} - set(glossary['entries'])),
            'training_ids_sha256': hashlib.sha256('\n'.join(metadata.transaction_id).encode()).hexdigest()}
