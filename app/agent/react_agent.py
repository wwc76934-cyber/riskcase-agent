from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from app.llm.glm_client import ProtocolError


DEFAULT_SYSTEM_PROMPT = """
You are a careful coding agent.

Use the available tools when the task requires access to files,
directories, or commands. Never claim that an action succeeded
until you have received its tool result.

All file paths must be relative to the configured working directory.
Do not invent file contents or command results.
""".strip()


class ChatClient(Protocol):
    """The interface required by the Agent."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Return one assistant message."""


class Toolset(Protocol):
    """Agent depends on a tool contract, not coding-specific file/command tools."""

    @property
    def schemas(self) -> list[dict[str, Any]]: ...

    def dispatch(self, name: str, arguments_json: str) -> dict[str, Any]: ...


@dataclass
class AgentResult:
    """Describe how one Agent run finished."""

    status: str
    content: str
    iterations: int
    messages: list[dict[str, Any]]


class Agent:
    """A minimal ReAct Agent using model tool calls."""

    def __init__(
        self,
        client: ChatClient,
        tools: Toolset,
        *,
        max_iterations: int = 8,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        if max_iterations <= 0:
            raise ValueError(
                "max_iterations must be greater than zero"
            )

        self.client = client
        self.tools = tools
        self.max_iterations = max_iterations
        self._messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

    @property
    def history(self) -> list[dict[str, Any]]:
        """Return a safe snapshot of the conversation."""
        return deepcopy(self._messages)

    def run(
        self,
        user_input: str,
    ) -> AgentResult:
        """Run one user turn with a fresh iteration budget."""
        if not isinstance(user_input, str):
            raise TypeError("user_input must be a string")

        if not user_input.strip():
            raise ValueError("user_input must not be empty")

        self._messages.append({
            "role": "user",
            "content": user_input,
        })

        for iteration in range(
            1,
            self.max_iterations + 1,
        ):
            response = self.client.chat(
                messages=deepcopy(self._messages),
                tools=self.tools.schemas,
            )

            self._validate_assistant_message(response)

            assistant_message = deepcopy(response)
            self._messages.append(assistant_message)

            tool_calls = (
                assistant_message.get("tool_calls")
                or []
            )

            if tool_calls:
                self._execute_tool_calls(tool_calls)
                continue

            content = assistant_message["content"]

            return AgentResult(
                status="completed",
                content=content,
                iterations=iteration,
                messages=self.history,
            )

        return AgentResult(
            status="iteration_limit",
            content=(
                "The task was not completed before the "
                "iteration limit was reached."
            ),
            iterations=self.max_iterations,
            messages=self.history,
        )

    def _execute_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        for tool_call in tool_calls:
            function = tool_call["function"]
            name = function["name"]
            arguments = function["arguments"]

            result = self.tools.dispatch(
                name,
                arguments,
            )

            observation = {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "name": name,
                "content": json.dumps(
                    result,
                    ensure_ascii=False,
                ),
            }

            self._messages.append(observation)

    @staticmethod
    def _validate_assistant_message(
        message: Any,
    ) -> None:
        if not isinstance(message, dict):
            raise ProtocolError(
                "assistant message must be an object"
            )

        if message.get("role") != "assistant":
            raise ProtocolError(
                "assistant message role must be assistant"
            )

        content = message.get("content")
        tool_calls = message.get("tool_calls")

        if content is not None and not isinstance(content, str):
            raise ProtocolError(
                "assistant content must be text or null"
            )

        if tool_calls is not None and not isinstance(
            tool_calls,
            list,
        ):
            raise ProtocolError(
                "assistant tool_calls must be a list or null"
            )

        if not content and not tool_calls:
            raise ProtocolError(
                "assistant message has no content or tool calls"
            )

        if not tool_calls:
            return

        seen_ids: set[str] = set()

        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                raise ProtocolError(
                    "each tool call must be an object"
                )

            call_id = tool_call.get("id")

            if not isinstance(call_id, str) or not call_id:
                raise ProtocolError(
                    "tool call id must be a non-empty string"
                )

            if call_id in seen_ids:
                raise ProtocolError(
                    f"duplicate tool call id: {call_id}"
                )

            seen_ids.add(call_id)

            if tool_call.get("type") != "function":
                raise ProtocolError(
                    "tool call type must be function"
                )

            function = tool_call.get("function")

            if not isinstance(function, dict):
                raise ProtocolError(
                    "tool call function must be an object"
                )

            name = function.get("name")
            arguments = function.get("arguments")

            if not isinstance(name, str) or not name:
                raise ProtocolError(
                    "tool function name must be "
                    "a non-empty string"
                )

            if not isinstance(arguments, str):
                raise ProtocolError(
                    "tool function arguments must be "
                    "a JSON string"
                )
