"""Run the registered A3 matrix with the separately frozen suffix-order correction."""
import json
from pathlib import Path
import sys

from app.risk.behavior_semantic.semantics import history_shuffle
from app.risk.behavior_semantic.study import sha
from scripts.behavior_v3 import run_semantic


def shuffle_visible_suffix(rows):
    """Permute exactly what the ordered model would see, retaining the terminal slot."""
    visible = [{**row, 'actions':row['actions'][-256:], 'times':row['times'][-256:]} for row in rows]
    return history_shuffle(visible)


def main():
    def gate_argument(flag):
        index = sys.argv.index(flag)
        path = Path(sys.argv[index+1])
        del sys.argv[index:index+2]
        return path

    replay_path = gate_argument('--a2-replay')
    audit_path = gate_argument('--input-audit')
    protocol = Path(sys.argv[sys.argv.index('--protocol')+1])
    policy = json.loads(protocol.read_text(encoding='utf8'))
    if policy.get('history_control_revision') != 'truncate_suffix_then_shuffle_keep_terminal':
        raise ValueError('Separate corrected protocol required')
    for path, digest in policy['code_sha256'].items():
        if sha(path) != digest:
            raise ValueError('Frozen source changed: ' + path)
    original = json.loads(Path('config/behavior-semantic/track-a-a3.json').read_text(encoding='utf8'))
    allowed = {'history_control_revision','code_sha256','order_control'}
    if {k:v for k,v in policy.items() if k not in allowed} != {k:v for k,v in original.items() if k not in allowed}:
        raise ValueError('Corrected protocol changes an unrelated A3 setting')
    replay = json.loads(replay_path.read_text(encoding='utf8'))
    audit = json.loads(audit_path.read_text(encoding='utf8'))
    a2_results = Path(policy['A2_run'])/'development-results.json'
    if (replay['status'] != 'full_development_replay_verified' or replay['instances'] != 21
            or replay['test_predictions_generated'] or audit['test_predictions_generated']
            or replay['development_results_sha256'] != sha(a2_results) or replay['input_audit_sha256'] != sha(audit_path)
            or audit['records_sha256'] != policy['records_sha256'] or audit['split_sha256'] != policy['split_sha256']):
        raise ValueError('A2 independent replay and reviewed inputs are required before A3')
    # Override the runner's single injected transform for this process. The frozen runner
    # and original registration remain available byte-for-byte for audit.
    run_semantic.history_shuffle = shuffle_visible_suffix
    run_semantic.main()


if __name__ == '__main__':
    main()
