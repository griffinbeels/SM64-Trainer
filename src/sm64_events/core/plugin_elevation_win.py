"""Windows-only probes and the narrow, user-initiated UAC launch boundary."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes as w
from pathlib import Path
import subprocess

from sm64_events.core.plugin_installation import ElevationCancelled, PluginInstallError


def write_access_error(path: Path) -> int:
    """Recover GetLastError that Python's CRT file opens frequently discard.

    OPEN_EXISTING never creates/truncates or writes the destination. All sharing
    modes are allowed, so a sharing violation is evidence from another handle.
    """
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                  w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x40000000, 7, None, 3, 0, None)
    if handle == w.HANDLE(-1).value:
        return ctypes.get_last_error()
    kernel.CloseHandle(handle)
    return 0


def file_users(path: Path) -> tuple[int, ...]:
    """Restart Manager's positive evidence of processes holding this file."""
    class UniqueProcess(ctypes.Structure):
        _fields_ = [("pid", w.DWORD), ("started", w.FILETIME)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [("process", UniqueProcess), ("app", w.WCHAR * 256),
                    ("service", w.WCHAR * 64), ("kind", w.DWORD),
                    ("status", w.ULONG), ("session", w.DWORD), ("restart", w.BOOL)]

    rm = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    session, key = w.DWORD(), ctypes.create_unicode_buffer(33)
    if rm.RmStartSession(ctypes.byref(session), 0, key):
        return ()
    try:
        files = (w.LPCWSTR * 1)(str(path.absolute()))
        if rm.RmRegisterResources(session, 1, files, 0, None, 0, None):
            return ()
        needed, count, reasons = w.UINT(), w.UINT(), w.DWORD()
        for _ in range(3):
            entries = (ProcessInfo * count.value)()
            result = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count),
                                  entries, ctypes.byref(reasons))
            if result == 0:
                return tuple(item.process.pid for item in entries[:count.value])
            if result != 234 or needed.value > 4096:
                return ()
            count.value = needed.value
        return ()
    finally:
        rm.RmEndSession(session)


class ElevatedProcess:
    def __init__(self, handle):
        self.handle = handle
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetProcessId.argtypes = [w.HANDLE]
        self.kernel.GetProcessId.restype = w.DWORD
        self.kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.pid = self.kernel.GetProcessId(handle)

    def exited(self):
        return self.kernel.WaitForSingleObject(self.handle, 0) == 0

    def close(self):
        self.kernel.CloseHandle(self.handle)


def launch_admin(argv: list[str]) -> ElevatedProcess:
    """The Install gesture authorizes UAC; the helper has no visible console."""
    class ExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("fMask", w.ULONG), ("hwnd", w.HWND),
                    ("lpVerb", w.LPCWSTR), ("lpFile", w.LPCWSTR),
                    ("lpParameters", w.LPCWSTR), ("lpDirectory", w.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", w.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", w.LPCWSTR),
                    ("hkeyClass", w.HKEY), ("dwHotKey", w.DWORD),
                    ("hIcon", w.HANDLE), ("hProcess", w.HANDLE)]

    info = ExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100 | 0x400  # process handle, synchronous shell, no error dialogs
    info.lpVerb, info.lpFile = "runas", argv[0]
    info.lpParameters = subprocess.list2cmdline(argv[1:])
    info.lpDirectory, info.nShow = str(Path(argv[0]).parent), 0
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ExecuteInfo)]
    shell.ShellExecuteExW.restype = w.BOOL
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.WinError(ctypes.get_last_error())
        kind = ElevationCancelled if error.winerror == 1223 else PluginInstallError
        raise kind("request administrator permission", argv[0], error,
                   reason="cancelled" if kind is ElevationCancelled else "elevation_failed")
    return ElevatedProcess(info.hProcess)


def process_image(pid: int) -> Path:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR,
                                                ctypes.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        name, size = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
        if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(name.value)
    finally:
        kernel.CloseHandle(handle)


@contextmanager
def locked_paths(directories, sources, destinations):
    """Pin paths against rename/reparse swaps until verification/rollback ends.

    Existing files cannot be replaced while held; missing destination slots are
    created and pinned before copying. Multiple hard links and reparse points
    are refused from the opened handle itself. Source handles also deny writes.
    """
    class FileInfo(ctypes.Structure):
        _fields_ = [("attributes", w.DWORD), ("created", w.FILETIME),
                    ("accessed", w.FILETIME), ("written", w.FILETIME),
                    ("volume", w.DWORD), ("size_hi", w.DWORD), ("size_lo", w.DWORD),
                    ("links", w.DWORD), ("index_hi", w.DWORD), ("index_lo", w.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                  w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.POINTER(FileInfo)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handles, created = [], []
    committed = False

    def pin(path, access, share, disposition, directory=False):
        handle = kernel.CreateFileW(str(path), access, share, None, disposition,
                                    0x00200000 | (0x02000000 if directory else 0), None)
        if handle == w.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        was_created = disposition == 4 and ctypes.get_last_error() != 183
        handles.append(handle)
        info = FileInfo()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & 0x400 or (not directory and info.links != 1):
            raise PermissionError(f"Installation refuses linked or redirected path: {path}")
        if was_created:
            created.append(path)

    try:
        for directory in directories:
            pin(directory, 0x80, 3, 3, True)
        for source in sources:
            pin(source, 0x80000000, 1, 3)
        for destination in destinations:
            pin(destination, 0xC0000000, 3, 4)
        yield
        committed = True
    finally:
        for handle in reversed(handles):
            kernel.CloseHandle(handle)
        if not committed:
            for path in created:
                path.unlink(missing_ok=True)
