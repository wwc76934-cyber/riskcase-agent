"""Versioned author simulation with real query outcomes and enforced expiry.

No labels/references, bank queries or provider clients. The old V3 contract is
unchanged. Simulated resource time is distinct from actual analyst labor/SLA.
"""
from copy import deepcopy
from datetime import datetime,timedelta
from app.investigation.contracts import digest
from app.investigation.v3.world import InvestigationWorld,ROOT_FIELDS
from app.investigation.v3.output import OutputFailure
from app.investigation.v3.source_roles import valid_role

CONTRACT='payment-investigation-v31-query-simulation'

class SourceQueryWorld(InvestigationWorld):
    def __init__(self,snapshot):
        if set(snapshot)!=ROOT_FIELDS|{'query_rules'} or snapshot.get('contract')!=CONTRACT:
            raise ValueError('Closed query simulation contract required')
        compatible=deepcopy(snapshot);rules=compatible.pop('query_rules');compatible['contract']='payment-investigation-v3'
        super().__init__(compatible)
        starts=[datetime.fromisoformat(t) for t in snapshot['stage_times']]
        for index,start in enumerate(starts[:-1]):
            if start+timedelta(minutes=snapshot['stage_windows_minutes'][index])>starts[index+1]:
                raise ValueError('Query window overlaps the next arrival stage')
        if not isinstance(rules,dict) or set(rules)!=set(self.records):raise ValueError('Exactly one query rule per source')
        for identity,rule in rules.items():
            if not isinstance(rule,dict) or set(rule)!={'business_role','expires_at','query_outcome'}:raise ValueError('Closed query rules')
            if rule['query_outcome'] not in ('available','unavailable') or not valid_role(self.records[identity]['source_type'],rule['business_role']):
                raise ValueError('Invalid source role or outcome')
            if rule['expires_at'] is not None:
                try:
                    expiry=datetime.fromisoformat(rule['expires_at'])
                    if expiry<datetime.fromisoformat(self.records[identity]['available_at']):raise ValueError()
                    # Reject timezone-incompatible clocks before any execution.
                    _=expiry<=datetime.fromisoformat(snapshot['stage_times'][0])
                except (TypeError,ValueError):raise ValueError('Invalid source expiry') from None
        self.snapshot=deepcopy(snapshot);self.records={r['source_id']:r for r in self.snapshot['records']}
        self.rules=deepcopy(rules);self.queried={}

    def query_clock(self):
        return datetime.fromisoformat(self.snapshot['stage_times'][self.stage])+timedelta(minutes=self.elapsed[self.stage])

    def expiry(self,identity):
        value=self.rules[identity]['expires_at']
        return datetime.fromisoformat(value) if value is not None else None

    def catalog(self):
        result=super().catalog();now=self.query_clock();sources=[]
        for entry in result['sources']:
            identity=entry['source_id'];rule=self.rules[identity]
            if identity in self.queried and identity not in self.acquired:continue
            expiry=self.expiry(identity)
            entry.update(business_role=rule['business_role'],expires_at=rule['expires_at'],
                readable=identity in self.acquired or expiry is None or now+timedelta(minutes=entry['latency_minutes'])<=expiry)
            sources.append(entry)
        result.update(sources=sources,query_clock=now.isoformat(),queried_source_ids=sorted(self.queried),
            source_query_results=deepcopy(self.queried),simulation_time_not_real_sla=True)
        return result

    def read(self,source_ids):
        if not isinstance(source_ids,list) or not source_ids or len(source_ids)>12 or any(not isinstance(k,str) for k in source_ids) or len(set(source_ids))!=len(source_ids):
            raise OutputFailure('tool_arguments')
        if any(k not in self.eligible() for k in source_ids):raise OutputFailure('source_not_available')
        if any(not set(self.records[k]['requires'])<=self.acquired.keys() for k in source_ids):raise OutputFailure('acquisition_dependency')
        # Reserve the complete deterministic batch before applying any effects.
        planned=[];clock=self.query_clock();cost=latency=0
        for identity in source_ids:
            if identity in self.queried:continue
            record=self.records[identity];expiry=self.expiry(identity);dispatch=expiry is None or clock<=expiry
            units=record['cost_units'] if dispatch else 0;minutes=record['latency_minutes'] if dispatch else 0
            clock+=timedelta(minutes=minutes);cost+=units;latency+=minutes
            planned.append((identity,dispatch,units,minutes,clock))
        if self.spent+cost>self.snapshot['access_budget_units']:raise OutputFailure('source_budget')
        if self.elapsed[self.stage]+latency>self.snapshot['stage_windows_minutes'][self.stage]:raise OutputFailure('source_deadline')
        fresh={identity:(dispatch,units,minutes,clock) for identity,dispatch,units,minutes,clock in planned}
        results=[];originals=[]
        for identity in source_ids:
            if identity not in fresh:
                receipt=deepcopy(self.queried[identity]);receipt.update(dispatched=False,cost_units=0,latency_minutes=0)
                results.append(receipt)
                if identity in self.acquired:originals.append(deepcopy(self.acquired[identity]))
                continue
            dispatch,units,minutes,clock=fresh[identity];rule=self.rules[identity];expiry=self.expiry(identity)
            status=('expired' if not dispatch or expiry is not None and clock>expiry else rule['query_outcome'])
            original=None
            if status=='available':
                original=deepcopy(self.records[identity]);original['business_role']=rule['business_role']
                original['lines']=[dict(number=i,text=t) for i,t in enumerate(original.pop('text').splitlines(),1)]
                self.acquired[identity]=original;originals.append(deepcopy(original))
            receipt=dict(source_id=identity,stage=self.stage,status=status,business_role=rule['business_role'],
                dispatched=dispatch,cost_units=units,latency_minutes=minutes,completed_at=clock.isoformat(),
                original_sha256=digest(original) if original is not None else None)
            self.receipts.append(receipt);self.queried[identity]=deepcopy(receipt);results.append(deepcopy(receipt))
        self.spent+=cost;self.elapsed[self.stage]+=latency
        return dict(case_id=self.snapshot['case_id'],cutoff=self.snapshot['cutoff'],data_kind=self.snapshot['data_kind'],source='investigation_originals',
            data=dict(B=originals,query_results=results,investigation_as_of=self.snapshot['stage_times'][self.stage],
                query_clock=self.query_clock().isoformat(),access_cost_units=cost,truth_included=False))

    @classmethod
    def restore(cls,snapshot,state):
        if set(state)!={'snapshot_sha256','stage','receipts'} or state['snapshot_sha256']!=digest(snapshot):raise ValueError('Source checkpoint binding')
        if type(state['stage']) is not int or not 0<=state['stage']<=2:raise ValueError('Checkpoint stage')
        world=cls(snapshot)
        for receipt in state['receipts']:
            if type(receipt.get('stage')) is not int or not 0<=receipt['stage']<=2:raise ValueError('Receipt stage')
            if receipt['source_id'] in world.queried:raise ValueError('Duplicate dispatched query receipt')
            while world.stage<receipt['stage']:world.advance(world.stage+1)
            if world.stage!=receipt['stage']:raise ValueError('Receipt stage regression')
            world.read([receipt['source_id']])
            if digest(world.receipts[-1])!=digest(receipt):raise ValueError('Query outcome, resource or hash changed')
        while world.stage<state['stage']:world.advance(world.stage+1)
        if world.stage!=state['stage']:raise ValueError('Checkpoint stage regression')
        return world

    def verify_evidence(self,evidence,index,checkpoint):
        replay=type(self)(self.snapshot);expected_index={}
        if evidence['E1']['result']!=replay.overview():raise ValueError('Initial source frame changed')
        for key in sorted((k for k in evidence if k!='E1'),key=lambda k:int(k[1:])):
            frame=evidence[key]['result'];queries=frame.get('data',{}).get('query_results',[])
            if not queries:raise ValueError('Missing query outcomes')
            ids=[q['source_id'] for q in queries];stage=queries[0]['stage']
            if type(stage) is not int or not 0<=stage<=2 or any(q['stage']!=stage for q in queries) or any(k in expected_index for k in ids):
                raise ValueError('Query evidence order changed')
            while replay.stage<stage:replay.advance(replay.stage+1)
            if replay.stage!=stage or digest(replay.read(ids))!=digest(frame):raise ValueError('Query result was not acquired')
            expected_index.update({identity:key for identity in ids})
        while replay.stage<checkpoint['stage']:replay.advance(replay.stage+1)
        if digest(replay.checkpoint())!=digest(checkpoint) or expected_index!=index:raise ValueError('Incomplete source outcome evidence')

def world_type(snapshot):
    if snapshot.get('contract')==CONTRACT:return SourceQueryWorld
    if snapshot.get('contract')=='payment-investigation-v3':return InvestigationWorld
    raise ValueError('Unsupported investigation runtime contract')
