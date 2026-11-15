"""Fenced investigation checkpoints extending the existing lease/journal store."""
import json
from app.service.store import JobStore, digest, encoded, Blocked


class HarnessStore(JobStore):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS investigation_state(job_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL, hash TEXT NOT NULL)')

    def load(self,identity):
        with self.transaction() as db:
            row=db.execute('SELECT * FROM investigation_state WHERE job_id=?',(identity,)).fetchone()
            if row is None: return None
            body=json.loads(row['body'])
            if digest(body)!=row['hash']: raise Blocked('checkpoint_hash_mismatch')
            return body

    def save(self,claim,state):
        with self.transaction() as db:
            self._guard(db,claim['id'],claim['owner'],claim['generation'])
            db.execute('INSERT INTO investigation_state VALUES (?,1,?,?) ON CONFLICT(job_id) DO UPDATE SET revision=revision+1,body=excluded.body,hash=excluded.hash',
                       (claim['id'],encoded(state),digest(state)))
            self.event(db,claim['id'],'investigation_checkpoint',turn=state['turn'],attempts=state['attempts'],phase=state['phase'])
