"""Owned Windows job; creation is suspended until containment succeeds."""

import ctypes as C
from ctypes import wintypes as W
import subprocess
import threading
from .process_resume import SuspendedChildResume
from sm64_events.core.childproc import quiet_spawn_kwargs


class Basic(C.Structure):
    _fields_ = [
        ("process_time", C.c_int64),
        ("job_time", C.c_int64),
        ("flags", W.DWORD),
        ("min_working", C.c_size_t),
        ("max_working", C.c_size_t),
        ("processes", W.DWORD),
        ("affinity", C.c_size_t),
        ("priority", W.DWORD),
        ("scheduling", W.DWORD),
    ]


class Extended(C.Structure):
    _fields_ = [
        ("basic", Basic),
        ("io", C.c_uint64 * 6),
        ("process_memory", C.c_size_t),
        ("job_memory", C.c_size_t),
        ("peak_process", C.c_size_t),
        ("peak_job", C.c_size_t),
    ]


class Accounting(C.Structure):
    _fields_ = [
        ("user_time", C.c_int64),
        ("kernel_time", C.c_int64),
        ("period_user", C.c_int64),
        ("period_kernel", C.c_int64),
        ("page_faults", W.DWORD),
        ("total_processes", W.DWORD),
        ("active_processes", W.DWORD),
        ("terminated_processes", W.DWORD),
    ]


class OwnedJob:
    def __init__(self):
        self.lock = threading.Lock()
        self.child = None
        self.closed = False
        self.resume_evidence = None
        self.spawning = False
        self.assigned = False
        self.empty = False
        self.active = None
        self.total = 0
        self.termination_requested = False
        self.error = None
        self.k = C.WinDLL("kernel32", use_last_error=True)
        for name, args, result in [
            ("CreateJobObjectW", [C.c_void_p, W.LPCWSTR], W.HANDLE),
            (
                "SetInformationJobObject",
                [W.HANDLE, C.c_int, C.c_void_p, W.DWORD],
                W.BOOL,
            ),
            ("AssignProcessToJobObject", [W.HANDLE, W.HANDLE], W.BOOL),
            ("CloseHandle", [W.HANDLE], W.BOOL),
            ("TerminateJobObject", [W.HANDLE, W.UINT], W.BOOL),
            (
                "QueryInformationJobObject",
                [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)],
                W.BOOL,
            ),
        ]:
            fn = getattr(self.k, name)
            fn.argtypes = args
            fn.restype = result
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        info = Extended()
        info.basic.flags = 0x2000
        if not self.k.SetInformationJobObject(
            self.handle, 9, C.byref(info), C.sizeof(info)
        ):
            error = C.WinError(C.get_last_error())
            self.k.CloseHandle(self.handle)
            self.handle = None
            raise error

    def spawn(self, command, cwd):
        with self.lock:
            if self.closed or self.child is not None or self.spawning:
                raise RuntimeError("job unavailable for creation")
            self.spawning = True
        child = None
        try:
            kwargs = quiet_spawn_kwargs()
            kwargs["creationflags"] |= 4
            child = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                **kwargs,
            )
            with self.lock:
                self.child = child
                if self.closed or not self.handle:
                    raise RuntimeError("job stopped during creation")
                if not self.k.AssignProcessToJobObject(
                    self.handle, W.HANDLE(child._handle)
                ):
                    raise C.WinError(C.get_last_error())
                self.assigned = True
                # This startup lock never covers helper pipes or encoder operations.
                self.resume_evidence = SuspendedChildResume(self.k).resume(child)
        except BaseException:
            # Assignment can fail while the exact owned Popen child is suspended.
            if child is not None and child.poll() is None:
                child.kill()
            self.stop()
            raise
        finally:
            with self.lock:
                self.spawning = False
        return child

    def stop(self):
        with self.lock:
            self.closed = True
            child = self.child
            if self.handle and not self.termination_requested:
                if self.k.TerminateJobObject(self.handle, 0xE0000001):
                    self.termination_requested = True
                else:
                    self.error = "TerminateJobObject failed: " + str(C.get_last_error())
            # An unassigned suspended process cannot create descendants. Assigned
            # children are disposed by the private job, including venv redirectors.
            if not self.assigned and child is not None and child.poll() is None:
                try:
                    child.kill()
                except OSError as exc:
                    self.error = (
                        "owned suspended child termination failed: " + str(exc)[:160]
                    )

    def snapshot(self):
        with self.lock:
            if self.handle:
                info = Accounting()
                size = W.DWORD()
                if not self.k.QueryInformationJobObject(
                    self.handle, 1, C.byref(info), C.sizeof(info), C.byref(size)
                ):
                    self.error = "QueryInformationJobObject failed: " + str(
                        C.get_last_error()
                    )
                    self.active = None
                elif size.value != C.sizeof(info):
                    self.error = "job accounting size mismatch"
                    self.active = None
                else:
                    self.active = info.active_processes
                    self.total = info.total_processes
                    child_exited = self.child is None or self.child.poll() is not None
                    if (
                        self.closed
                        and not self.spawning
                        and self.active == 0
                        and child_exited
                    ):
                        # Keep the accounting handle until the complete private job
                        # is observed empty. Launcher exit alone is insufficient.
                        self.empty = True
                        if self.k.CloseHandle(self.handle):
                            self.handle = None
                        else:
                            self.error = "job CloseHandle failed: " + str(
                                C.get_last_error()
                            )
            return {
                "stopped": self.closed,
                "assigned": self.assigned,
                "spawning": self.spawning,
                "termination_requested": self.termination_requested,
                "active_processes": self.active,
                "total_processes": self.total,
                "empty": self.empty,
                "handle_closed": self.handle is None,
                "error": self.error,
            }
