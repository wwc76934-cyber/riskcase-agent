import numpy as np
import pandas as pd
import pytest
from copy import deepcopy

from app.cli.release_demo import rank_alerts
from app.cli.fraud_bridge import queue_audit
from app.risk.review_groups import group_alerts,group_id
from tests.test_release_demo import inputs,ToyEnsemble
from tests.test_fraud_bridge import fraud_case,manifest


def test_api_separates_loaded_model_versions_and_does_not_claim_full_queue(tmp_path,fraud_case):
    import json
    from pathlib import Path
    from fastapi.testclient import TestClient
    from app.service.api import create_app
    from app.service.runtime import ResearchService,Settings
    path,_=fraud_case
    content=json.loads(path.read_text());content['alert']['model_report_sha256']='c'*64
    other=path.parent/'other-model.json';other.write_text(json.dumps(content))
    parent=ResearchService(Settings(Path(__file__).resolve().parents[1],tmp_path/'parent',{'old':path,'new':other},'t'*40))
    with TestClient(create_app(parent),headers={'Authorization':'Bearer '+'t'*40}) as client:
        data=client.get('/fraud-alerts').json()
    assert data['group_scope']=='loaded_catalog_only_NOT_all_selected_alerts'
    assert len(data['groups'])==2 and len({r['group_id'] for r in data['alerts']})==2
    assert {g['model_version'] for g in data['groups']}=={'bbbbbbbb','cccccccc'}


def test_preparation_binds_members_and_rejects_provenance_change(tmp_path,inputs):
    import json
    from pathlib import Path
    from app.cli.release_demo import prepare
    from app.cli.fraud_queue import inspect_queue,prepare_group
    from app.investigation.fraud import from_case
    run,features,cohort=inputs;release=tmp_path/'release'
    prepare(Path(__file__).resolve().parents[1],release,neural_run=run,features_manifest=features,cohort_report=cohort,
            users=1,scorer_factory=ToyEnsemble)
    plan=inspect_queue(release);assert plan['alerts_preserved']==4
    selected=plan['groups'][0];output=tmp_path/'group'
    receipt=prepare_group(release,selected['group_id'],cohort,output)
    catalog=json.loads((output/'catalog.json').read_text())
    assert len(catalog)==len(selected['members'])==len(receipt['prepared_members'])
    for identity,path in catalog.items():
        snapshot=from_case(path,identity)
        assert snapshot['cutoff']==selected['members'][0]['event_time']
        assert snapshot['manifest']['user_id']==selected['user_id']
    with pytest.raises(FileExistsError):prepare_group(release,selected['group_id'],cohort,output)
    with pytest.raises(ValueError,match='absent'):prepare_group(release,'unknown',cohort,tmp_path/'absent')
    assert not (tmp_path/'absent').exists()
    cohort.write_text(cohort.read_text()+'\n')
    with pytest.raises(ValueError,match='provenance'):prepare_group(release,selected['group_id'],cohort,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


def test_queue_auditor_accepts_detector_equal_score_tie_order():
    metadata = pd.DataFrame({'transaction_id': ['t'+str(i) for i in range(24)],
                             'user_id': ['u']*24, 'event_time': ['2018-06-01T12:00:00']*24,
                             'split': ['validation']*24})
    rows = rank_alerts(metadata, np.full(24, .5))
    assert queue_audit(rows)['alert_transactions'] == 10


def test_grouping_preserves_every_row_and_individual_cutoff():
    rows=[dict(transaction_id='t'+str(i),user_id='u',event_time=f'2018-06-01T0{i}:00:00',score=.9-i*.1,day_rank=i+1,tie_hash=str(i)) for i in range(3)]
    before=deepcopy(rows)
    result=group_alerts(rows,'a'*64,'b'*64)
    assert len(result)==1 and result[0]['members']==rows
    assert result[0]['queue_available_at']=='2018-06-02T00:00:00'
    assert group_alerts(list(reversed(rows)),'a'*64,'b'*64)==result
    result[0]['members'][0]['score']=0
    assert rows==before


@pytest.mark.parametrize('field,value',[(0,'c'*64),(1,'c'*64),(2,'other'),(3,'2018-06-02')])
def test_group_identity_separates_sources_models_users_and_days(field,value):
    args=['a'*64,'b'*64,'u','2018-06-01'];first=group_id(*args);args[field]=value
    assert group_id(*args)!=first


@pytest.mark.parametrize('args',[
    ['not-sha','b'*64,'u','2018-06-01'],['a'*64,'b'*64,'','2018-06-01'],
    ['a'*64,'b'*64,'u','2018-6-1']])
def test_group_rejects_ambiguous_identity(args):
    with pytest.raises(ValueError):group_id(*args)


def test_group_separates_cross_day_and_cross_user_rows():
    rows=[dict(transaction_id=str(i),user_id=user,event_time=stamp,day_rank=i+1)
          for i,(user,stamp) in enumerate([('u','2018-06-01T23:59:00'),('u','2018-06-02T00:01:00'),('v','2018-06-01T23:59:00')])]
    groups=group_alerts(rows,'a'*64,'b'*64)
    assert len(groups)==3
    assert sorted(r['transaction_id'] for g in groups for r in g['members'])==['0','1','2']
