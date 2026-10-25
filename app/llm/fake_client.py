from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


class FakeLLMClient:
    """Return predefined responses and record every request."""

    def __init__(
        self,
        responses: list[dict[str, Any]],
    ) -> None:
        self._responses = deepcopy(responses)
        self.requests: list[dict[str, Any]] = []
        self._next_response = 0

    @property
    def call_count(self) -> int:
        """Return how many times chat() was called."""
        return self._next_response

    @property
    def remaining_responses(self) -> int:
        """Return how many predefined responses remain."""
        return (
            len(self._responses)
            - self._next_response
        )

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Record one request and return the next response."""
        self.requests.append({
            "messages": deepcopy(messages),
            "tools": deepcopy(tools),
        })

        if self._next_response >= len(self._responses):
            raise AssertionError(
                "FakeLLMClient has no response left"
            )

        response = deepcopy(
            self._responses[self._next_response]
        )
        self._next_response += 1

        return response


def make_tool_call_message(
    call_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    content: str | None = None,
) -> dict[str, Any]:
    """Build one assistant tool-call message."""
    return {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(
                        arguments,
                        ensure_ascii=False,
                    ),
                },
            }
        ],
    }


def build_lab03_fake_responses(
    report_path: str,
) -> list[dict[str, Any]]:
    """Build the predefined five-step Lab03 trajectory."""
    return [
        make_tool_call_message(
            "call_list",
            "list_dir",
            {"path": "."},
        ),
        make_tool_call_message(
            "call_read",
            "read_file",
            {"path": "notes.txt"},
        ),
        make_tool_call_message(
            "call_write",
            "write_file",
            {
                "path": report_path,
                "content": (
                    "Lab03 Fake 演示报告\n"
                    "\n"
                    "演示目录已经准备完成，"
                    "Agent 已读取说明文件。\n"
                    "\n"
                    "注意：本报告文字由 Fake "
                    "预设响应提供，用于验证控制流程。\n"
                ),
            },
        ),
        make_tool_call_message(
            "call_command",
            "execute_command",
            {
                "command": "echo lab03-shell-ok",
            },
        ),
        {
            "role": "assistant",
            "content": (
                "Fake 四工具任务已完成。"
                f"报告文件为 {report_path}，"
                "测试命令返回了 lab03-shell-ok。"
            ),
        },
    ]