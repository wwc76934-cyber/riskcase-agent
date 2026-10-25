import json

import httpx
import pytest
import subprocess
from pathlib import Path
from app.tools.basic_tools import BasicTools

from app.llm.glm_client import GLMClient, LLMError, ProtocolError
from app.agent.react_agent import Agent
from app.llm.fake_client import (
    FakeLLMClient,
    build_lab03_fake_responses,
    make_tool_call_message,
)

def test_glm_client_sends_request_and_returns_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)

        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert payload["model"] == "test-model"
        assert payload["stream"] is False
        assert payload["messages"] == [
            {"role": "user", "content": "你好"}
        ]
        assert "tools" not in payload

        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "模拟回复成功",
                        },
                    }
                ]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GLMClient(
            "test-key",
            "test-model",
            base_url="https://example.test/chat",
            http_client=http_client,
        )

        message = client.chat([
            {"role": "user", "content": "你好"}
        ])

    assert message == {
        "role": "assistant",
        "content": "模拟回复成功",
    }


def test_glm_client_sends_tools_when_provided() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)

        assert payload["tools"] == tools

        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"notes.txt"}',
                                    },
                                }
                            ],
                        },
                    }
                ]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GLMClient(
            "test-key",
            "test-model",
            base_url="https://example.test/chat",
            http_client=http_client,
        )

        message = client.chat(
            [{"role": "user", "content": "读取说明"}],
            tools=tools,
        )

    assert message["tool_calls"][0]["id"] == "call_1"
    assert (
        message["tool_calls"][0]["function"]["name"]
        == "read_file"
    )


def test_glm_client_converts_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"error": "invalid api key"},
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GLMClient(
            "test-key",
            "test-model",
            base_url="https://example.test/chat",
            http_client=http_client,
        )

        with pytest.raises(
            LLMError,
            match="HTTP 401",
        ):
            client.chat([
                {"role": "user", "content": "你好"}
            ])


def test_glm_client_rejects_invalid_protocol() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": []},
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler)
    ) as http_client:
        client = GLMClient(
            "test-key",
            "test-model",
            base_url="https://example.test/chat",
            http_client=http_client,
        )

        with pytest.raises(
            ProtocolError,
            match="non-empty choices",
        ):
            client.chat([
                {"role": "user", "content": "你好"}
            ])


def test_tool_schemas_match_four_tools(
    tmp_path: Path,
) -> None:
    tool = BasicTools(tmp_path)

    schemas = tool.schemas
    names = [
        item["function"]["name"]
        for item in schemas
    ]

    assert names == [
        "read_file",
        "write_file",
        "list_dir",
        "execute_command",
    ]

    schemas[0]["function"]["name"] = "changed"

    fresh_names = [
        item["function"]["name"]
        for item in tool.schemas
    ]

    assert fresh_names[0] == "read_file"


def test_basic_tools_file_operations(
    tmp_path: Path,
) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text(
        "first line\nsecond line\n",
        encoding="utf-8",
    )

    tool = BasicTools(tmp_path)

    listing = tool.list_dir(".")
    assert listing["ok"] is True
    assert listing["entries"][0]["name"] == "notes.txt"

    reading = tool.read_file("notes.txt")
    assert reading["ok"] is True
    assert reading["content"] == (
        "first line\nsecond line\n"
    )
    assert reading["line_count"] == 2

    writing = tool.write_file(
        "report.txt",
        "report content\n",
    )
    assert writing["ok"] is True
    assert writing["created"] is True

    assert (
        (tmp_path / "report.txt").read_text(
            encoding="utf-8"
        )
        == "report content\n"
    )


def test_dispatch_executes_valid_tool(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.txt").write_text(
        "real file content",
        encoding="utf-8",
    )

    tool = BasicTools(tmp_path)

    result = tool.dispatch(
        "read_file",
        json.dumps({"path": "notes.txt"}),
    )

    assert result["ok"] is True
    assert result["content"] == "real file content"


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("read_file", "not-json"),
        ("read_file", "[]"),
        ("read_file", "{}"),
        ("read_file", '{"path": 17}'),
        (
            "read_file",
            '{"path": "notes.txt", "extra": true}',
        ),
        ("unknown_tool", "{}"),
    ],
)
def test_dispatch_rejects_invalid_tools(
    tmp_path: Path,
    name: str,
    arguments: str,
) -> None:
    tool = BasicTools(tmp_path)

    result = tool.dispatch(name, arguments)

    assert result["ok"] is False
    assert result["error_type"] == "ToolInputError"


