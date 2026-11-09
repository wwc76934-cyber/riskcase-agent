"""Read-only Windows process totals; retain CPU accounting across host pauses."""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pid', required=True, type=int)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if os.name != 'nt': raise RuntimeError('This observer is explicitly Windows-only')
    output = Path(args.output); output.mkdir(parents=True, exist_ok=False)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    class Memory(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)]+[(n, ctypes.c_size_t) for n in (
            'peak_working_set', 'working_set', 'peak_paged_pool', 'paged_pool', 'peak_nonpaged_pool',
            'nonpaged_pool', 'pagefile', 'peak_pagefile', 'private')]
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000 | 0x0010, False, args.pid)
    if not handle: raise ctypes.WinError(ctypes.get_last_error())
    number = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
    first, last, maximum = None, None, 0
    try:
        with (output/'samples.jsonl').open('x', encoding='utf8') as stream:
            while True:
                created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
                if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
                    raise ctypes.WinError(ctypes.get_last_error())
                code = wintypes.DWORD()
                if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)): raise ctypes.WinError(ctypes.get_last_error())
                memory = Memory(); memory.cb = ctypes.sizeof(Memory)
                available = psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb)
                maximum = max(maximum, int(memory.peak_working_set) if available else 0)
                last = {'observed_at_UTC': datetime.now(timezone.utc).isoformat(), 'pid': args.pid,
                        'created_at_UTC': datetime.fromtimestamp(number(created)/1e7-11644473600, timezone.utc).isoformat(),
                        'CPU_seconds_since_process_start': (number(system)+number(user))/1e7,
                        'whole_process_peak_bytes': int(memory.peak_working_set) if available else None,
                        'working_set_bytes': int(memory.working_set) if available else None,
                        'exit_code': int(code.value), 'running': code.value == 259}
                if first is None: first = last
                stream.write(json.dumps(last)+'\n'); stream.flush()
                (output/'latest.json').write_text(json.dumps(last, indent=2)+'\n', encoding='utf8')
                if not last['running']: break
                time.sleep(30)
        result = {'status': 'observed_process_exited', 'first_observation': first, 'final_observation': last,
                  'exact_total_process_CPU_seconds': last['CPU_seconds_since_process_start'],
                  'largest_observed_peak_bytes': maximum,
                  'limit': 'CPU since original process start includes all its work, not per-model CPU. Peak observed while alive is a lower bound on final peak if memory info is unavailable after exit. No target-process mutation.'}
        (output/'report.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf8')
    finally: kernel.CloseHandle(handle)


if __name__ == '__main__': main()
