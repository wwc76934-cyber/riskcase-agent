"""Local version-bound review records; no deployment or production identity claims."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3


class ReviewConflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}",value):
        raise ValueError("Bounded identifier required")


def transition(current, command):
    """Deterministic reducer shared by writes and audit replay."""
    expected = command["expected_version"]
    version = current["version"] if current else 0
    if type(expected) is not int or expected != version: raise ReviewConflict("stale_version")
    action = command["action"]
    if action=="register":
        package = command["package"]
        package_hash = digest(package)
        if current and current["package_hash"]==package_hash:
            return dict(current)
        return {"candidate_id":command["candidate_id"],"version":version+1,"package_hash":package_hash,
            "package":package,"status":"pending_review","decision":None,"deployed":False}
    if action!="decide" or current is None: raise ReviewConflict("candidate_not_registered")
    if command["package_hash"]!=current["package_hash"]: raise ReviewConflict("wrong_package_hash")
    if current["status"]!="pending_review": raise ReviewConflict("revision_already_decided")
    if command["decision"] not in ("approve","reject"): raise ValueError("approve or reject required")
    result = dict(current)
    result.update(status="approved" if command["decision"]=="approve" else "rejected",
        decision={"action":command["decision"],"actor":command["actor"],"note":command["note"]})
    return result


class ReviewStore:
    def __init__(self,path: Path):
        self.path = path
        path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript("""CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY,state_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,
                    payload_json TEXT NOT NULL,previous_hash TEXT NOT NULL,event_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,result_json TEXT NOT NULL);""")

    @staticmethod
    def _verify(db):
        states,receipts = {},{}
        previous = "0"*64
        for seq,request_id,raw,prev,hashed in db.execute("SELECT seq,request_id,payload_json,previous_hash,event_hash FROM events ORDER BY seq"):
            payload = json.loads(raw)
            if seq!=len(receipts)+1 or prev!=previous or digest({"previous_hash":prev,"payload":payload})!=hashed:
                raise ReviewConflict("audit_chain_mismatch")
            command = payload["command"]
            state = transition(states.get(command["candidate_id"]),command)
            if state!=payload["result"]: raise ReviewConflict("audit_transition_mismatch")
            states[command["candidate_id"]] = state
            receipts[request_id] = (digest(command),canonical(state))
            previous = hashed
        stored_states = {key:json.loads(raw) for key,raw in db.execute("SELECT id,state_json FROM candidates")}
        stored_receipts = {key:(fingerprint,raw) for key,fingerprint,raw in db.execute("SELECT id,fingerprint,result_json FROM requests")}
        if stored_states!=states or stored_receipts!=receipts: raise ReviewConflict("audit_projection_mismatch")
        return states,previous,len(receipts)

    def _apply(self,command,request_id):
        _identifier(request_id); _identifier(command["candidate_id"])
        if not isinstance(command["actor"],str) or not 1<=len(command["actor"])<=100: raise ValueError("Bounded local actor required")
        with closing(sqlite3.connect(self.path,timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            states,previous,_ = self._verify(db)
            receipt = db.execute("SELECT fingerprint,result_json FROM requests WHERE id=?",(request_id,)).fetchone()
            fingerprint = digest(command)
            if receipt:
                if receipt[0]!=fingerprint: raise ReviewConflict("idempotency_key_reused_with_different_command")
                return json.loads(receipt[1])
            state = transition(states.get(command["candidate_id"]),command)
            payload = {"command":command,"result":state,"created_at":datetime.now(timezone.utc).isoformat()}
            event_hash = digest({"previous_hash":previous,"payload":payload})
            db.execute("INSERT INTO events(request_id,payload_json,previous_hash,event_hash) VALUES (?,?,?,?)",
                (request_id,canonical(payload),previous,event_hash))
            db.execute("INSERT OR REPLACE INTO candidates VALUES (?,?)",(command["candidate_id"],canonical(state)))
            db.execute("INSERT INTO requests VALUES (?,?,?)",(request_id,fingerprint,canonical(state)))
        return json.loads(canonical(state))

    def register(self,candidate_id,package,*,expected_version,actor,request_id):
        if not isinstance(package,dict) or len(canonical(package))>100000: raise ValueError("Bounded evidence package required")
        return self._apply({"action":"register","candidate_id":candidate_id,"package":package,
            "expected_version":expected_version,"actor":actor},request_id)

    def decide(self,candidate_id,package,*,expected_version,package_hash,decision,actor,note,request_id):
        if not isinstance(note,str) or not 1<=len(note.strip())<=2000: raise ValueError("Review rationale required")
        if digest(package)!=package_hash: raise ReviewConflict("source_package_changed")
        return self._apply({"action":"decide","candidate_id":candidate_id,"expected_version":expected_version,
            "package_hash":package_hash,"decision":decision,"actor":actor,"note":note},request_id)

    def current(self,candidate_id):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            states,_,_ = self._verify(db)
        if candidate_id not in states: raise ReviewConflict("candidate_not_registered")
        return states[candidate_id]

    def effective_status(self,candidate_id,package):
        state = self.current(candidate_id)
        unchanged = state["package_hash"]==digest(package)
        return {"candidate_id":candidate_id,"version":state["version"],"recorded_status":state["status"],
            "package_hash":state["package_hash"],"source_package_unchanged":unchanged,
            "effective_status":state["status"] if unchanged else "source_changed_requires_new_version",
            "approved_for_local_research":unchanged and state["status"]=="approved","deployed":False,
            "identity_scope":"Local operator label; not authenticated enterprise reviewer"}

    def verify(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            states,head,count = self._verify(db)
        return {"status":"passed","events":count,"candidates":len(states),"head_hash":head,
            "limits":"Local consistency checks; not tamper-proof storage, authenticated identities or deployment authorization."}
