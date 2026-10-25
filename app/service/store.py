"""SQLite job leases and response journal; external HTTP is never exactly-once."""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


class Conflict(RuntimeError): pass
class LeaseLost(RuntimeError): pass
class Cancelled(RuntimeError): pass
class Blocked(RuntimeError): pass


class JobStore:
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, spec_hash TEXT NOT NULL,
                    spec TEXT NOT NULL, state TEXT NOT NULL, version INTEGER NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 0, owner TEXT, lease_until REAL,
                    deadline REAL, created REAL NOT NULL, updated REAL NOT NULL,
                    reason TEXT, result TEXT, result_hash TEXT);
                CREATE TABLE IF NOT EXISTS calls(
                    job_id TEXT NOT NULL, ordinal INTEGER NOT NULL, request_hash TEXT NOT NULL,
                    state TEXT NOT NULL, response TEXT, response_hash TEXT, audit TEXT,
                    PRIMARY KEY(job_id, ordinal));
                CREATE TABLE IF NOT EXISTS events(
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    at REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews(
                    request_key TEXT PRIMARY KEY, job_id TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS plans(
                    job_id TEXT PRIMARY KEY, proposal TEXT NOT NULL, proposal_hash TEXT NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0);
            """)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, db, identity, kind, **detail):
        db.execute("INSERT INTO events(job_id,at,kind,detail) VALUES (?,?,?,?)",
                   (identity, self.clock(), kind, encoded(detail)))

    @staticmethod
    def _row(db, identity):
        row = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
        if row is None: raise KeyError("job_not_found")
        return dict(row)

    def get(self, identity):
        with self.transaction() as db:
            row = self._row(db, identity)
            for key in ("spec", "result"):
                if row[key] is not None: row[key] = json.loads(row[key])
            return row

    def list_jobs(self, limit=25):
        if type(limit) is not int or not 1 <= limit <= 100: raise ValueError("invalid_job_limit")
        with self.transaction() as db:
            rows = []
            for row in db.execute("SELECT id,state,version,created,updated,spec FROM jobs ORDER BY created DESC,id DESC LIMIT ?", (limit,)):
                item, spec = dict(row), json.loads(row["spec"])
                item.pop("spec")
                question = spec["question"]
                item.update(case_id=spec["case_id"], task_type=spec.get("task_type", "merchant"),
                            question_text='按优先级分步核查' if spec.get('task_type')=='triage' else question.get("text", " / ".join(question.get("checks", []))))
                rows.append(item)
            return rows

    def events(self, identity, after=0):
        with self.transaction() as db:
            self._row(db, identity)
            return [dict(row) | {"detail": json.loads(row["detail"])} for row in db.execute(
                "SELECT * FROM events WHERE job_id=? AND sequence>? ORDER BY sequence LIMIT 100",
                (identity, after))]

    def submit(self, request_key, spec):
        fingerprint, now = digest(spec), self.clock()
        with self.transaction() as db:
            row = db.execute("SELECT id,spec_hash FROM jobs WHERE request_key=?", (request_key,)).fetchone()
            if row:
                if row["spec_hash"] != fingerprint: raise Conflict("idempotency_payload_changed")
                return row["id"], False
            identity = "job-" + uuid4().hex
            db.execute("INSERT INTO jobs(id,request_key,spec_hash,spec,state,version,created,updated) VALUES (?,?,?,?,'queued',1,?,?)",
                       (identity, request_key, fingerprint, encoded(spec), now, now))
            self.event(db, identity, "submitted", mode=spec["mode"])
            return identity, True

    def claim(self, owner, *, lease_seconds=90, deadline_seconds=180):
        now = self.clock()
        with self.transaction() as db:
            # Expired workers are fenced before a successor can execute any step.
            expired = list(db.execute("SELECT * FROM jobs WHERE state IN ('running','cancel_requested') AND lease_until<=?", (now,)))
            for row in expired:
                pending = db.execute("SELECT 1 FROM calls WHERE job_id=? AND state!='completed'", (row["id"],)).fetchone()
                state = "cancelled" if row["state"] == "cancel_requested" else "blocked" if pending or now >= row["deadline"] or row["generation"] >= 5 else "queued"
                reason = "cancelled" if state == "cancelled" else "unresolved_call" if pending else "deadline_or_recovery_limit" if state == "blocked" else "lease_expired_recoverable"
                db.execute("UPDATE jobs SET state=?,reason=?,version=version+1,owner=NULL,lease_until=NULL,updated=? WHERE id=?", (state, reason, now, row["id"]))
                self.event(db, row["id"], "lease_expired", state=state, reason=reason)
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if row is None: return None
            db.execute("UPDATE jobs SET state='running',version=version+1,generation=generation+1,owner=?,lease_until=?,deadline=COALESCE(deadline,?),updated=? WHERE id=?",
                       (owner, now + lease_seconds, now + deadline_seconds, now, row["id"]))
            self.event(db, row["id"], "claimed", generation=row["generation"]+1)
            return self._row(db, row["id"])

    def _guard(self, db, identity, owner, generation, *, permit_cancel=False):
        row = self._row(db, identity)
        if row["owner"] != owner or row["generation"] != generation or row["lease_until"] is None or row["lease_until"] <= self.clock():
            raise LeaseLost("worker_lease_lost")
        if row["state"] == "cancel_requested" and not permit_cancel: raise Cancelled("cancel_requested")
        if row["state"] not in ("running", "cancel_requested"): raise LeaseLost("job_not_running")
        return row

    def checkpoint(self, identity, owner, generation, *, lease_seconds=90):
        with self.transaction() as db:
            row = self._guard(db, identity, owner, generation)
            if self.clock() >= row["deadline"]: raise Blocked("task_deadline")
            db.execute("UPDATE jobs SET lease_until=? WHERE id=?", (self.clock()+lease_seconds, identity))

    def begin_call(self, identity, owner, generation, ordinal, request_hash):
        with self.transaction() as db:
            self._guard(db, identity, owner, generation)
            row = db.execute("SELECT * FROM calls WHERE job_id=? AND ordinal=?", (identity, ordinal)).fetchone()
            if row:
                if row["request_hash"] != request_hash: raise Blocked("replay_request_changed")
                if row["state"] != "completed": raise Blocked("unresolved_call")
                response = json.loads(row["response"])
                if digest(response) != row["response_hash"]: raise Blocked("journal_hash_mismatch")
                self.event(db, identity, "response_replayed", ordinal=ordinal)
                return response
            if not 1 <= ordinal <= 6: raise Blocked("request_limit")
            db.execute("INSERT INTO calls VALUES (?,?,?,'started',NULL,NULL,NULL)", (identity, ordinal, request_hash))
            self.event(db, identity, "call_intent_saved", ordinal=ordinal)
            return None

    def complete_call(self, identity, owner, generation, ordinal, response, audit=None):
        with self.transaction() as db:
            # Save a received response even when cancellation arrived during HTTP.
            self._guard(db, identity, owner, generation, permit_cancel=True)
            changed = db.execute("UPDATE calls SET state='completed',response=?,response_hash=?,audit=? WHERE job_id=? AND ordinal=? AND state='started'",
                                 (encoded(response), digest(response), encoded(audit), identity, ordinal)).rowcount
            if changed != 1: raise Conflict("call_not_started")
            self.event(db, identity, "response_saved", ordinal=ordinal)

    def uncertain_call(self, identity, owner, generation, ordinal, audit):
        with self.transaction() as db:
            self._guard(db, identity, owner, generation, permit_cancel=True)
            db.execute("UPDATE calls SET state='uncertain',audit=? WHERE job_id=? AND ordinal=? AND state='started'",
                       (encoded(audit), identity, ordinal))
            self.event(db, identity, "call_uncertain", ordinal=ordinal)

    def finish(self, identity, owner, generation, state, *, reason=None, result=None, proposal=None):
        if state not in ("awaiting_review", "awaiting_plan_confirmation", "needs_clarification", "out_of_scope", "failed", "blocked", "cancelled"): raise ValueError("invalid_finish")
        if (state == "awaiting_plan_confirmation") != (proposal is not None): raise ValueError("proposal_state_mismatch")
        with self.transaction() as db:
            row = self._guard(db, identity, owner, generation, permit_cancel=True)
            if row["state"] == "cancel_requested": state, reason, result = "cancelled", "cancel_requested", None
            if state == "awaiting_plan_confirmation":
                db.execute("INSERT INTO plans VALUES (?,?,?,0)", (identity, encoded(proposal), digest(proposal)))
            db.execute("UPDATE jobs SET state=?,reason=?,result=?,result_hash=?,version=version+1,owner=NULL,lease_until=NULL,updated=? WHERE id=?",
                       (state, reason, encoded(result) if result is not None else None, digest(result) if result is not None else None, self.clock(), identity))
            self.event(db, identity, "finished", state=state, reason=reason)

    def cancel(self, identity):
        with self.transaction() as db:
            row = self._row(db, identity)
            state = {"queued": "cancelled", "running": "cancel_requested", "awaiting_plan_confirmation": "cancelled"}.get(row["state"])
            if state:
                db.execute("UPDATE jobs SET state=?,version=version+1,updated=? WHERE id=?", (state, self.clock(), identity))
                self.event(db, identity, "cancellation_requested", state=state)
            return state or row["state"]

    def plan(self, identity):
        with self.transaction() as db:
            self._row(db, identity)
            row = db.execute("SELECT * FROM plans WHERE job_id=?", (identity,)).fetchone()
            if row is None: return None
            body = json.loads(row["proposal"])
            if digest(body) != row["proposal_hash"]: raise Conflict("plan_changed")
            return {"proposal": body, "plan_hash": row["proposal_hash"], "confirmed": bool(row["confirmed"])}

    def confirm_plan(self, identity, *, expected_version, result_hash, plan_hash, decision, request_key):
        if decision not in ("confirm_plan", "reject_plan"): raise ValueError("readonly_plan_review_only")
        payload_hash = digest([identity, expected_version, result_hash, plan_hash, decision])
        with self.transaction() as db:
            prior = db.execute("SELECT * FROM reviews WHERE request_key=?", (request_key,)).fetchone()
            if prior:
                if prior["payload_hash"] != payload_hash: raise Conflict("review_idempotency_changed")
                return json.loads(prior["result"])
            row = self._row(db, identity)
            saved = db.execute("SELECT * FROM plans WHERE job_id=?", (identity,)).fetchone()
            if (row["state"] != "awaiting_plan_confirmation" or row["version"] != expected_version or row["result_hash"] != result_hash
                    or not saved or saved["proposal_hash"] != plan_hash or digest(json.loads(saved["proposal"])) != plan_hash):
                raise Conflict("stale_plan_confirmation")
            state = "queued" if decision == "confirm_plan" else "rejected"
            if state == "queued": db.execute("UPDATE plans SET confirmed=1 WHERE job_id=?", (identity,))
            # Human wait is outside execution time. The second phase has no model calls.
            db.execute("UPDATE jobs SET state=?,version=version+1,deadline=NULL,updated=? WHERE id=?", (state, self.clock(), identity))
            result = {"state": state, "version": row["version"]+1, "plan_hash": plan_hash, "policy_approved": False, "deployed": False}
            db.execute("INSERT INTO reviews VALUES (?,?,?,?)", (request_key, identity, payload_hash, encoded(result)))
            self.event(db, identity, "scope_reviewed", decision=decision, plan_hash=plan_hash, policy_approved=False)
            return result

    def review(self, identity, *, expected_version, result_hash, decision, request_key):
        if decision not in ("accept_report", "reject_report"): raise ValueError("research_report_review_only")
        payload_hash = digest([identity, expected_version, result_hash, decision])
        with self.transaction() as db:
            prior = db.execute("SELECT * FROM reviews WHERE request_key=?", (request_key,)).fetchone()
            if prior:
                if prior["payload_hash"] != payload_hash: raise Conflict("review_idempotency_changed")
                return json.loads(prior["result"])
            row = self._row(db, identity)
            if row["state"] != "awaiting_review" or row["version"] != expected_version or row["result_hash"] != result_hash:
                raise Conflict("stale_report_review")
            state = "completed" if decision == "accept_report" else "rejected"
            result = {"state": state, "version": row["version"]+1, "policy_approved": False, "deployed": False}
            db.execute("UPDATE jobs SET state=?,version=version+1,updated=? WHERE id=?", (state, self.clock(), identity))
            db.execute("INSERT INTO reviews VALUES (?,?,?,?)", (request_key, identity, payload_hash, encoded(result)))
            self.event(db, identity, "report_reviewed", decision=decision, policy_approved=False)
            return result
