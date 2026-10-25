"""Independent source SQL observations. No detection labels or runtime aggregators."""

from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from app.risk.case_snapshot import COLUMNS
from app.risk.history import sha256_file


class SourceAudit:
    def __init__(self, cohort_report):
        self.report = Path(cohort_report)
        self.cohort = json.loads(self.report.read_text(encoding="utf-8"))
        self.source = self.report.parent / "dataset/facts.sqlite"
        self.report_hash = sha256_file(self.report)
        self.source_hash = sha256_file(self.source)
        if self.source_hash != self.cohort["artifact_sha256"]["dataset/facts.sqlite"]: raise ValueError("Source hash mismatch")
        self.db = sqlite3.connect(self.source.resolve().as_uri()+"?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA query_only=ON")

    def close(self): self.db.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()

    def target(self, manifest):
        row = self.db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions WHERE transaction_id=? AND split='validation' AND event_time>='2018-01-01' AND event_time<'2019-01-01'", (manifest["target_transaction_id"],)).fetchone()
        if row is None: raise ValueError("Only development source targets permitted")
        row = dict(row)
        if row["user_id"] != manifest["user_id"] or row["event_time"] != manifest["cutoff"]: raise ValueError("Target binding mismatch")
        return row

    def projection(self, manifest, database):
        if manifest["source_facts_sha256"] != self.source_hash or manifest["source_report_sha256"] != self.report_hash: raise ValueError("Source provenance differs")
        if sha256_file(database) != manifest["projection_sha256"]: raise ValueError("Projection hash differs")
        if manifest["full_source_scanned"] and "coverage" in self.cohort:
            expected_start = max(self.cohort["coverage"]["warmup_start"], (datetime.fromisoformat(manifest["cutoff"])-timedelta(days=30)).isoformat())
            if manifest["history_start"] != expected_start or self.cohort.get("source_csv_fully_scanned") is not True:
                raise ValueError("Complete coverage lacks source proof")
        rows = [dict(row) for row in self.db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions WHERE user_id=? AND event_time>=? AND event_time<? ORDER BY event_time,source_row",
                (manifest["user_id"], manifest["history_start"], manifest["cutoff"]))] + [self.target(manifest)]
        with closing(sqlite3.connect(Path(database).resolve().as_uri()+"?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            if [r[1] for r in db.execute("PRAGMA table_info(transactions)")] != list(COLUMNS): raise ValueError("Unexpected fields")
            if {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} != {"transactions"}: raise ValueError("Unexpected tables")
            actual = [dict(row) for row in db.execute("SELECT * FROM transactions")]
        if sorted(rows, key=lambda r: r["transaction_id"]) != sorted(actual, key=lambda r: r["transaction_id"]): raise ValueError("Projection/source join mismatch")
        return len(rows)

    def observation(self, manifest, name, arguments):
        target = self.target(manifest)
        if name == "get_alert_transaction":
            if arguments != {}: raise ValueError("Target arguments differ")
            data = {"tool": name, "transaction": target, "alert": manifest["alert"]}
        else:
            hours = arguments.get("lookback_hours")
            allowed = (24, 168, 720) if name == "summarize_merchant_distribution" else (1, 24, 168, 720)
            if name not in ("summarize_history", "query_target_merchant", "summarize_merchant_distribution") or set(arguments) != {"lookback_hours"} or type(hours) is not int or hours not in allowed: raise ValueError("Invalid observation query")
            begin = (datetime.fromisoformat(manifest["cutoff"])-timedelta(hours=hours)).isoformat()
            complete = begin >= manifest["history_start"] and manifest["full_source_scanned"] is True
            predicate = "user_id=? AND event_time>=? AND event_time<?"
            params = (manifest["user_id"], max(begin, manifest["history_start"]), manifest["cutoff"])
            aggregate = self.db.execute(f"SELECT COUNT(*) AS n,COALESCE(SUM(amount_cents),0) AS net,MAX(CASE WHEN amount_cents>0 THEN amount_cents END) AS positive_max FROM transactions WHERE {predicate}", params).fetchone()
            data = {"tool": name, "lookback_hours": hours, "window_start_inclusive": begin, "window_end_exclusive": manifest["cutoff"],
                    "complete_within_source": complete, "prior_count": aggregate["n"], "incomplete_reasons": [] if complete else ["history_coverage_not_proven"]}
            if name == "summarize_history":
                data.update(amount_sum_cents=aggregate["net"], positive_amount_max_cents=aggregate["positive_max"], target_amount_cents=target["amount_cents"])
            elif name == "query_target_merchant":
                predicate += " AND merchant_id=?"; params += (target["merchant_id"],)
                count = self.db.execute(f"SELECT COUNT(*) FROM transactions WHERE {predicate}", params).fetchone()[0]
                data.update(merchant_id=target["merchant_id"], merchant_prior_count=count)
            else:
                groups = [dict(row) for row in self.db.execute(f"SELECT merchant_id,COUNT(*) AS n,SUM(CASE WHEN amount_cents>0 THEN 1 ELSE 0 END) AS pn,SUM(CASE WHEN amount_cents>0 THEN amount_cents ELSE 0 END) AS ps,SUM(CASE WHEN amount_cents<0 THEN 1 ELSE 0 END) AS nn,SUM(CASE WHEN amount_cents<0 THEN amount_cents ELSE 0 END) AS ns,SUM(CASE WHEN amount_cents=0 THEN 1 ELSE 0 END) AS zn FROM transactions WHERE {predicate} GROUP BY merchant_id", params)]
                counts = sorted(groups, key=lambda r: (-r["n"], r["merchant_id"]))
                amounts = sorted((r for r in groups if r["pn"]), key=lambda r: (-r["ps"], r["merchant_id"]))
                n, ps = sum(r["n"] for r in groups), sum(r["ps"] for r in groups)
                data.update(merchant_count=len(groups), record_count=n, count_denominator=n, positive_record_count=sum(r["pn"] for r in groups),
                            positive_amount_sum_cents=ps, positive_amount_denominator_cents=ps, negative_record_count=sum(r["nn"] for r in groups),
                            negative_amount_sum_cents=sum(r["ns"] for r in groups), zero_record_count=sum(r["zn"] for r in groups),
                            top1_count_numerator=counts[0]["n"] if counts else 0, top1_positive_amount_cents=amounts[0]["ps"] if amounts else 0,
                            top_by_count=[{"merchant_id": r["merchant_id"], "record_count": r["n"]} for r in counts[:3]],
                            top_by_positive_amount=[{"merchant_id": r["merchant_id"], "positive_amount_cents": r["ps"]} for r in amounts[:3]],
                            merchant_preview_truncated=len(groups)>3,
                            semantics="Count includes positive/zero/negative records; amount shares use positive amounts only. Merchant IDs need not be real stores.")
            rows = [dict(r) for r in self.db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions WHERE {predicate} ORDER BY event_time,source_row LIMIT 4", params)]
            data.update(preview=rows[:3], preview_truncated=len(rows)>3)
        data.update(projection_sha256=manifest["projection_sha256"], currency="unspecified", time_basis=manifest["time_basis"])
        data["evidence_id"] = "ev-" + hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
        return data

    def gold(self, manifest, question):
        """Assessment code is independent from runtime expected_assessment/compare_distributions."""
        result = {}
        for check in question.checks:
            if check == "frequency":
                row = self.observation(manifest, "summarize_history", {"lookback_hours": 1})
                result[check] = "supported" if row["prior_count"] >= 4 else "not_supported" if row["complete_within_source"] else "insufficient_evidence"
            elif check == "amount":
                row = self.observation(manifest, "summarize_history", {"lookback_hours": 720})
                result[check] = "insufficient_evidence" if not row["complete_within_source"] or row["target_amount_cents"] <= 0 or row["positive_amount_max_cents"] is None else "supported" if row["target_amount_cents"] > row["positive_amount_max_cents"] else "not_supported"
            elif check == "merchant":
                row = self.observation(manifest, "query_target_merchant", {"lookback_hours": 720})
                result[check] = "not_supported" if row["merchant_prior_count"] else "supported" if row["complete_within_source"] and row["prior_count"] else "insufficient_evidence"
            else:
                short, long = (self.observation(manifest, "summarize_merchant_distribution", {"lookback_hours": h}) for h in (168, 720))
                n, d, sample = ("top1_count_numerator", "count_denominator", "record_count") if check == "count_share" else ("top1_positive_amount_cents", "positive_amount_denominator_cents", "positive_record_count")
                if any(not r["complete_within_source"] or r[sample] < question.min_records or r[d] <= 0 for r in (short, long)): result[check] = "insufficient_evidence"
                else:
                    diff = short[n]*long[d] - long[n]*short[d]
                    result[check] = "increased" if diff > 0 else "decreased" if diff < 0 else "unchanged"
        return result
