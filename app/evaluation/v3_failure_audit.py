"""Read-only historical failure audit; never imports or instantiates an LLM client."""
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from statistics import mean

from jsonschema import ValidationError, validate
from app.agent.structured_output import parse_model_json
from app.evaluation.world_score import score
from app.investigation.fraud_world import ANSWER, originals
from app.service.store import digest

CATEGORIES = (
    'json_schema', 'citation_or_source_role', 'business_reference_mismatch',
    'missing_key_evidence', 'invalid_tool_selection', 'repetition_no_progress',
    'provider_timeout_or_empty', 'request_or_tool_budget', 'recovery_or_transport',
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sanitized_issue(issue):
    # No original prose, source transaction IDs, amounts, customer IDs or paths.
    clean = {k: issue[k] for k in ('kind', 'message', 'rule') if k in issue}
    if 'message' in clean:
        clean['message'] = re.sub(r'tabformer:[^\s"\']+|src-[0-9a-f]+', '<source>', str(clean['message']))[:180]
    return clean


def classify(result, state, usage, runtime, truth):
    tags = set(); issues = []; schema_error = None
    for review in state.get('draft_reviews', []):
        issues.extend(sanitized_issue(i) for i in review.get('issues', []))
    answer = state.get('proposed_answer')
    if answer is None:
        try: answer, _ = parse_model_json(state.get('raw', {}).get('content') or '')
        except (ValueError, TypeError, KeyError): pass
    if answer is not None:
        try: validate(answer, ANSWER)
        except ValidationError as exc:
            tags.add('json_schema')
            schema_error = dict(path=[str(v) for v in exc.absolute_path], rule=exc.validator)
    if result.get('reason') == 'ValidationError': tags.add('json_schema')
    if any(i.get('kind') in ('citation', 'timeline', 'timeline_not_sorted', 'hypothesis_claim_reference',
                            'recommendation_evidence_reference', 'tension_claim_reference', 'method_reference_not_read') for i in issues):
        tags.add('citation_or_source_role')
    raw_diagnostic = None
    if isinstance(answer, dict):
        try:
            raw_diagnostic = score(runtime, truth, answer, host_accepted=True)
            if raw_diagnostic['unsupported_claim_ids']: tags.add('business_reference_mismatch')
        except (ValueError, TypeError, KeyError, AttributeError): pass
    read_ids = set()
    for evidence in state.get('evidence', {}).values():
        read_ids.update(originals(evidence.get('result', {})))
    required = {c['source_id'] for c in truth['expected_claims'] if c['predicate'] in truth.get('required_predicates', [])}
    if required - read_ids: tags.add('missing_key_evidence')
    failures = [o for o in state.get('observations', []) if not o.get('ok', True)]
    if any(o.get('error') in ('invalid_host_operation', 'invalid_arguments', 'disabled_tool') for o in failures):
        tags.add('invalid_tool_selection')
    reason = result.get('reason') or ''; stop = usage.get('last_error_code') or ''
    if reason == 'no_progress_repeated_call': tags.add('repetition_no_progress')
    if stop == 'Assistant message has no content or tool calls' or stop == 'transport_error_unknown_charge':
        tags.add('provider_timeout_or_empty')
    if reason in ('request_limit', 'tool_budget', 'action_batch_limit') or 'budget' in stop or 'spend_limit' in stop:
        tags.add('request_or_tool_budget')
    if reason == 'unresolved_call' and not stop or stop == 'transport_error_unknown_charge':
        tags.add('recovery_or_transport')
    priority = ('provider_timeout_or_empty', 'recovery_or_transport', 'request_or_tool_budget',
                'repetition_no_progress', 'json_schema', 'citation_or_source_role',
                'invalid_tool_selection', 'business_reference_mismatch', 'missing_key_evidence')
    primary = next((t for t in priority if t in tags), 'unresolved_insufficient_record')
    return dict(primary=primary, tags=sorted(tags), schema_error=schema_error,
                issues=issues, unread_required_original_count=len(required-read_ids),
                draft_reference_diagnostic=raw_diagnostic,
                diagnostic_is_unvalidated_draft_not_recovered_success=True)


def audit(upgrade, dataset, output):
    upgrade, dataset, output = map(Path, (upgrade, dataset, output))
    if output.exists(): raise FileExistsError('New audit directory required')
    db_path = upgrade/'service/investigations/jobs.sqlite'
    before_db = sha(db_path); hashes = {}; rows = []; representatives = {}
    tasks = {t['case_id']:t for t in read(dataset/'dataset.json')['tasks']}
    with sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro', uri=True) as db:
        for phase in ('main-01', 'main-finance-02'):
            directory = upgrade/'phases'/phase
            for entry in read(directory/'prepared.json')['jobs']:
                path = directory/(entry['job']+'-result.json')
                if not path.exists(): continue  # cancelled, never-started financial migration
                result = read(path); hashes[str(path.relative_to(upgrade))] = sha(path)
                saved = db.execute('SELECT body,hash FROM investigation_state WHERE job_id=?', (entry['job'],)).fetchone()
                state = json.loads(saved[0]) if saved else {}
                if saved and digest(state) != saved[1]: raise ValueError('Checkpoint changed')
                usage_path = directory/'model-records'/entry['job']/'usage.json'
                usage = read(usage_path); task = tasks[entry['case_id']]
                runtime, truth = read(dataset/task['runtime']), read(dataset/task['evaluation'])
                row = dict(case_id=entry['case_id'], condition=entry['condition'], accepted=result['assessment']['host_accepted'],
                           original_assessment=deepcopy(result['assessment']), reason=result['reason'],
                           result_sha256=sha(path), usage_sha256=sha(usage_path), model_requests=usage['requests_sent'],
                           provider_stop_code=usage.get('last_error_code'))
                if not row['accepted']:
                    row['attribution'] = classify(result, state, usage, runtime, truth)
                    for tag in row['attribution']['tags']:
                        if tag not in representatives:
                            representatives[tag] = dict(case_id=row['case_id'], condition=row['condition'],
                                reason=row['reason'], primary=row['attribution']['primary'],
                                issues=row['attribution']['issues'][:4], schema_error=row['attribution']['schema_error'],
                                unread_required_original_count=row['attribution']['unread_required_original_count'],
                                phase=state.get('phase'), turn=state.get('turn'),
                                tool_trace=[dict(tool=t.get('tool'), ok=t.get('ok')) for t in state.get('trace', [])],
                                provider_stop_code=row['provider_stop_code'], original_result_sha256=row['result_sha256'],
                                content_omitted_for_minimal_disclosure=True)
                rows.append(row)
    if len(rows) != 180 or len({(r['case_id'],r['condition']) for r in rows}) != 180:
        raise ValueError('Expected exactly 180 original main conditions')
    failed = [r for r in rows if not r['accepted']]
    if len(failed) != 108: raise ValueError('Historical failure denominator changed')
    if sha(db_path) != before_db or any(sha(upgrade/p) != h for p,h in hashes.items()):
        raise ValueError('Read-only historical audit changed an input')
    groups = {}
    for condition in ('B1','B2','B3'):
        own = [r for r in rows if r['condition']==condition]; accepted = [r for r in own if r['accepted']]
        groups[condition] = dict(runs=len(own), delivered=len(accepted), delivery_rate=len(accepted)/len(own),
            full_sample_Q=mean(r['original_assessment']['quality'] for r in own),
            delivered_only_Q=mean(r['original_assessment']['quality'] for r in accepted),
            strict_full=sum(r['original_assessment']['strict_reference_passed'] for r in own)/len(own),
            strict_given_delivery=sum(r['original_assessment']['strict_reference_passed'] for r in accepted)/len(accepted))
    report = dict(schema='v3-historical-readonly-audit-v1', total=180, failed=108, accepted=72, groups=groups,
        primary_counts=dict(Counter(r['attribution']['primary'] for r in failed)),
        multilabel_counts={tag:sum(tag in r['attribution']['tags'] for r in failed) for tag in CATEGORIES},
        representatives={tag:representatives.get(tag,dict(observed=False,reason='No attributable record; do not fabricate an example')) for tag in CATEGORIES},
        all_cases=rows, input_results_sha256=hashes, original_db_sha256=before_db,
        original_inputs_unchanged=True, external_model_requests=0, external_expert_validation=False,
        limits=['primary is observed stop mechanism, not proven causal root cause',
                'tags overlap; counts must not be added', 'business mismatch uses old finite author reference only',
                'missing required source is not proof of irrational planning', 'draft diagnostics are not accepted reports'])
    output.mkdir(parents=True); (output/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
