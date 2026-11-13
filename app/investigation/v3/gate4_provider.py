"""Opt-in transport for frozen Gate 4 candidate policies."""
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal
import json
import re
from time import monotonic
from uuid import uuid4

import httpx

from app.investigation.contracts import digest
from app.investigation.v3.schema_client import delivery_format
from app.llm.cost_ledger import BudgetExceeded
from app.llm.glm_client import GLMClient, LLMError, ProtocolError
from app.llm.siliconflow_client import usage_counts


PROVIDERS = {
    "siliconflow-cn": (
        "https://api.siliconflow.cn/v1/chat/completions",
        frozenset({"Qwen/Qwen3.8-27B", "Pro/zai-org/GLM-5.1", "deepseek-ai/DeepSeek-V4-Flash"}),
    ),
    "ark-standard-cn": (
        "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        frozenset({"doubao-seed-2-1-lite-260915", "doubao-seed-2-1-pro-260915"}),
    ),
}
DELIVERY_STAGES = ("extract", "report", "extract_and_report")


@dataclass(frozen=True)
class Gate4Policy:
    provider: str
    model: str
    json_mode: str
    input_rate: str
    output_rate: str
    price_source: str
    price_date: str
    max_input_bytes: int = 32000
    max_output_tokens: int = 2048
    max_requests: int = 6
    timeout_seconds: int = 60
    deadline_seconds: int = 360
    expand_hypothesis_branches: bool = False

    def __post_init__(self):
        if self.provider not in PROVIDERS or self.model not in PROVIDERS[self.provider][1]:
            raise ValueError("Unregistered Gate 4 provider or model")
        if self.json_mode not in ("json_schema", "json_object"):
            raise ValueError("Freeze one structured output mode per candidate")
        if type(self.expand_hypothesis_branches) is not bool or (
                self.expand_hypothesis_branches and self.json_mode != "json_schema"):
            raise ValueError("Expanded hypothesis layout requires strict Schema mode")
        for value, ceiling in ((self.max_input_bytes, 180000), (self.max_output_tokens, 4096),
                               (self.max_requests, 6), (self.timeout_seconds, 90),
                               (self.deadline_seconds, 480)):
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError("Candidate policy limit out of bounds")
        if not self.price_source.startswith("https://") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.price_date):
            raise ValueError("Documented pricing snapshot required")
        for rate in (self.input_rate, self.output_rate):
            number = Decimal(rate)
            if not number.is_finite() or number <= 0:
                raise ValueError("Positive finite CNY rates required")

    @property
    def endpoint(self):
        return PROVIDERS[self.provider][0]

    @property
    def binding(self):
        return digest(dict(contract="gate4-provider-policy-v1", **asdict(self)))

    def estimate(self, prompt, completion):
        return (prompt * Decimal(self.input_rate) + completion * Decimal(self.output_rate)) / 1000000


def expanded_hypothesis_schema(schema, stage):
    """Distribute the shared object constraints over the existing anyOf branches."""
    schema = deepcopy(schema)
    report = schema["properties"]["report"] if stage == "extract_and_report" else schema
    items = report["properties"]["investigation"]["properties"]["hypotheses"]["items"]
    branches = items.pop("anyOf")
    complete = []
    for branch in branches:
        if set(branch) != {"properties"}:
            raise ProtocolError("unsupported_hypothesis_branch")
        variant = deepcopy(items)
        for field, constraints in branch["properties"].items():
            shared = variant["properties"][field]
            for name, value in constraints.items():
                if name == "minItems":
                    shared[name] = max(shared.get(name, 0), value)
                elif name == "const" and name not in shared:
                    shared[name] = value
                else:
                    raise ProtocolError("unsupported_hypothesis_constraint")
        complete.append(variant)
    items.clear()
    items["anyOf"] = complete
    return schema


