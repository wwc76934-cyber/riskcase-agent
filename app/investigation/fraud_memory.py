"""Audited review continuity, not learned fraud labels or autonomous rule changes.

The immutable ledger keeps event time separate from actual review availability.
Candidates are invisible until source checks and content-bound operator approval.
DELETE withdraws retrieval eligibility; it does not promise physical erasure.
"""
from copy import deepcopy
import json
from uuid import uuid4

from app.investigation.fraud import BoundSource, CONTRACT, MISSING, LEGACY_POLICY, verify
from app.service.store import Conflict, digest, encoded

FOCUS = {
    'authorization': 'Request independent cardholder authorization evidence; prior behavioral leads do not establish authorization.',
    'coverage': 'Inspect source coverage before interpreting absence of prior behavior.',
    'merchant_context': 'Check current merchant history and external context; a remembered merchant is not a clearance decision.',
    'none': 'Recheck current evidence; preserve unresolved external evidence requirements.',
}
RETENTION_SECONDS = 30 * 86400
EMPTY = dict(release=0, head=digest([]), records=[])


def scope(snapshot):
    BoundSource(snapshot)
    # Numeric user IDs can be reused in another source revision/cohort. Origin
    # labels alone do not establish entity continuity across data artifacts.
    return digest([snapshot['source_origin'],snapshot['manifest']['source_facts_sha256'],
                   snapshot['scope'],snapshot['manifest']['user_id']])


def payload(snapshot, report, report_hash, job, focus):
    if focus not in FOCUS: raise ValueError('Bounded review focus required')
    return dict(kind='episodic_review_with_advisory_reflection',source_job=job,source_report_hash=report_hash,
        subject_scope=scope(snapshot),target_transaction_id=snapshot['manifest']['target_transaction_id'],
        event_cutoff=snapshot['cutoff'],time_basis=snapshot['manifest']['time_basis'],
        source_origin=snapshot['source_origin'],case_id=snapshot['case_id'],
        source_facts_sha256=snapshot['manifest']['source_facts_sha256'],
        findings=[dict(check=f['check'],assessment=f['assessment']) for f in report['answer']['findings']],
        unresolved=deepcopy(MISSING),focus=focus,advisory=FOCUS[focus],
        authority='reviewed_behavioral_leads_NOT_confirmed_outcome',feedback_origin='operator_selected_focus')


