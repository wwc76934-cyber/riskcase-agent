"""Bounded SiliconFlow HTTP client with sanitized usage audit and no retries."""

from decimal import Decimal
import json
import time
from uuid import uuid4

import httpx

from app.llm.cost_ledger import BudgetExceeded
from app.llm.glm_client import GLMClient, LLMError, ProtocolError


ENDPOINT = "https://api.siliconflow.cn/v1/chat/completions"
MODEL = "Pro/zai-org/GLM-5.1"


def validate_policy(policy):
    if policy["endpoint"] != ENDPOINT or policy["model"] != MODEL:
        raise ValueError("First live experiment is bound to the declared SiliconFlow endpoint/model")
    for key, lower, upper in (("max_requests",1,6),("max_tokens",1,1024),("thinking_budget",128,128),
        ("max_input_bytes",1000,40000),("max_response_bytes",1000,524288),
        ("request_timeout_seconds",1,45),("run_deadline_seconds",1,180),("token_overhead_reserve",4096,4096)):
        if type(policy[key]) is not int or not lower <= policy[key] <= upper:
            raise ValueError(f"Invalid bounded policy: {key}")
    if policy["enable_thinking"] is not False or policy["automatic_retries"] != 0:
        raise ValueError("First experiment disables thinking and automatic retries")
    run, project = Decimal(policy["run_budget_cny"]), Decimal(policy["project_budget_cny"])
    if not run.is_finite() or not project.is_finite() or not 0 < run <= 1 or not run <= project <= 20:
        raise ValueError("Budget exceeds current authorization")
    for key in ("input_rate_cny_per_million", "output_rate_cny_per_million"):
        rate = Decimal(policy[key])
        if not rate.is_finite() or rate <= 0:
            raise ValueError("Positive finite pricing snapshot required")


def usage_counts(data):
    usage = data.get("usage")
    if not isinstance(usage, dict):
        raise ProtocolError("usage_missing_or_invalid")
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if type(usage.get(key)) is not int or usage[key] < 0:
            raise ProtocolError("usage_missing_or_invalid")
        result[key] = usage[key]
    if result["total_tokens"] != result["prompt_tokens"]+result["completion_tokens"]:
        raise ProtocolError("usage_totals_disagree")
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and "reasoning_tokens" in details:
        value = details["reasoning_tokens"]
        if type(value) is not int or not 0 <= value <= result["completion_tokens"]:
            raise ProtocolError("invalid_reasoning_usage")
        result["reasoning_tokens"] = value  # Already included in completion_tokens.
    return result


