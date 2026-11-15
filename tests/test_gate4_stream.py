import json

import pytest

from app.investigation.v3.gate4_stream import Gate4StreamingClient, aggregate_sse
from app.llm.glm_client import ProtocolError


def event(**kw):
    return "data: " + json.dumps(kw) + "\n\n"


def sample():
    return (event(id="r1", model="m", choices=[{"delta": {"role": "assistant", "content": '{"a":'}}])
            + ": keep-alive\n\n"
            + event(id="r1", model="m", choices=[{"delta": {"content": "1}"}, "finish_reason": "stop"}])
            + event(id="r1", model="m", choices=[],
                    usage=dict(prompt_tokens=10, completion_tokens=4, total_tokens=14))
            + "data: [DONE]\n\n").encode()


def test_chunks_fold_into_the_non_streaming_shape():
    data = aggregate_sse(sample())
    assert data["id"] == "r1" and data["model"] == "m"
    assert data["choices"][0]["message"]["content"] == '{"a":1}'
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["total_tokens"] == 14


def test_tool_call_deltas_are_joined_by_index():
    body = (event(choices=[{"delta": {"tool_calls": [{"index": 0, "id": "c1",
                "function": {"name": "read", "arguments": '{"s":'}}]}}])
            + event(choices=[{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '1}'}}]},
                              "finish_reason": "tool_calls"}])
            + "data: [DONE]\n").encode()
    call = aggregate_sse(body)["choices"][0]["message"]["tool_calls"][0]
    assert call["id"] == "c1" and call["function"] == {"name": "read", "arguments": '{"s":1}'}


@pytest.mark.parametrize("body", [
    b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n',   # cut off, no finish and no DONE
    b'data: {"error":{"message":"boom"}}\n\n',
])
def test_incomplete_or_error_stream_is_rejected(body):
    with pytest.raises(ProtocolError):
        aggregate_sse(body)


def test_streaming_client_requests_a_stream_with_usage():
    class Policy:
        provider = "siliconflow-cn"
        max_output_tokens = 10
        expand_hypothesis_branches = False
        json_mode = "json_object"

    client = object.__new__(Gate4StreamingClient)
    client.model, client.policy = "m", Policy()
    payload = client.request_payload([{"role": "user", "content": "{}"}], tools=[{"type": "function"}])
    assert payload["stream"] is True and payload["stream_options"] == {"include_usage": True}
    assert client.transport_timeout(500).read == 180.0 and client.total_deadline(500) == 420.0
    assert client.max_body_bytes == 4 * 1024 * 1024
