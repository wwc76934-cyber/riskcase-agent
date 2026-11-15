"""Authenticated localhost API. Inputs select registered cases, never paths/SQL."""

import hmac
import json
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.risk.merchant_research import MerchantQuestion
from app.risk.mixed_investigation import CHECKS, MixedQuestion as MixedContract
from app.service.store import Conflict, digest
from app.risk.intent_router import NaturalQuestion as NaturalContract


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    short_hours: Literal[24, 168] = 168
    long_hours: Literal[168, 720] = 720
    min_records: int = Field(default=10, ge=1, le=1000)

    @model_validator(mode="after")
    def ordered(self):
        MerchantQuestion(**self.model_dump())
        return self


class MixedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    checks: list[Literal["frequency", "amount", "merchant", "count_share", "amount_share"]] = Field(default_factory=lambda: list(CHECKS), min_length=1, max_length=5)
    min_records: int = Field(default=10, ge=1, le=1000)

    @model_validator(mode="after")
    def contract(self):
        MixedContract(**self.model_dump())
        return self


class NaturalQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=800)

    @model_validator(mode="after")
    def contract(self):
        NaturalContract(self.text)
        return self


class TriageQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    policy_version: Literal['first-lead-v1'] = 'first-lead-v1'


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    mode: Literal["offline", "live"] = "offline"
    task_type: Literal["merchant", "mixed", "natural", "natural_v2", "triage"] = "merchant"
    question: Question | MixedQuestion | NaturalQuestion | TriageQuestion = Field(default_factory=Question)

    @model_validator(mode="after")
    def matching_type(self):
        if not isinstance(self.question, {"mixed": MixedQuestion, "merchant": Question, "natural": NaturalQuestion, "natural_v2": NaturalQuestion,'triage':TriageQuestion}[self.task_type]): raise ValueError("Question must match task type")
        return self


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=1)
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["accept_report", "reject_report"]


class PlanReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_version: int = Field(ge=1)
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["confirm_plan", "reject_plan"]


