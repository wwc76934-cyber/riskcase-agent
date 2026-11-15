"""Atomic experiment-batch ceiling in the existing project cost ledger."""
from contextlib import closing
from decimal import Decimal
import sqlite3
from uuid import uuid4
from app.llm.cost_ledger import CostLedger, BudgetExceeded


class BatchLedger(CostLedger):
    def __init__(self,path,batch_id):
        super().__init__(path)
        if len(batch_id)!=32 or any(c not in '0123456789abcdef' for c in batch_id): raise ValueError('Batch identity')
        self.prefix='multisource-'+batch_id+':'

    def reserve(self,run_id,amount,*,run_budget,project_budget,max_requests):
        amount,run_budget,project_budget=map(Decimal,(amount,run_budget,project_budget))
        if not run_id.startswith(self.prefix) or not all(x.is_finite() for x in (amount,run_budget,project_budget)):
            raise ValueError('Invalid reservation')
        if not 0<amount or not 0<run_budget<=1 or not run_budget<=project_budget<=20 or not 1<=max_requests<=6:
            raise ValueError('Reservation bounds')
        with closing(sqlite3.connect(self.path,timeout=5)) as db,db:
            db.execute('BEGIN IMMEDIATE')
            own=db.execute('SELECT reserved_cny,estimated_cny FROM reservations WHERE run_id=?',(run_id,)).fetchall()
            batch=db.execute('SELECT run_id,reserved_cny,estimated_cny FROM reservations WHERE substr(run_id,1,?)=?',(len(self.prefix),self.prefix)).fetchall()
            all_rows=db.execute('SELECT reserved_cny,estimated_cny FROM reservations').fetchall()
            if len(own)>=max_requests: raise BudgetExceeded('request_limit')
            if not own and len({r[0] for r in batch})>=12: raise BudgetExceeded('batch_case_limit')
            if self.committed([(r[1],r[2]) for r in batch])+amount>Decimal('2'):
                raise BudgetExceeded('batch_estimated_spend_limit')
            if self.committed(own)+amount>run_budget or self.committed(all_rows)+amount>project_budget:
                raise BudgetExceeded('estimated_spend_limit')
            identity=uuid4().hex
            db.execute("INSERT INTO reservations VALUES (?,?,?,NULL,'reserved',NULL)",(identity,run_id,str(amount)))
        return identity
