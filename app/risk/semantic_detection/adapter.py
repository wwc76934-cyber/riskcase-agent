"""Optional label-blind queue/catalog adapter; never registers a default detector."""
import json
from pathlib import Path
import secrets

import numpy as np

from app.cli.release_demo import load_label_free_development, rank_alerts
from app.risk.case_snapshot import export_case
from app.risk.source_merchant_pack import select_users
from .common import digest, fingerprint, write_json
from .model import load_model


def checked_run(directory):
    directory = Path(directory).resolve()
    report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
    if report['status'] != 'E1_complete' or report['final_2019_read'] is not False:
        raise ValueError('Completed development-only research required')
    for name, expected in report['artifact_sha256'].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or digest(path) != expected:
            raise ValueError('Research material changed: ' + name)
    return report


def export_challenger(source_root, run, variant, output, users=2):
    source_root, run, output = map(lambda p: Path(p).resolve(), (source_root, run, output))
    if variant not in {'B0', 'B1', 'B2', 'B3', 'B3_shuffled'} or not 1 <= users <= 4:
        raise ValueError('Known challenger and bounded case count required')
    research = checked_run(run)
    if research.get('encoding_contract') != 'additive-v2':
        raise ValueError('Only formal additive challenger can be exported')
    if output.exists():
        raise FileExistsError('New challenger release directory required')
    features = source_root / 'data/features/features-b9692149f1c1/manifest.json'
    cohort_path = source_root / 'data/cohorts/cohort-3bfdbaab486f/build-report.json'
    X, meta, _, _, source = load_label_free_development(source_root / 'runs/neural-v2-development-01', features, cohort_path)
    research_data = json.loads((run / 'data.json').read_text(encoding='utf-8'))
    if research_data['features_sha256'] != source['features_sha256']:
        raise ValueError('Research and registered source snapshots differ')
    scores, differences, models = [], [], {}
    for seed in (42, 43, 44):
        model_path = run / variant / f'seed-{seed}' / 'model'
        expected = np.load(model_path.parent / 'development-scores.npy', allow_pickle=False)
        actual = load_model(model_path).predict(X)
        difference = float(np.max(np.abs(actual - expected)))
        if actual.shape != (len(meta),) or difference > 1e-6:
            raise ValueError('All-row score and identity alignment failed')
        scores.append(actual); differences.append(difference)
        models[str(seed)] = fingerprint(model_path)
    ensemble = np.mean(scores, axis=0)
    saved = np.load(run / variant / 'ensemble.npy', allow_pickle=False)
    if np.max(np.abs(ensemble - saved)) > 1e-6 or (ensemble < 0).any() or (ensemble > 1).any():
        raise ValueError('Ensemble differs or invalid scores')
    queue = rank_alerts(meta, ensemble)
    selected = select_users(queue, count=users, seed='semantic-challenger-release-v1', excluded=())
    output.mkdir(parents=True, exist_ok=False)
    release_id = 'semantic-challenger-' + variant.lower() + '-' + digest(run / 'report.json')[:12]
    write_json(output / 'daily-review.json', queue)
    write_json(output / 'alignment.json', {'rows_per_seed': len(meta), 'seeds': [42, 43, 44],
               'max_abs_difference': differences, 'labels_read': False, 'final_2019_read': False})
    write_json(output / 'model-metadata.json', {'release_id': release_id, 'role': 'challenger', 'variant': variant,
               'source': source, 'models': models, 'feature_version': 1,
               'report_sha256': digest(run / 'report.json'), 'score_kind': 'uncalibrated_ranking_sigmoid_not_probability',
               'registered_default': False, 'promotion_approved': False})
    cohort = json.loads(cohort_path.read_text(encoding='utf-8'))
    catalog = {}
    for i, alert in enumerate(selected):
        identity = f'{release_id}-case-{i}'
        path = export_case(output / 'cases' / identity, source_database=cohort_path.parent / 'dataset/facts.sqlite',
                           expected_source_sha256=source['source_facts_sha256'], transaction_id=alert['transaction_id'],
                           alert={'transaction_id': alert['transaction_id'], 'score': alert['score'], 'day_rank': alert['day_rank'],
                                  'detector': release_id, 'score_kind': 'uncalibrated_ranking_sigmoid_not_probability',
                                  'model_artifacts_sha256': __import__('hashlib').sha256(json.dumps(models, sort_keys=True).encode()).hexdigest(),
                                  'model_report_sha256': digest(run / 'report.json'),
                                  'selection': 'Label-blind fixed-seed user selection from 2018 daily10 challenger; not realtime'},
                           coverage_start=cohort['coverage']['warmup_start'], coverage_end=cohort['coverage']['end'],
                           full_source_scanned=True, source_report_sha256=source['cohort_report_sha256'])
        catalog[identity] = str(path)
    write_json(output / 'catalog.json', catalog)
    # Exercise existing generic service without changing its model-specific fraud contract.
    from app.service.runtime import ResearchService, Settings, Worker
    from app.risk.mixed_sql import SourceAudit
    from scripts.verify_mixed import verify
    service_dir = output / 'service'; service_dir.mkdir()
    token = secrets.token_urlsafe(32)
    (service_dir / 'auth.token').write_text(token, encoding='ascii')
    code_root = Path(__file__).resolve().parents[3]
    service = ResearchService(Settings(code_root, service_dir, {k: Path(v) for k, v in catalog.items()}, token, allow_live=False))
    receipts = []
    with SourceAudit(cohort_path) as audit:
        for case_id in catalog:
            job, _ = service.submit('challenger-' + case_id, case_id=case_id, task_type='natural_v2', mode='offline',
                                    question={'text': '只查频次和新商户'})
            row = Worker(service).step()
            if row['state'] != 'awaiting_plan_confirmation':
                raise ValueError('Scope proposal failed')
            service.confirm_plan(job, expected_version=row['version'], result_hash=row['result_hash'],
                                 plan_hash=row['result']['plan_hash'], decision='confirm_plan', request_key='simulated-' + case_id)
            row = Worker(service).step()
            if row['state'] != 'awaiting_review':
                raise ValueError('Investigation did not reach review')
            consumed = (service_dir / row['result']['directory'] / 'case/manifest.json').resolve()
            if not consumed.is_relative_to(service_dir) or not consumed.exists():
                raise ValueError('Bound consumer projection absent')
            bound_alert = json.loads(consumed.read_text(encoding='utf-8'))['alert']
            if (bound_alert['detector'] != release_id or bound_alert['model_report_sha256'] != digest(run / 'report.json')
                    or bound_alert['score_kind'] != 'uncalibrated_ranking_sigmoid_not_probability'):
                raise ValueError('Consumer detector version binding differs')
            proof = verify(service.artifact(job, 'summary.json'), audit)
            if not proof['task_passed']:
                raise ValueError('Independent SQL failed')
            receipts.append({'case_id': case_id, 'job_id': job, 'state': row['state'], 'sql': proof,
                             'confirmation': 'SIMULATED_NOT_HUMAN', 'real_llm_requests': 0,
                             'consumer_detector_id': bound_alert['detector'], 'detector_version_binding_verified': True})
    report = {'status': 'challenger_catalog_verified', 'release_id': release_id, 'cases': receipts,
              'generic_service_compatible': True, 'specialized_fraud_contract_compatible': False,
              'specialized_contract_limit': 'Existing BoundSource explicitly accepts only neural_v2_ensemble. Separate shared integration PR required; no spoofing or bypass.',
              'default_detector_changed': False, 'paid_calls': 0, 'labels_read': False,
              'adapter_code_sha256': digest(Path(__file__)), 'artifacts': fingerprint(output)}
    write_json(output / 'report.json', report)
    return report
