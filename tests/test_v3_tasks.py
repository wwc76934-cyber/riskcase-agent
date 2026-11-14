from copy import deepcopy
import json
import pytest
from app.evaluation.v3_tasks import build
from app.evaluation.v3_task_audit import audit
from app.investigation.v3.world import InvestigationWorld
from app.investigation.v3.output import OutputFailure
from app.investigation.contracts import digest


@pytest.fixture(scope='module')
def candidates(tmp_path_factory):
    root=tmp_path_factory.mktemp('v3-candidates')/'version';manifest=build(root)
    return root,manifest


def choose(candidates,cost=False):
    root,manifest=candidates
    entry=next(e for e in manifest['entries'] if (e['family']=='channel_verification')==cost)
    return json.loads((root/entry['runtime']).read_text(encoding='utf-8')),json.loads((root/entry['reference']).read_text(encoding='utf-8'))


def test_70_new_cases_pass_mechanical_split_clock_and_feasibility_audit(candidates):
    root,_=candidates;r=audit(root,root/'audit.json')
    assert r['counts']=={'development':30,'evaluation':40}
    assert r['families']=={'development':6,'evaluation':8} and r['cost_limited_cases']=={'development':5,'evaluation':10}
    assert not r['formal_frozen'] and not r['independent_person_reviewed']


@pytest.mark.parametrize('where',['root','record','transaction'])
def test_reference_fields_are_not_runtime_inputs(candidates,where):
    snapshot,_=choose(candidates)
    if where=='root':snapshot['expected_authorization']='authorized'
    elif where=='transaction':snapshot['transactions'][0]['expected_authorization']='authorized'
    else:
        snapshot['records'][0]['expected_authorization']='authorized';snapshot['records_sha256']=digest(snapshot['records'])
    with pytest.raises(ValueError):InvestigationWorld(snapshot)


def test_future_material_is_absent_from_catalog_and_cannot_be_guessed(candidates):
    snapshot,reference=choose(candidates);world=InvestigationWorld(snapshot);future=reference['never_visible_source_ids'][0]
    for stage in range(3):
        if stage:world.advance(stage)
        assert future not in json.dumps(world.catalog())
        with pytest.raises(OutputFailure,match='source_not_available'):world.read([future])
    assert world.spent==0 and not world.acquired


def test_time_progress_is_monotonic_and_does_not_rewrite_old_evidence(candidates):
    snapshot,reference=choose(candidates);world=InvestigationWorld(snapshot)
    first=next(r['source_id'] for r in snapshot['records'] if r['source_type']=='customer' and r['available_at']==snapshot['stage_times'][1])
    with pytest.raises(OutputFailure,match='source_not_available'):world.read([first])
    world.advance(1);old=world.read([first]);before=digest(old)
    world.advance(2);assert digest(old)==before and world.acquired[first]['lines']==old['data']['B'][0]['lines']
    with pytest.raises(ValueError):world.advance(1)
    with pytest.raises(ValueError):world.advance(3)


def test_costly_queries_share_budget_dependencies_and_durable_receipts(candidates):
    snapshot,reference=choose(candidates,cost=True);world=InvestigationWorld(snapshot);world.advance(1)
    route=reference['routing_required_source'];world.advance(2)
    paid=[r['source_id'] for r in snapshot['records'] if r['cost_units']==8]
    with pytest.raises(OutputFailure,match='acquisition_dependency'):world.read([paid[0]])
    world.read([route]);assert world.spent==1
    with pytest.raises(OutputFailure,match='source_budget'):world.read(paid)
    world.read([paid[0]]);assert world.spent==9
    world.read([paid[0]]);assert world.spent==9 and not world.receipts[-1]['dispatched']
    state=world.checkpoint();resumed=InvestigationWorld.restore(snapshot,state)
    assert resumed.spent==world.spent and resumed.acquired==world.acquired and resumed.elapsed==world.elapsed
    with pytest.raises(OutputFailure,match='source_budget'):resumed.read([paid[1]])
    forged=deepcopy(state);forged['receipts'][-2]['cost_units']=0
    with pytest.raises(ValueError,match='receipt changed'):InvestigationWorld.restore(snapshot,forged)


def test_deadline_is_a_distinct_source_failure(candidates):
    snapshot,reference=choose(candidates,cost=True);snapshot['stage_windows_minutes'][2]=5
    world=InvestigationWorld(snapshot);world.advance(1);world.read([reference['routing_required_source']]);world.advance(2)
    paid=next(r['source_id'] for r in snapshot['records'] if r['cost_units']==8)
    with pytest.raises(OutputFailure,match='source_deadline'):world.read([paid])
    assert world.spent==1


def test_candidate_files_and_audits_never_overwrite_versions(candidates):
    root,_=candidates
    with pytest.raises(FileExistsError):build(root)
    output=root/'second-audit.json';audit(root,output)
    with pytest.raises(FileExistsError):audit(root,output)
