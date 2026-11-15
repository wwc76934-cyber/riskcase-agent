"""HTTP service for the semantic assessment: mask, screen, extract, check quotes, decide, audit.

    uvicorn app.semantic.service:app --host 127.0.0.1 --port 8800

The service binds to the loopback address in every example. It keeps no raw conversation text: the audit
row holds a hash of the masked text, the signals with their quotes (already masked), the action and the
model identity. The model client is injected so tests need no network.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.investigation.v3.pii import mask
from app.investigation.v3.untrusted_text import screen
from app.semantic.extractor import PROMPT_VERSION, extract
from app.semantic.policy import decide

MODEL = os.environ.get("SEMANTIC_MODEL", "Qwen/Qwen3.8-27B")
AUDIT = """CREATE TABLE IF NOT EXISTS assessments(
  request_id TEXT PRIMARY KEY, ts REAL, text_sha256 TEXT, model TEXT, prompt_version TEXT, action TEXT, score INTEGER,
  signals TEXT, injection_findings TEXT, masked TEXT, extraction_ok INTEGER, dropped_signals INTEGER, elapsed REAL)"""


class Assessment(BaseModel):
    conversation: str = Field(min_length=10, max_length=8000)
    amount_cny: float | None = Field(default=None, ge=0)


def create_app(llm=None, model=MODEL, audit_path="runs/semantic-audit.sqlite"):
    app = FastAPI(title="RiskCase semantic assessment", version="1")
    audit_path = Path(audit_path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(audit_path)) as db, db:
        db.execute(AUDIT)

    @app.get("/health")
    def health():
        return dict(status="ok", model=model, prompt_version=PROMPT_VERSION, llm_configured=llm is not None)

    @app.post("/v1/assess")
    def assess(body: Assessment):
        if llm is None:
            raise HTTPException(503, "model client not configured")
        begin = time.monotonic()
        masked_text, masked = mask(body.conversation)
        injection = screen(masked_text)
        result = extract(llm, model, masked_text, purpose="service")
        if not result["ok"]:
            # fail safe: an unreadable model reply never releases a payment on its own
            decision = dict(action="STEP_UP_VERIFICATION", score=None, core=[], support=[], protective=[])
            signals = []
        else:
            signals = result["signals"]
            decision = decide([s["name"] for s in signals])
            if injection and decision["action"] == "RELEASE":
                decision = dict(decision, action="STEP_UP_VERIFICATION")  # instruction-like text is itself suspicious
        request_id = uuid.uuid4().hex
        elapsed = time.monotonic() - begin
        with closing(sqlite3.connect(audit_path)) as db, db:
            db.execute("INSERT INTO assessments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                request_id, time.time(), hashlib.sha256(masked_text.encode()).hexdigest(), model, PROMPT_VERSION,
                decision["action"], decision["score"], json.dumps(signals, ensure_ascii=False), json.dumps(injection),
                json.dumps(masked), int(result["ok"]), result.get("dropped_signals", 0), elapsed))
        return dict(request_id=request_id, action=decision["action"], score=decision["score"], signals=signals,
                    protective=decision["protective"], injection_findings=injection, pii_masked=masked,
                    model_says_fraud=result.get("is_fraud"), fraud_type=result.get("fraud_type"),
                    extraction_ok=result["ok"], prompt_version=PROMPT_VERSION, elapsed_seconds=round(elapsed, 3))

    return app


def _default_app():
    try:
        from app.semantic.llm import LLM, Budget
        return create_app(LLM(Budget("runs/semantic-ledger.sqlite", os.environ.get("SEMANTIC_CAP_CNY", "5"))))
    except Exception:  # no credential: the app still starts and reports 503 on /v1/assess
        return create_app(None)


app = _default_app() if os.environ.get("SEMANTIC_SERVICE_AUTOSTART") == "1" else create_app(None)
