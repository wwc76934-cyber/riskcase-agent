"""Fenced, durable local source access shared by workflows and MCP.

Acquisition cost is simulated. Transactional idempotence here does not claim
exactly-once execution of a future external bank query or model request.
"""
from copy import deepcopy
import json
from app.service.store import JobStore,Blocked,digest as state_digest,encoded
from app.investigation.contracts import digest as content_digest
from app.investigation.v3.world import InvestigationWorld
from app.investigation.v3.output import OutputFailure


class SourceAccess(JobStore):
    def __init__(self,path,claim,snapshot,*,max_tools=20,**kwargs):
        if type(max_tools) is not int or not 1<=max_tools<=40:raise ValueError('Bounded source tool allowance')
        initial=InvestigationWorld(snapshot)
        self.claim=deepcopy(claim);self.snapshot=deepcopy(snapshot);self.max_tools=max_tools
        super().__init__(path,**kwargs)
        with self.transaction() as db:
            row=self._bound_guard(db)
            if json.loads(row['spec'])['case_id']!=snapshot['case_id']:raise Blocked('source_case_scope')
            db.execute('CREATE TABLE IF NOT EXISTS v3_source_state(job_id TEXT PRIMARY KEY,body TEXT NOT NULL,hash TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS v3_source_operations(job_id TEXT NOT NULL,operation_key TEXT NOT NULL,stage INTEGER NOT NULL,request_hash TEXT NOT NULL,body TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(job_id,operation_key))')
            current=db.execute('SELECT body,hash FROM v3_source_state WHERE job_id=?',(claim['id'],)).fetchone()
            if current:
                state=self._decode(current)
                if state['world']['snapshot_sha256']!=content_digest(snapshot) or state['max_tools']!=max_tools:raise Blocked('source_snapshot_or_policy_changed')
                self._verified_world(state)
            else:
                frame=initial.overview()
                state=dict(contract='v3-source-access-v1',world=initial.checkpoint(),max_tools=max_tools,attempts=0,
                    evidence={'E1':dict(result=frame,sha256=content_digest(frame))},source_evidence={})
                self._save(db,state)

    @staticmethod
    def _decode(row):
        state=json.loads(row['body'])
        if state_digest(state)!=row['hash']:raise Blocked('source_state_hash_changed')
        if set(state)!={'contract','world','max_tools','attempts','evidence','source_evidence'} or state['contract']!='v3-source-access-v1':raise Blocked('source_state_contract')
        for entry in state['evidence'].values():
            if content_digest(entry['result'])!=entry['sha256']:raise Blocked('source_evidence_hash_changed')
        return state

    def _load(self,db):
        c=self.claim;self._bound_guard(db)
        row=db.execute('SELECT body,hash FROM v3_source_state WHERE job_id=?',(c['id'],)).fetchone()
        if not row:raise Blocked('source_state_missing')
        state=self._decode(row)
        if state['world']['snapshot_sha256']!=content_digest(self.snapshot) or state['max_tools']!=self.max_tools:raise Blocked('source_snapshot_or_policy_changed')
        return state,self._verified_world(state)

    def _verified_world(self,state):
        if type(state['attempts']) is not int or not 0<=state['attempts']<=self.max_tools:raise Blocked('source_attempt_state')
        try:world=InvestigationWorld.restore(self.snapshot,state['world'])
        except (ValueError,KeyError,TypeError,OutputFailure):raise Blocked('source_receipt_invalid') from None
        evidence=state['evidence']
        if set(evidence)!={'E'+str(i) for i in range(1,len(evidence)+1)} or evidence['E1']['result']!=world.overview():raise Blocked('source_evidence_scope')
        if set(state['source_evidence'])!=set(world.acquired):raise Blocked('source_acquisition_index')
        stages={r['source_id']:r['stage'] for r in world.receipts if r['dispatched']};seen=set()
        for key in sorted(evidence,key=lambda k:int(k[1:])):
            if key=='E1':continue
            frame=evidence[key]['result'];records=frame.get('data',{}).get('B',[])
            ids=[r.get('source_id') for r in records]
            if not ids or len(set(ids))!=len(ids) or seen&set(ids) or any(k not in world.acquired or state['source_evidence'].get(k)!=key for k in ids):raise Blocked('source_evidence_index')
            if len({stages[k] for k in ids})!=1:raise Blocked('source_evidence_time')
            expected=dict(case_id=self.snapshot['case_id'],cutoff=self.snapshot['cutoff'],data_kind=self.snapshot['data_kind'],source='investigation_originals',
                data=dict(B=[world.acquired[k] for k in ids],investigation_as_of=self.snapshot['stage_times'][stages[ids[0]]],
                          access_cost_units=sum(world.records[k]['cost_units'] for k in ids),truth_included=False))
            if frame!=expected:raise Blocked('source_evidence_not_acquired')
            seen.update(ids)
        if seen!=set(world.acquired):raise Blocked('source_evidence_incomplete')
        return world

    def _bound_guard(self,db):
        c=self.claim;row=self._guard(db,c['id'],c['owner'],c['generation'])
        if row['deadline'] is not None and self.clock()>=row['deadline']:raise Blocked('task_deadline')
        return row

    def _save(self,db,state):
        db.execute('INSERT INTO v3_source_state VALUES (?,?,?) ON CONFLICT(job_id) DO UPDATE SET body=excluded.body,hash=excluded.hash',
            (self.claim['id'],encoded(state),state_digest(state)))

    def advance(self,stage):
        """Trusted host only; never registered as an MCP/model capability."""
        with self.transaction() as db:
            state,world=self._load(db);world.advance(stage);state['world']=world.checkpoint();self._save(db,state)
            self.event(db,self.claim['id'],'v3_source_stage_advanced',stage=stage)

    def evidence(self):
        with self.transaction() as db:
            state,_=self._load(db);return {k:deepcopy(state['evidence'][k]) for k in sorted(state['evidence'],key=lambda k:int(k[1:]))}

    def position(self):
        with self.transaction() as db:
            state,world=self._load(db)
            return dict(stage=world.stage,as_of=self.snapshot['stage_times'][world.stage],tool_attempts=state['attempts'],
                        acquired_source_ids=sorted(world.acquired),catalog=world.catalog())

    def call(self,name,arguments,*,operation_key=None):
        if operation_key is not None and (not isinstance(operation_key,str) or not 1<=len(operation_key)<=128):raise Blocked('invalid_host_operation_key')
        failure=None;frame=None;evidence_id=None;cached=[]
        with self.transaction() as db:
            state,world=self._load(db)
            request_hash=content_digest([name,arguments])
            if operation_key is not None:
                previous=db.execute('SELECT * FROM v3_source_operations WHERE job_id=? AND operation_key=?',(self.claim['id'],operation_key)).fetchone()
                if previous:
                    if previous['stage']!=world.stage:raise Blocked('source_operation_time_changed')
                    if previous['request_hash']!=request_hash:raise Blocked('source_operation_request_changed')
                    response=json.loads(previous['body'])
                    if state_digest(response)!=previous['hash']:raise Blocked('source_operation_hash_changed')
                    self.event(db,self.claim['id'],'v3_source_operation_replayed',stage=world.stage,operation_key=operation_key)
                    return dict(response,operation_replayed=True)
            if state['attempts']>=self.max_tools:raise OutputFailure('source_tool_budget')
            state['attempts']+=1
            try:
                if name=='v3_context':
                    if arguments!={}:raise OutputFailure('tool_arguments')
                    frame=state['evidence']['E1']['result'];evidence_id='E1'
                elif name=='v3_read':
                    if not isinstance(arguments,dict) or set(arguments)!={'source_ids'}:raise OutputFailure('tool_arguments')
                    ids=arguments['source_ids']
                    if not isinstance(ids,list) or not ids or len(ids)>12 or any(not isinstance(k,str) for k in ids) or len(set(ids))!=len(ids):raise OutputFailure('tool_arguments')
                    cached=[k for k in ids if k in state['source_evidence']];fresh=[k for k in ids if k not in state['source_evidence']]
                    if fresh:
                        frame=world.read(fresh);evidence_id='E'+str(len(state['evidence'])+1)
                        state['evidence'][evidence_id]=dict(result=frame,sha256=content_digest(frame))
                        state['source_evidence'].update({k:evidence_id for k in fresh})
                else:raise OutputFailure('disabled_tool')
            except OutputFailure as exc:failure=exc
            state['world']=world.checkpoint();self._save(db,state)
            self.event(db,self.claim['id'],'v3_source_tool',tool=name,ok=failure is None,
                error=failure.code if failure else None,stage=world.stage,new_evidence_id=evidence_id,
                argument_sha256=content_digest(arguments),operation_key=operation_key,cached_source_ids=cached,cost_units=frame['data'].get('access_cost_units',0) if frame else 0)
            response=dict(ok=failure is None,frame=frame,evidence_id=evidence_id,cached_source_ids=cached,
                          catalog=world.catalog(),error=dict(code=failure.code) if failure else None,operation_replayed=False)
            if operation_key is not None:
                db.execute('INSERT INTO v3_source_operations VALUES (?,?,?,?,?,?)',(self.claim['id'],operation_key,world.stage,request_hash,encoded(response),state_digest(response)))
        return response
