"""Delivery checkpoints for three time stages under one fenced case lease.

Each stage reserves the final two of six journal slots for delivery. Source time
cannot be changed by the model, and completed stages keep separate raw archives.
"""
from copy import deepcopy
import json
from app.service.store import Blocked,Conflict,digest,encoded


class DeliveryPhase:
    def __init__(self,source,stage,*,lane='delivery'):
        if type(stage) is not int or not 0<=stage<=2:raise ValueError('T0/T1/T2 required')
        if lane not in ('planner','delivery'):raise ValueError('Closed model lanes')
        self.source,self.stage=source,stage;self.path,self.clock=source.path,source.clock
        self.parent=deepcopy(source.claim);self.lane=lane;self.limit=2 if lane=='delivery' else 4
        self.identity=self.parent['id']+'-'+lane+'-T'+str(stage)
        self.claim={**self.parent,'id':self.identity}
        with source.transaction() as db:
            self._live(db)
            db.execute('CREATE TABLE IF NOT EXISTS v3_delivery_phase(job_id TEXT NOT NULL,stage INTEGER NOT NULL,body TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(job_id,stage))')
            db.execute('CREATE TABLE IF NOT EXISTS v3_phase_calls(job_id TEXT NOT NULL,stage INTEGER NOT NULL,lane TEXT NOT NULL,ordinal INTEGER NOT NULL,request_hash TEXT NOT NULL,state TEXT NOT NULL,response TEXT,response_hash TEXT,audit TEXT,PRIMARY KEY(job_id,stage,lane,ordinal))')

    def _identity(self,identity,owner=None,generation=None):
        if identity!=self.identity or owner is not None and owner!=self.parent['owner'] or generation is not None and generation!=self.parent['generation']:raise Blocked('delivery_phase_scope')

    def _live(self,db):
        _,world=self.source._load(db)
        if world.stage!=self.stage:raise Blocked('delivery_source_time_changed')

    def load(self,identity):
        self._identity(identity)
        if self.lane!='delivery':raise Blocked('delivery_phase_role')
        with self.source.transaction() as db:
            self._live(db)
            row=db.execute('SELECT body,hash FROM v3_delivery_phase WHERE job_id=? AND stage=?',(self.parent['id'],self.stage)).fetchone()
            if row is None:return None
            state=json.loads(row['body'])
            if digest(state)!=row['hash']:raise Blocked('delivery_phase_hash_changed')
            return state

    def save(self,claim,state):
        self._identity(claim['id'],claim['owner'],claim['generation'])
        if self.lane!='delivery':raise Blocked('delivery_phase_role')
        with self.source.transaction() as db:
            self._live(db)
            db.execute('INSERT INTO v3_delivery_phase VALUES (?,?,?,?) ON CONFLICT(job_id,stage) DO UPDATE SET body=excluded.body,hash=excluded.hash',
                (self.parent['id'],self.stage,encoded(state),digest(state)))
            self.source.event(db,self.parent['id'],'v3_delivery_checkpoint',stage=self.stage,phase=state['phase'],turn=state['turn'])

    def _ordinal(self,ordinal):
        if type(ordinal) is not int or not 1<=ordinal<=self.limit:raise Blocked('delivery_reserved_slot')
        return ordinal

    def _key(self,ordinal):return self.parent['id'],self.stage,self.lane,self._ordinal(ordinal)

    def begin_call(self,identity,owner,generation,ordinal,request_hash):
        self._identity(identity,owner,generation)
        with self.source.transaction() as db:
            self._live(db);key=self._key(ordinal)
            row=db.execute('SELECT * FROM v3_phase_calls WHERE job_id=? AND stage=? AND lane=? AND ordinal=?',key).fetchone()
            if row:
                if row['request_hash']!=request_hash:raise Blocked('replay_request_changed')
                if row['state']!='completed':raise Blocked('unresolved_call')
                response=json.loads(row['response'])
                if digest(response)!=row['response_hash']:raise Blocked('journal_hash_mismatch')
                self.source.event(db,self.parent['id'],'v3_phase_response_replayed',stage=self.stage,lane=self.lane,ordinal=ordinal)
                return response
            if db.execute("SELECT 1 FROM v3_phase_calls WHERE job_id=? AND state!='completed'",(self.parent['id'],)).fetchone() or db.execute("SELECT 1 FROM calls WHERE job_id=? AND state!='completed'",(self.parent['id'],)).fetchone():raise Blocked('unresolved_case_call')
            db.execute("INSERT INTO v3_phase_calls(job_id,stage,lane,ordinal,request_hash,state) VALUES (?,?,?,?,?,'started')",(*key,request_hash))
            self.source.event(db,self.parent['id'],'v3_phase_request_started',stage=self.stage,lane=self.lane,ordinal=ordinal)
            return None

    def complete_call(self,identity,owner,generation,ordinal,response,audit=None):
        self._identity(identity,owner,generation)
        # Received responses remain journaled even if the source clock advanced;
        # save()/load() subsequently refuse to commit them as the current stage.
        with self.source.transaction() as db:
            self.source._guard(db,self.parent['id'],owner,generation,permit_cancel=True)
            changed=db.execute("UPDATE v3_phase_calls SET state='completed',response=?,response_hash=?,audit=? WHERE job_id=? AND stage=? AND lane=? AND ordinal=? AND state='started'",
                (encoded(response),digest(response),encoded(audit),*self._key(ordinal))).rowcount
            if changed!=1:raise Conflict('call_not_started')
            self.source.event(db,self.parent['id'],'v3_phase_response_saved',stage=self.stage,lane=self.lane,ordinal=ordinal)

    def uncertain_call(self,identity,owner,generation,ordinal,audit):
        self._identity(identity,owner,generation)
        with self.source.transaction() as db:
            self.source._guard(db,self.parent['id'],owner,generation,permit_cancel=True)
            db.execute("UPDATE v3_phase_calls SET state='uncertain',audit=? WHERE job_id=? AND stage=? AND lane=? AND ordinal=? AND state='started'",(encoded(audit),*self._key(ordinal)))
            self.source.event(db,self.parent['id'],'v3_phase_call_uncertain',stage=self.stage,lane=self.lane,ordinal=ordinal)

    def checkpoint(self,identity,owner,generation,**kwargs):
        self._identity(identity,owner,generation)
        with self.source.transaction() as db:self._live(db)
        return self.source.checkpoint(self.parent['id'],owner,generation,**kwargs)
