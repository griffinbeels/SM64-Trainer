"""Resume only the one initial thread of an owned suspended, assigned child.

Toolhelp is read-only. The opened thread handle is checked against the original
still-live Popen process HANDLE before ResumeThread, protecting against PID/TID
reuse. Require previous suspend count exactly1; no retry or guessed resume.
"""

import ctypes as C
from ctypes import wintypes as W


class ThreadEntry(C.Structure):
    _fields_ = [
        ("size", W.DWORD),
        ("usage", W.DWORD),
        ("thread_id", W.DWORD),
        ("owner_pid", W.DWORD),
        ("base_priority", W.LONG),
        ("delta_priority", W.LONG),
        ("flags", W.DWORD),
    ]


class SuspendedChildResume:
    def __init__(self, kernel):
        self.k = kernel
        signatures = [
            ("CreateToolhelp32Snapshot", [W.DWORD, W.DWORD], W.HANDLE),
            ("Thread32First", [W.HANDLE, C.POINTER(ThreadEntry)], W.BOOL),
            ("Thread32Next", [W.HANDLE, C.POINTER(ThreadEntry)], W.BOOL),
            ("OpenThread", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            ("GetProcessId", [W.HANDLE], W.DWORD),
            ("GetProcessIdOfThread", [W.HANDLE], W.DWORD),
            ("WaitForSingleObject", [W.HANDLE, W.DWORD], W.DWORD),
            ("ResumeThread", [W.HANDLE], W.DWORD),
            ("CloseHandle", [W.HANDLE], W.BOOL),
        ]
        for name, args, result in signatures:
            fn = getattr(self.k, name)
            fn.argtypes = args
            fn.restype = result

    def resume(self, child):
        snapshot = self.k.CreateToolhelp32Snapshot(4, 0)
        if snapshot in (None, 0, C.c_void_p(-1).value):
            raise C.WinError(C.get_last_error())
        matches = []
        scanned = 0
        try:
            entry = ThreadEntry()
            entry.size = C.sizeof(entry)
            more = self.k.Thread32First(snapshot, C.byref(entry))
            while more:
                scanned += 1
                if (
                    scanned > 65536
                    or entry.size < ThreadEntry.owner_pid.offset + C.sizeof(W.DWORD)
                ):
                    raise RuntimeError("thread snapshot bound/shape")
                if entry.owner_pid == child.pid:
                    matches.append(entry.thread_id)
                if len(matches) > 1:
                    raise RuntimeError("suspended child has ambiguous initial threads")
                entry.size = C.sizeof(entry)
                more = self.k.Thread32Next(snapshot, C.byref(entry))
            if C.get_last_error() != 18:
                raise C.WinError(C.get_last_error())
        finally:
            self.k.CloseHandle(snapshot)
        if len(matches) != 1:
            raise RuntimeError("suspended child has no unique initial thread")
        thread = self.k.OpenThread(
            0x0802, False, matches[0]
        )  # QUERY_LIMITED_INFORMATION | SUSPEND_RESUME
        if not thread:
            raise C.WinError(C.get_last_error())
        try:
            original = W.HANDLE(child._handle)
            if (
                self.k.GetProcessId(original) != child.pid
                or self.k.GetProcessIdOfThread(thread) != child.pid
                or self.k.WaitForSingleObject(original, 0) != 0x102
            ):
                raise RuntimeError(
                    "opened thread is not in the original live owned process"
                )
            previous = self.k.ResumeThread(thread)
            if previous != 1:
                raise RuntimeError("initial thread suspend count was not exactly one")
            return {
                "owner_pid": child.pid,
                "thread_id": matches[0],
                "snapshot_entries": scanned,
                "previous_suspend_count": previous,
            }
        finally:
            self.k.CloseHandle(thread)
