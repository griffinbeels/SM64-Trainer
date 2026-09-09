"""Project64 compatibility is not synonymous with a Windows version resource."""
import hashlib

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
