"""Read/write ONLY this project's generic Windows Credential Manager entry."""

import ctypes
from ctypes import wintypes
import os


TARGET = "RiskCaseAgent/SiliconFlow"


class CredentialStoreError(ValueError):
    pass


class Credential(ctypes.Structure):
    _fields_ = [("Flags",wintypes.DWORD),("Type",wintypes.DWORD),("TargetName",wintypes.LPWSTR),
        ("Comment",wintypes.LPWSTR),("LastWritten",wintypes.FILETIME),("CredentialBlobSize",wintypes.DWORD),
        ("CredentialBlob",ctypes.POINTER(ctypes.c_ubyte)),("Persist",wintypes.DWORD),
        ("AttributeCount",wintypes.DWORD),("Attributes",ctypes.c_void_p),
        ("TargetAlias",wintypes.LPWSTR),("UserName",wintypes.LPWSTR)]


def _api():
    if os.name != "nt": raise CredentialStoreError("Windows credential store unavailable")
    api = ctypes.WinDLL("advapi32",use_last_error=True)
    api.CredReadW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(Credential))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(Credential),wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def validate_key(key):
    if not isinstance(key,str) or not 8 <= len(key) <= 1280 or any(char.isspace() or ord(char)<32 for char in key):
        raise CredentialStoreError("Invalid API credential format")
    if len(key.encode("utf-16-le")) > 2560: raise CredentialStoreError("Credential too long")
    return key


def load_key():
    if os.name != "nt": return None
    api = _api()
    pointer = ctypes.POINTER(Credential)()
    if not api.CredReadW(TARGET,1,0,ctypes.byref(pointer)):
        if ctypes.get_last_error() == 1168: return None
        raise CredentialStoreError("Windows credential read failed")
    try:
        size = pointer.contents.CredentialBlobSize
        if not 0 < size <= 2560: raise CredentialStoreError("Invalid stored credential size")
        try:
            key = ctypes.string_at(pointer.contents.CredentialBlob,size).decode("utf-16-le")
        except UnicodeError:
            raise CredentialStoreError("Invalid stored credential encoding") from None
        return validate_key(key)
    finally:
        api.CredFree(pointer)


def save_key(key):
    key = validate_key(key)
    api = _api()
    encoded = key.encode("utf-16-le")
    blob = (ctypes.c_ubyte*len(encoded)).from_buffer_copy(encoded)
    record = Credential(Type=1,TargetName=TARGET,Comment="RiskCase Agent SiliconFlow API",
        CredentialBlobSize=len(encoded),CredentialBlob=blob,Persist=2,UserName="SiliconFlow")
    try:
        if not api.CredWriteW(ctypes.byref(record),0): raise CredentialStoreError("Windows credential write failed")
    finally:
        ctypes.memset(blob,0,len(encoded))


def delete_key():
    api = _api()
    if not api.CredDeleteW(TARGET,1,0) and ctypes.get_last_error() != 1168:
        raise CredentialStoreError("Windows credential deletion failed")
