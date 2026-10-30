"""Bounded, reviewable method-memory releases; no case text crosses this boundary.

This is a constrained policy revision workflow, not autonomous self-improvement.
The evaluator measures structured task decisions, not arbitrary prose semantics.
"""
import json
from uuid import uuid4
from app.service.store import digest,encoded,Conflict

RULES={
    'coverage':'先检查历史覆盖；历史不完整即 insufficient_evidence，不能被其他解释覆盖。',
    'settlement':'若重复授权，独立对账不完整即 insufficient_evidence；完整且一笔扣款为 duplicate_record，多笔为 needs_review。',
    'merchant':'若商户提出业务变化，独立处置 confirmed 为 normal_change，denied 为 needs_review，缺失为 insufficient_evidence。',
}


def lesson(order):
    if not isinstance(order,list) or len(order)!=3 or set(order)!=set(RULES):raise ValueError('Three unique bounded rules required')
    return dict(order=order,text='合成研究规程的调查优先级；按顺序选择首个适用分支：\n'+'\n'.join(f'{i+1}. {RULES[k]}' for i,k in enumerate(order))+'\n仅提供调查方法，不提供本案事实；结论仍须引用本案 E 证据。')


def decide(order,facts):
    for rule in order:
        if rule=='coverage' and not facts['history']:return 'insufficient_evidence'
        if rule=='settlement' and facts['repeated']:
            return 'insufficient_evidence' if not facts['complete'] else 'duplicate_record' if facts['debits']==1 else 'needs_review'
        if rule=='merchant' and facts['merchant']:
            return {'confirmed':'normal_change','denied':'needs_review',None:'insufficient_evidence'}[facts['disposition']]
    return 'insufficient_evidence'


def regression_cases():
    # Gold decisions authored independently of decide(), intentionally public.
    rows=[
        (False,True,True,1,'confirmed','insufficient_evidence'),
        (False,False,True,1,'confirmed','insufficient_evidence'),
        (True,True,True,1,'confirmed','duplicate_record'),
        (True,True,True,2,'confirmed','needs_review'),
        (True,True,False,1,'confirmed','insufficient_evidence'),
        (True,False,True,1,'confirmed','normal_change'),
        (True,False,True,1,'denied','needs_review'),
        (True,False,True,1,None,'insufficient_evidence'),
        (False,True,True,3,'denied','insufficient_evidence'),
        (False,False,False,3,'denied','insufficient_evidence'),
        (True,True,True,3,'denied','needs_review'),
        (True,True,True,1,'denied','duplicate_record'),
        (True,True,False,3,'denied','insufficient_evidence'),
        (True,False,False,3,'confirmed','normal_change'),
        (True,False,False,3,'denied','needs_review'),
        (True,False,False,3,None,'insufficient_evidence'),
    ]
    return [dict(id=f'method-{i+1:02}',split='regression' if i<8 else 'transfer_development',
                 facts=dict(history=h,repeated=r,complete=c,debits=d,merchant=True,disposition=s),expected=e)
            for i,(h,r,c,d,s,e) in enumerate(rows)]


def evaluate(order,baseline=None):
    lesson(order);cases=regression_cases();rows=[]
    for c in cases:
        before=decide(baseline,c['facts']) if baseline else None
        after=decide(order,c['facts'])
        rows.append(dict(id=c['id'],split=c['split'],expected=c['expected'],before=before,after=after,
                         correct=after==c['expected'],regressed=before==c['expected'] and after!=c['expected']))
    return dict(passed=all(r['correct'] and not r['regressed'] for r in rows),cases=rows,
                suite_hash=digest(cases),evaluator='bounded-method-decisions-v1',
                baseline='published_method' if baseline else 'no_method_prediction_not_agent_baseline')


