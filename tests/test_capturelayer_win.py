"""Project64 compatibility is not synonymous with a Windows version resource."""
import hashlib
from contextlib import nullcontext
import sys
from types import SimpleNamespace

import pytest

from sm64_events.core import capturelayer_win as win


@pytest.fixture
def unversioned(tmp_path, monkeypatch):
    image = tmp_path / "Project64.exe"
    image.write_bytes(b"a known compatible unversioned test build")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    monkeypatch.setattr(win, "executable_version", lambda _: None)
    monkeypatch.setattr(win, "_UNVERSIONED_16_BUILDS", {digest: "Verified test build"}, raising=False)
    return image


def test_known_unversioned_build_is_detected_by_folder_and_running_process(unversioned, monkeypatch):
    folder = win.WinProcesses.check_folder(str(unversioned.parent))
    assert folder["state"] == "ready"
    assert folder["version"] is None  # Never invent Windows resource metadata.
    assert folder["build"] == "Verified test build"
    processes = win.WinProcesses()
    monkeypatch.setattr(processes, "_images", lambda: [(123, str(unversioned))])
    target = processes.setup_target()
    assert target["state"] == "ready" and target["pid"] == 123
    assert target["path"] == str(unversioned)


def test_mutated_or_merely_named_unversioned_build_is_not_authorized(unversioned):
    unversioned.write_bytes(unversioned.read_bytes() + b"Project64 Version 1.6")
    assert win.WinProcesses.check_folder(str(unversioned.parent))["state"] == "unsupported"


@pytest.mark.parametrize("version, expected", [("1.6.0.0", "ready"), ("1.6.1.0", "ready"),
                                               ("1.7.0.0", "unsupported"), ("2.6.0.0", "unsupported")])
def test_resource_version_remains_authoritative(unversioned, monkeypatch, version, expected):
    monkeypatch.setattr(win, "executable_version", lambda _: version)
    assert win.WinProcesses.check_folder(str(unversioned.parent))["state"] == expected


def test_missing_or_unreadable_image_cannot_be_recognized(unversioned, monkeypatch):
    unversioned.unlink()
    assert win.WinProcesses.check_folder(str(unversioned.parent))["state"] != "ready"
    unversioned.write_bytes(b"exists but unreadable")
    def denied(_):
        raise PermissionError("cannot read this executable")
    monkeypatch.setattr(type(unversioned), "read_bytes", denied)
    assert win.WinProcesses.check_folder(str(unversioned.parent))["state"] != "ready"


@pytest.mark.parametrize("outcome", ["deleted", "already_absent", "denied"])
def test_registry_delete_restores_absence_and_preserves_actual_failures(monkeypatch, outcome):
    opened, deleted = [], []
    handle = object()

    def open_key(*args):
        opened.append(args)
        return nullcontext(handle)

    def delete_value(key, name):
        deleted.append((key, name))
        if outcome == "already_absent":
            raise FileNotFoundError("Value is already absent")
        if outcome == "denied":
            raise PermissionError("Current user's registry key is read-only")

    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(
        HKEY_CURRENT_USER="original-user", KEY_SET_VALUE=2,
        OpenKey=open_key, DeleteValue=delete_value))
    if outcome == "denied":
        with pytest.raises(PermissionError, match="read-only"):
            win.WinRegistry().delete("PJ64/Dll", "Graphics Dll")
    else:
        win.WinRegistry().delete("PJ64/Dll", "Graphics Dll")
    assert opened == [("original-user", "PJ64/Dll", 0, 2)]
    assert deleted == [(handle, "Graphics Dll")]


def test_finding_project64_opens_only_processes_already_named_project64(monkeypatch):
    """The process table names every process without opening one; only a
    Project64.exe is opened, with QUERY_LIMITED_INFORMATION (0x1000) alone,
    to read its full path. The old scan opened every process on the machine."""
    from sm64_events.memory import winprocess
    opened, closed = [], []

    def close(handle):
        closed.append(handle)
        return 1

    def open_process(access, _inherit, pid):
        opened.append((pid, access))
        return 0x500 + pid

    def image_name(handle, _flags, buffer, _size):
        buffer.value = rf"C:\Games\PJ64-{handle - 0x500}\Project64.exe"
        return 1

    kernel32 = SimpleNamespace(OpenProcess=open_process, CloseHandle=close,
                               QueryFullProcessImageNameW=image_name)
    monkeypatch.setattr(win.ctypes, "WinDLL", lambda name, **_: kernel32)
    monkeypatch.setattr(winprocess, "pids_named",
                        lambda exe: [11, 12] if exe.lower() == "project64.exe" else [])
    assert win.WinProcesses()._images() == [(11, r"C:\Games\PJ64-11\Project64.exe"),
                                            (12, r"C:\Games\PJ64-12\Project64.exe")]
    assert opened == [(11, 0x1000), (12, 0x1000)]
    assert closed == [0x500 + 11, 0x500 + 12]