class SiliconFlowClient(GLMClient):
    def __init__(self, api_key, *, policy, ledger, run_id, http_client=None, clock=time.monotonic):
        validate_policy(policy)
        if not isinstance(api_key,str) or len(api_key.strip()) < 8 or any(char.isspace() for char in api_key):
            raise ValueError("Valid local API credential required; never print it")
        super().__init__(api_key, policy["model"], base_url=policy["endpoint"])
        self.policy, self.ledger, self.run_id, self.clock = dict(policy), ledger, run_id, clock
        self.started, self.sent_requests, self.audit, self.last_error_code = clock(), 0, [], None
        self._owned_http = http_client is None
        self.http = http_client or httpx.Client(transport=httpx.HTTPTransport(retries=0),follow_redirects=False)

    def close(self):
        if self._owned_http:
            self.http.close()

    def scrub(self, value):
        if isinstance(value,str):
            return value.replace(self.api_key,"[REDACTED]")
        if isinstance(value,list):
            return [self.scrub(item) for item in value]
        if isinstance(value,dict):
            return {self.scrub(key):self.scrub(item) for key,item in value.items()}
        return value

    def estimated_cost(self, prompt, completion):
        return (Decimal(prompt)*Decimal(self.policy["input_rate_cny_per_million"])
            +Decimal(completion)*Decimal(self.policy["output_rate_cny_per_million"]))/Decimal(1000000)

    def chat(self, messages, tools=None):
        remaining = self.policy["run_deadline_seconds"]-(self.clock()-self.started)
        if remaining <= 0:
            self.last_error_code = "run_deadline"
            raise BudgetExceeded(self.last_error_code)
        payload = {"model":self.model,"messages":messages,"tools":tools or [],"stream":False,
            "max_tokens":self.policy["max_tokens"],"enable_thinking":False,
            "thinking_budget":self.policy["thinking_budget"],"temperature":self.policy["temperature"],"n":1}
        encoded = json.dumps(payload,ensure_ascii=False,allow_nan=False).encode("utf-8")
        if self.api_key in encoded.decode("utf-8"):
            self.last_error_code = "credential_in_payload"
            raise ProtocolError(self.last_error_code)
        if len(encoded) > self.policy["max_input_bytes"]:
            self.last_error_code = "input_byte_limit"
            raise BudgetExceeded(self.last_error_code)
        reservation = self.estimated_cost(len(encoded)+self.policy["token_overhead_reserve"],
            self.policy["max_tokens"]+self.policy["thinking_budget"])
        try:
            identity = self.ledger.reserve(self.run_id,reservation,run_budget=self.policy["run_budget_cny"],
                project_budget=self.policy["project_budget_cny"],max_requests=self.policy["max_requests"])
        except BudgetExceeded as exc:
            self.last_error_code = str(exc)
            raise
        entry = {"request_number":self.sent_requests+1,"reservation_id":identity,
            "request_trace_id":uuid4().hex,"input_bytes":len(encoded),"reserved_estimate_cny":str(reservation)}
        self.sent_requests += 1
        begin, settled = self.clock(), False
        try:
            timeout = httpx.Timeout(min(self.policy["request_timeout_seconds"],remaining),connect=min(10,remaining))
            chunks, size = [], 0
            with self.http.stream("POST",ENDPOINT,content=encoded,
                headers={"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json","X-Trace-Id":entry["request_trace_id"]},
                timeout=timeout,follow_redirects=False) as response:
                entry["http_status"] = response.status_code
                if response.status_code != 200:
                    raise LLMError(f"http_{response.status_code}")
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.policy["max_response_bytes"]:
                        raise ProtocolError("response_byte_limit")
                    if self.clock()-self.started > self.policy["run_deadline_seconds"]:
                        raise BudgetExceeded("run_deadline")
                    chunks.append(chunk)
            data = json.loads(b"".join(chunks))
            if not isinstance(data,dict):
                raise ProtocolError("response_object_required")
            counts = usage_counts(data)
            estimate = self.estimated_cost(counts["prompt_tokens"],counts["completion_tokens"])
            self.ledger.settle(identity,estimate=estimate,usage=counts)
            settled = True
            entry.update(usage=counts,usage_cost_upper_estimate_cny=str(estimate),
                response_id=self.scrub(str(data.get("id","unknown")))[:128],
                returned_model=self.scrub(str(data.get("model","unknown")))[:128])
            if counts["completion_tokens"] > self.policy["max_tokens"]+self.policy["thinking_budget"]:
                raise ProtocolError("reported_output_exceeds_requested_budget")
            if counts.get("reasoning_tokens",0) > self.policy["thinking_budget"]:
                raise ProtocolError("unexpected_reasoning_usage")
            if estimate > reservation:
                raise BudgetExceeded("usage_exceeded_heuristic_reservation")
            message = super()._extract_message(data)
            # Provider reasoning_content is deliberately not retained or returned.
            clean = {key:self.scrub(message[key]) for key in ("role","content","tool_calls") if key in message}
            entry["status"] = "response_received"
            return clean
        except httpx.HTTPError:
            entry["error_code"] = self.last_error_code = "transport_error_unknown_charge"
            raise LLMError(self.last_error_code) from None
        except (ValueError,UnicodeDecodeError):
            entry["error_code"] = self.last_error_code = "invalid_response_json"
            raise ProtocolError(self.last_error_code) from None
        except LLMError as exc:
            entry["error_code"] = self.last_error_code = self.scrub(str(exc))[:160]
            raise
        except KeyboardInterrupt:
            entry["error_code"] = self.last_error_code = "cancelled_charge_unknown"
            raise
        finally:
            if not settled:
                self.ledger.settle(identity)
            entry["status"] = entry.get("status","failed_or_uncertain")
            entry["elapsed_seconds"] = round(self.clock()-begin,3)
            self.audit.append(entry)

    def snapshot(self):
        return {"provider":"SiliconFlow China","endpoint":ENDPOINT,"requested_model":self.model,
            "requests_sent":self.sent_requests,"request_audit":self.scrub(self.audit),
            "last_error_code":self.last_error_code,"ledger":self.ledger.summary(self.run_id),
            "limits":"Usage-based conservative local estimates, not provider invoices or provider-enforced caps."}
