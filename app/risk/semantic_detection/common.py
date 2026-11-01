import hashlib
import json
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def fingerprint(root):
    root = Path(root)
    return {str(p.relative_to(root)).replace('\\', '/'): digest(p)
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def peak_working_set_bytes():
    """Whole process peak, includes framework/data; not isolated per-model allocation."""
    import os
    if os.name != 'nt':
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    counts = Counters(); counts.cb = ctypes.sizeof(counts)
    kernel, psapi = ctypes.WinDLL('kernel32'), ctypes.WinDLL('psapi')
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counts), counts.cb):
        raise OSError('Cannot measure process working set')
    return int(counts.PeakWorkingSetSize)
