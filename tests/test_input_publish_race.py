"""Cross-platform atomic input publication: collisions differ from I/O failures."""

import errno
from pathlib import Path

import pytest

from tests.test_merchant_research import manifest
from tests.test_service import service, submit


@pytest.mark.parametrize("collision", [errno.EEXIST, errno.ENOTEMPTY])
def test_identical_directory_winner_is_verified_and_reused(service, monkeypatch, collision):
    original = Path.rename
    def another_publisher_won(staging, target):
        if not staging.name.startswith("staging-"): return original(staging, target)
        # Simulate the other publisher's complete destination, not an empty
        # directory or a skipped hash check.
        original(staging, target)
        staging.mkdir()
        raise OSError(collision, "Another publisher installed the same input")
    monkeypatch.setattr(Path, "rename", another_publisher_won)
    identity = submit(service)
    assert submit(service) == identity
    assert not list((service.settings.directory / "inputs").glob("staging-*"))


def test_permission_failure_is_not_treated_as_a_collision(service, monkeypatch):
    def denied(staging, target): raise PermissionError(errno.EACCES, "Synthetic permission failure")
    monkeypatch.setattr(Path, "rename", denied)
    with pytest.raises(PermissionError): submit(service)
    assert not list((service.settings.directory / "inputs").glob("staging-*"))


def test_nonidentical_directory_winner_is_rejected(service, monkeypatch):
    original = Path.rename
    def corrupt_winner(staging, target):
        original(staging, target)
        (target / "facts.sqlite").write_bytes(b"different immutable input")
        staging.mkdir()
        raise OSError(errno.ENOTEMPTY, "Existing destination differs")
    monkeypatch.setattr(Path, "rename", corrupt_winner)
    with pytest.raises(ValueError, match="frozen_case_changed"): submit(service)
