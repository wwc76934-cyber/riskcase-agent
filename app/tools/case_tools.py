"""Bounded read-only case tools; merchant distribution requires explicit opt-in."""

from contextlib import closing
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from app.risk.case_snapshot import COLUMNS, local_time
from app.risk.history import sha256_file
from app.risk.merchant_stats import merchant_distribution


def strict_json(text):
    if not isinstance(text, str) or len(text) > 16000:
        raise ValueError("Bounded JSON text required")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON keys")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("Nonfinite JSON number")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


class CaseTools:
    def __init__(self, manifest_path: Path, *, max_calls=6, allow_merchant_distribution=False):
        if type(allow_merchant_distribution) is not bool: raise ValueError("Explicit tool opt-in required")
        self.allow_merchant_distribution = allow_merchant_distribution
        if type(max_calls) is not int or not 1 <= max_calls <= 12:
            raise ValueError("Tool attempt budget must be 1..12")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        path = manifest_path.parent / "facts.sqlite"
        if manifest["status"] != "case_ready" or sha256_file(path) != manifest["projection_sha256"]:
            raise ValueError("Case projection not ready or changed")
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in db.execute(f"SELECT {','.join(COLUMNS)} FROM transactions ORDER BY event_time,source_row LIMIT 100002")]
        self._bind(manifest, rows, max_calls)

    @classmethod
    def from_snapshot(cls, manifest, rows, *, max_calls=12):
        """Host-frozen projection only; retain the same scope/temporal validation."""
        if type(max_calls) is not int or not 1 <= max_calls <= 12:
            raise ValueError("Tool attempt budget must be 1..12")
        instance = cls.__new__(cls)
        instance.allow_merchant_distribution = False
        instance._bind(deepcopy(manifest), deepcopy(rows), max_calls)
        return instance

    def _bind(self, manifest, rows, max_calls):
        self.manifest, self.max_calls, self.trace, self._evidence = manifest, max_calls, [], {}
        self.attempts = 0
        self.cutoff, self.start = local_time(manifest["cutoff"]), local_time(manifest["history_start"])
        if not rows or any(set(row) != set(COLUMNS) for row in rows):
            raise ValueError("Only label-free projection columns allowed")
        if len({row["transaction_id"] for row in rows}) != len(rows):
            raise ValueError("Duplicate transaction identity")
        targets = [row for row in rows if row["transaction_id"] == manifest["target_transaction_id"]]
        if len(targets) != 1 or len(rows) != manifest["prior_rows"]+1 or len(rows) > 100001:
            raise ValueError("Case row identities/counts disagree")
        self.target = targets[0]
        self.prior = [row for row in rows if row["transaction_id"] != manifest["target_transaction_id"]]
        if local_time(self.target["event_time"]) != self.cutoff:
            raise ValueError("Target cutoff disagrees")
        for row in rows:
            if row["user_id"] != manifest["user_id"] or type(row["amount_cents"]) is not int:
                raise ValueError("Cross-user or invalid amount in case projection")
        if any(not self.start <= local_time(row["event_time"]) < self.cutoff for row in self.prior):
            raise ValueError("Future/equal-time/out-of-scope prior row")

    @property
    def evidence(self):
        return deepcopy(self._evidence)

    @property
    def schemas(self):
        result = []
        descriptions = {
            "get_alert_transaction": "Read the bound alert transaction. A score is not a fraud verdict or causal explanation.",
            "summarize_history": "Exact prior same-user window; signed sums in 0.01 source units. Check completeness; previews are not the full aggregate.",
            "query_target_merchant": "Check the target merchant ID in prior same-user history; IDs are not proven unique real stores.",
        }
        if self.allow_merchant_distribution:
            descriptions["summarize_merchant_distribution"] = "Bound user prior merchant distribution; count shares include all records, amount shares positive amounts only. Check coverage/sample counts."
        for name, description in descriptions.items():
            parameters = {"type": "object", "properties": {}, "additionalProperties": False, "required": []}
            if name != "get_alert_transaction":
                parameters["properties"] = {"lookback_hours": {"type": "integer", "enum": [24,168,720] if name=="summarize_merchant_distribution" else [1,24,168,720]}}
                parameters["required"] = ["lookback_hours"]
            result.append({"type": "function", "function": {"name": name, "description": description, "parameters": parameters}})
        return result

    def dispatch(self, name, arguments_json):
        self.attempts += 1
        attempt = self.attempts
        if attempt > self.max_calls:
            result = {"ok": False, "error_type": "ToolBudgetExceeded", "error": "Case tool attempt budget exhausted"}
            self.trace.append({"attempt": attempt, "tool": name, "arguments": None, "result": deepcopy(result)})
            return result
        arguments = None
        try:
            arguments = strict_json(arguments_json)
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            if name not in {schema["function"]["name"] for schema in self.schemas}:
                raise ValueError("Unsupported case tool")
            if name == "get_alert_transaction":
                if arguments:
                    raise ValueError("Bound target accepts no parameters")
                data = {"tool": name, "transaction": deepcopy(self.target), "alert": deepcopy(self.manifest["alert"])}
            elif name in {"summarize_history", "query_target_merchant"} or name=="summarize_merchant_distribution" and self.allow_merchant_distribution:
                allowed = {24,168,720} if name=="summarize_merchant_distribution" else {1,24,168,720}
                if set(arguments) != {"lookback_hours"} or type(arguments["lookback_hours"]) is not int or arguments["lookback_hours"] not in allowed:
                    raise ValueError("Only the advertised integer lookback_hours allowed")
                hours = arguments["lookback_hours"]
                begin = self.cutoff-timedelta(hours=hours)
                rows = [row for row in self.prior if local_time(row["event_time"]) >= begin]
                complete = begin >= self.start and self.manifest["full_source_scanned"] is True
                data = {"tool": name, "lookback_hours": hours, "window_start_inclusive": begin.isoformat(),
                    "window_end_exclusive": self.cutoff.isoformat(), "complete_within_source": complete,
                    "prior_count": len(rows), "incomplete_reasons": [] if complete else ["history_coverage_not_proven"]}
                if name == "summarize_merchant_distribution":
                    data.update(merchant_distribution(rows))
                elif name == "query_target_merchant":
                    rows = [row for row in rows if row["merchant_id"] == self.target["merchant_id"]]
                    data.update(merchant_id=self.target["merchant_id"], merchant_prior_count=len(rows))
                else:
                    positives = [row["amount_cents"] for row in rows if row["amount_cents"] > 0]
                    data.update(amount_sum_cents=sum(row["amount_cents"] for row in rows),
                        positive_amount_max_cents=max(positives) if positives else None,
                        target_amount_cents=self.target["amount_cents"])
                data.update(preview=deepcopy(rows[:3]), preview_truncated=len(rows)>3)
            else:
                raise ValueError("Unsupported case tool")
            data.update(projection_sha256=self.manifest["projection_sha256"], currency="unspecified",
                time_basis=self.manifest["time_basis"])
            identity = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
            data["evidence_id"] = f"ev-{identity}"
            self._evidence[data["evidence_id"]] = deepcopy(data)
            result = {"ok": True, "data": data}
        except (ValueError, TypeError, KeyError) as exc:
            result = {"ok": False, "error_type": "ToolInputError", "error": str(exc)}
        self.trace.append({"attempt": attempt, "tool": name, "arguments": arguments, "result": deepcopy(result)})
        return result
