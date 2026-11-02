"""Explicitly authorized NEW pilot scope, sharing the original project ledger."""
from contextlib import closing
from decimal import Decimal
import json,sqlite3
import hashlib
from pathlib import Path
from copy import deepcopy
from uuid import uuid4
from app.investigation.contracts import digest
from app.llm.cost_ledger import CostLedger,BudgetExceeded


def validate_grant(proposal,grant,proposal_sha256):
    if proposal.get('schema')!='v3-new-development-delivery-pilot-v1':raise ValueError('New V3 pilot proposal required')
    required={'state','grant_message','proposal_sha256','max_estimated_cny','project_limit_cny'}
    if set(grant)!=required or grant['state']!='approved' or not grant['grant_message'] or grant['proposal_sha256']!=proposal_sha256:
        raise ValueError('Explicit new user grant bound to this proposal required')
    if proposal['state']!='pending_user_authorization' or proposal['grant_message'] is not None:raise ValueError('Original pending proposal is immutable')
    if grant['max_estimated_cny']!=proposal['max_estimated_cny'] or grant['project_limit_cny']!=proposal['project_limit_cny'] or (grant['max_estimated_cny'],grant['project_limit_cny'])!=('6','34'):
        raise ValueError('This pilot needs the separately approved 6/34 scope')
    bounds={'max_case_runs':12,'max_phase_jobs':36,'max_requests':72,'repeats':2,'max_requests_per_phase':2,'max_requests_per_case_run':6}
    if any(proposal[k]!=v for k,v in bounds.items()) or len(set(proposal['case_ids']))!=6 or len(proposal['case_ids'])!=6:
        raise ValueError('Frozen pilot counts required')
    if proposal['model']!='zai-org/GLM-5.3-Flash' or proposal['max_case_estimated_cny']!='.50':raise ValueError('Model/case allowance changed')


