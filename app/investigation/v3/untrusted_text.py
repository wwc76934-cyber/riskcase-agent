"""Screen customer or merchant text for instruction-like content before it reaches a prompt.

The investigation treats every source text as data. This module reports patterns that
look like instructions to the model or like attempts to name an action or a verdict.
It does not rewrite or delete text; the caller records the finding and may refuse the
source, mark it untrusted, or pass it through unchanged.

Pattern lists are short and illustrative. A determined attacker can phrase around
them, so this is one layer in front of the Policy Guard, which does not depend on it.
"""
import re

PATTERNS = {
    "ignore_instructions": re.compile(
        r"(忽略|无视|不要理会|忘记).{0,8}(以上|之前|前面|上述|所有).{0,6}(指令|提示|规则|要求)"
        r"|ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules)", re.I),
    "role_override": re.compile(r"(你现在是|你扮演|从现在起你|system\s*prompt|\bsystem\s*:)", re.I),
    "verdict_dictation": re.compile(
        r"(请|务必|必须|直接).{0,6}(判定|输出|返回|标记).{0,10}(授权|已授权|safe_close|close_alert|source_question_resolved)", re.I),
    "action_dictation": re.compile(
        r"(safe_close|confirm_fraud|confirmed_fraud|freeze_account|refund|write_fraud_label)", re.I),
    "tool_call_shape": re.compile(r"\"?(tool_calls?|function_call)\"?\s*[:=]|<\s*tool", re.I),
    "schema_escape": re.compile(r"```|\"proposed_disposition\"|\"stop_reason\"|\"source_statement\""),
}


def screen(text):
    """Return the sorted names of patterns found in ``text``."""
    if not isinstance(text, str):
        return []
    return sorted(name for name, pattern in PATTERNS.items() if pattern.search(text))


def screen_records(records):
    """Map ``source_id`` to findings for records whose text looks like instructions."""
    found = {}
    for record in records:
        hits = screen(record.get("text", ""))
        if hits:
            found[record["source_id"]] = hits
    return found
