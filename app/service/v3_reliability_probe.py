"""Offline V3 process-death probe: authored fixture, no key lookup or provider."""
import argparse
import json
import os
from pathlib import Path
from app.investigation.contracts import digest
from app.investigation.harness_store import HarnessStore
from app.investigation.v3.delivery import StagedDelivery
from app.investigation.v3.output import AcquiredEvidence
from app.service.runtime import write_json


def fixture():
    cutoff='2026-01-01T10:00:00';case='v3-installation-probe'
    ledger=AcquiredEvidence(case_id=case,cutoff=cutoff,as_of='2026-01-01T12:00:00',subject_id='fictional-user',transaction_ids=['fictional-payment'])
    record=dict(source_id='fictional-authentication',source_type='authentication',subject_id='fictional-user',
        transaction_ids=['fictional-payment'],event_time=cutoff,available_at='2026-01-01T10:01:00',
        origin='authored_installation_fixture_NOT_IBM_NOT_bank',version=1,
        lines=[dict(number=1,text='A fictional challenge succeeded; the customer intent was not established.')])
    body=dict(case_id=case,cutoff=cutoff,source='investigation_originals',data_kind='authored_installation_fixture',data=record)
    ledger.ingest({'E1':dict(result=body,sha256=digest(body))})
    return ledger


class OfflineProbeClient:
    simulation=True
    def __init__(self,ledger):self.calls=0;self.ledger=ledger
    def chat(self,messages,tools=None):
        self.calls+=1;stage=json.loads(messages[0]['content'])['stage']
        if stage=='extract':
            answer=dict(facts=[dict(id='F1',predicate='authentication',value='success',
                transaction_ids=['fictional-payment'],refs=list(self.ledger.lines))])
        else:
            answer=dict(fact_ids=['F1'],hypotheses=[dict(description='Authorization remains unverified',status='open',support=['F1'],counterevidence=[],unknowns=['Customer statement'])],
                tensions=[],timeline_refs=list(self.ledger.events),recommendations=[dict(action='analyst_review',fact_ids=['F1'],reason='Fixture requires human review')],
                missing=['confirmed_case_outcome','customer_statement'],stop_reason='unavailable_evidence',summary='Authored installation probe; not model reasoning evidence.')
        return dict(role='assistant',content=json.dumps(answer))
    def snapshot(self):return dict(simulated=True,requests=self.calls,estimated_cny='0',external_model_requests=0)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--exit-after-response',action='store_true');args=parser.parse_args()
    if (args.directory/'recovery-receipt.json').exists():raise FileExistsError('Preserve the completed probe')
    store=HarnessStore(args.directory/'jobs.sqlite');ledger=fixture()
    job,_=store.submit('v3-offline-process-probe',dict(mode='offline',case_id=ledger.case_id,question=dict(text='Authored offline probe')))
    claim=store.claim('offline-probe-'+str(os.getpid()),lease_seconds=30,deadline_seconds=180,mode='offline')
    if claim is None:raise ValueError('No claimable job; wait for lease expiry')
    def hook(kind,ordinal):
        if args.exit_after_response and kind=='response_saved' and ordinal==1:os._exit(86)
    client=OfflineProbeClient(ledger);report=StagedDelivery(store,claim,ledger,client,max_requests=2,hook=hook,lease_seconds=30).run()
    state=store.load(job);store.finish(job,claim['owner'],claim['generation'],'awaiting_review',result=report)
    receipt=dict(generation=claim['generation'],replayed_responses=state['replayed_responses'],new_client_requests=client.calls,
        external_model_requests=0,simulated=True,authored_installation_fixture=True,model_planning_evidence=False,
        state=store.get(job)['state'],saved_raw_responses=len(state['raw_responses']))
    write_json(args.directory/'recovery-receipt.json',receipt);print(json.dumps(receipt))


if __name__=='__main__':main()
