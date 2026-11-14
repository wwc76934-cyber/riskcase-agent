"""Auditable presentation normalization; never salvage embedded JSON from prose."""
import json
import re
from app.tools.case_tools import strict_json


def parse_model_json(raw):
    try:
        return strict_json(raw), 'strict_json'
    except ValueError:
        if not isinstance(raw, str) or len(raw) > 16000:
            raise ValueError('Bounded model output required')
        match = re.fullmatch(r'\s*```(?:json)?\r?\n([\s\S]*?)\r?\n```\s*', raw)
        if not match: raise ValueError('Expected JSON or exactly one JSON code fence')
        return strict_json(match[1]), 'single_json_fence'


def canonical_model_json(raw):
    body, mode = parse_model_json(raw)
    return json.dumps(body, ensure_ascii=False, allow_nan=False), mode
