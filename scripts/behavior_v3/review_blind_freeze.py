"""Verify the existing V3 approval proposal without loading the test membership or labels."""
import argparse
import hashlib
import json
from pathlib import Path


PROPOSAL_SHA = '4ae2526f1e94491135daa33d56713e8e85fe11c6370cdcd55e2b29a1e6eaa3d0'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def verify(path):
    if sha(path) != PROPOSAL_SHA:
        raise ValueError('V3 proposal fingerprint changed')
    proposal = read(path)
    if (proposal['status'] != 'pending_explicit_final_scoring_approval' or proposal['final_test_previously_scored']
            or proposal['final_test_predictions_generated'] or len(proposal['models']) != 35
            or len(proposal['artifact_sha256']) != 145 or proposal['a4_decision'] != 'C_stop_fusion'):
        raise ValueError('V3 freeze metadata changed')
    for name, digest in proposal['artifact_sha256'].items():
        if sha(name) != digest:
            raise ValueError('Frozen file changed: ' + name)
    observed = {}
    for phase, count in (('a2',21),('a3',14)):
        receipt = read(f'runs/track-a-{phase}-replay-01/replay.json')
        if (receipt['instances'] != count or receipt['test_predictions_generated']
                or receipt['status'] != 'full_development_replay_verified' or len(receipt['models']) != count):
            raise ValueError('Invalid development replay: '+phase)
        for key,value in receipt['models'].items():
            model = proposal['models'][key]
            folder = Path(model['folder'])
            if any(proposal['artifact_sha256'][(folder/name).as_posix()] != digest for name,digest in value['sha256'].items()):
                raise ValueError('Model identity/replay mismatch: '+key)
            observed[key] = value['score_replay_max_abs_error']
    if len(observed) != 35 or max(observed.values()) > 1e-6:
        raise ValueError('Incomplete independent development replay')
    outputs = [p for p in Path('runs').glob('track-a*/**/*') if p.is_file() and
               (p.name == 'test-scores.npy' or p.name.endswith('-ensemble-test.npy'))]
    if outputs:
        raise ValueError('V3 test outputs already exist')
    return proposal, {'status':'awaiting_explicit_approval','proposal_sha256':PROPOSAL_SHA,
                      'models':len(observed),'verified_fingerprints':145,'max_development_replay_error':max(observed.values()),
                      'test_membership_or_labels_opened':False,'test_scoring_started':False}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--proposal',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    _,receipt=verify(a.proposal)
    with a.output.open('x',encoding='utf8') as stream:
        json.dump(receipt,stream,indent=2)
    print(json.dumps(receipt))


if __name__=='__main__':
    main()