def test_dispatch_rejects_path_escape(
    tmp_path: Path,
) -> None:
    tool = BasicTools(tmp_path)

    result = tool.dispatch(
        "read_file",
        json.dumps({"path": "../outside.txt"}),
    )

    assert result["ok"] is False
    assert "inside the working directory" in result["error"]


def test_write_file_does_not_overwrite(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "report.txt"
    existing.write_text(
        "original",
        encoding="utf-8",
    )

    tool = BasicTools(tmp_path)

    result = tool.dispatch(
        "write_file",
        json.dumps({
            "path": "report.txt",
            "content": "replacement",
        }),
    )

    assert result["ok"] is False
    assert result["error_type"] == "FileWriteError"
    assert existing.read_text(
        encoding="utf-8"
    ) == "original"


def test_invalid_command_never_starts_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        pytest.fail(
            "subprocess.run must not be called"
        )

    monkeypatch.setattr(
        "app.tools.basic_tools.subprocess.run",
        fail_if_called,
    )

    tool = BasicTools(tmp_path)

    result = tool.dispatch(
        "execute_command",
        json.dumps({
            "command": (
                "echo lab03-shell-ok && whoami"
            ),
        }),
    )

    assert result["ok"] is False
    assert result["error_type"] == "ToolInputError"


def test_command_timeout_becomes_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_timeout(
        *args: object,
        **kwargs: object,
    ) -> None:
        raise subprocess.TimeoutExpired(
            cmd="echo lab03-shell-ok",
            timeout=5.0,
        )

    monkeypatch.setattr(
        "app.tools.basic_tools.subprocess.run",
        raise_timeout,
    )

    tool = BasicTools(tmp_path)

    result = tool.dispatch(
        "execute_command",
        json.dumps({
            "command": "echo lab03-shell-ok",
        }),
    )

    assert result["ok"] is False
    assert result["error_type"] == "ToolExecutionError"
    assert "timed out" in result["error"]


def test_execute_allowed_command(
    tmp_path: Path,
) -> None:
    tool = BasicTools(tmp_path)

    result = tool.execute_command(
        "echo lab03-shell-ok"
    )

    assert result["ok"] is True
    assert result["returncode"] == 0
    assert "lab03-shell-ok" in result["stdout"]

def test_agent_completes_four_tool_trajectory(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.txt").write_text(
        "actual notes content\n",
        encoding="utf-8",
    )

    fake = FakeLLMClient(
        build_lab03_fake_responses("report.txt")
    )
    agent = Agent(
        fake,
        BasicTools(tmp_path),
        max_iterations=8,
    )

    result = agent.run("完成四工具任务")

    assert result.status == "completed"
    assert result.iterations == 5
    assert fake.call_count == 5
    assert fake.remaining_responses == 0
    assert (tmp_path / "report.txt").exists()
    assert "lab03-shell-ok" in result.content

    for request_number, request in enumerate(
        fake.requests,
        start=1,
    ):
        tool_messages = [
            message
            for message in request["messages"]
            if message["role"] == "tool"
        ]

        assert len(tool_messages) == (
            request_number - 1
        )


def test_agent_pairs_tool_call_id(
    tmp_path: Path,
) -> None:
    fake = FakeLLMClient([
        make_tool_call_message(
            "call_pairing",
            "list_dir",
            {"path": "."},
        ),
        {
            "role": "assistant",
            "content": "目录检查完成。",
        },
    ])

    agent = Agent(fake, BasicTools(tmp_path))
    result = agent.run("查看目录")

    roles = [
        message["role"]
        for message in result.messages
    ]

    assert roles == [
        "system",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]

    tool_message = result.messages[3]
    assert (
        tool_message["tool_call_id"]
        == "call_pairing"
    )
    assert tool_message["name"] == "list_dir"


def test_agent_does_not_finish_when_content_and_tool_calls_exist(
    tmp_path: Path,
) -> None:
    fake = FakeLLMClient([
        make_tool_call_message(
            "call_mixed",
            "list_dir",
            {"path": "."},
            content="我先查看目录。",
        ),
        {
            "role": "assistant",
            "content": "现在任务完成。",
        },
    ])

    agent = Agent(fake, BasicTools(tmp_path))
    result = agent.run("查看目录")

    assert result.status == "completed"
    assert result.iterations == 2
    assert fake.call_count == 2
    assert result.content == "现在任务完成。"


def test_agent_executes_multiple_calls_in_order(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.txt").write_text(
        "notes",
        encoding="utf-8",
    )

    fake = FakeLLMClient([
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_first",
                    "type": "function",
                    "function": {
                        "name": "list_dir",
                        "arguments": json.dumps({
                            "path": ".",
                        }),
                    },
                },
                {
                    "id": "call_second",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({
                            "path": "notes.txt",
                        }),
                    },
                },
            ],
        },
        {
            "role": "assistant",
            "content": "两个工具都执行完成。",
        },
    ])

    agent = Agent(fake, BasicTools(tmp_path))
    result = agent.run("列目录并读取文件")

    tool_messages = [
        message
        for message in result.messages
        if message["role"] == "tool"
    ]

    assert [
        message["tool_call_id"]
        for message in tool_messages
    ] == [
        "call_first",
        "call_second",
    ]


