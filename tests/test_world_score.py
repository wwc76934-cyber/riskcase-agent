from copy import deepcopy
from tests.test_fraud_inquiry import inquiry_snapshot
from app.evaluation.fraud_world_data import make_case
from app.evaluation.world_score import score
from app.investigation.fraud_world import World,materialize_timeline,verify
from app.investigation.world_baseline import report_from_observations
from app.investigation.contracts import digest


def test_version_metadata_is_valid_without_rule_line_and_wrong_version_still_fails(inquiry_snapshot):
    runtime,truth=make_case(inquiry_snapshot,'auth_denial',0,'metadata-reference-probe');source=World(runtime)
    observations=[dict(ok=True,evidence_id='E'+str(i),**source.call(n,{})) for i,n in enumerate(['inquiry_overview','world_materials'],1)]
    answer=report_from_observations(observations)
    version=next(o for o in answer['observations'] if o['predicate']=='policy_version')
    record=next(r for r in source.call('world_materials',{})['data']['B'] if r['source_type']=='procedure')
    version['citations'][0].update(lines=[1],quote=record['lines'][0]['text'])
    assert not score(runtime,truth,answer,host_accepted=True)['unsupported_claim_ids']
    version['value']=99
    assert version['id'] in score(runtime,truth,answer,host_accepted=True)['unsupported_claim_ids']


def test_source_layers_are_host_metadata_not_model_guesses(inquiry_snapshot):
    runtime,_=make_case(inquiry_snapshot,'auth_denial',0,'layer-materialization');source=World(runtime);evidence={};obs=[]
    for i,name in enumerate(['inquiry_overview','world_materials'],1):
        result=source.call(name,{});identity='E'+str(i);evidence[identity]=dict(result=result,sha256=digest(result));obs.append(dict(ok=True,evidence_id=identity,**result))
    raw=report_from_observations(obs)
    for event in raw['timeline']:event['layer']='A'
    canonical,changes=materialize_timeline(raw,evidence);verify(canonical,evidence)
    assert any(c['operation']=='hydrate_source_layer' for c in changes)
    assert all(e['layer']=='B' for e in canonical['timeline'] if e['source_id'].startswith('src-'))
    assert all(e['layer']=='A' for e in raw['timeline'])
