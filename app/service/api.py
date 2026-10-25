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
from app.service.store import Conflict
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


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    mode: Literal["offline", "live"] = "offline"
    task_type: Literal["merchant", "mixed", "natural", "natural_v2"] = "merchant"
    question: Question | MixedQuestion | NaturalQuestion = Field(default_factory=Question)

    @model_validator(mode="after")
    def matching_type(self):
        if not isinstance(self.question, {"mixed": MixedQuestion, "merchant": Question, "natural": NaturalQuestion, "natural_v2": NaturalQuestion}[self.task_type]): raise ValueError("Question must match task type")
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


def create_app(service):
    app = FastAPI(title="RiskCase research tasks", version="1", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        expected = ("Bearer " + service.settings.token).encode()
        public_page = request.method == "GET" and request.url.path == "/"
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
        from app.service.workbench import HTML
        return HTML

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

    return app
