"""Private installer probes use disposable ZIPs and never start the real app."""
import hashlib
import json
import os
from pathlib import Path
import stat
import zipfile

import pytest

from sm64_events.bootstrap import beta_installer as installer
from sm64_events.bootstrap import beta_payload as payload
from test_beta_channel import metadata


class UI:
    def __init__(self):
        self.errors, self.done_exe = [], None

    def status(self, _text):
        pass

    def progress(self, _fraction):
        pass

    def error(self, text):
        self.errors.append(text)

    def done(self, path):
        self.done_exe = path


def make_installer(tmp_path, *, app=b"application", extra=()):
    folder = tmp_path / "input"
    folder.mkdir(exist_ok=True)
    stub, archive, executable = (folder / name for name in ("stub.exe", "payload.zip", "beta.exe"))
    stub.write_bytes(b"MZ harmless test stub")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        zipped.writestr(installer.APP_EXE, app)
        zipped.writestr("_internal/build-channel.json", json.dumps(metadata()))
        for path in ("ffmpeg.exe", "sm64_events/ui/index.html", *(
                "sm64_events/data/plugin/" + name for name in
                ("sm64_trainer_gfx.dll", "GLideN64_SM64Trainer.dll", "SM64GpuEncoderV1.dll"))):
            zipped.writestr("_internal/" + path, b"fixture")
        for path, value in extra:
            info = zipfile.ZipInfo(path)
            info.filename = path  # Preserve a hostile original spelling on Windows.
            zipped.writestr(info, value)
    digest = payload.append_payload(stub, archive, executable)
    return executable, archive, digest


def test_one_file_payload_installs_without_network_or_profile_writes_and_reuses(tmp_path, monkeypatch):
    executable, archive, checksum = make_installer(tmp_path)
    assert checksum == hashlib.sha256(executable.read_bytes()).hexdigest()
    assert payload.verify_payload(executable) == hashlib.sha256(archive.read_bytes()).hexdigest()
    target = tmp_path / "programs" / "SM64TrainerBeta"
    ui = UI()
    assert installer.run_install(executable, ui, target=target, launch=False, shortcut=False)
    assert ui.done_exe == target / installer.APP_EXE
    assert (target / installer.APP_EXE).read_bytes() == b"application"
    assert not (tmp_path / "SM64TrainerBeta").exists()
    monkeypatch.setattr(installer, "extract_payload", lambda *args: pytest.fail("re-extracted installed runtime"))
    assert installer.run_install(executable, UI(), target=target, launch=False, shortcut=False)
    assert not list(target.parent.glob(".sm64-beta-new-*"))
    assert not list(target.parent.glob(".sm64-beta-old-*"))


def test_bad_payload_never_replaces_working_install(tmp_path):
    executable, _, _ = make_installer(tmp_path)
    target = tmp_path / "programs" / "SM64TrainerBeta"
    target.mkdir(parents=True)
    (target / installer.APP_EXE).write_bytes(b"last good")
    with executable.open("r+b") as stream:
        stream.seek(-payload.FOOTER.size - 3, 2)
        stream.write(b"bad")
    ui = UI()
    assert not installer.run_install(executable, ui, target=target, launch=False, shortcut=False)
    assert (target / installer.APP_EXE).read_bytes() == b"last good"
    assert "verification" in ui.errors[0]


@pytest.mark.parametrize("path", ["../escaped.txt", "/absolute.txt", "C:/escaped.txt", "a\\b.txt",
                                   "a/./b", "NUL.txt", "a/CON/x", "trailing. /file", "a\x01b"])
def test_unsafe_zip_paths_are_rejected_before_install(tmp_path, path):
    executable, _, _ = make_installer(tmp_path, extra=[(path, b"bad")])
    target = tmp_path / "programs" / "SM64TrainerBeta"
    assert not installer.run_install(executable, UI(), target=target, launch=False, shortcut=False)
    assert not target.exists()


def test_symlinks_and_windows_case_duplicates_are_rejected(tmp_path):
    archive = tmp_path / "unsafe.zip"
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(link, b"target")
    with zipfile.ZipFile(archive) as zipped, pytest.raises(ValueError, match="unsafe"):
        payload.checked_members(zipped)
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("A.txt", b"a")
        zipped.writestr("a.txt", b"b")
    with zipfile.ZipFile(archive) as zipped, pytest.raises(ValueError, match="duplicate"):
        payload.checked_members(zipped)


def test_failed_directory_swap_restores_old_install_and_retries(tmp_path, monkeypatch):
    executable, _, _ = make_installer(tmp_path)
    target = tmp_path / "programs" / "SM64TrainerBeta"
    target.mkdir(parents=True)
    (target / installer.APP_EXE).write_bytes(b"last good")
    original_replace = os.replace

    def fail_fresh(source, destination):
        if Path(source).name.startswith(".sm64-beta-new-") and Path(destination) == target:
            raise OSError("locked")
        return original_replace(source, destination)

    monkeypatch.setattr(installer.os, "replace", fail_fresh)
    assert not installer.run_install(executable, UI(), target=target, launch=False, shortcut=False)
    assert (target / installer.APP_EXE).read_bytes() == b"last good"
    monkeypatch.setattr(installer.os, "replace", original_replace)
    assert installer.run_install(executable, UI(), target=target, launch=False, shortcut=False)
    assert (target / installer.APP_EXE).read_bytes() == b"application"


def test_interrupted_swap_recovery_restores_existing_install(tmp_path):
    target = tmp_path / "programs" / "SM64TrainerBeta"
    target.parent.mkdir()
    staging, previous = target.parent / ".sm64-beta-new-example", target.parent / ".sm64-beta-old-example"
    staging.mkdir()
    previous.mkdir()
    (previous / installer.APP_EXE).write_bytes(b"last good")
    journal = target.parent / ".SM64TrainerBeta.install.json"
    installer._write_json(journal, {"schema": 1, "staging": staging.name, "previous": previous.name})
    installer._repair(target, journal)
    assert (target / installer.APP_EXE).read_bytes() == b"last good"
    assert not journal.exists() and not staging.exists() and not previous.exists()


def test_installer_default_target_is_separate_from_profile_and_public_app(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert installer.default_install_dir() == tmp_path / "Programs" / "SM64TrainerBeta"
    assert not installer.run_install(tmp_path / "absent", UI(), target=tmp_path / "SM64Trainer", launch=False)
