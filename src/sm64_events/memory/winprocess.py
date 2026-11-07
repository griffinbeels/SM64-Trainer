# src/sm64_events/memory/winprocess.py
"""Another process's memory, opened with only the rights each use needs.

The Trainer reads Project64 and asks Windows for no more than reading takes:
never the debug privilege, never an all-access handle. Until 2026-11-06 the
reader attached through pymem, whose default open enabled SeDebugPrivilege and
asked for PROCESS_ALL_ACCESS on every attach -- what password stealers and
code injectors do. After Defender flagged our renderer, the clean-behavior
review named it among the things our software does that look like malware,
so the open is ours now.

The rights each call needs, from Microsoft's "Process Security and Access
Rights" table and each function's page, checked against a child process on
Windows 11 (2026-11-06):

- ReadProcessMemory: PROCESS_VM_READ.
- VirtualQueryEx: PROCESS_QUERY_INFORMATION, the right its page names.
  Windows 11 also accepted QUERY_LIMITED, but nothing promises that elsewhere.
- Waiting on the process (is it still running?): SYNCHRONIZE.
- WriteProcessMemory: PROCESS_VM_WRITE | PROCESS_VM_OPERATION. VM_WRITE
  alone was refused.

`READ_RIGHTS` is the reader's handle; `WRITE_RIGHTS` is a separate handle
opened for one write and closed after it. Finding a process by name opens no
process at all: it reads the kernel's process table (`core/procmem.py`).
"""
import ctypes
from ctypes import wintypes

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
SYNCHRONIZE = 0x00100000

READ_RIGHTS = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION | SYNCHRONIZE
WRITE_RIGHTS = PROCESS_VM_WRITE | PROCESS_VM_OPERATION

_WAIT_TIMEOUT = 0x102


class ProcessAccessError(OSError):
    """Windows refused to open, read or write another process."""


_kernel32 = None  # bound on first use; tests put a fake here


def kernel32():
    """kernel32 with explicit argtypes, on our own WinDLL so the signatures
    cannot collide with another module's (64-bit handles and addresses must
    not be truncated to a C int)."""
    global _kernel32
    if _kernel32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in (
            ("OpenProcess", [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
            ("ReadProcessMemory", [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
            ("WriteProcessMemory", [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)], wintypes.BOOL),
            ("VirtualQueryEx", [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t],
             ctypes.c_size_t),
            ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        ):
            fn = getattr(k, name)
            fn.argtypes, fn.restype = args, result
        _kernel32 = k
    return _kernel32


def pids_named(exe: str) -> list[int]:
    """Pids of the processes whose image is named `exe` (case-insensitive),
    in the kernel's table order. Reads one machine snapshot and opens none of
    them. Empty off Windows or when the snapshot fails."""
    from sm64_events.core.procmem import machine_snapshot
    snapshot = machine_snapshot()
    if snapshot is None:
        return []
    want = exe.lower()
    return [p.pid for p in snapshot.procs.values() if p.exe.lower() == want]


class ProcessHandle:
    """One open handle to process `pid` holding exactly `access`."""

    def __init__(self, pid: int, access: int):
        self.pid, self.access = int(pid), access
        self.handle = kernel32().OpenProcess(access, False, self.pid)
        if not self.handle:
            raise ProcessAccessError(f"OpenProcess(pid {self.pid}, 0x{access:X}) failed: "
                                     f"error {ctypes.get_last_error()}")

    def read(self, address: int, size: int) -> bytes:
        """`size` bytes at `address`. ReadProcessMemory fails whole when any
        of the span cannot be read, so a success is the full span."""
        buffer = ctypes.create_string_buffer(size)
        if not kernel32().ReadProcessMemory(self.handle, address, buffer, size, None):
            raise ProcessAccessError(f"ReadProcessMemory(0x{address:X}, {size}) failed: "
                                     f"error {ctypes.get_last_error()}")
        return buffer.raw

    def write(self, address: int, data: bytes) -> None:
        if not kernel32().WriteProcessMemory(self.handle, address, data, len(data), None):
            raise ProcessAccessError(f"WriteProcessMemory(0x{address:X}, {len(data)}) failed: "
                                     f"error {ctypes.get_last_error()}")

    def alive(self) -> bool:
        """The process has not exited. A handle keeps an exited process's pid
        from naming anything, so a reused pid reads as not alive."""
        return self.handle is not None and kernel32().WaitForSingleObject(self.handle, 0) == _WAIT_TIMEOUT

    def close(self) -> None:
        if self.handle is not None:
            kernel32().CloseHandle(self.handle)
            self.handle = None

    def __enter__(self) -> "ProcessHandle":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
