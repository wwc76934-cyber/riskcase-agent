"""Streaming variant of the Gate 4 transport.

A non-streaming completion returns no header until the whole JSON body exists,
so a long report can hit a header-read timeout while the provider is still
generating. Streaming sends the first bytes early and keeps the connection
active. The chunks are folded back into the same response object the
non-streaming path produces, so reservation, settlement, schema validation
and the unknown-execution protection are unchanged.
"""
import json

import httpx

from app.investigation.v3.gate4_provider import Gate4ProviderClient
from app.llm.glm_client import ProtocolError

CONNECT_TIMEOUT = 10.0
CHUNK_READ_TIMEOUT = 30.0
TOTAL_TIMEOUT = 120.0


def aggregate_sse(body):
    """Fold a ``text/event-stream`` chat completion into one response dict."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ProtocolError("invalid_stream_encoding") from None
    head, usage, finish, done = {}, None, None, False
    parts, calls = [], {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            done = True
            continue
        event = json.loads(payload)
        if not isinstance(event, dict):
            raise ProtocolError("invalid_stream_event")
        if "error" in event:
            raise ProtocolError("provider_stream_error")
        for key in ("id", "model", "system_fingerprint"):
            if event.get(key) is not None:
                head[key] = event[key]
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        for choice in event.get("choices") or ():
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                parts.append(delta["content"])
            for call in delta.get("tool_calls") or ():
                slot = calls.setdefault(call.get("index", 0), {"id": "", "type": "function",
                                                              "function": {"name": "", "arguments": ""}})
                slot["id"] = call.get("id") or slot["id"]
                function = call.get("function") or {}
                slot["function"]["name"] += function.get("name") or ""
                slot["function"]["arguments"] += function.get("arguments") or ""
            finish = choice.get("finish_reason") or finish
    if not done and finish is None:
        raise ProtocolError("stream_incomplete")
    message = {"role": "assistant", "content": "".join(parts)}
    if calls:
        message["tool_calls"] = [calls[i] for i in sorted(calls)]
    return dict(head, choices=[{"index": 0, "message": message, "finish_reason": finish}], usage=usage)


class Gate4StreamingClient(Gate4ProviderClient):
    streaming = True

    def transport_timeout(self, remaining):
        return httpx.Timeout(CHUNK_READ_TIMEOUT, connect=min(remaining, CONNECT_TIMEOUT))

    def total_deadline(self, remaining):
        return min(remaining, TOTAL_TIMEOUT)

    def decode_body(self, body):
        return aggregate_sse(body)
