from __future__ import annotations

from typing import Any

import httpx


DEFAULT_GLM_URL = (
    "https://open.bigmodel.cn/api/paas/v4/chat/completions"
)


class LLMError(RuntimeError):
    """Raised when communication with the language model fails."""


class ProtocolError(LLMError):
    """Raised when the model response does not follow the expected protocol."""


class GLMClient:
    """A small HTTP client for the GLM Chat Completions API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = DEFAULT_GLM_URL,
        timeout: float = 30.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")

        if not model.strip():
            raise ValueError("model must not be empty")

        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.http_client = http_client

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }

        if tools is not None:
            payload["tools"] = tools

        return payload

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = self._build_payload(messages, tools)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            if self.http_client is None:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(
                        self.base_url,
                        headers=headers,
                        json=payload,
                    )
            else:
                response = self.http_client.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout,
                )

            response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            raise LLMError(
                f"GLM API returned HTTP {status_code}"
            ) from exc

        except httpx.HTTPError as exc:
            raise LLMError(
                f"Could not communicate with GLM API: {exc}"
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ProtocolError(
                "GLM API response is not valid JSON"
            ) from exc

        return self._extract_message(data)

    def _extract_message(
        self,
        data: Any,
    ) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ProtocolError("GLM response must be a JSON object")

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProtocolError(
                "GLM response must contain a non-empty choices list"
            )

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ProtocolError("GLM choice must be an object")

        if first_choice.get("finish_reason") == "length":
            raise ProtocolError("GLM response was truncated")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ProtocolError(
                "GLM choice must contain a message object"
            )

        if message.get("role") != "assistant":
            raise ProtocolError(
                "GLM message role must be assistant"
            )

        content = message.get("content")
        tool_calls = message.get("tool_calls")

        if content is not None and not isinstance(content, str):
            raise ProtocolError(
                "Assistant content must be text or null"
            )

        if tool_calls is not None and not isinstance(tool_calls, list):
            raise ProtocolError(
                "Assistant tool_calls must be a list or null"
            )

        if not content and not tool_calls:
            raise ProtocolError(
                "Assistant message has no content or tool calls"
            )

        return message