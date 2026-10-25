"""Expose only the bound history query to an Agent, not files, SQL or labels."""

from datetime import datetime
import json
import sqlite3

from app.risk.history import UserHistoryTool


class HistoryTools:
    def __init__(self, history: UserHistoryTool):
        self.history = history

    @property
    def schemas(self) -> list[dict]:
        return [{"type": "function", "function": {
            "name": "query_user_history",
            "description": "Read source-local history [as_of-lookback_hours, as_of). Check coverage before conclusions.",
            "parameters": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "user_id": {"type": "string"}, "as_of": {"type": "string"},
                    "lookback_hours": {"type": "integer", "minimum": 1, "maximum": 168},
                    "evidence_limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["user_id", "as_of"],
            },
        }}]

    def dispatch(self, name: str, arguments_json: str) -> dict:
        try:
            if name != "query_user_history" or not isinstance(arguments_json, str):
                raise ValueError("unsupported tool or arguments type")
            arguments = json.loads(arguments_json)
            allowed = {"user_id", "as_of", "lookback_hours", "evidence_limit"}
            if not isinstance(arguments, dict) or set(arguments) - allowed:
                raise ValueError("unknown tool parameters")
            if not {"user_id", "as_of"} <= arguments.keys():
                raise ValueError("missing user_id/as_of")
            if not isinstance(arguments["as_of"], str):
                raise ValueError("as_of must be text")
            arguments["as_of"] = datetime.fromisoformat(arguments["as_of"])
            return {"ok": True, "data": self.history.query(**arguments)}
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error_type": "ToolInputError", "error": str(exc)}
        except sqlite3.Error:
            return {"ok": False, "error_type": "ToolExecutionError", "error": "history query failed"}
