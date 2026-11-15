"""One explicitly authorized upgrade scope, sharing the ORIGINAL project ledger."""
from contextlib import closing
from decimal import Decimal
import sqlite3
import json
from uuid import uuid4
from app.llm.cost_ledger import CostLedger,BudgetExceeded
from app.investigation.contracts import digest


def validate_budget_extension(authorization,amendment,extension):
    """The separately approved 10/25 -> 13/28 extension changes money only."""
    keys={'state','grant_message','base_authorization_hash','previous_amendment_hash','max_estimated_cny','project_limit_cny'}
    if not amendment or amendment.get('state')!='approved' or not amendment.get('grant_message') or amendment.get('max_estimated_cny')!='10' or amendment.get('base_authorization_hash')!=digest(authorization):
        raise ValueError('Financial extension requires the original bound 10-CNY amendment')
    if set(extension)!=keys or extension['state']!='approved' or not extension['grant_message'] or extension['base_authorization_hash']!=digest(authorization) or extension['previous_amendment_hash']!=digest(amendment) or extension['max_estimated_cny']!='13' or extension['project_limit_cny']!='28':
        raise ValueError('Explicit chained 13/28 authorization required; model/data/count scope is unchanged')


class WorldLedger(CostLedger):
    def __init__(self,path,authorization,*,budget_amendment=None,budget_extension=None):
        if authorization.get('state')!='approved' or not authorization.get('grant_message'):
            raise ValueError('Explicit upgrade authorization is required before any reservation')
        if authorization['max_estimated_cny']!='8' or authorization['project_limit_cny']!='25':
            raise ValueError('Bounded upgrade proposal required')
        if len(authorization.get('id',''))!=32 or any(c not in '0123456789abcdef' for c in authorization['id']):raise ValueError('Upgrade identity required')
        limits={'development':(24,{'B1','B2'},30),'main_eval':(180,{'B1','B2','B3'},60),'memory_eval':(60,{'raw_history','stale','wrong'},20)}
        if set(authorization.get('groups',{}))!=set(limits):raise ValueError('Frozen experiment groups required')
        for name,(runs,conditions,count) in limits.items():
            scope=authorization['groups'][name]
            if scope['max_runs']!=runs or set(scope['conditions'])!=conditions or len(set(scope['case_ids']))!=count or len(scope['case_ids'])!=count:
                raise ValueError('Authorized group bounds changed')
        self.authorization=authorization;self.prefix='world-upgrade-'+authorization['id']+':';self.batch_limit=Decimal('8');self.project_limit=Decimal('25')
        if budget_amendment is not None:
            if set(budget_amendment)!={'state','grant_message','base_authorization_hash','max_estimated_cny'} or budget_amendment['state']!='approved' or not budget_amendment['grant_message'] or budget_amendment['base_authorization_hash']!=digest(authorization) or budget_amendment['max_estimated_cny']!='10':
                raise ValueError('Explicit bounded 8-to-10 CNY amendment required; other scope cannot change')
            self.batch_limit=Decimal('10')
        if budget_extension is not None:
            validate_budget_extension(authorization,budget_amendment,budget_extension)
            self.batch_limit=Decimal('13');self.project_limit=Decimal('28')
        super().__init__(path)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS world_authorizations(id TEXT PRIMARY KEY,hash TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS world_execution_slots(run_id TEXT PRIMARY KEY,authorization_id TEXT NOT NULL,group_name TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS world_uncertain_reviews(reservation_id TEXT PRIMARY KEY,authorization_id TEXT NOT NULL,reservation_hash TEXT NOT NULL,body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS world_budget_amendments(id TEXT PRIMARY KEY,hash TEXT NOT NULL,body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS world_budget_extensions(id TEXT PRIMARY KEY,hash TEXT NOT NULL,body TEXT NOT NULL)')
            prior=db.execute('SELECT hash FROM world_authorizations WHERE id=?',(authorization['id'],)).fetchone()
            if prior and prior[0]!=digest(authorization):raise ValueError('Authorization content changed')
            db.execute('INSERT OR IGNORE INTO world_authorizations VALUES (?,?)',(authorization['id'],digest(authorization)))
            if budget_amendment is not None:
                old=db.execute('SELECT hash FROM world_budget_amendments WHERE id=?',(authorization['id'],)).fetchone()
                if old and old[0]!=digest(budget_amendment):raise ValueError('Budget amendment changed')
                db.execute('INSERT OR IGNORE INTO world_budget_amendments VALUES (?,?,?)',(authorization['id'],digest(budget_amendment),json.dumps(budget_amendment,ensure_ascii=False)))
            if budget_extension is not None:
                old=db.execute('SELECT hash FROM world_budget_extensions WHERE id=?',(authorization['id'],)).fetchone()
                if old and old[0]!=digest(budget_extension):raise ValueError('Financial extension changed')
                db.execute('INSERT OR IGNORE INTO world_budget_extensions VALUES (?,?,?)',(authorization['id'],digest(budget_extension),json.dumps(budget_extension,ensure_ascii=False)))

    def register(self,run_id):
        group,_,case,condition=self._parts(run_id)
        scope=self.authorization['groups'][group]
        with closing(sqlite3.connect(self.path,timeout=5)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM world_execution_slots WHERE run_id=?',(run_id,)).fetchone():return
            count=db.execute('SELECT COUNT(*) FROM world_execution_slots WHERE authorization_id=? AND group_name=?',(self.authorization['id'],group)).fetchone()[0]
            if count>=scope['max_runs']:raise BudgetExceeded('authorized_group_case_limit')
            db.execute('INSERT INTO world_execution_slots VALUES (?,?,?)',(run_id,self.authorization['id'],group))

    def _parts(self,run_id):
        if not run_id.startswith(self.prefix):raise ValueError('Wrong authorized scope')
        parts=run_id[len(self.prefix):].split(':')
        if len(parts)!=4:raise ValueError('Expected group:phase:case:condition')
        group,phase,case,condition=parts
        if not phase or not all(c.isalnum() or c in '-_' for c in phase):raise ValueError('Bounded phase identity required')
        scopes=self.authorization['groups']
        if group not in scopes or case not in scopes[group]['case_ids'] or condition not in scopes[group]['conditions']:
            raise ValueError('Run is outside authorized case/condition set')
        return group,phase,case,condition

    def review_transport_uncertainty(self,reservation_id,usage_receipt,*,reason,reviewer):
        """Operator-only review, never automatic retry or a claim of known usage.

        Retains the unknown reservation AND holds the rest of the entire .50 CNY
        case allowance. Original receipts remain unchanged. A new unknown request
        stops the next batch again; model substitutions cannot use this path.
        """
        if not reviewer or not isinstance(reason,str) or len(reason)<30:raise ValueError('Specific operator risk review required')
        audit=[e for e in usage_receipt.get('request_audit',[]) if e.get('reservation_id')==reservation_id]
        if len(audit)!=1 or audit[0].get('error_code')!='transport_error_unknown_charge' or audit[0].get('returned_model'):
            raise ValueError('Only individually inspected transport uncertainty is reviewable')
        with closing(sqlite3.connect(self.path,timeout=5)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM reservations WHERE id=?',(reservation_id,)).fetchone()
            if not row or row[4]!='uncertain_reserved':raise ValueError('A terminal uncertain reservation is required')
            self._parts(row[1]);reservation_hash=digest(list(row));buffer=max(Decimal('0'),Decimal('.50')-Decimal(row[2]))
            body=dict(reservation_id=reservation_id,reviewer=reviewer,reason=reason,audit=audit[0],
                      provider_charge_still_unknown=True,case_budget_hold_cny='.50',additional_hold_cny=str(buffer),automatic_retry=False)
            previous=db.execute('SELECT reservation_hash FROM world_uncertain_reviews WHERE reservation_id=?',(reservation_id,)).fetchone()
            if previous:
                if previous[0]!=reservation_hash:raise ValueError('Reviewed reservation changed')
                return body
            batch=db.execute('SELECT reserved_cny,estimated_cny FROM reservations WHERE run_id LIKE ?',(self.prefix+'%',)).fetchall()
            total=db.execute('SELECT reserved_cny,estimated_cny FROM reservations').fetchall()
            if self.committed(batch)+self.holds(db,prefix=self.prefix)+buffer>self.batch_limit or self.committed(total)+self.holds(db)+buffer>self.project_limit:
                raise BudgetExceeded('Insufficient budget for conservative uncertainty hold')
            db.execute('INSERT INTO world_uncertain_reviews VALUES (?,?,?,?)',(reservation_id,self.authorization['id'],reservation_hash,json.dumps(body,ensure_ascii=False)))
            db.execute('INSERT INTO budget_holds VALUES (?,?,?,?)',('uncertain:'+reservation_id,row[1],str(buffer),reason))
            return body

    def reserve(self,run_id,amount,*,run_budget,project_budget,max_requests):
        group,phase,case,condition=self._parts(run_id)
        scopes=self.authorization['groups']
        self.register(run_id)
        amount,run_budget,project_budget=map(Decimal,(amount,run_budget,project_budget))
        if not all(x.is_finite() for x in (amount,run_budget,project_budget)) or not 0<amount or not 0<run_budget<=Decimal('0.5') or project_budget!=self.project_limit or not 1<=max_requests<=6:
            raise ValueError('Invalid bounded reservation')
        with closing(sqlite3.connect(self.path,timeout=5)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            all_rows=db.execute('SELECT run_id,reserved_cny,estimated_cny,status,id FROM reservations').fetchall()
            batch=[r for r in all_rows if r[0].startswith(self.prefix)]
            own=[r for r in batch if r[0]==run_id]
            grouped=[r for r in batch if r[0].startswith(self.prefix+group+':')]
            for r in batch:
                if r[3]=='estimated_from_usage':continue
                original=db.execute('SELECT * FROM reservations WHERE id=?',(r[4],)).fetchone()
                reviewed=db.execute('SELECT reservation_hash FROM world_uncertain_reviews WHERE reservation_id=? AND authorization_id=?',(r[4],self.authorization['id'])).fetchone()
                if r[3]!='uncertain_reserved' or not reviewed or reviewed[0]!=digest(list(original)):raise BudgetExceeded('unknown_upgrade_charge_requires_review')
            if len(own)>=max_requests:raise BudgetExceeded('request_limit')
            if not own and len({r[0] for r in grouped})>=scopes[group]['max_runs']:raise BudgetExceeded('authorized_group_case_limit')
            if len(batch)>=1584:raise BudgetExceeded('upgrade_request_limit')
            if self.committed([(r[1],r[2]) for r in batch])+self.holds(db,prefix=self.prefix)+amount>self.batch_limit:raise BudgetExceeded('upgrade_estimated_spend_limit')
            if self.committed([(r[1],r[2]) for r in own])+self.holds(db,run_id=run_id)+amount>run_budget or self.committed([(r[1],r[2]) for r in all_rows])+self.holds(db)+amount>project_budget:
                raise BudgetExceeded('estimated_spend_limit')
            identity=uuid4().hex
            db.execute("INSERT INTO reservations VALUES (?,?,?,NULL,'reserved',NULL)",(identity,run_id,str(amount)))
        return identity
