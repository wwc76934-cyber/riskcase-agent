"""Filesystem regressions for the shared durable JSON writer; no model calls."""
import json
import os

import pytest

from app.service import runtime


def test_atomic_json_handles_valid_long_destination_name(tmp_path):
    # The destination is valid. A temporary name must not add its entire basename
    # again and exceed a component limit (POSIX) or a Windows path limit.
    length = min(235, 240 - len(str(tmp_path)) - 1) if os.name == "nt" else 235
    if length < 16:
        pytest.skip("Test parent itself leaves no supported Windows destination")
    target = tmp_path / ("r" * (length - 5) + ".json")
    value = {"message": "调查证据保存", "revision": 1}
    runtime.write_json(target, value)
    assert json.loads(target.read_text(encoding="utf-8")) == value
    assert list(tmp_path.iterdir()) == [target]


def test_failed_atomic_replacement_keeps_old_document_and_removes_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "report.json"
    runtime.write_json(target, {"revision": 1})

    def denied_replace(source, destination):
        raise PermissionError("simulated destination sharing conflict")

    monkeypatch.setattr(runtime.os, "replace", denied_replace)
    with pytest.raises(PermissionError):
        runtime.write_json(target, {"revision": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"revision": 1}
    assert list(tmp_path.iterdir()) == [target]
