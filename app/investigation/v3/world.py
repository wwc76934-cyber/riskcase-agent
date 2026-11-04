"""Independent authored V3 source environment; no controller/reference imports."""
from copy import deepcopy
from datetime import datetime
from app.investigation.contracts import digest
from app.investigation.v3.output import OutputFailure
from app.investigation.fraud_world import SOURCE_TYPES

KIND='fully_author_simulated_NOT_IBM_NOT_bank'
ROOT_FIELDS={'contract','case_id','data_kind','subject_id','alert','cutoff','stage_times','transactions','records','records_sha256','access_budget_units','stage_windows_minutes'}
RECORD_FIELDS={'source_id','source_type','subject_id','transaction_ids','event_time','available_at','version','supersedes_id',
               'origin','coverage','text','cost_units','latency_minutes','channel_ref','requires'}


class InvestigationWorld:
    def __init__(self,snapshot):
        if set(snapshot)!=ROOT_FIELDS or snapshot['contract']!='payment-investigation-v3' or snapshot['data_kind']!=KIND:
            raise ValueError('Closed V3 runtime, author simulation only')
        if digest(snapshot['records'])!=snapshot['records_sha256']:raise ValueError('Record fingerprint changed')
        if not 1<=len(snapshot['records'])<=24 or not 1<=len(snapshot['transactions'])<=12:raise ValueError('Bounded source count')
        times=[datetime.fromisoformat(t) for t in snapshot['stage_times']]
        if len(times)!=3 or times!=sorted(set(times)) or datetime.fromisoformat(snapshot['cutoff'])>times[0]:raise ValueError('T0/T1/T2 order required')
        if type(snapshot['access_budget_units']) is not int or not 1<=snapshot['access_budget_units']<=100:raise ValueError('Bounded simulated budget')
        if len(snapshot['stage_windows_minutes'])!=3 or any(type(v) is not int or v<=0 for v in snapshot['stage_windows_minutes']):raise ValueError('Stage windows required')
        ids={r['transaction_id'] for r in snapshot['transactions']}
        if len(ids)!=len(snapshot['transactions']) or not snapshot['alert']['transaction_id'] in ids:raise ValueError('Alert transaction binding')
        if set(snapshot['alert'])!={'alert_id','transaction_id','model_version','source_kind'} or snapshot['alert']['source_kind']!=KIND:raise ValueError('Explicit fixture alert required')
        for transaction in snapshot['transactions']:
            if set(transaction)!={'transaction_id','user_id','event_time','amount_cents','currency'}:raise ValueError('Closed transaction fields')
            if transaction['user_id']!=snapshot['subject_id'] or datetime.fromisoformat(transaction['event_time'])>datetime.fromisoformat(snapshot['cutoff']):raise ValueError('Transaction scope')
        seen=set()
        for record in snapshot['records']:
            if set(record)!=RECORD_FIELDS or record['origin']!=KIND or record['source_id'] in seen:raise ValueError('Closed original fields and distinct IDs required')
            if record['source_type'] not in SOURCE_TYPES:raise ValueError('Source role is not a controller channel')
            seen.add(record['source_id'])
            if record['subject_id']!=snapshot['subject_id'] or not record['transaction_ids'] or not set(record['transaction_ids'])<=ids:raise ValueError('Original scope')
            if datetime.fromisoformat(record['event_time'])>datetime.fromisoformat(record['available_at']):raise ValueError('Availability precedes event')
            if any(type(record[k]) is not int or record[k]<0 for k in ('cost_units','latency_minutes')):raise ValueError('Nonnegative simulation cost/time')
            if not isinstance(record['text'],str) or not 1<=len(record['text'])<=6000:raise ValueError('Bounded original text')
            coverage=record['coverage']
            if set(coverage)!={'start','end','complete','note'} or type(coverage['complete']) is not bool:
                raise ValueError('Closed coverage fields')
            if not datetime.fromisoformat(coverage['start'])<=datetime.fromisoformat(coverage['end'])<=datetime.fromisoformat(record['available_at']):raise ValueError('Coverage exceeds available record')
            if type(record['version']) is not int or record['version']<1:raise ValueError('Positive original version')
        for record in snapshot['records']:
            if not set(record['requires'])<=seen or record['source_id'] in record['requires']:raise ValueError('Invalid acquisition dependency')
            if record['supersedes_id'] is not None and record['supersedes_id'] not in seen:raise ValueError('Unknown predecessor')
        by_id={r['source_id']:r for r in snapshot['records']}
        def visit(identity,active,done):
            if identity in active:raise ValueError('Acquisition dependency cycle')
            if identity in done:return
            for dependency in by_id[identity]['requires']:visit(dependency,active|{identity},done)
            done.add(identity)
        done=set()
        for record in snapshot['records']:
            visit(record['source_id'],set(),done)
            if record['supersedes_id']:
                previous=by_id[record['supersedes_id']]
                if previous['source_type']!=record['source_type'] or previous['version']>=record['version'] or previous['available_at']>record['available_at']:raise ValueError('Invalid succession')
        self.snapshot=deepcopy(snapshot);self.records={r['source_id']:r for r in self.snapshot['records']}
        self.stage=0;self.spent=0;self.elapsed=[0,0,0];self.acquired={};self.receipts=[]

    def advance(self,stage):
        # Host only: model tools never expose this operation or arbitrary clocks.
        if type(stage) is not int or stage!=self.stage+1 or stage>2:raise ValueError('Host advances exactly one stage')
        self.stage=stage

    def eligible(self):
        return {k:r for k,r in self.records.items() if datetime.fromisoformat(r['available_at'])<=datetime.fromisoformat(self.snapshot['stage_times'][self.stage])}

    def catalog(self):
        return dict(stage=self.stage,as_of=self.snapshot['stage_times'][self.stage],
            remaining_cost_units=self.snapshot['access_budget_units']-self.spent,
            remaining_minutes=self.snapshot['stage_windows_minutes'][self.stage]-self.elapsed[self.stage],
            simulation_cost_not_real_money=True,sources=[{k:deepcopy(r[k]) for k in ('source_id','source_type','available_at','version','cost_units','latency_minutes','channel_ref','requires')} for r in self.eligible().values()])

    def overview(self):
        s=self.snapshot
        return dict(case_id=s['case_id'],cutoff=s['cutoff'],data_kind=KIND,source='inquiry_facts',
            data=dict(alert=deepcopy(s['alert']),rows=deepcopy(s['transactions'])))

    def read(self,source_ids):
        if not isinstance(source_ids,list) or not source_ids or len(source_ids)>12 or len(set(source_ids))!=len(source_ids):raise OutputFailure('tool_arguments')
        if any(k not in self.eligible() for k in source_ids):raise OutputFailure('source_not_available')
        if any(not set(self.records[k]['requires'])<=self.acquired.keys() for k in source_ids):raise OutputFailure('acquisition_dependency')
        fresh=[k for k in source_ids if k not in self.acquired]
        cost=sum(self.records[k]['cost_units'] for k in fresh);latency=sum(self.records[k]['latency_minutes'] for k in fresh)
        if self.spent+cost>self.snapshot['access_budget_units']:raise OutputFailure('source_budget')
        if self.elapsed[self.stage]+latency>self.snapshot['stage_windows_minutes'][self.stage]:raise OutputFailure('source_deadline')
        self.spent+=cost;self.elapsed[self.stage]+=latency;result=[]
        for k in source_ids:
            record=deepcopy(self.records[k]);record['lines']=[dict(number=i,text=t) for i,t in enumerate(record.pop('text').splitlines(),1)]
            self.receipts.append(dict(source_id=k,stage=self.stage,dispatched=k in fresh,cost_units=record['cost_units'] if k in fresh else 0,
                latency_minutes=record['latency_minutes'] if k in fresh else 0,original_sha256=digest(record)))
            self.acquired[k]=record;result.append(record)
        return dict(case_id=self.snapshot['case_id'],cutoff=self.snapshot['cutoff'],data_kind=KIND,source='investigation_originals',
            data=dict(B=result,investigation_as_of=self.snapshot['stage_times'][self.stage],access_cost_units=cost,truth_included=False))

    def checkpoint(self):
        return dict(snapshot_sha256=digest(self.snapshot),stage=self.stage,receipts=deepcopy(self.receipts))

    @classmethod
    def restore(cls,snapshot,state):
        if set(state)!={'snapshot_sha256','stage','receipts'} or state['snapshot_sha256']!=digest(snapshot):raise ValueError('Source checkpoint binding')
        result=cls(snapshot)
        for receipt in state['receipts']:
            while result.stage<receipt['stage']:result.advance(result.stage+1)
            if result.stage!=receipt['stage']:raise ValueError('Receipt time regression')
            result.read([receipt['source_id']])
            if result.receipts[-1]!=receipt:raise ValueError('Source cost/hash receipt changed')
        while result.stage<state['stage']:result.advance(result.stage+1)
        if result.stage!=state['stage']:raise ValueError('Checkpoint stage regression')
        return result
