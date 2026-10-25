from __future__ import annotations

import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.tools.file_tool import FileTool, FileToolError


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a UTF-8 text file inside the "
                "configured working directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File path relative to the "
                            "working directory."
                        ),
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create a UTF-8 text file inside the "
                "configured working directory. Existing "
                "files are not overwritten."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "New file path relative to the "
                            "working directory."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Complete text to write to the file."
                        ),
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": (
                "List files and directories inside the "
                "configured working directory."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Directory path relative to the "
                            "working directory."
                        ),
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": (
                "Execute one command from the fixed "
                "course allowlist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": [
                            "echo lab03-shell-ok",
                            "pwd",
                            "cd",
                        ],
                        "description": (
                            "Exact command to execute. "
                            "Additional shell operators are "
                            "not allowed."
                        ),
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
]


def get_tool_schemas() -> list[dict[str, Any]]:
    """Return an independent copy of the tool schemas."""
    return deepcopy(TOOL_SCHEMAS)


class ToolInputError(ValueError):
    """Raised when a tool receives invalid input."""


class ToolExecutionError(RuntimeError):
    """Raised when an allowed tool cannot finish execution."""


class BasicTools:
    """Provide controlled file and command tools for the Agent."""

    def __init__(
        self,
        working_directory: str | Path,
        file_tool: FileTool | None = None,
    ) -> None:
        root = Path(working_directory).resolve()

        if not root.exists():
            raise ValueError(
                f"Working directory does not exist: {root}"
            )

        if not root.is_dir():
            raise ValueError(
                f"Working directory is not a directory: {root}"
            )

        self.root = root
        self.file_tool = file_tool or FileTool()

    @property
    def schemas(self) -> list[dict[str, Any]]:
        """Return the schemas exposed to the model."""
        return get_tool_schemas()

    def _resolve_path(
        self,
        relative_path: str,
    ) -> Path:
        if not isinstance(relative_path, str):
            raise ToolInputError("path must be a string")

        if not relative_path.strip():
            raise ToolInputError("path must not be empty")

        supplied_path = Path(relative_path)

        if supplied_path.is_absolute():
            raise ToolInputError(
                "absolute paths are not allowed"
            )

        target = (self.root / supplied_path).resolve()

        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise ToolInputError(
                "path must stay inside the working directory"
            ) from exc

        return target

    @staticmethod
    def _validate_arguments(
        arguments: dict[str, Any],
        expected: dict[str, type],
    ) -> None:
        received_names = set(arguments)
        expected_names = set(expected)

        missing = expected_names - received_names
        unexpected = received_names - expected_names

        if missing:
            names = ", ".join(sorted(missing))
            raise ToolInputError(
                f"missing required arguments: {names}"
            )

        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise ToolInputError(
                f"unexpected arguments: {names}"
            )

        for name, expected_type in expected.items():
            value = arguments[name]

            if not isinstance(value, expected_type):
                raise ToolInputError(
                    f"{name} must be a "
                    f"{expected_type.__name__}"
                )

    def read_file(
        self,
        path: str,
    ) -> dict[str, Any]:
        """Read a UTF-8 text file from the working directory."""
        target = self._resolve_path(path)
        result = self.file_tool.read(
            target,
            encoding="utf-8",
        )

        return {
            "ok": True,
            "path": path,
            "content": result.content,
            "encoding": result.encoding,
            "size": result.size,
            "line_count": result.line_count,
        }

    def write_file(
        self,
        path: str,
        content: str,
    ) -> dict[str, Any]:
        """Create a UTF-8 file without overwriting it."""
        if not isinstance(content, str):
            raise ToolInputError(
                "content must be a string"
            )

        target = self._resolve_path(path)
        result = self.file_tool.write(
            target,
            content,
            encoding="utf-8",
            overwrite=False,
        )

        return {
            "ok": True,
            "path": path,
            "bytes_written": result.bytes_written,
            "created": result.created,
        }

    def list_dir(
        self,
        path: str,
    ) -> dict[str, Any]:
        """List one directory inside the working directory."""
        target = self._resolve_path(path)
        entries = self.file_tool.list_dir(target)

        serialized_entries = []

        for entry in entries:
            entry_path = Path(entry.path).resolve()
            relative_path = entry_path.relative_to(
                self.root
            )

            serialized_entries.append({
                "name": entry.name,
                "path": relative_path.as_posix(),
                "is_dir": entry.is_dir,
                "size": entry.size,
            })

        return {
            "ok": True,
            "path": path,
            "entries": serialized_entries,
        }

    def execute_command(
        self,
        command: str,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Execute one exact command from the allowlist."""
        if not isinstance(command, str):
            raise ToolInputError(
                "command must be a string"
            )

        if os.name == "nt":
            allowed_commands = {
                "echo lab03-shell-ok",
                "cd",
            }
            process_args = [
                "cmd.exe",
                "/d",
                "/s",
                "/c",
                command,
            ]
        else:
            allowed_commands = {
                "echo lab03-shell-ok",
                "pwd",
            }
            process_args = [
                "/bin/sh",
                "-c",
                command,
            ]

        if command not in allowed_commands:
            raise ToolInputError(
                f"command is not allowed: {command}"
            )

        try:
            completed = subprocess.run(
                process_args,
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolExecutionError(
                f"command timed out after {timeout} seconds"
            ) from exc
        except OSError as exc:
            raise ToolExecutionError(
                f"could not start command: {exc}"
            ) from exc

        return {
            "ok": completed.returncode == 0,
            "command": command,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
        }

    def dispatch(
        self,
        name: str,
        arguments_json: str,
    ) -> dict[str, Any]:
        """Validate and execute one model-requested tool call."""
        try:
            if not isinstance(name, str):
                raise ToolInputError(
                    "tool name must be a string"
                )

            if not isinstance(arguments_json, str):
                raise ToolInputError(
                    "tool arguments must be a JSON string"
                )

            try:
                arguments = json.loads(arguments_json)
            except json.JSONDecodeError as exc:
                raise ToolInputError(
                    "tool arguments are not valid JSON"
                ) from exc

            if not isinstance(arguments, dict):
                raise ToolInputError(
                    "tool arguments must be a JSON object"
                )

            if name == "read_file":
                self._validate_arguments(
                    arguments,
                    {"path": str},
                )
                return self.read_file(**arguments)

            if name == "write_file":
                self._validate_arguments(
                    arguments,
                    {
                        "path": str,
                        "content": str,
                    },
                )
                return self.write_file(**arguments)

            if name == "list_dir":
                self._validate_arguments(
                    arguments,
                    {"path": str},
                )
                return self.list_dir(**arguments)

            if name == "execute_command":
                self._validate_arguments(
                    arguments,
                    {"command": str},
                )
                return self.execute_command(**arguments)

            raise ToolInputError(
                f"unknown tool: {name}"
            )

        except (
            ToolInputError,
            ToolExecutionError,
            FileToolError,
        ) as exc:
            return {
                "ok": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }