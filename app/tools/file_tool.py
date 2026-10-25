from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import chardet


class FileToolError(Exception):
    """Base exception for all FileTool errors."""


class FileReadError(FileToolError):
    """Raised when reading a file fails."""


class FileWriteError(FileToolError):
    """Raised when writing a file fails."""


@dataclass
class FileContent:
    """Structured result returned by FileTool.read()."""

    path: str
    content: str
    encoding: str
    size: int
    line_count: int


@dataclass
class WriteResult:
    """Structured result returned by FileTool.write()."""

    path: str
    bytes_written: int
    created: bool

@dataclass
class DirEntry:
    """Structured description of a directory entry."""

    name: str
    path: str
    is_dir: bool
    size: int

class FileTool:
    """File read/write tool for the AI Coding Agent CLI."""

    def read(self, path: str | Path, encoding: str | None = "utf-8") -> FileContent:
        """Read a text file and return structured file content."""
        target = Path(path)

        if not target.exists():
            raise FileReadError(f"File does not exist: {target}")

        if not target.is_file():
            raise FileReadError(f"Path is not a regular file: {target}")

        try:
            if encoding is None:
                raw_data = target.read_bytes()
                detected = chardet.detect(raw_data)
                detected_encoding = detected.get("encoding") or "utf-8"
                content = raw_data.decode(detected_encoding)
                used_encoding = detected_encoding
            else:
                content = target.read_text(encoding=encoding)
                used_encoding = encoding
        except UnicodeDecodeError as e:
            raise FileReadError(
                f"Failed to decode file with encoding={encoding}: {e}"
            ) from e
        except OSError as e:
            raise FileReadError(f"Failed to read file: {e}") from e

        size = len(content.encode(used_encoding))

        if content == "":
            line_count = 0
        elif content.endswith("\n"):
            line_count = content.count("\n")
        else:
            line_count = content.count("\n") + 1

        return FileContent(
            path=str(target),
            content=content,
            encoding=used_encoding,
            size=size,
            line_count=line_count,
        )

    def write(
        self,
        path: str | Path,
        content: str,
        encoding: str = "utf-8",
        overwrite: bool = True,
    ) -> WriteResult:
        """Write text content to a file and return structured write result."""
        target = Path(path)

        if target.exists() and not overwrite:
            raise FileWriteError(f"File already exists and overwrite is disabled: {target}")

        created = not target.exists()

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding=encoding)
        except OSError as e:
            raise FileWriteError(f"Failed to write file: {e}") from e

        return WriteResult(
            path=str(target),
            bytes_written=len(content.encode(encoding)),
            created=created,
        )

    def list_dir(
        self,
        path: str | Path,
        recursive: bool = False,
        pattern: str = "*",
    ) -> list[DirEntry]:
        """List directory entries and return structured results."""
        target = Path(path)

        if not target.exists():
            raise FileReadError(f"Directory does not exist: {target}")

        if not target.is_dir():
            raise FileReadError(f"Path is not a directory: {target}")

        try:
            children = list(target.rglob(pattern)) if recursive else list(target.iterdir())
        except OSError as e:
            raise FileReadError(f"Failed to list directory: {e}") from e

        entries: list[DirEntry] = []

        for child in sorted(children):
            try:
                size = child.stat().st_size if child.is_file() else 0
            except OSError:
                size = 0

            entries.append(
                DirEntry(
                    name=child.name,
                    path=str(child),
                    is_dir=child.is_dir(),
                    size=size,
                )
            )

        return entries

    def append(
        self,
        path: str | Path,
        content: str,
        encoding: str = "utf-8",
    ) -> WriteResult:
        """Append text content to a file and return structured write result."""
        target = Path(path)
        created = not target.exists()

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a", encoding=encoding) as f:
                f.write(content)
        except OSError as e:
            raise FileWriteError(f"Failed to append file: {e}") from e

        return WriteResult(
            path=str(target),
            bytes_written=len(content.encode(encoding)),
            created=created,
        )

    def read_lines(
        self,
        path: str | Path,
        encoding: str = "utf-8",
    ) -> list[str]:
        """Read a text file and return a list of lines without line endings."""
        file_content = self.read(path, encoding=encoding)
        return file_content.content.splitlines()

    def read_bytes(self, path: str | Path) -> bytes:
        """Read a binary file and return raw bytes."""
        target = Path(path)

        if not target.exists():
            raise FileReadError(f"File does not exist: {target}")

        if not target.is_file():
            raise FileReadError(f"Path is not a regular file: {target}")

        try:
            return target.read_bytes()
        except OSError as e:
            raise FileReadError(f"Failed to read binary file: {e}") from e

    def write_bytes(
        self,
        path: str | Path,
        data: bytes,
        overwrite: bool = True,
    ) -> WriteResult:
        """Write raw bytes to a binary file and return structured write result."""
        target = Path(path)

        if target.exists() and not overwrite:
            raise FileWriteError(f"File already exists and overwrite is disabled: {target}")

        created = not target.exists()

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as e:
            raise FileWriteError(f"Failed to write binary file: {e}") from e

        return WriteResult(
            path=str(target),
            bytes_written=len(data),
            created=created,
        )

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Print logs around a batch of file operations."""
        print("Batch started.")
        try:
            yield
        finally:
            print("Batch finished.")