class Gate4ProviderClient(GLMClient):
    """The controller owns the key, HTTP client and preauthorized spend scope."""

    def __init__(self, policy, key, *, budget, http, clock=monotonic):
        if not isinstance(key, str) or len(key) < 8 or any(c.isspace() for c in key):
            raise ValueError("Valid local provider credential required")
        if budget.policy_binding != policy.binding:
            raise ValueError("Model and budget policy bindings differ")
        super().__init__(key, policy.model)
        self.policy, self.budget, self.http = policy, budget, http
        self.clock, self.started = clock, clock()
        self.audit = []
        self.last_response_capture = None
        self.terminal = False

    streaming = False
    max_body_bytes = 524288

    def transport_timeout(self, remaining):
        return httpx.Timeout(min(remaining, self.policy.timeout_seconds), connect=min(remaining, 10))

    def total_deadline(self, remaining):
        return min(remaining, self.policy.timeout_seconds)

    def decode_body(self, body):
        return json.loads(body)

    def scrub(self, value):
        if isinstance(value, str):
            return value.replace(self.api_key, "[REDACTED]")
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        if isinstance(value, dict):
            return {self.scrub(key): self.scrub(item) for key, item in value.items()}
        return value

    def request_payload(self, messages, tools=None):
        payload = dict(model=self.model, messages=messages, stream=self.streaming,
                       max_tokens=self.policy.max_output_tokens, temperature=0.2)
        if self.streaming:
            payload["stream_options"] = {"include_usage": True}
        if self.policy.provider == "siliconflow-cn":
            payload["enable_thinking"] = False
        else:
            payload["thinking"] = {"type": "disabled"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        else:
            strict = delivery_format(messages, stages=DELIVERY_STAGES)
            if self.policy.expand_hypothesis_branches:
                spec = strict["json_schema"]
                stage = spec["name"].removeprefix("riskcase_")
                if stage in ("report", "extract_and_report"):
                    spec["schema"] = expanded_hypothesis_schema(spec["schema"], stage)
            payload["response_format"] = strict if self.policy.json_mode == "json_schema" else {"type": "json_object"}
        return payload

    def chat(self, messages, tools=None):
        self.last_response_capture = None
        remaining = self.policy.deadline_seconds - (self.clock() - self.started)
        if self.terminal or len(self.audit) >= self.policy.max_requests or remaining <= 0:
            raise BudgetExceeded("gate4_request_stopped")
        payload = self.request_payload(messages, tools)
        wire = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if self.api_key.encode("utf-8") in wire or self.scrub(payload) != payload:
            raise ProtocolError("credential_in_payload")
        if len(wire) > self.policy.max_input_bytes:
            raise BudgetExceeded("gate4_input_byte_limit")
        reserved = self.policy.estimate(len(wire) + 4096, self.policy.max_output_tokens)
        identity = self.budget.reserve(reserved)
        trace = uuid4().hex
        entry = dict(reservation_id=identity, trace_id=trace, request_bytes=len(wire),
                     reserved_cny=str(reserved), outcome="unknown")
        self.audit.append(entry)
        begin = self.clock()
        settled = False
        try:
            headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
            if self.policy.provider == "siliconflow-cn":
                headers["X-Trace-Id"] = trace
            timeout = self.transport_timeout(remaining)
            with self.http.stream("POST", self.policy.endpoint, headers=headers, content=wire,
                                  timeout=timeout, follow_redirects=False) as response:
                entry["http_status"] = response.status_code
                provider_trace = response.headers.get(
                    "x-siliconcloud-trace-id" if self.policy.provider == "siliconflow-cn" else "x-request-id", "")
                if re.fullmatch(r"[A-Za-z0-9_-]{8,100}", provider_trace) and self.api_key not in provider_trace:
                    entry["provider_trace_id"] = provider_trace
                if response.status_code != 200:
                    raise LLMError(f"http_{response.status_code}")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > self.max_body_bytes or self.clock() - begin > self.total_deadline(remaining):
                        raise ProtocolError("response_limit_or_deadline")
                    body.extend(chunk)
            data = self.decode_body(bytes(body))
            if not isinstance(data, dict):
                raise ProtocolError("invalid_response_object")
            choices = data.get("choices")
            choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
            message = choice.get("message")
            safe_message = ({k: message[k] for k in ("role", "content", "tool_calls") if k in message}
                            if isinstance(message, dict) else None)
            self.last_response_capture = self.scrub(dict(id=data.get("id"), model=data.get("model"),
                system_fingerprint=data.get("system_fingerprint"), finish_reason=choice.get("finish_reason"),
                usage=data.get("usage"), message=safe_message))
            if data.get("model") != self.model:
                raise ProtocolError("provider_model_mismatch")
            counts = usage_counts(data)
            estimate = self.policy.estimate(counts["prompt_tokens"], counts["completion_tokens"])
            self.budget.settle(identity, estimate=estimate, usage=counts)
            settled = True
            entry.update(returned_model=self.model, usage=counts, estimated_cny=str(estimate),
                         system_fingerprint=self.last_response_capture["system_fingerprint"])
            if estimate > reserved or counts["completion_tokens"] > self.policy.max_output_tokens:
                raise BudgetExceeded("provider_usage_over_reservation")
            try:
                parsed = self._extract_message(data)
            except ProtocolError as exc:
                entry["outcome"] = "metered_invalid_response"
                outcome = ("empty_content" if str(exc) == "Assistant message has no content or tool calls"
                           else "invalid_response")
                return dict(role="assistant", content="", known_provider_outcome=outcome)
            entry["outcome"] = "metered_response"
            return self.scrub({k: parsed[k] for k in ("role", "content", "tool_calls") if k in parsed})
        except httpx.TimeoutException as exc:
            entry["transport_timeout_type"] = type(exc).__name__
            entry["transport_timeout_after_headers"] = "http_status" in entry
            entry["error"] = "transport_unknown"
            raise LLMError("transport_unknown") from None
        except httpx.HTTPError:
            entry["error"] = "transport_unknown"
            raise LLMError("transport_unknown") from None
        except (ValueError, RecursionError):
            entry["error"] = "invalid_provider_json"
            raise ProtocolError("invalid_provider_json") from None
        except LLMError as exc:
            entry["error"] = str(exc)
            raise
        finally:
            entry["elapsed_seconds"] = round(self.clock() - begin, 6)
            if not settled:
                self.terminal = True
                self.budget.settle(identity)
            elif entry["outcome"] == "unknown":
                self.terminal = True

    def snapshot(self):
        return self.scrub(dict(provider=self.policy.provider, requested_model=self.model,
                               policy_binding=self.policy.binding, request_audit=self.audit))
