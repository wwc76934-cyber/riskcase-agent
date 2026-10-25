"""Credential API contract tests use an in-memory fake, never the user's store."""

import ctypes
from types import SimpleNamespace

import pytest

from app.llm import credentials
from app.cli import credentials as cli
from app.cli import investigate_live


class MemoryAPI:
    def __init__(self): self.value,self.error,self.freed = None,1168,0
    def CredWriteW(self,ptr,flags):
        record = ctypes.cast(ptr,ctypes.POINTER(credentials.Credential)).contents
        assert record.TargetName == credentials.TARGET and record.Type == 1 and record.Persist == 2
        self.value = ctypes.string_at(record.CredentialBlob,record.CredentialBlobSize)
        self.original_blob = record.CredentialBlob
        return True
    def CredReadW(self,target,kind,flags,out):
        assert target == credentials.TARGET and kind == 1
        if self.value is None: return False
        self.blob = (ctypes.c_ubyte*len(self.value)).from_buffer_copy(self.value)
        self.record = credentials.Credential(CredentialBlob=self.blob,CredentialBlobSize=len(self.value))
        ctypes.cast(out,ctypes.POINTER(ctypes.POINTER(credentials.Credential)))[0] = ctypes.pointer(self.record)
        return True
    def CredFree(self,ptr): self.freed += 1
    def CredDeleteW(self,*args): self.value = None; return True


@pytest.fixture
def store(monkeypatch):
    api = MemoryAPI()
    monkeypatch.setattr(credentials,"os",SimpleNamespace(name="nt"))
    monkeypatch.setattr(credentials,"_api",lambda:api)
    monkeypatch.setattr(ctypes,"get_last_error",lambda:api.error,raising=False)
    return api


def test_named_entry_round_trip_and_delete_only_fake_store(store):
    assert credentials.load_key() is None
    credentials.save_key("fake-test-credential")
    assert credentials.load_key() == "fake-test-credential" and store.freed == 1
    credentials.delete_key()
    assert credentials.load_key() is None


@pytest.mark.parametrize("key",["", "short", "has whitespace", "x\nsecret", "x"*1281, None])
def test_invalid_key_never_written(store,key):
    with pytest.raises(credentials.CredentialStoreError): credentials.save_key(key)
    assert store.value is None


def test_access_denied_is_not_reported_as_missing(store):
    store.error = 5
    with pytest.raises(credentials.CredentialStoreError,match="read failed"): credentials.load_key()


def test_automatic_saved_key_and_environment_override(monkeypatch):
    monkeypatch.setattr(investigate_live,"load_saved_key",lambda:"saved-test-key")
    monkeypatch.delenv("SILICONFLOW_API_KEY",raising=False)
    assert investigate_live.local_key() == "saved-test-key"
    monkeypatch.setenv("SILICONFLOW_API_KEY","environment-test-key")
    assert investigate_live.local_key() == "environment-test-key"


def test_status_never_prints_secret(store,capsys):
    credentials.save_key("fake-hidden-credential")
    assert cli.main(["--status"]) == 0
    output = capsys.readouterr().out
    assert '"present"' in output and "fake-hidden-credential" not in output


def test_setup_refuses_echo_fallback(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin,"isatty",lambda:False)
    assert cli.main(["--set"]) == 2
