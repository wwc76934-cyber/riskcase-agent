from copy import deepcopy
from datetime import datetime,timedelta
import json
from pathlib import Path

import pytest
from app.investigation.contracts import digest
from app.investigation.inquiry import Source,query_args,verify,MISSING,CONTRACT
from app.investigation.inquiry_service import InquiryService
from app.investigation.harness import HarnessWorker
from app.investigation.tickets import Tickets,MockDestination
from app.service.store import Conflict


@pytest.fixture
def inquiry_snapshot():
    end=datetime(2018,4,20,12)
    rows=[]
    for i,(hours,merchant,card,amount,channel) in enumerate([
        (1,'recent','c1',100,'Chip'),(2,'recent','c1',200,'Chip'),
        (3,'recent','c2',-50,'Online'),(48,'usual','c1',900,'Online'),
        (49,'usual','c1',700,'Online'),(50,'third','c2',300,'Chip'),
        (800,'old','c2',400,'Chip')]):
        rows.append(dict(transaction_id=f't{i}',source_row=i+1,user_id='u',card_id=card,
                         event_time=(end-timedelta(hours=hours)).isoformat(),amount_cents=amount,
                         merchant_id=merchant,channel=channel,mcc='5411'))
    rows.append(dict(transaction_id='target',source_row=100,user_id='u',card_id='c1',
                     event_time=end.isoformat(),amount_cents=500,merchant_id='target-m',channel='Chip',mcc='5411'))
    return dict(task_contract=CONTRACT,case_id='fixture-inquiry',cutoff=end.isoformat(),
                scope='fraud_inquiry',data_kind='synthetic_development',source_origin='IBM_TabFormer_synthetic',
                history_start=(end-timedelta(hours=1000)).isoformat(),source_facts_sha256='a'*64,
                source_report_sha256='b'*64,rows_sha256=digest(rows),target_id='target',rows=rows,
                alert=dict(transaction_id='target',score=.6,day_rank=1,detector='neural_v2_ensemble',
                           score_kind='uncalibrated_ranking_sigmoid_not_probability',model_artifacts_sha256='c'*64,
                           model_report_sha256='d'*64,selection='unit_fixture_not_detector_evaluation'))


def test_composable_queries_and_time_exclusions(inquiry_snapshot):
    source=Source(inquiry_snapshot)
    query=source.query(query_args(24,group_by=['merchant_id']))
    assert query['metrics']['count']==3 and query['groups'][0]['key']=={'merchant_id':'recent'}
    assert query['groups'][0]['metrics']['sum_positive_cents']==300
    earlier=source.query(query_args(168,exclude=24,filters=[dict(field='merchant_id',op='eq',value='recent')]))
    assert earlier['metrics']['count']==0 and earlier['coverage_complete']
    assert source.query(query_args(720,group_by=['card_id','channel']))['group_count']==4
    assert not source.query(query_args(8760))['coverage_complete']
    assert source.query(query_args(720,group_by=['merchant_id'],limit=1))['truncated']


@pytest.mark.parametrize('change',['label','other_subject','future','same_time','duplicate','wrong_hash','target_score'])
def test_inquiry_scope_is_host_owned(inquiry_snapshot,change):
    s=deepcopy(inquiry_snapshot)
    if change=='label':s['rows'][0]['label']=1
    if change=='other_subject':s['rows'][0]['user_id']='other'
    if change=='future':s['rows'][0]['event_time']='2019-01-01T00:00:00'
    if change=='same_time':s['rows'][0]['event_time']=s['cutoff']
    if change=='duplicate':s['rows'].append(s['rows'][0])
    if change=='target_score':s['alert']['score']=float('nan')
    if change!='wrong_hash':s['rows_sha256']=digest(s['rows'])
    else:s['rows_sha256']='0'*64
    with pytest.raises(ValueError):Source(s)


def test_query_identifier_arithmetic_and_unknown_rows_denied(inquiry_snapshot):
    source=Source(inquiry_snapshot)
    with pytest.raises(ValueError):source.query(query_args(24,exclude=24))
    with pytest.raises(ValueError):source.query(query_args(24,filters=[dict(field='merchant_id',op='gt',value='recent')]))
    with pytest.raises(PermissionError):source.call('inquiry_rows',dict(transaction_ids=['outside']))


class DiscoveryProbe:
    """Simulated transport contract probe; never counted as LLM planning."""
    simulation=True
    def chat(self,messages,tools=None):
        frame=json.loads(messages[1]['content']);obs=frame['observations']
        queries=[o for o in obs if o.get('source')=='inquiry_facts' and 'query' in o.get('data',{})]
        if not queries:
            calls=[('inquiry_query',query_args(24,group_by=['merchant_id']))]
        elif not frame['final_only'] and len(queries)==1:
            # The chosen merchant is read from the last observation, not the question or gold.
            merchant=queries[0]['data']['groups'][0]['key']['merchant_id']
            calls=[('record_plan',dict(hypotheses=['Recent concentration may be transient'],todo=['Compare earlier window'],unknowns=MISSING)),
                   ('inquiry_query',query_args(168,exclude=24,filters=[dict(field='merchant_id',op='eq',value=merchant)]))]
        else:
            last=queries[-1];ref=last['evidence_id'];count=last['data']['metrics']['count']
            answer=dict(status='analysis_for_review',claims=[dict(statement='Selected window transaction count',
                        left=dict(evidence_id=ref,path=['data','metrics','count']),operator='eq',right=dict(literal=count))],
                        hypotheses=[dict(explanation='A concentration alone does not establish fraud',support=[ref],counterevidence=[],unresolved=MISSING)],
                        evidence_ids=[o['evidence_id'] for o in obs if o.get('evidence_id')],missing=MISSING)
            return dict(role='assistant',content=json.dumps(answer))
        return dict(role='assistant',content=None,tool_calls=[dict(id=f'q{i}',type='function',function=dict(name=n,arguments=json.dumps(a))) for i,(n,a) in enumerate(calls)])


@pytest.mark.parametrize('strategy,expected_queries',[('adaptive',2),('plan_once',1)])
def test_real_mcp_inquiry_harness_and_content_review(tmp_path,inquiry_snapshot,strategy,expected_queries):
    service=InquiryService(tmp_path/strategy,strategy=strategy)
    identity,_=service.submit('test',inquiry_snapshot,'Find concentration and check an earlier period')
    row=HarnessWorker(service,client_factory=DiscoveryProbe).step()
    assert row['state']=='awaiting_review',row['reason']
    report=row['result'];state=service.store.load(identity)
    assert report['client_kind']=='simulation' and report['validation']['semantic_validation'] is False
    assert len([t for t in state['trace'] if t['tool']=='inquiry_query'])==expected_queries
    handshakes=json.loads((service.directory/identity/'mcp-handshakes.json').read_text())
    assert len(handshakes)==2 and handshakes['transactions']['server']['name'].startswith('fraud-inquiry')
    evidence=deepcopy(state['evidence']);answer=deepcopy(report['answer'])
    answer['claims'][0]['right']['literal']+=1
    with pytest.raises(ValueError,match='disproved'):verify(answer,evidence)
    tickets=Tickets(service.store,MockDestination(tmp_path/(strategy+'-mock.sqlite')))
    draft=tickets.draft(identity,'Review inquiry and unresolved authorization evidence')
    with pytest.raises(Conflict,match='semantic'):tickets.approve(identity,draft['hash'],reviewer='tester')
    tickets.approve(identity,draft['hash'],reviewer='tester',reviewed_claims=['question_coverage','interpretations','counterevidence','missing'])
