"""Procedural-method extension of FraudMemory's existing review/release ledger.

Cross-subject methods contain no source case facts. Their application here is a
retrospective research evaluation, not information available to a bank in 2018.
"""
from copy import deepcopy
import json
from uuid import uuid4
from app.investigation.fraud_memory import FraudMemory,RETENTION_SECONDS
from app.investigation.fraud_world import World,CONTRACT,verify,LEGACY_ANSWER
from app.investigation.contracts import digest as evidence_digest
from app.service.store import Conflict,digest,encoded

LESSONS={
 'source_selection':'List available source metadata together before spending the content-read budget. Select originals that resolve open hypotheses, reserve room for a post-evidence revision, and distinguish unread material from material that is unavailable. A catalog is not content evidence.',
 'attribution':'Separate the actual customer statement from quoted merchant/staff statements. Resolve the final corrected statement about THIS transaction before assigning authorization.',
 'authorization':'Authentication success and customer denial can both be true. Treat the former as a process record; seek transaction-specific authorization evidence and preserve uncertainty.',
 'coverage':'Check the original export interval before interpreting absence. A log starting after the target cannot establish that authentication never occurred.',
 'transaction_scope':'Authorization of a prior purchase or a delegated card is not blanket authorization of this transaction. Check which transaction each sentence refers to.',
 'policy_version':'Use the procedure effective and available at the investigation time. Keep obsolete versions as historical evidence, not current instructions.',
 'stop_rule':'When enough supported material is organized for review, distinguish unresolved external outcomes from a reason to repeat already-completed queries. Recommend the specific next missing evidence.',
}


def method_scope(snapshot):
    return digest(['world_procedural_methods_v1',snapshot['source_origin'],snapshot['transaction_snapshot']['source_facts_sha256']])


def admission_reason(expires,policy_versions,versions,now):
    if expires<=now:return 'expired'
    if not set(policy_versions)&set(versions):return 'policy_version_mismatch'
    return None


