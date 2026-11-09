"""Strong B1 alternative: complete current materials, one generation + one repair.

Extraction and report have distinct validators. A repair sees only the saved
model response and protocol failures, never the evaluation reference.
"""
from copy import deepcopy
from app.investigation.contracts import obj,digest
from app.investigation.recording import RecordedClient
from app.service.v3_paths import native_archive_path
from app.investigation.v3.delivery import parse,request_for
from app.investigation.v3.control import journaled_response,with_lease_heartbeat
from app.investigation.v3.output import checked,OutputFailure
from app.service.runtime import write_json


class CompositeDelivery:
    def __init__(self,phase,ledger,client,contract,*,hook=lambda *args:None):
        self.phase,self.ledger,self.contract,self.hook=phase,ledger,contract,hook
        self.directory=native_archive_path(phase.path.parent/'v3-model-records'/phase.identity)
        self.client=RecordedClient(client,self.directory)
        placeholders=dict(facts={'F'+str(i):None for i in range(1,13)})
        composite_schema=getattr(contract,'composite_schema_for',None)
        report_schema=composite_schema(ledger) if composite_schema else contract.schema_for(placeholders,ledger)
        self.schema=obj(dict(extraction=contract.extraction_schema(ledger),report=report_schema),['extraction','report'])
        self.binding=digest([self.schema,ledger.model_context(),contract.name,contract.rules_for('extract'),contract.rules_for('report')])

    def run(self):
        state=self.phase.load(self.phase.identity) or dict(contract='v3-composite-delivery-v1',binding=self.binding,phase='generating',turn=0,attempts=0,
            raw_responses=[],replayed_responses=0,protocol_errors=[])
        if state.get('contract')!='v3-composite-delivery-v1' or state['binding']!=self.binding:raise OutputFailure('delivery_snapshot_changed')
        if state['phase']=='failed':raise OutputFailure('terminal_delivery_failure')
        while state['phase']=='generating' and state['turn']<2:
            if 'pending_request' not in state:
                rules=self.contract.rules_for('extract')+self.contract.rules_for('report')+[
                    'Return extraction and report in this single JSON object. Host observation IDs are F1 for the first extraction fact, F2 for the second, etc. Report references must point to actual entries in that array.',
                    'Use only acquired originals. A protocol repair does not authorize new facts, source acquisition, or a different target.']
                context=dict(acquired=self.ledger.model_context(),protocol_errors=state['protocol_errors'],
                    previous_model_response=state['raw_responses'][-1] if state['raw_responses'] else None)
                extra_context=getattr(self.contract,'composite_context_for',None)
                if extra_context:context.update(extra_context(self.ledger))
                state['pending_request']=request_for('extract_and_report',self.schema,context,rules=rules);self.phase.save(self.phase.claim,state)
            if 'pending_response' not in state:
                ordinal=state['turn']+1
                def invoke():
                    try:return self.client.chat(**state['pending_request'])
                    finally:write_json(self.directory/f'usage-{ordinal:02}.json',self.client.snapshot())
                response,replayed=journaled_response(self.phase,self.phase.claim,ordinal,state['pending_request'],
                    lambda:with_lease_heartbeat(self.phase,self.phase.claim,invoke),audit_snapshot=self.client.snapshot)
                self.hook('response_saved',ordinal)
                state['turn']+=1;state['replayed_responses']+=int(replayed)
                state['pending_response']=deepcopy(response);state['raw_responses'].append(deepcopy(response));self.phase.save(self.phase.claim,state)
            try:
                payload=parse(state['pending_response']);checked(payload,self.schema)
                extraction=self.contract.extract(payload['extraction'],self.ledger)
                state['report']=self.contract.assemble(payload['report'],extraction,self.ledger)
                state['report']['delivery_mode']='one_generation_with_at_most_one_protocol_repair'
                state['phase']='done'
            except OutputFailure as exc:
                state['protocol_errors'].append(dict(code=exc.code,detail=exc.detail))
                state['attempts']+=1
                if state['turn']>=2:state['phase']='failed'
            state.pop('pending_response');state.pop('pending_request');self.phase.save(self.phase.claim,state)
        if state['phase']!='done':raise OutputFailure('terminal_delivery_failure',issues=state['protocol_errors'])
        return deepcopy(state['report'])