class FraudMemory:
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS fraud_memory_candidates(
                  id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL,
                  created REAL NOT NULL, expires REAL NOT NULL, assessed_hash TEXT);
                CREATE TABLE IF NOT EXISTS fraud_memory_releases(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, hash TEXT NOT NULL,
                  created REAL NOT NULL, operation TEXT NOT NULL, reviewer TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS fraud_memory_events(
                  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS fraud_memory_active(
                  key TEXT PRIMARY KEY,candidate TEXT UNIQUE NOT NULL,hash TEXT NOT NULL,
                  subject TEXT NOT NULL,event_cutoff TEXT NOT NULL,target TEXT NOT NULL,
                  recorded_at REAL NOT NULL,expires REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS fraud_memory_scope_time ON fraud_memory_active(subject,event_cutoff,expires);
            ''')

    def _event(self, db, **body):
        db.execute('INSERT INTO fraud_memory_events(created,body) VALUES (?,?)', (self.store.clock(), encoded(body)))

    def _head(self, db, *, listing=True):
        row = db.execute('SELECT * FROM fraud_memory_releases ORDER BY id DESC LIMIT 1').fetchone()
        if row is None: return deepcopy(EMPTY)
        body = json.loads(row['body'])
        if digest(body) != row['hash']: raise Conflict('memory_release_corrupt')
        records=[dict(r) for r in db.execute('SELECT * FROM fraud_memory_active ORDER BY recorded_at DESC,candidate LIMIT 100')] if listing else []
        return dict(release=row['id'],head=row['hash'],records=records,
                    active_count=db.execute('SELECT COUNT(*) FROM fraud_memory_active').fetchone()[0],listing_limit=100)

    def head(self):
        with self.store.transaction() as db: return self._head(db)

    def _source(self, db, job, *, approved=False):
        row = self.store._row(db, job)
        if row['state'] != 'awaiting_review' or not row['result']: raise Conflict('validated_fraud_report_required')
        spec, report = json.loads(row['spec']), json.loads(row['result'])
        if digest(spec) != row['spec_hash'] or digest(report) != row['result_hash']: raise Conflict('memory_source_corrupt')
        if spec['snapshot'].get('task_contract') != CONTRACT: raise Conflict('fraud_memory_scope_required')
        source=BoundSource(spec['snapshot'])
        checkpoint = db.execute('SELECT body,hash FROM investigation_state WHERE job_id=?', (job,)).fetchone()
        if checkpoint is None: raise Conflict('source_checkpoint_required')
        state = json.loads(checkpoint['body'])
        if digest(state) != checkpoint['hash']: raise Conflict('memory_source_checkpoint_corrupt')
        for entry in state['evidence'].values():
            expected=source.call(entry['tool'],entry['arguments'])
            # Completed v1 reports retain their exact old research-policy text.
            # Only historical provenance checks accept it; new runs require v2.
            if entry['tool']=='fraud_policy' and entry['result']['data']==LEGACY_POLICY:expected['data']=deepcopy(LEGACY_POLICY)
            if expected!=entry['result']:raise Conflict('memory_evidence_source_mismatch')
        # Revalidate actual E facts. A caller cannot label arbitrary text "verified".
        validation = verify(report['answer'], state['evidence'],allow_legacy_policy=True)
        if validation != report['validation']: raise Conflict('memory_source_validation_changed')
        reviewer = None
        if approved:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_drafts'").fetchone()
            ticket = db.execute('SELECT * FROM ticket_drafts WHERE job_id=?', (job,)).fetchone() if exists else None
            if not ticket or ticket['approved_hash'] != ticket['hash'] or digest(json.loads(ticket['payload'])) != ticket['hash']:
                raise Conflict('explicit_source_review_required')
            if json.loads(ticket['payload'])['report_hash'] != row['result_hash']: raise Conflict('source_report_changed')
            reviewer = ticket['reviewer']
        return spec['snapshot'], report, row['result_hash'], reviewer

    def candidate(self, job, expected_report_hash, *, focus='none'):
        if focus not in FOCUS: raise ValueError('Bounded review focus required')
        with self.store.transaction() as db:
            snapshot, report, report_hash, _ = self._source(db, job)
            if report_hash != expected_report_hash: raise Conflict('source_report_changed')
            body=payload(snapshot,report,report_hash,job,focus)
            content_hash = digest(body)
            prior = db.execute('SELECT id FROM fraud_memory_candidates WHERE hash=?', (content_hash,)).fetchone()
            if prior: return self._candidate(db, prior['id'])
            identity = 'fm-' + uuid4().hex
            db.execute('INSERT INTO fraud_memory_candidates VALUES (?,?,?,?,?,NULL)',
                (identity, encoded(body), content_hash, self.store.clock(), self.store.clock()+RETENTION_SECONDS))
            self._event(db, operation='candidate', candidate=identity, hash=content_hash, source_job=job)
            return self._candidate(db, identity)

    def _candidate(self, db, identity):
        row = db.execute('SELECT * FROM fraud_memory_candidates WHERE id=?', (identity,)).fetchone()
        if not row: raise KeyError(identity)
        out = dict(row); out['body'] = json.loads(out['body'])
        if digest(out['body']) != out['hash']: raise Conflict('memory_candidate_corrupt')
        return out

    def get(self, identity):
        with self.store.transaction() as db: return self._candidate(db, identity)

    def _validate_candidate(self,db,item,*,approved=False):
        body=item['body']
        snapshot,report,report_hash,reviewer=self._source(db,body['source_job'],approved=approved)
        if body!=payload(snapshot,report,report_hash,body['source_job'],body['focus']):raise Conflict('memory_source_changed')
        return reviewer

    def assess(self, identity, expected_hash):
        with self.store.transaction() as db:
            item = self._candidate(db, identity); body = item['body']
            if item['hash'] != expected_hash: raise Conflict('memory_candidate_changed')
            self._validate_candidate(db,item)
            if self.store.clock() >= item['expires']: raise Conflict('memory_candidate_expired')
            db.execute('UPDATE fraud_memory_candidates SET assessed_hash=? WHERE id=?', (expected_hash, identity))
            self._event(db, operation='assess', candidate=identity, hash=expected_hash,
                        claim='provenance_scope_structure_only_NOT_business_effectiveness')
            return dict(candidate=identity, hash=expected_hash, source_checks_passed=True, efficacy_tested=False)

    def publish(self, identity, expected_hash, expected_head, *, reviewed_claims, reviewer='local-token-reviewer'):
        if not isinstance(reviewed_claims,list) or len(reviewed_claims)!=3 or set(reviewed_claims)!={'source','scope','advisory_only'}:
            raise Conflict('explicit_memory_content_review_required')
        with self.store.transaction() as db:
            item=self._candidate(db,identity); body=item['body']; head=self._head(db)
            if item['hash']!=expected_hash or item['assessed_hash']!=expected_hash: raise Conflict('assessed_exact_candidate_required')
            if head['head']!=expected_head: raise Conflict('memory_head_changed')
            if self.store.clock()>=item['expires']: raise Conflict('memory_candidate_expired')
            self._validate_candidate(db,item,approved=True)
            if db.execute('SELECT 1 FROM fraud_memory_active WHERE candidate=?',(identity,)).fetchone():
                self._event(db,operation='NOOP',candidate=identity,head=expected_head)
                return dict(operation='NOOP',**head)
            key=digest([body['subject_scope'],body['target_transaction_id']])
            operation='UPDATE' if db.execute('SELECT 1 FROM fraud_memory_active WHERE key=?',(key,)).fetchone() else 'ADD'
            record=dict(key=key,candidate=identity,hash=expected_hash,subject=body['subject_scope'],event_cutoff=body['event_cutoff'],
                        target=body['target_transaction_id'],recorded_at=self.store.clock(),expires=item['expires'])
            revision=dict(previous_head=expected_head,operation=operation,record=record)
            db.execute('INSERT INTO fraud_memory_active VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET candidate=excluded.candidate,hash=excluded.hash,subject=excluded.subject,event_cutoff=excluded.event_cutoff,target=excluded.target,recorded_at=excluded.recorded_at,expires=excluded.expires',tuple(record.values()))
            db.execute('INSERT INTO fraud_memory_releases(body,hash,created,operation,reviewer) VALUES (?,?,?,?,?)',
                (encoded(revision),digest(revision),self.store.clock(),operation,reviewer))
            self._event(db,operation=operation,candidate=identity,hash=expected_hash,reviewer=reviewer,reviewed_claims=reviewed_claims)
            return dict(operation=operation,**self._head(db))

    def withdraw(self, identity, expected_head, *, reviewer='local-token-reviewer'):
        with self.store.transaction() as db:
            head=self._head(db)
            if head['head']!=expected_head: raise Conflict('memory_head_changed')
            if not db.execute('SELECT 1 FROM fraud_memory_active WHERE candidate=?',(identity,)).fetchone():raise Conflict('active_memory_required')
            db.execute('DELETE FROM fraud_memory_active WHERE candidate=?',(identity,))
            revision=dict(previous_head=expected_head,operation='DELETE_withdraw',candidate=identity)
            db.execute('INSERT INTO fraud_memory_releases(body,hash,created,operation,reviewer) VALUES (?,?,?,?,?)',
                (encoded(revision),digest(revision),self.store.clock(),'DELETE_withdraw',reviewer))
            self._event(db,operation='DELETE_withdraw',candidate=identity,reviewer=reviewer,
                        meaning='excluded_from_future_tasks; immutable_audit_and_existing_task_snapshots_retained')
            return self._head(db)

    def freeze(self, snapshot):
        subject=scope(snapshot); now=self.store.clock()
        with self.store.transaction() as db:
            head=self._head(db,listing=False); eligible=[]
            records=db.execute('SELECT * FROM fraud_memory_active WHERE subject=? AND event_cutoff<=? AND recorded_at<=? AND expires>? ORDER BY (target=?) DESC,event_cutoff DESC,recorded_at DESC,candidate LIMIT 100',
                (subject,snapshot['cutoff'],now,now,snapshot['manifest']['target_transaction_id'])).fetchall()
            for record in records:
                item=self._candidate(db,record['candidate']); body=item['body']
                if item['hash']!=record['hash']: raise Conflict('memory_release_item_changed')
                if body['subject_scope']!=subject or body['event_cutoff']>snapshot['cutoff'] or record['recorded_at']>now or record['expires']<=now: continue
                # Approvals can be revoked by editing their source draft. New tasks
                # then exclude the episode even if its release was not withdrawn.
                try: reviewer=self._validate_candidate(db,item,approved=True)
                except Conflict: continue
                eligible.append(dict(memory_id='M'+item['hash'][:20],body=body,review_actor=reviewer,recorded_at=record['recorded_at'],expires=record['expires']))
                if len(eligible)==3:break
            eligible.sort(key=lambda e:(e['body']['target_transaction_id']==snapshot['manifest']['target_transaction_id'],e['body']['event_cutoff'],e['recorded_at']),reverse=True)
            return dict(release=head['release'],head=head['head'],review_as_of_utc_epoch=now,event_cutoff=snapshot['cutoff'],
                subject_scope=subject,records=eligible,candidate_window=len(records),candidate_window_limit=100,
                policy='same_subject_past_events_available_at_review; current_facts_must_be_requeried; fixed_snapshot_not_live_access')


def recall(frozen):
    """No query-supplied user ID, arbitrary SQL, writes, or E evidence IDs."""
    return dict(ok=True,kind='review_memory',authority='advisory_only_NOT_current_evidence',
                found=bool(frozen['records']),snapshot_hash=digest(frozen),records=deepcopy(frozen['records']))
