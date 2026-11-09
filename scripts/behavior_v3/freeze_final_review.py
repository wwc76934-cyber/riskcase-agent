"""Freeze a blind final-scoring proposal from successful development-only receipts."""
import argparse
import json
from pathlib import Path

from app.risk.behavior_semantic.study import sha, write


def read(path):
    return json.loads(path.read_text(encoding='utf8'))


def main():
    parser = argparse.ArgumentParser()
    for name in ('a2','a3','a2-replay','a3-replay','analysis','a4-decision','output'):
        parser.add_argument('--'+name,type=Path,required=True)
    args = parser.parse_args()
    decision = read(args.a4_decision)
    if (decision['decision'] != 'C_stop_fusion' or decision['final_test_predictions_generated']
            or decision['analysis_sha256'] != sha(args.analysis)):
        raise ValueError('This review requires the recorded no-fusion development decision')
    analysis = read(args.analysis)
    if analysis['final_test_predictions_generated'] or analysis['A4_status'] != 'C_no_reliable_semantic_increment_stop_fusion':
        raise ValueError('Development evidence does not match the decision')
    hashes = {p.as_posix():sha(p) for p in (args.analysis,args.a4_decision)}
    models = {}
    policy = None
    for phase, run, replay_path, count in [('A2',args.a2,args.a2_replay,21),('A3',args.a3,args.a3_replay,14)]:
        report_path, manifest_path, protocol_path = [run/name for name in
            ('development-results.json','all-training-complete.json','protocol.json')]
        report, manifest, protocol, replay = [read(p) for p in (report_path,manifest_path,protocol_path,replay_path)]
        if (report['final_test_predictions_generated'] or replay['test_predictions_generated']
                or replay['status'] != 'full_development_replay_verified' or manifest['instances'] != count
                or replay['instances'] != count or replay['development_results_sha256'] != sha(report_path)
                or analysis[f'{phase}_source_sha256'] != sha(report_path)
                or set(manifest['artifacts']) != set(replay['models']) or len(replay['models']) != count):
            raise ValueError('Incomplete blind development evidence: '+phase)
        if policy is not None and any(policy[name] != protocol[name] for name in ('records','split','records_sha256','split_sha256')):
            raise ValueError('A2 and A3 use different inputs')
        policy = protocol
        for p in (report_path,manifest_path,protocol_path,replay_path):
            hashes[p.as_posix()] = sha(p)
        for p,digest in protocol['code_sha256'].items():
            if sha(p) != digest:
                raise ValueError('Frozen fit source changed: '+p)
            hashes[p] = digest
        for key,spec in manifest['artifacts'].items():
            if key in models:
                raise ValueError('Duplicate model identity: '+key)
            if replay['models'][key]['score_replay_max_abs_error'] > 1e-6:
                raise ValueError('Development replay tolerance exceeded: '+key)
            folder = Path(spec['folder'])
            for name,digest in replay['models'][key]['sha256'].items():
                path = folder/name
                if sha(path) != digest:
                    raise ValueError('Artifact changed: '+str(path))
                hashes[path.as_posix()] = digest
            models[key] = {'family':key.split('/')[0],'folder':folder.as_posix(),'condition':spec.get('condition',{}),
                           'history_control_revision':protocol.get('history_control_revision')}
    for name in ('records','split'):
        if sha(policy[name]) != policy[name+'_sha256']:
            raise ValueError('Frozen input changed: '+name)
        hashes[policy[name]] = policy[name+'_sha256']
    scorer = 'scripts/behavior_v3/final_evaluate.py'
    hashes[scorer] = sha(scorer)
    proposal = {'status':'pending_explicit_final_scoring_approval','final_test_previously_scored':False,
        'a4_decision':decision['decision'],'records':policy['records'],'split':policy['split'],
        'models':models,'artifact_sha256':hashes,'primary_metric':'AP, stepwise precision weighted by recall increments',
        'static_review_budget':'ceil(0.01 * 20673) = 207 rows; stable label-independent tie-break; all frozen test rows',
        'thresholds':'F1 threshold learned only from saved development predictions; no test retuning',
        'primary_contrasts':['qwen_minus_gru','qwen_minus_random','qwen_minus_tfidf_svd','qwen_minus_combined_lr'],
        'test_size_from_frozen_aggregate':{'rows':20673,'positive':18},
        'acceptance_scope':'One-pass scoring of all 35 frozen models. No additional fits or filtering based on final test labels.',
        'final_test_predictions_generated':False}
    if args.output.exists():
        raise FileExistsError('Use a new freeze manifest path')
    write(args.output,proposal)
    print('blind proposal',len(models),'models',len(hashes),'fingerprints',sha(args.output))


if __name__=='__main__':
    main()
