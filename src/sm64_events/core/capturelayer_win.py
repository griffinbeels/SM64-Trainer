"""Windows discovery/registry adapters and Project64 compatibility evidence.

Compatibility tests cover resource versions and exact known unversioned builds.
Native API probes additionally check the actual executable without launching it.
The installer itself uses the fake Registry/Processes protocols in its tests.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
from pathlib import Path

_MAX_PATH = 260
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# LINK's unversioned Project64 1.6, distributed in Wermi's build v7:
# https://wermi.neocities.org/emuguide/getting_emu/
# Fingerprinted from the working installation reported in onboarding round 3.
# VERSIONINFO is absent in this build. Names, folders and window titles are not
# compatibility evidence; accept only these exact bytes when metadata is absent.
_UNVERSIONED_16_BUILDS = {
    "8d7d373d024206f7513721b320ef3359b885aa6ea73dc2c14b3a42f0c099be2b":
        "LINK's Project64 1.6 (Wermi build v7)",
}


def _known_unversioned_build(path: Path) -> str | None:
    try:
        return _UNVERSIONED_16_BUILDS.get(hashlib.sha256(path.read_bytes()).hexdigest())
    except OSError:
        return None


def executable_version(path: Path) -> str | None:
    """Read the executable's version resource without launching it."""
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                     ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, data):
        return None
    value = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.VerQueryValueW(data, "\\", ctypes.byref(value), ctypes.byref(length)):
        return None
    # VS_FIXEDFILEINFO's third and fourth DWORDs are the file version.
    words = ctypes.cast(value, ctypes.POINTER(wintypes.DWORD))
    if length.value < 16 or words[0] != 0xFEEF04BD:
        return None
    return f"{words[2] >> 16}.{words[2] & 0xFFFF}.{words[3] >> 16}.{words[3] & 0xFFFF}"


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
        images = self._images()
        return images[0][1] if images else None

    @staticmethod
    def check_folder(folder: str) -> dict:
        path = Path(folder) / "Project64.exe"
        version = executable_version(path) if path.is_file() else None
        build = _known_unversioned_build(path) if version is None else None
        supported = bool(build) or (version is not None and version.split(".")[:2] == ["1", "6"])
        return {"state": "ready" if supported else "unsupported", "pid": None,
                "path": str(path), "version": version, "build": build,
                "message": ("Project64 v1.6 found." if supported else
                            "This Project64 build could not be verified as version 1.6.")}

    def setup_target(self) -> dict:
        images = self._images()
        if not images:
            return {"state": "missing", "pid": None, "message": "Open Project64 v1.6."}
        if len(images) > 1:
            return {"state": "multiple", "pid": None,
                    "message": "More than one Project64 is open. Close the extra copies so we can find yours."}
        pid, image_path = images[0]
        target = self.check_folder(str(Path(image_path).parent))
        return {**target, "pid": pid}

    def _images(self) -> list:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

        pid_slots = 1024
        while True:
            pids = (wintypes.DWORD * pid_slots)()
            bytes_returned = wintypes.DWORD()
            ok = psapi.EnumProcesses(ctypes.byref(pids), ctypes.sizeof(pids),
                                      ctypes.byref(bytes_returned))
            if not ok:
                return []
            pid_count = bytes_returned.value // ctypes.sizeof(wintypes.DWORD)
            if pid_count < pid_slots:
                break
            pid_slots *= 2   # the table was full; it may have truncated -- grow and re-read

        images = []
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
                images.append((pid, image_path))
        return images

    @staticmethod
    def _query_image_path(kernel32, handle) -> str | None:
        buffer = ctypes.create_unicode_buffer(_MAX_PATH)
        size = wintypes.DWORD(_MAX_PATH)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
        return buffer.value if ok else None