class PilotLedger(CostLedger):
    def __init__(self,path,proposal_path,grant):
        self.proposal_path=Path(proposal_path).resolve();raw=self.proposal_path.read_bytes()
        proposal=json.loads(raw);proposal_sha256=hashlib.sha256(raw).hexdigest()
        validate_grant(proposal,grant,proposal_sha256)
        baseline=Decimal(proposal['project_before_pilot_committed_cny'])
        if not baseline.is_finite() or baseline<0 or baseline>=Decimal(grant['project_limit_cny']):raise ValueError('Finite prior project budget required')
        if baseline:
            if not Path(path).exists():raise ValueError('Original shared project ledger is missing')
            with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                previous=db.execute('SELECT reserved_cny,estimated_cny FROM reservations').fetchall()
                if self.committed(previous)+self.holds(db)<baseline:raise ValueError('Prior project spend cannot disappear in a fresh ledger')
        self.proposal,self.grant,self.proposal_sha256=proposal,deepcopy(grant),proposal_sha256
        self.proposal_body_hash=digest(proposal)
        self.identity=digest([proposal_sha256,grant]);self.prefix='v3-delivery-'+self.identity+':'
        self.project_limit=Decimal(grant['project_limit_cny']);self.batch_limit=Decimal(grant['max_estimated_cny'])
        super().__init__(path)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS v3_pilot_grants(id TEXT PRIMARY KEY,proposal_sha256 TEXT NOT NULL,grant_hash TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS v3_pilot_cases(run_key TEXT PRIMARY KEY,grant_id TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS v3_pilot_phase_ends(run_id TEXT PRIMARY KEY,grant_id TEXT NOT NULL,outcome TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS v3_pilot_reviews(reservation_id TEXT PRIMARY KEY,grant_id TEXT NOT NULL,reservation_hash TEXT NOT NULL,body TEXT NOT NULL)')
            old=db.execute('SELECT proposal_sha256,grant_hash FROM v3_pilot_grants WHERE id=?',(self.identity,)).fetchone()
            if old and old!=(proposal_sha256,digest(grant)):raise ValueError('Grant record changed')
            db.execute('INSERT OR IGNORE INTO v3_pilot_grants VALUES (?,?,?)',(self.identity,proposal_sha256,digest(grant)))

    def run_id(self,case,repeat,stage):return self.prefix+case+':r'+str(repeat)+':T'+str(stage)

    def parts(self,run_id):
        if not run_id.startswith(self.prefix):raise ValueError('Different authorization scope')
        values=run_id[len(self.prefix):].split(':')
        if len(values)!=3 or values[0] not in self.proposal['case_ids'] or values[1] not in ('r1','r2') or values[2] not in ('T0','T1','T2'):raise ValueError('Outside approved case/repeat/stage')
        return run_id.rsplit(':',1)[0]

    def _authority(self,db):
        if hashlib.sha256(self.proposal_path.read_bytes()).hexdigest()!=self.proposal_sha256 or digest(self.proposal)!=self.proposal_body_hash:
            raise ValueError('Original proposal changed')
        if self.project_limit!=Decimal(self.grant['project_limit_cny']) or self.batch_limit!=Decimal(self.grant['max_estimated_cny']):
            raise ValueError('Grant limits changed in memory')
        row=db.execute('SELECT proposal_sha256,grant_hash FROM v3_pilot_grants WHERE id=?',(self.identity,)).fetchone()
        if row!=(self.proposal_sha256,digest(self.grant)):raise ValueError('Stored grant binding changed')

    def validated_project_limit(self,run_id):
        self.parts(run_id)
        with closing(sqlite3.connect(self.path)) as db:self._authority(db)
        return self.project_limit

    def reserve(self,run_id,amount,*,run_budget,project_budget,max_requests):
        case_key=self.parts(run_id);amount=Decimal(amount)
        if not amount.is_finite() or amount<=0 or Decimal(run_budget)!=Decimal('.50') or Decimal(project_budget)!=self.project_limit or max_requests!=2:
            raise ValueError('Frozen request and case budgets required')
        if amount>Decimal('0.063104'):raise ValueError('Request reservation exceeds frozen input/output profile')
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            db.execute('BEGIN IMMEDIATE');self._authority(db)
            if db.execute('SELECT 1 FROM v3_pilot_phase_ends WHERE run_id=?',(run_id,)).fetchone():raise BudgetExceeded('terminal_phase_no_retry')
            allrows=db.execute('SELECT * FROM reservations').fetchall();batch=[r for r in allrows if r[1].startswith(self.prefix)]
            own=[r for r in batch if r[1]==run_id];case=[r for r in batch if r[1].rsplit(':',1)[0]==case_key]
            for row in batch:
                if row[4]=='estimated_from_usage':continue
                reviewed=db.execute('SELECT reservation_hash FROM v3_pilot_reviews WHERE reservation_id=? AND grant_id=?',(row[0],self.identity)).fetchone()
                if row[4]!='uncertain_reserved' or not reviewed or reviewed[0]!=digest(list(row)):
                    raise BudgetExceeded('uncertain_pilot_request_requires_review')
            if len(own)>=2 or len(case)>=6 or len(batch)>=72:raise BudgetExceeded('pilot_request_limit')
            def committed(rows):return self.committed([(r[2],r[3]) for r in rows])
            if committed(case)+self.holds(db,prefix=case_key)+amount>Decimal('.50'):raise BudgetExceeded('pilot_case_limit')
            if committed(batch)+self.holds(db,prefix=self.prefix)+amount>self.batch_limit:raise BudgetExceeded('pilot_round_limit')
            if committed(allrows)+self.holds(db)+amount>self.project_limit:raise BudgetExceeded('project_limit')
            db.execute('INSERT OR IGNORE INTO v3_pilot_cases VALUES (?,?)',(case_key,self.identity))
            if db.execute('SELECT COUNT(*) FROM v3_pilot_cases WHERE grant_id=?',(self.identity,)).fetchone()[0]>12:raise BudgetExceeded('pilot_case_slots')
            identity=uuid4().hex;db.execute("INSERT INTO reservations VALUES (?,?,?,NULL,'reserved',NULL)",(identity,run_id,str(amount)))
        return identity

    def close_phase(self,run_id,outcome):
        self.parts(run_id)
        if outcome not in ('delivered','model_failure','model_protocol_failure','provider_failure','execution_failure','source_failure','blocked_unknown','budget_failure','operator_cancelled'):raise ValueError('Typed phase outcome required')
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            self._authority(db);old=db.execute('SELECT outcome FROM v3_pilot_phase_ends WHERE run_id=?',(run_id,)).fetchone()
            if old and old[0]!=outcome:raise ValueError('Terminal phase cannot be relabeled')
            db.execute('INSERT OR IGNORE INTO v3_pilot_phase_ends VALUES (?,?,?)',(run_id,self.identity,outcome))

    def phase_outcome(self,run_id):
        self.parts(run_id)
        with closing(sqlite3.connect(self.path)) as db:
            self._authority(db);row=db.execute('SELECT outcome FROM v3_pilot_phase_ends WHERE run_id=?',(run_id,)).fetchone()
            return row[0] if row else None

    def review_transport_uncertainty(self,reservation_id,usage,*,reviewer,reason):
        if not reviewer or not isinstance(reason,str) or len(reason)<30:raise ValueError('Specific individual operator review required')
        audits=[e for e in usage.get('request_audit',[]) if e.get('reservation_id')==reservation_id]
        if len(audits)!=1 or audits[0].get('error_code')!='transport_error_unknown_charge' or audits[0].get('returned_model'):raise ValueError('Only inspected transport uncertainty is reviewable')
        with closing(sqlite3.connect(self.path,timeout=10)) as db,db:
            db.execute('BEGIN IMMEDIATE');self._authority(db)
            row=db.execute('SELECT * FROM reservations WHERE id=?',(reservation_id,)).fetchone()
            if not row or row[4]!='uncertain_reserved':raise ValueError('Terminal uncertain reservation required')
            case_key=self.parts(row[1]);existing=db.execute('SELECT reservation_hash,body FROM v3_pilot_reviews WHERE reservation_id=?',(reservation_id,)).fetchone()
            if existing:
                if existing[0]!=digest(list(row)):raise ValueError('Reviewed reservation changed')
                return json.loads(existing[1])
            own=db.execute('SELECT reserved_cny,estimated_cny FROM reservations WHERE run_id LIKE ?',(case_key+':%',)).fetchall()
            hold=max(Decimal(0),Decimal('.50')-self.committed(own)-self.holds(db,prefix=case_key))
            batch=db.execute('SELECT reserved_cny,estimated_cny FROM reservations WHERE run_id LIKE ?',(self.prefix+'%',)).fetchall();allrows=db.execute('SELECT reserved_cny,estimated_cny FROM reservations').fetchall()
            if self.committed(batch)+self.holds(db,prefix=self.prefix)+hold>self.batch_limit or self.committed(allrows)+self.holds(db)+hold>self.project_limit:raise BudgetExceeded('insufficient_uncertainty_hold')
            body=dict(reservation_id=reservation_id,reviewer=reviewer,reason=reason,additional_hold_cny=str(hold),
                full_case_allowance_includes_known_spend=True,provider_charge_still_unknown=True,automatic_retry=False)
            db.execute('INSERT INTO v3_pilot_reviews VALUES (?,?,?,?)',(reservation_id,self.identity,digest(list(row)),json.dumps(body)))
            db.execute('INSERT INTO budget_holds VALUES (?,?,?,?)',('v3-uncertain:'+reservation_id,case_key,str(hold),reason))
            return body

    def summary(self,run_id):
        case_key=self.parts(run_id);result=super().summary(run_id)
        with closing(sqlite3.connect(self.path)) as db:
            self._authority(db)
            case=db.execute('SELECT reserved_cny,estimated_cny FROM reservations WHERE run_id LIKE ?',(case_key+':%',)).fetchall()
            batch=db.execute('SELECT reserved_cny,estimated_cny FROM reservations WHERE run_id LIKE ?',(self.prefix+'%',)).fetchall()
            case_hold=self.holds(db,prefix=case_key);batch_hold=self.holds(db,prefix=self.prefix)
        result.update(case_committed_estimate_cny=str(self.committed(case)+case_hold),case_additional_risk_hold_cny=str(case_hold),
            batch_committed_estimate_cny=str(self.committed(batch)+batch_hold),batch_limit_cny=str(self.batch_limit),validated_project_limit_cny=str(self.project_limit))
        return result
