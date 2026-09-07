"""The real `Registry`/`Processes` adapters for `capturelayer.py` -- split
out once that module passed its ~300-line budget (round 32 item 95). Every
test exercises `CaptureLayer` through the `FakeRegistry`/`FakeProcesses`
pair instead; nothing here is unit-testable without a real Windows machine,
so it stays a thin, deliberately dumb translation of the Win32 calls into
the two Protocols `capturelayer.py` declares."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

_MAX_PATH = 260
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class WinRegistry:
    """The real Windows registry, under HKEY_CURRENT_USER -- the only hive
    PJ64 1.6 writes its plugin choice to. `get` returns `None` for a
    missing key or value (a fresh PJ64 install that has never touched
    that setting), never raises."""

    def get(self, subkey: str, name: str) -> str | int | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                value, _ = winreg.QueryValueEx(key, name)
                return value
        except OSError:
            return None

    def set(self, subkey: str, name: str, value: str) -> None:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, subkey) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


class WinProcesses:
    """The live process list, read only far enough to find Project64.exe's
    own image path. Uses `PROCESS_QUERY_LIMITED_INFORMATION` -- the same
    same-privilege handle level pymem's own attach uses -- so no admin
    elevation is ever required."""

    def pj64_image_path(self) -> str | None:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        pid_slots = 1024
        while True:
            pids = (wintypes.DWORD * pid_slots)()
            bytes_returned = wintypes.DWORD()
            ok = psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids),
                                      ctypes.byref(bytes_returned))
            if not ok:
                return None
            pid_count = bytes_returned.value // ctypes.sizeof(wintypes.DWORD)
            if pid_count < pid_slots:
                break
            pid_slots *= 2   # the table was full; it may have truncated -- grow and re-read

        for pid in pids[:pid_count]:
            if pid == 0:
                continue
            handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                continue
            try:
                image_path = self._query_image_path(kernel32, handle)
            finally:
                kernel32.CloseHandle(handle)
            if image_path and Path(image_path).name.lower() == "project64.exe":
                return image_path
        return None

    @staticmethod
    def _query_image_path(kernel32, handle) -> str | None:
        buffer = ctypes.create_unicode_buffer(_MAX_PATH)
        size = wintypes.DWORD(_MAX_PATH)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
        return buffer.value if ok else None
