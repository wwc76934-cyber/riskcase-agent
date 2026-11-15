"""One-off: mark the early HTTP-refused calls as unbilled. A non-200 answer means the request was refused.

Before the client change, such calls were recorded at worst-case cost under status 'unknown:RuntimeError'
(the only RuntimeError raised in the client was the non-200 branch).
"""
import sqlite3

db = sqlite3.connect("runs/semantic-ledger.sqlite", timeout=30)
db.execute("UPDATE calls SET cny='0', status='http_refused_unbilled' WHERE status='unknown:RuntimeError'")
db.execute("UPDATE calls SET cny='0', status='stopped_before_send' WHERE status='reserved' AND prompt_tokens=0")
db.commit()
print(db.execute("SELECT status, COUNT(*), SUM(CAST(cny AS REAL)) FROM calls GROUP BY status").fetchall())
