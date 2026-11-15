"""Local analyst feedback -> immutable candidate -> retrieval regression -> promotion.

The gate measures retrieval, not reasoning quality, semantic truth or business value.
No model can write/promote knowledge. The bearer-token analyst remains the reviewer.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from uuid import uuid4

from app.service.store import Conflict, digest
from app.tools.knowledge_tools import KnowledgeStore, FrozenKnowledge


SCOPE = 'local-research'


def now(): return datetime.now(timezone.utc).isoformat()


class Experience:
    def __init__(self, path, suite):
        self.store = KnowledgeStore(path)
        self.path, self.suite = path, suite
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS lessons(id TEXT PRIMARY KEY, payload TEXT NOT NULL, evaluation TEXT, promoted TEXT)')

    def corpus(self): return self.store.snapshot(scope=SCOPE, as_of=now())

    def propose(self, *, source_job, result_hash, identity, body, feedback):
        if identity not in {r['expected'] for r in self.suite if r['expected']}:
            raise ValueError('Unsupported procedure identity')
        if not all(isinstance(s, str) and 1 <= len(s.strip()) <= limit for s, limit in [(body, 4000), (feedback, 1000)]):
            raise ValueError('Bounded lesson and feedback required')
        payload = dict(source_job=source_job, result_hash=result_hash, identity=identity,
                       body=body, feedback=feedback, created=now(), suite_hash=digest(self.suite))
        identity = 'lesson-' + uuid4().hex
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT INTO lessons(id,payload) VALUES (?,?)', (identity, json.dumps(payload, ensure_ascii=False)))
        return self.get(identity)

    def get(self, identity):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT payload,evaluation,promoted FROM lessons WHERE id=?', (identity,)).fetchone()
        if row is None: raise KeyError(identity)
        return dict(id=identity, candidate=json.loads(row[0]), evaluation=json.loads(row[1]) if row[1] else None,
                    evaluation_hash=digest(json.loads(row[1])) if row[1] else None, promoted=row[2])

    def evaluate(self, identity):
        record = self.get(identity)
        if record['promoted']: raise Conflict('candidate_already_promoted')
        candidate, rows = record['candidate'], self.corpus()
        if candidate['suite_hash'] != digest(self.suite): raise Conflict('evaluation_suite_changed')
        version = max([r[1] for r in rows if r[0] == candidate['identity']] or [0]) + 1
        added = [candidate['identity'], version, 'lesson', candidate['body'],
                 'rejected-job:' + candidate['source_job'], 'local-token-reviewer',
                 hashlib.sha256(candidate['body'].encode()).hexdigest(), 0]
        baseline, challenger = FrozenKnowledge(rows), FrozenKnowledge(rows + [added])
        outcomes = []
        for case in self.suite:
            def score(store):
                hits = store.search(case['query'], limit=1)
                predicted = hits[0]['citation'].split(':')[1] if hits else None
                return predicted == case['expected']
            outcomes.append(case | dict(before=score(baseline), after=score(challenger)))
        groups = {group: [r for r in outcomes if r['partition'] == group] for group in ('regression', 'transfer')}
        # Each partition must improve, no previously correct query may regress.
        passed = all(any(not r['before'] and r['after'] for r in group) for group in groups.values()) and not any(r['before'] and not r['after'] for r in outcomes)
        evaluation = dict(passed=passed, baseline_hash=digest(rows), candidate_hash=digest(candidate),
                          suite_hash=digest(self.suite), version=version, outcomes=outcomes,
                          evaluated=now(), metric='top1_procedure_retrieval_only',
                          independent_holdout=False)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE lessons SET evaluation=? WHERE id=? AND promoted IS NULL', (json.dumps(evaluation, ensure_ascii=False), identity))
        return self.get(identity)

    def promote(self, identity, *, evaluation_hash):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            raw, evaluated, promoted = db.execute('SELECT payload,evaluation,promoted FROM lessons WHERE id=?', (identity,)).fetchone() or (None, None, None)
            if raw is None: raise KeyError(identity)
            if promoted:
                if evaluated and digest(json.loads(evaluated)) == evaluation_hash: return self.get(identity)
                raise Conflict('wrong_evaluation_hash')
            candidate, evaluation = json.loads(raw), json.loads(evaluated) if evaluated else None
            current_time = datetime.now(timezone.utc).timestamp()
            rows = db.execute('''SELECT identity,version,kind,body,source,reviewer,hash,available FROM knowledge k
                WHERE scope=? AND available<=? AND version=(SELECT MAX(version) FROM knowledge n
                WHERE n.scope=k.scope AND n.identity=k.identity AND n.available<=?) ORDER BY identity,version''',
                (SCOPE, current_time, current_time)).fetchall()
            if not evaluation or not evaluation['passed'] or digest(evaluation) != evaluation_hash:
                raise Conflict('passing_version_bound_evaluation_required')
            if digest([list(r) for r in rows]) != evaluation['baseline_hash'] or digest(candidate) != evaluation['candidate_hash'] or digest(self.suite) != evaluation['suite_hash']:
                raise Conflict('stale_evaluation')
            stamp = now()
            db.execute('INSERT INTO knowledge VALUES (?,?,?,?,?,?,?,?,?)', (SCOPE, candidate['identity'], evaluation['version'],
                datetime.fromisoformat(stamp).timestamp(), 'lesson', candidate['body'], 'rejected-job:' + candidate['source_job'],
                'local-token-reviewer', hashlib.sha256(candidate['body'].encode()).hexdigest()))
            db.execute('UPDATE lessons SET promoted=? WHERE id=?', (stamp, identity))
        return self.get(identity)


def attach(spec, directory):
    """Publish reproducible advisory retrieval; deterministic finding math is unchanged."""
    from app.service.runtime import write_json
    corpus = FrozenKnowledge(spec['knowledge']['rows'])
    question = spec['question']
    query = question.get('text') or ' '.join(question.get('checks', ['merchant']))
    if spec.get('task_type')=='triage': query='frequency amount merchant 频次 金额 商户'
    result = dict(corpus_hash=spec['knowledge']['hash'], as_of=spec['knowledge']['as_of'],
                  advisory_only=True, query=query, hits=corpus.search(query[:300]),
                  role='procedure_context_not_transaction_evidence')
    write_json(directory/'knowledge.json', result)
    return result
