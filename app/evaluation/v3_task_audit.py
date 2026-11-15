"""Mechanical candidate audit, not linguistic truth or expert certification."""
from collections import Counter
import hashlib,json,re
from pathlib import Path
from app.investigation.v3.world import InvestigationWorld
from app.investigation.fraud_world import ACTIONS,MISSING_TYPES


def load(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def audit(directory,output):
    directory,output=Path(directory),Path(output)
    if output.exists():raise FileExistsError('New audit receipt required')
    manifest=load(directory/'dataset.json')
    if sha(directory/'dataset.json')!=load(directory/'dataset-sha256.json')['sha256']:raise ValueError('Dataset fingerprint changed')
    families={fold:set() for fold in ('development','evaluation')};subjects={k:set() for k in families};templates={k:set() for k in families};bodies={k:set() for k in families}
    cases=[];alerts=set();transactions=set();cost_cases=Counter()
    for entry in manifest['entries']:
        runtime_path=directory/entry['runtime'];reference_path=directory/entry['reference']
        if runtime_path.resolve().parent!= (directory/'runtime').resolve() or reference_path.resolve().parent!=(directory/'controller').resolve():raise ValueError('Artifact path escapes registered directories')
        if sha(runtime_path)!=entry['runtime_sha256'] or sha(reference_path)!=entry['reference_sha256']:raise ValueError('Case file changed')
        runtime=load(runtime_path);reference=load(reference_path);world=InvestigationWorld(runtime);fold=entry['fold']
        if reference['independent_person_reviewed'] or reference['external_expert_reviewed']:raise ValueError('No independent review was performed')
        if runtime['case_id']!=reference['case_id'] or runtime['case_id']!=entry['case_id']:raise ValueError('Case ID mismatch')
        families[fold].add(entry['family']);subjects[fold].add(runtime['subject_id']);templates[fold].add(entry['template_id'])
        if runtime['alert']['alert_id'] in alerts:raise ValueError('Repeated alert')
        alerts.add(runtime['alert']['alert_id'])
        ids={r['transaction_id'] for r in runtime['transactions']}
        if transactions&ids:raise ValueError('Repeated transaction across cases')
        transactions.update(ids)
        for record in runtime['records']:
            if record['source_type'] not in ('customer','account') or record['source_id'] in reference['never_visible_source_ids']:continue
            text=record['text']
            if text=='接听者未通过身份核验，只能复述别人的说法，不能形成持卡人对目标交易的确认。':continue  # explicitly shared status boilerplate
            for identity in sorted(ids,key=len,reverse=True):text=text.replace(identity,'<PAYMENT>')
            text=re.sub(r'channel-[AB]','<CHANNEL>',text)
            bodies[fold].add(hashlib.sha256(text.encode()).hexdigest())
        total_possible_cost=[]
        for stage in range(3):
            if stage:world.advance(stage)
            eligible=set(world.eligible());expected=reference['stages'][stage]
            if set(expected['allowed_sources'])!=eligible or eligible&set(reference['never_visible_source_ids']):raise ValueError('Reference availability differs from source clock')
            if expected['expected_source_statement']['value'] not in ('authorized','denied','unresolved'):raise ValueError('Unbounded source-statement reference')
            if expected['expected_source_statement']['transaction_id']!=runtime['alert']['transaction_id']:raise ValueError('Reference target changed')
            if 'confirmed_case_outcome' not in expected['required_missing']:raise ValueError('Outcome was improperly confirmed')
            if reference.get('reference_schema')=='v3-author-reference-v2':
                if not set(expected['critical_sources'])<=eligible or not set(expected['required_fact_source_ids'])<=set(expected['critical_sources']):raise ValueError('Critical/reference fact source mismatch')
                if not set(expected['required_missing'])<=set(MISSING_TYPES) or not set(expected['acceptable_actions'])<=set(ACTIONS):raise ValueError('Reference cannot be expressed by the output contract')
                if set(expected['acceptable_dispositions'])&set(expected['prohibited_dispositions']):raise ValueError('Disposition both allowed and prohibited')
                if expected['reopen_on_new_material'] is not True:raise ValueError('Evaluation cutoff must not permanently prevent reopening')
                if any(f['source_id'] not in eligible or f['transaction_id']!=runtime['alert']['transaction_id'] for f in expected['expected_observations']):raise ValueError('Expected observation scope')
            total_possible_cost.append(sum(r['cost_units'] for r in world.eligible().values()))
        required=reference['stages'][2]['critical_sources'];oracle=InvestigationWorld(runtime)
        oracle.advance(1)
        if reference['routing_required_source']:oracle.read([reference['routing_required_source']])
        oracle.advance(2);oracle.read(required)
        if total_possible_cost[2]>runtime['access_budget_units']:cost_cases[fold]+=1
        cases.append(dict(case_id=runtime['case_id'],all_stage_access_checked=True,oracle_required_sources_feasible=True,
            full_material_cost_units=total_possible_cost,case_budget_units=runtime['access_budget_units']))
    for name,groups in [('families',families),('subjects',subjects),('declared templates',templates),('normalized narrative bodies',bodies)]:
        if groups['development']&groups['evaluation']:raise ValueError('Cross-split overlap: '+name)
    counts=dict(Counter(e['fold'] for e in manifest['entries']))
    if counts!={'development':30,'evaluation':40} or len(subjects['development'])!=30 or len(subjects['evaluation'])!=40:raise ValueError('Expected 30/40 distinct-case split')
    receipt=dict(schema='v3-candidate-mechanical-audit-v1',dataset_sha256=sha(directory/'dataset.json'),counts=counts,
        families={k:len(v) for k,v in families.items()},subjects={k:len(v) for k,v in subjects.items()},
        cross_split_exact_normalized_narrative_overlap=0,cost_limited_cases=dict(cost_cases),
        independent_person_reviewed=False,external_expert_reviewed=False,formal_frozen=False,external_model_requests=0,
        cases=cases,limits=['Oracle feasibility uses hidden reference ONLY in audit, never a baseline or runtime',
            'Exact normalized overlap check does not certify semantic/template independence',
            'Author candidate references are not expert-reviewed or confirmed bank outcomes',
            'No model performance, planning necessity or analyst benefit has been demonstrated'])
    with output.open('x',encoding='utf-8') as stream:json.dump(receipt,stream,ensure_ascii=False,indent=2)
    return receipt