class WorldMemory(FraudMemory):
    def __init__(self,store):
        super().__init__(store)
        with store.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS world_feedback_reviews(job TEXT PRIMARY KEY,body TEXT NOT NULL,hash TEXT NOT NULL,reviewed_hash TEXT,reviewer TEXT)')

    def _source(self,db,job,*,approved=False):
        row=self.store._row(db,job)
        if row['state']!='awaiting_review' or not row['result']:raise Conflict('completed_world_report_required')
        spec,report=json.loads(row['spec']),json.loads(row['result'])
        if digest(spec)!=row['spec_hash'] or digest(report)!=row['result_hash'] or spec['snapshot'].get('task_contract')!=CONTRACT:
            raise Conflict('world_source_changed')
        checkpoint=db.execute('SELECT body,hash FROM investigation_state WHERE job_id=?',(job,)).fetchone()
        if checkpoint is None:raise Conflict('world_checkpoint_required')
        state=json.loads(checkpoint['body'])
        if digest(state)!=checkpoint['hash']:raise Conflict('world_checkpoint_changed')
        source=World(spec['snapshot'])
        for e in state['evidence'].values():
            if source.call(e['tool'],e['arguments'])!=e['result'] or evidence_digest(e['result'])!=e['sha256']:
                raise Conflict('world_source_recomputation_failed')
        memory_ids={r['memory_id'] for o in state['observations'] if o.get('kind')=='method_memory' for r in o['records']}
        if verify(report['answer'],state['evidence'],memory_ids=memory_ids,schema=spec.get('world_answer_schema',LEGACY_ANSWER))!=report['validation']:raise Conflict('world_validation_changed')
        review=db.execute('SELECT * FROM world_feedback_reviews WHERE job=?',(job,)).fetchone()
        if not review:raise Conflict('independent_fact_feedback_required')
        body=json.loads(review['body'])
        if digest(body)!=review['hash'] or body['report_hash']!=row['result_hash']:raise Conflict('feedback_changed')
        if approved and review['reviewed_hash']!=review['hash']:raise Conflict('explicit_feedback_review_required')
        return spec['snapshot'],report,row['result_hash'],review['reviewer']

    def _payload(self,db,job,focus):
        if focus not in LESSONS:raise ValueError('Bounded method topic required')
        snapshot,report,report_hash,_=self._source(db,job)
        review=db.execute('SELECT body,hash FROM world_feedback_reviews WHERE job=?',(job,)).fetchone()
        feedback=json.loads(review['body'])
        if focus not in feedback['error_topics']:raise Conflict('method_not_supported_by_feedback')
        world=World(snapshot)
        versions=sorted({r['version'] for r in world.eligible.values() if r['source_type']=='procedure' and world.original(r['source_id'])['effective_at_investigation']})
        return dict(kind='reviewed_procedural_method',source_job=job,source_report_hash=report_hash,
                    feedback_hash=review['hash'],feedback_origin=feedback['review_type'],
                    subject_scope=method_scope(snapshot),target_transaction_id='method-slot:'+focus,
                    source_target_transaction_id=snapshot['transaction_snapshot']['target_id'],
                    event_cutoff=snapshot['cutoff'],case_id=snapshot['case_id'],focus=focus,
                    lesson=LESSONS[focus],policy_versions=versions,source_origin=snapshot['source_origin'],
                    source_client_kind=feedback['source_client_kind'],
                    authority='advisory_method_NOT_current_case_facts',evaluation_time_basis='retrospective_research_methods')

    def candidate(self,job,expected_report_hash,*,focus):
        with self.store.transaction() as db:
            body=self._payload(db,job,focus)
            if body['source_report_hash']!=expected_report_hash:raise Conflict('source_report_changed')
            content_hash=digest(body)
            prior=db.execute('SELECT id FROM fraud_memory_candidates WHERE hash=?',(content_hash,)).fetchone()
            if prior:return self._candidate(db,prior['id'])
            identity='wm-'+uuid4().hex
            db.execute('INSERT INTO fraud_memory_candidates VALUES (?,?,?,?,?,NULL)',
                       (identity,encoded(body),content_hash,self.store.clock(),self.store.clock()+RETENTION_SECONDS))
            self._event(db,operation='world_method_candidate',candidate=identity,hash=content_hash,source_job=job)
            return self._candidate(db,identity)

    def _validate_candidate(self,db,item,*,approved=False):
        body=item['body']
        if body!=self._payload(db,body['source_job'],body['focus']):raise Conflict('method_source_changed')
        return self._source(db,body['source_job'],approved=approved)[3]

    def freeze(self,snapshot):
        world=World(snapshot);scope=method_scope(snapshot);now=self.store.clock()
        versions={r['version'] for r in world.eligible.values() if r['source_type']=='procedure' and world.original(r['source_id'])['effective_at_investigation']}
        with self.store.transaction() as db:
            head=self._head(db,listing=False);eligible=[];rejected=[]
            active=db.execute('SELECT * FROM fraud_memory_active WHERE subject=? AND recorded_at<=? ORDER BY recorded_at DESC,candidate LIMIT 100',(scope,now)).fetchall()
            for row in active:
                item=self._candidate(db,row['candidate']);body=item['body'];reason=None
                if item['hash']!=row['hash'] or body['subject_scope']!=scope:raise Conflict('method_release_item_changed')
                reason=admission_reason(row['expires'],body['policy_versions'],versions,now)
                if reason:pass
                else:
                    try:self._validate_candidate(db,item,approved=True)
                    except Conflict:reason='source_or_feedback_review_invalidated'
                if reason:rejected.append(dict(candidate=item['id'],reason=reason));continue
                eligible.append(dict(memory_id='M'+item['hash'][:20],lesson=body['lesson'],topic=body['focus'],
                                     policy_versions=body['policy_versions'],origin='reviewed_reference_feedback_method',
                                     feedback_origin=body['feedback_origin']))
                if len(eligible)==3:break
            return dict(release=head['release'],head=head['head'],records=eligible,rejected=rejected,
                        method_review_as_of_utc_epoch=now,case_investigation_as_of=snapshot['investigation_as_of'],
                        scope=scope,application_mode='retrospective_research_evaluation_NOT_2018_deployment',
                        authority='advisory_only_NO_case_facts_or_new_permissions')


def recall(frozen):
    return dict(ok=True,kind='method_memory',snapshot_hash=digest(frozen),
                authority='advisory_only_NOT_E_evidence',records=[{k:r[k] for k in ('memory_id','lesson','topic','policy_versions')} for r in frozen['records']],
                rejected=deepcopy(frozen.get('rejected',[])))


def validate_override(value):
    if not isinstance(value,dict) or value.get('controlled_stress_not_release') is not True:raise ValueError('Explicit research-only memory snapshot required')
    if len(value.get('records',[]))>3 or len(json.dumps(value,ensure_ascii=False).encode())>20000:raise ValueError('Bounded method memory required')
    for record in value['records']:
        if set(record)!={'memory_id','lesson','topic','policy_versions'} or not isinstance(record['lesson'],str) or len(record['lesson'].encode())>16000:
            raise ValueError('Only bounded advisory fields are allowed')
    return deepcopy(value)


def controlled_snapshot(records,snapshot,*,now):
    """Research-only stress admission, using the SAME TTL/policy predicate."""
    world=World(snapshot)
    versions={r['version'] for r in world.eligible.values() if r['source_type']=='procedure' and world.original(r['source_id'])['effective_at_investigation']}
    accepted=[];rejected=[]
    for record in records:
        reason=admission_reason(record['expires'],record['policy_versions'],versions,now)
        if reason:rejected.append(dict(candidate=record['memory_id'],reason=reason))
        else:accepted.append({k:deepcopy(record[k]) for k in ('memory_id','lesson','topic','policy_versions')})
    return validate_override(dict(controlled_stress_not_release=True,records=accepted,rejected=rejected))
