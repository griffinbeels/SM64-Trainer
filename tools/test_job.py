"""A Windows job owns the pytest child tree, including children missed by polling.

Closing the handle (also on runner crash) terminates only members of this job.
The child starts suspended so it cannot spawn outside the job before assignment.
This is test infrastructure; it never joins OBS or another runner to the job.
"""
import ctypes
from ctypes import wintypes as wt
import subprocess

import psutil

from sm64_events.core.childproc import quiet_spawn_kwargs


class _BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                ("flags", wt.DWORD), ("min_working", ctypes.c_size_t),
                ("max_working", ctypes.c_size_t), ("processes", wt.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wt.DWORD),
                ("scheduling", wt.DWORD)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class TestJob:
    """Context manager around one child; fail closed if containment cannot start."""

    __test__ = False

    def __init__(self, command, cwd):
        self.command = command
        self.cwd = cwd
        self.child = None
        self.handle = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wt.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]

    def __enter__(self):
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            kwargs = quiet_spawn_kwargs()
            kwargs["creationflags"] |= 0x4  # CREATE_SUSPENDED: no spawn-before-assignment race
            self.child = subprocess.Popen(self.command, cwd=self.cwd, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT, text=True, errors="replace", **kwargs)
            if not self.kernel.AssignProcessToJobObject(self.handle, wt.HANDLE(self.child._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            psutil.Process(self.child.pid).resume()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self.child

    def __exit__(self, *exc):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.child is not None:
            if self.child.poll() is None:
                self.child.kill()  # Also covers failed assignment, before membership.
            self.child.wait()
