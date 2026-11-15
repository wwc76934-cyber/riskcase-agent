"""Content-bound approvals and an idempotent local mock destination."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from app.service.store import Conflict, digest, encoded


class MockDestination:
    def __init__(self,path):
        self.path=Path(path)
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS tickets(key TEXT PRIMARY KEY, id TEXT NOT NULL, payload TEXT NOT NULL)')

    def lookup(self,key):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT id,payload FROM tickets WHERE key=?',(key,)).fetchone()
            return None if row is None else dict(id=row[0],payload=json.loads(row[1]))

    def create(self,key,payload):
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            db.execute('INSERT OR IGNORE INTO tickets VALUES (?,?,?)',(key,'ticket-'+uuid4().hex,encoded(payload)))
        record=self.lookup(key)
        if record['payload']!=payload:raise Conflict('destination_idempotency_conflict')
        return record


class Tickets:
    def __init__(self,store,destination):
        self.store,self.destination=store,destination
        with store.transaction() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS ticket_drafts(job_id TEXT PRIMARY KEY,payload TEXT NOT NULL,hash TEXT NOT NULL,approved_hash TEXT,reviewer TEXT);
                CREATE TABLE IF NOT EXISTS ticket_outbox(job_id TEXT PRIMARY KEY,key TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,hash TEXT NOT NULL,status TEXT NOT NULL,receipt TEXT);''')

    def get(self,job):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM ticket_drafts WHERE job_id=?',(job,)).fetchone()
            if row is None:return None
            out=dict(row);out['payload']=json.loads(out['payload'])
            if digest(out['payload'])!=out['hash']:raise Conflict('draft_corrupt')
            sent=db.execute('SELECT status,receipt FROM ticket_outbox WHERE job_id=?',(job,)).fetchone()
            out.update(status=sent['status'] if sent else 'draft',receipt=json.loads(sent['receipt']) if sent and sent['receipt'] else None)
            return out

    def draft(self,job,text,*,expected_hash=None):
        if not isinstance(text,str) or not 1<=len(text)<=2000:raise ValueError('Bounded draft text required')
        with self.store.transaction() as db:
            source=self.store._row(db,job)
            if source['state']!='awaiting_review' or not source['result']:raise Conflict('validated_report_required')
            result=json.loads(source['result'])
            if digest(result)!=source['result_hash']:raise Conflict('report_corrupt')
            if db.execute('SELECT 1 FROM ticket_outbox WHERE job_id=?',(job,)).fetchone():raise Conflict('submission_started_draft_locked')
            prior=db.execute('SELECT hash FROM ticket_drafts WHERE job_id=?',(job,)).fetchone()
            if (prior['hash'] if prior else None)!=expected_hash:raise Conflict('draft_version_changed')
            payload=dict(case_id=json.loads(source['spec'])['case_id'],report_hash=source['result_hash'],
                         verdict=result['answer']['status'] if result.get('validation',{}).get('contract') in ('fraud-inquiry-v1','fraud-world-v1') else result['answer']['verdict'],text=text,destination='local_mock_review_queue')
            db.execute('INSERT INTO ticket_drafts VALUES (?,?,?,NULL,NULL) ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload,hash=excluded.hash,approved_hash=NULL,reviewer=NULL',
                       (job,encoded(payload),digest(payload)))
            self.store.event(db,job,'ticket_drafted',payload_hash=digest(payload))
        return self.get(job)

    def approve(self,job,expected_hash,*,reviewer='local-token-reviewer',reviewed_claims=None):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM ticket_drafts WHERE job_id=?',(job,)).fetchone()
            if not row or row['hash']!=expected_hash or digest(json.loads(row['payload']))!=expected_hash:raise Conflict('draft_version_changed')
            source=self.store._row(db,job)
            if source['state']!='awaiting_review' or source['result_hash']!=json.loads(row['payload'])['report_hash']:raise Conflict('source_report_changed')
            report=json.loads(source['result'])
            if digest(report)!=source['result_hash']:raise Conflict('report_corrupt')
            if report.get('validation',{}).get('semantic_validation') is False:
                contract=report['validation'].get('contract')
                required=({'observations','hypotheses','timeline','recommendations','missing'} if contract=='fraud-world-v1' else
                          {'question_coverage','interpretations','counterevidence','missing'} if contract=='fraud-inquiry-v1' else
                          {'findings','scope','missing'} if contract=='detector-fraud-review-v1' else
                          {'settled_debits','net_debit_cents','reversal_state','coverage','policy','missing'})
                if not isinstance(reviewed_claims,list) or len(reviewed_claims)!=len(required) or set(reviewed_claims)!=required:
                    raise Conflict('explicit_semantic_review_required')
            db.execute('UPDATE ticket_drafts SET approved_hash=?,reviewer=? WHERE job_id=?',(expected_hash,reviewer,job))
            self.store.event(db,job,'ticket_approved',payload_hash=expected_hash,reviewer=reviewer,reviewed_claims=reviewed_claims or [])
        return self.get(job)

    def submit(self,job,expected_hash,*,after_created=None):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM ticket_drafts WHERE job_id=?',(job,)).fetchone()
            if not row or row['hash']!=expected_hash or row['approved_hash']!=expected_hash:raise Conflict('approval_required_for_exact_content')
            payload=json.loads(row['payload'])
            if digest(payload)!=expected_hash:raise Conflict('draft_corrupt')
            source=self.store._row(db,job)
            if source['state']!='awaiting_review' or source['result_hash']!=payload['report_hash']:raise Conflict('source_report_changed')
            key=digest([job,expected_hash])
            db.execute("INSERT OR IGNORE INTO ticket_outbox VALUES (?,?,?,?,'pending',NULL)",(job,key,encoded(payload),expected_hash))
        # Query by durable idempotency key before attempting an external write.
        try:
            receipt=self.destination.lookup(key)
            if receipt is None:receipt=self.destination.create(key,payload)
            if receipt['payload']!=payload:raise Conflict('destination_payload_changed')
            if after_created:after_created()
        except Exception:
            with self.store.transaction() as db:
                db.execute("UPDATE ticket_outbox SET status='uncertain' WHERE job_id=? AND status!='submitted'",(job,))
            raise
        with self.store.transaction() as db:
            db.execute("UPDATE ticket_outbox SET status='submitted',receipt=? WHERE job_id=?",(encoded(receipt),job))
            self.store.event(db,job,'ticket_submission_reconciled',key=key,ticket_id=receipt['id'])
        return self.get(job)