def test_agent_stops_at_iteration_limit(
    tmp_path: Path,
) -> None:
    responses = [
        make_tool_call_message(
            f"call_{number}",
            "list_dir",
            {"path": "."},
        )
        for number in range(4)
    ]

    fake = FakeLLMClient(responses)

    agent = Agent(
        fake,
        BasicTools(tmp_path),
        max_iterations=2,
    )

    result = agent.run("不断查看目录")

    assert result.status == "iteration_limit"
    assert result.iterations == 2
    assert fake.call_count == 2
    assert fake.remaining_responses == 2


def test_agent_rejects_missing_tool_call_id(
    tmp_path: Path,
) -> None:
    fake = FakeLLMClient([
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "list_dir",
                        "arguments": '{"path":"."}',
                    },
                }
            ],
        }
    ])

    agent = Agent(fake, BasicTools(tmp_path))

    with pytest.raises(
        ProtocolError,
        match="tool call id",
    ):
        agent.run("查看目录")


def test_agent_rejects_empty_final_response(
    tmp_path: Path,
) -> None:
    fake = FakeLLMClient([
        {
            "role": "assistant",
            "content": "",
        }
    ])

    agent = Agent(fake, BasicTools(tmp_path))

    with pytest.raises(
        ProtocolError,
        match="no content or tool calls",
    ):
        agent.run("回答问题")


def test_agent_preserves_history_for_follow_up(
    tmp_path: Path,
) -> None:
    fake = FakeLLMClient([
        {
            "role": "assistant",
            "content": "报告名是 report.txt。",
        },
        {
            "role": "assistant",
            "content": "我记得上一轮的报告名。",
        },
    ])

    agent = Agent(fake, BasicTools(tmp_path))

    first = agent.run("报告文件叫什么？")
    second = agent.run("你还记得吗？")

    assert first.iterations == 1
    assert second.iterations == 1
    assert fake.call_count == 2

    second_request = fake.requests[1]["messages"]

    assert [
        message["role"]
        for message in second_request
    ] == [
        "system",
        "user",
        "assistant",
        "user",
    ]

    assert (
        second_request[2]["content"]
        == "报告名是 report.txt。"
    )


def test_tool_failure_is_returned_to_model(
    tmp_path: Path,
) -> None:
    fake = FakeLLMClient([
        make_tool_call_message(
            "call_missing",
            "read_file",
            {"path": "missing.txt"},
        ),
        {
            "role": "assistant",
            "content": "文件不存在，任务未执行。",
        },
    ])

    agent = Agent(fake, BasicTools(tmp_path))
    result = agent.run("读取 missing.txt")

    assert result.status == "completed"

    second_request = fake.requests[1]["messages"]
    observation = second_request[-1]

    assert observation["role"] == "tool"
    assert (
        observation["tool_call_id"]
        == "call_missing"
    )

    observation_data = json.loads(
        observation["content"]
    )

    assert observation_data["ok"] is False
    assert (
        observation_data["error_type"]
        == "FileReadError"
    )


def test_fake_client_records_deep_snapshots() -> None:
    fake = FakeLLMClient([
        {
            "role": "assistant",
            "content": "完成。",
        }
    ])

    messages = [
        {
            "role": "user",
            "content": "原始问题",
        }
    ]

    fake.chat(messages)
    messages[0]["content"] = "修改后的问题"
    messages.append({
        "role": "user",
        "content": "新增消息",
    })

    saved_messages = fake.requests[0]["messages"]

    assert len(saved_messages) == 1
    assert (
        saved_messages[0]["content"]
        == "原始问题"
    )