class Experiences:
    def __init__(self,store):
        self.store=store
        with store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS method_candidates(id TEXT PRIMARY KEY, body TEXT NOT NULL, hash TEXT NOT NULL, evaluation TEXT);
                CREATE TABLE IF NOT EXISTS method_releases(id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL, hash TEXT NOT NULL);
            ''')

    @staticmethod
    def _head(db):
        row=db.execute('SELECT * FROM method_releases ORDER BY id DESC LIMIT 1').fetchone()
        if row is None:return dict(id=0,hash=digest(None),lesson=None)
        body=json.loads(row['body'])
        if digest(body)!=row['hash']:raise Conflict('release_corrupted')
        return dict(id=row['id'],hash=row['hash'],**body)

    def head(self):
        with self.store.transaction() as db:return self._head(db)

    def frozen(self):
        head=self.head();entry=head['lesson']
        if entry and entry['expires_at']<=self.store.clock():entry=None
        # No provenance, job identity, feedback, facts or candidate text is exposed.
        public=None if entry is None else {k:entry[k] for k in ('text','order','expires_at','content_hash')}
        return dict(release=head['id'],release_hash=head['hash'],lesson=public)

    @staticmethod
    def _source(db,source_job):
        row=db.execute('SELECT * FROM jobs WHERE id=?',(source_job,)).fetchone()
        if row is None:raise KeyError('source_job_missing')
        if row['state'] not in ('awaiting_review','failed','blocked'):raise Conflict('reviewable_source_required')
        spec=json.loads(row['spec']);result=json.loads(row['result']) if row['result'] else None
        if digest(spec)!=row['spec_hash'] or (result is not None and digest(result)!=row['result_hash']):raise Conflict('source_corrupted')
        state=db.execute('SELECT body,hash FROM investigation_state WHERE job_id=?',(source_job,)).fetchone()
        checkpoint=json.loads(state['body']) if state else None
        if state and digest(checkpoint)!=state['hash']:raise Conflict('source_checkpoint_corrupted')
        if result is None and checkpoint is None:raise Conflict('source_trace_required')
        return digest(dict(spec=spec,result=result,checkpoint=checkpoint))

    def candidate(self,source_job,order,*,parent=None):
        content=lesson(order)
        identity='candidate-'+uuid4().hex
        with self.store.transaction() as db:
            provenance=self._source(db,source_job)
            if parent and db.execute('SELECT 1 FROM method_candidates WHERE id=?',(parent,)).fetchone() is None:raise KeyError('candidate_not_found')
            body=dict(content=content,source_job=source_job,source_hash=provenance,parent=parent,base_hash=self._head(db)['hash'])
            db.execute('INSERT INTO method_candidates VALUES (?,?,?,NULL)',(identity,encoded(body),digest(body)))
        return self.get(identity)

    def get(self,identity):
        with self.store.transaction() as db:
            row=db.execute('SELECT * FROM method_candidates WHERE id=?',(identity,)).fetchone()
            if row is None:raise KeyError('candidate_not_found')
            body=json.loads(row['body'])
            if digest(body)!=row['hash']:raise Conflict('candidate_corrupted')
            return dict(id=identity,hash=row['hash'],**body,evaluation=json.loads(row['evaluation']) if row['evaluation'] else None)

    def assess(self,identity):
        candidate=self.get(identity);head=self.head()
        if head['hash']!=candidate['base_hash']:raise Conflict('release_changed_revise_candidate')
        baseline=head['lesson']['order'] if head['lesson'] and head['lesson']['expires_at']>self.store.clock() else None
        result=evaluate(candidate['content']['order'],baseline)
        result.update(candidate_hash=candidate['hash'],base_hash=head['hash'])
        with self.store.transaction() as db:
            db.execute('UPDATE method_candidates SET evaluation=? WHERE id=?',(encoded(result),identity))
        return self.get(identity)

    def publish(self,identity,expected_hash,expected_head):
        candidate=self.get(identity)
        if candidate['hash']!=expected_hash:raise Conflict('candidate_changed')
        with self.store.transaction() as db:
            if self._source(db,candidate['source_job'])!=candidate['source_hash']:raise Conflict('source_changed')
            head=self._head(db)
            if head['hash']!=expected_head or head['hash']!=candidate['base_hash']:raise Conflict('release_changed')
            baseline=head['lesson']['order'] if head['lesson'] and head['lesson']['expires_at']>self.store.clock() else None
            expected=evaluate(candidate['content']['order'],baseline)|dict(candidate_hash=candidate['hash'],base_hash=head['hash'])
            if candidate['evaluation']!=expected or not expected['passed']:raise Conflict('passing_current_task_evaluation_required')
            entry=candidate['content']|dict(content_hash=digest(candidate['content']),expires_at=self.store.clock()+30*86400)
            body=dict(lesson=entry,previous=head['id'],action='review_publish',candidate=identity,
                      reviewer='authenticated-local-reviewer',at=self.store.clock())
            db.execute('INSERT INTO method_releases(body,hash) VALUES (?,?)',(encoded(body),digest(body)))
        return self.head()

    def rollback(self,target,expected_head):
        if type(target)is not int or target<0:raise ValueError('Invalid release')
        with self.store.transaction() as db:
            head=self._head(db)
            if head['hash']!=expected_head:raise Conflict('release_changed')
            if target==0:entry=None
            else:
                row=db.execute('SELECT * FROM method_releases WHERE id=?',(target,)).fetchone()
                if row is None:raise KeyError('release_not_found')
                body=json.loads(row['body'])
                if digest(body)!=row['hash']:raise Conflict('release_corrupted')
                entry=body['lesson']
                if entry and entry['expires_at']<=self.store.clock():raise Conflict('expired_release')
            body=dict(lesson=entry,previous=head['id'],action='review_rollback',target=target,
                      reviewer='authenticated-local-reviewer',at=self.store.clock())
            db.execute('INSERT INTO method_releases(body,hash) VALUES (?,?)',(encoded(body),digest(body)))
        return self.head()

    def listing(self):
        with self.store.transaction() as db:
            candidates=[dict(r) for r in db.execute('SELECT id,hash FROM method_candidates ORDER BY rowid DESC LIMIT 20')]
            releases=[dict(id=r['id'],**json.loads(r['body'])) for r in db.execute('SELECT * FROM method_releases ORDER BY id DESC LIMIT 20')]
        return dict(head=self.head(),candidates=candidates,releases=releases)


def search_memory(snapshot,query):
    from app.investigation.retrieval import tokens
    if not isinstance(query,str) or not 1<=len(query.strip())<=240:raise ValueError('Bounded query required')
    entry=snapshot['lesson']
    # All active methods share the same bounded policy; lexical gate avoids unrelated recall.
    relevant=entry and bool(set(tokens(query)) & set(tokens(entry['text'])))
    return dict(ok=True,kind='method_memory',advisory_only=True,release=snapshot['release'],
                hits=[dict(citation=f'M:release-{snapshot["release"]}',text=entry['text'],content_hash=entry['content_hash'])] if relevant else [])