class LessonProposal(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_job: str = Field(min_length=1, max_length=100)
    result_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    identity: Literal['coverage', 'amount', 'merchant']
    body: str = Field(min_length=1, max_length=4000)
    feedback: str = Field(min_length=1, max_length=1000)


class Promotion(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    evaluation_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


def create_app(service):
    app = FastAPI(title="RiskCase research tasks", version="1", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        expected = ("Bearer " + service.settings.token).encode()
        public_page = request.method == "GET" and request.url.path in ("/", "/investigations/page", "/baseline/page")
        if not public_page and not hmac.compare_digest(request.headers.get("authorization", "").encode(), expected):
            return JSONResponse({"error": "authentication_required"}, status_code=401)
        if request.method in ("POST", "PUT", "PATCH"):
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 8192: return JSONResponse({"error": "body_too_large"}, status_code=413)
            if body:
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return JSONResponse({"error": "json_required"}, status_code=415)
                def pairs(items):
                    result = {}
                    for key, value in items:
                        if key in result: raise ValueError("duplicate")
                        result[key] = value
                    return result
                def constant(_): raise ValueError("nonfinite")
                try: json.loads(body, object_pairs_hook=pairs, parse_constant=constant)
                except (ValueError, UnicodeError, RecursionError):
                    return JSONResponse({"error": "invalid_json"}, status_code=400)
            request._body = bytes(body)
        try:
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            return response
        except KeyError: return JSONResponse({"error": "not_found"}, status_code=404)
        except Conflict as exc: return JSONResponse({"error": str(exc)}, status_code=409)
        except (ValueError, OSError): return JSONResponse({"error": "invalid_or_unavailable_input"}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Never reflect arbitrary request values (including accidentally pasted keys).
        return JSONResponse({"error": "invalid_request_schema"}, status_code=422)

    def request_key(request):
        value = request.headers.get("idempotency-key", "")
        if not 8 <= len(value) <= 128 or not all(c.isascii() and (c.isalnum() or c in "-_") for c in value):
            raise ValueError("idempotency_key_required")
        return value

    def public(identity):
        row = service.store.get(identity)
        return {key: row[key] for key in ("id", "state", "version", "generation", "created", "updated", "reason", "result", "result_hash")}

    @app.get("/", response_class=HTMLResponse)
    def workbench():
        if getattr(service,'world_catalog',{}):
            from app.service.investigation_page import HTML
            return HTML
        return baseline_page()

    @app.get('/baseline/page',response_class=HTMLResponse)
    def baseline_page():
        from app.service.workbench import HTML
        return HTML.replace('</header>','<p><a style="color:white" href="/investigations/page">进入欺诈告警调查与历史实验（免费接线）</a></p></header>')

    @app.get("/health")
    def health(): return {"status": "ok", "live_enabled": service.settings.allow_live, "data_kind": "synthetic"}

    @app.get("/cases")
    def cases(): return {"case_ids": sorted(service.settings.catalog)}

    @app.get("/cases/details")
    def case_details():
        result = []
        for identity, manifest_path in sorted(service.settings.catalog.items()):
            from pathlib import Path
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            alert = manifest["alert"]
            result.append({"case_id": identity, "cutoff": manifest["cutoff"], "data_kind": alert.get("data_kind", "IBM_TabFormer_synthetic"),
                           "ranking_score": alert["score"], "selection": alert.get("selection", "frozen_development_alert")})
        return {"cases": result}

    @app.get("/jobs")
    def jobs(limit: int = 25): return {"jobs": service.store.list_jobs(limit)}

    @app.get('/knowledge/search')
    def knowledge_search(query: str):
        from app.tools.knowledge_tools import FrozenKnowledge
        rows = service.experience.corpus()
        return {'corpus_hash': digest(rows), 'advisory_only': True, 'hits': FrozenKnowledge(rows).search(query)}

    @app.post('/knowledge/candidates')
    def propose_lesson(body: LessonProposal):
        job = service.store.get(body.source_job)
        if job['state'] != 'rejected' or job['result_hash'] != body.result_hash:
            raise Conflict('rejected_report_with_current_hash_required')
        service.artifact(body.source_job, 'report.md')
        return service.experience.propose(**body.model_dump())

    @app.get('/knowledge/candidates/{identity}')
    def lesson(identity: str): return service.experience.get(identity)

    @app.post('/knowledge/candidates/{identity}/evaluate')
    def evaluate_lesson(identity: str): return service.experience.evaluate(identity)

    @app.post('/knowledge/candidates/{identity}/promote')
    def promote_lesson(identity: str, body: Promotion):
        record = service.experience.get(identity)
        source = service.store.get(record['candidate']['source_job'])
        if source['state'] != 'rejected' or source['result_hash'] != record['candidate']['result_hash']:
            raise Conflict('feedback_source_changed')
        service.artifact(source['id'], 'report.md')
        return service.experience.promote(identity, **body.model_dump())

    @app.post("/jobs")
    def submit(body: Submission, request: Request):
        identity, created = service.submit(request_key(request), case_id=body.case_id, question=body.question.model_dump(), mode=body.mode, task_type=body.task_type)
        return JSONResponse(public(identity), status_code=202 if created else 200)

    @app.get("/jobs/{identity}")
    def status(identity: str): return public(identity)

    @app.get("/jobs/{identity}/events")
    def events(identity: str, after: int = 0): return {"events": service.store.events(identity, max(0, after))}

    @app.post("/jobs/{identity}/cancel")
    def cancel(identity: str):
        service.store.cancel(identity)
        return public(identity)

    @app.get("/jobs/{identity}/artifacts/{name}")
    def artifact(identity: str, name: str): return FileResponse(service.artifact(identity, name))

    @app.post("/jobs/{identity}/review")
    def review(identity: str, body: Review, request: Request):
        return service.review(identity, request_key=request_key(request), **body.model_dump())

    @app.post("/jobs/{identity}/confirm-plan")
    def confirm_plan(identity: str, body: PlanReview, request: Request):
        return service.confirm_plan(identity, request_key=request_key(request), **body.model_dump())

    from app.service.investigations import install
    install(app,service)
    return app
