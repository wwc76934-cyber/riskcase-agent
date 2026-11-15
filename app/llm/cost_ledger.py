"""Local atomic estimated-spend reservations, not a provider account balance."""

from contextlib import closing
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.llm.glm_client import LLMError


class BudgetExceeded(LLMError):
    pass


class CostLedger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS reservations (
                id TEXT PRIMARY KEY,run_id TEXT NOT NULL,reserved_cny TEXT NOT NULL,
                estimated_cny TEXT,status TEXT NOT NULL,usage_json TEXT)""")
            db.execute('CREATE TABLE IF NOT EXISTS budget_holds(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,amount_cny TEXT NOT NULL,reason TEXT NOT NULL)')

    @staticmethod
    def holds(db,*,run_id=None,prefix=None):
        rows=db.execute('SELECT amount_cny FROM budget_holds WHERE (? IS NULL OR run_id=?) AND (? IS NULL OR run_id LIKE ?)',
                        (run_id,run_id,prefix,None if prefix is None else prefix+'%')).fetchall()
        return sum((Decimal(row[0]) for row in rows),Decimal(0))

    @staticmethod
    def committed(rows):
        return sum((Decimal(estimate if estimate is not None else reserve) for reserve,estimate in rows), Decimal(0))

    def reserve(self, run_id, amount, *, run_budget, project_budget, max_requests):
        amount, run_budget, project_budget = map(Decimal, (amount, run_budget, project_budget))
        if not all(value.is_finite() for value in (amount,run_budget,project_budget)) or not 0 < amount or not 0 < run_budget <= 1 or not run_budget <= project_budget <= 20:
            raise ValueError("Finite positive local budgets within project authorization required")
        identity = uuid4().hex
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            own = db.execute("SELECT reserved_cny,estimated_cny FROM reservations WHERE run_id=?", (run_id,)).fetchall()
            all_rows = db.execute("SELECT reserved_cny,estimated_cny FROM reservations").fetchall()
            if len(own) >= max_requests:
                raise BudgetExceeded("request_limit")
            if self.committed(own)+self.holds(db,run_id=run_id)+amount > run_budget or self.committed(all_rows)+self.holds(db)+amount > project_budget:
                raise BudgetExceeded("estimated_spend_limit")
            db.execute("INSERT INTO reservations VALUES (?,?,?,NULL,'reserved',NULL)", (identity,run_id,str(amount)))
        return identity

    def settle(self, identity, *, estimate=None, usage=None):
        if estimate is not None:
            estimate = Decimal(estimate)
            if not estimate.is_finite() or estimate < 0:
                raise ValueError("Finite nonnegative usage estimate required")
        with closing(sqlite3.connect(self.path)) as db, db:
            previous = db.execute("SELECT status FROM reservations WHERE id=?", (identity,)).fetchone()
            if previous is None or previous[0] != "reserved":
                raise ValueError("Reservation must be settled exactly once")
            db.execute("UPDATE reservations SET estimated_cny=?,status=?,usage_json=? WHERE id=?",
                (str(estimate) if estimate is not None else None,
                 "estimated_from_usage" if estimate is not None else "uncertain_reserved",
                 json.dumps(usage) if usage is not None else None, identity))

    def summary(self, run_id):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT id,reserved_cny,estimated_cny,status,usage_json FROM reservations WHERE run_id=? ORDER BY rowid", (run_id,)).fetchall()
            total = db.execute("SELECT reserved_cny,estimated_cny FROM reservations").fetchall()
            own_hold,total_hold=self.holds(db,run_id=run_id),self.holds(db)
        entries = [{"reservation_id": identity,"reserved_cny": reserve,"estimated_cny": estimate,
            "status":status,"usage":json.loads(usage) if usage else None} for identity,reserve,estimate,status,usage in rows]
        return {"currency":"CNY", "entries":entries,
            "run_committed_estimate_cny":str(self.committed([(row[1],row[2]) for row in rows])+own_hold),
            "project_committed_estimate_cny":str(self.committed(total)+total_hold),
            "additional_risk_hold_cny":str(own_hold),"project_additional_risk_hold_cny":str(total_hold),
            "unknown_cost_requests":sum(row[3]!="estimated_from_usage" for row in rows),
            "limits":"Local estimated spend; uncertain requests keep reservations. Provider billing is authoritative."}
