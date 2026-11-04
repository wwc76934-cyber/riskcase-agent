"""Fail-closed research admission. No pickle loader or network client."""
from pathlib import PurePosixPath


def validate_member(name, size, limit=150_000_000):
    path=PurePosixPath(name.replace('\\','/'))
    if path.is_absolute() or '..' in path.parts or ':' in name or size<0 or size>limit:
        raise ValueError('Unsafe archive member')
    return path.as_posix()


def require_training_admission(evidence):
    required=('data_rights_cleared','safe_data_conversion_verified','behavior_sequences_verified',
              'labels_and_limitations_reviewed','initial_screening_feature_allowlist_verified',
              'split_and_metric_protocol_frozen')
    missing=[key for key in required if evidence.get(key) is not True]
    if missing:raise ValueError('Training not admitted: '+', '.join(missing))
    return True
