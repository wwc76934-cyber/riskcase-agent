"""Read-only preservation proof for pre-existing detector evidence and frozen source."""
import json
from pathlib import Path

from app.risk.semantic_detection.common import digest, write_json


def run(source_root, research_root, history_run, output):
    source_root, research_root, history_run, output = map(Path, (source_root, research_root, history_run, output))
    output.mkdir(parents=True, exist_ok=False)
    manifest = research_root/'runs/semantic-detection-sources/original-artifacts-before.json'
    baseline = json.loads(manifest.read_text(encoding='utf8'))
    checked, differences = 0, []
    for group, entries in baseline.items():
        for name, expected in entries.items():
            path = source_root/group/name
            actual = digest(path) if path.is_file() else None
            checked += 1
            if actual != expected:
                differences.append({'path': str(Path(group)/name), 'expected': expected, 'actual': actual})
    protocol = json.loads((history_run/'protocol.json').read_text(encoding='utf8'))
    changed_source = [name for name, expected in protocol['code'].items() if digest(research_root/name) != expected]
    record = {'status': 'original_artifacts_and_frozen_source_preserved' if not differences and not changed_source else 'preservation_check_failed',
              'preexisting_files_checked': checked, 'differences': differences, 'critical_source_checked': len(protocol['code']),
              'changed_critical_source': changed_source, 'baseline_manifest_sha256': digest(manifest),
              'hash_only': True, 'records_or_labels_evaluated': False,
              'limit': 'Hashing can include historical 2019 artifact bytes for integrity; no 2019 arrays or outcomes are decoded/evaluated'}
    write_json(output/'report.json', record)
    if differences or changed_source:
        raise ValueError('Pre-existing evidence or frozen source differs; inspect retained preservation report')
    return record
