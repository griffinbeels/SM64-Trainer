import json
import os
from pathlib import Path

import pytest

from sm64_events.core.update_apply import (BACKUP_DIR, JOURNAL_NAME,
                                           apply_plan, read_journal,
                                           startup_repair, sweep_backup)


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for rel, content in files.items():
        p = root.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return root


def _setup(tmp_path):
    install = _tree(tmp_path / "app", {
        "SM64Trainer.exe": b"OLD-EXE",
        "_internal/lib.dll": b"OLD-DLL",
        "_internal/gone.pyd": b"REMOVE-ME"})
    staging = _tree(tmp_path / "stage", {
        "SM64Trainer.exe": b"NEW-EXE",
        "_internal/newfile.dat": b"BRAND-NEW"})
    return install, staging


def test_apply_plan_swaps_deletes_and_adds(tmp_path):
    install, staging = _setup(tmp_path)
    apply_plan(install, staging,
               replace=["SM64Trainer.exe", "_internal/newfile.dat"],
               delete=["_internal/gone.pyd"])
    assert (install / "SM64Trainer.exe").read_bytes() == b"NEW-EXE"
    assert (install / "_internal/newfile.dat").read_bytes() == b"BRAND-NEW"
    assert not (install / "_internal/gone.pyd").exists()
    assert (install / "_internal/lib.dll").read_bytes() == b"OLD-DLL"
    # originals preserved in the backup tree; journal marked done
    assert (install / BACKUP_DIR / "SM64Trainer.exe").read_bytes() == b"OLD-EXE"
    assert (install / BACKUP_DIR / "_internal/gone.pyd").exists()
    assert read_journal(install)["state"] == "done"


def test_apply_plan_rolls_back_on_failure(tmp_path):
    install, staging = _setup(tmp_path)
    real = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(str(src))
        # fail when placing the SECOND staged file
        if "newfile" in str(src):
            raise PermissionError("locked")
        return real(src, dst)

    with pytest.raises(PermissionError):
        apply_plan(install, staging,
                   replace=["SM64Trainer.exe", "_internal/newfile.dat"],
                   delete=["_internal/gone.pyd"],
                   os_replace=flaky, retries=2, sleep=lambda s: None)
    # everything restored
    assert (install / "SM64Trainer.exe").read_bytes() == b"OLD-EXE"
    assert (install / "_internal/gone.pyd").read_bytes() == b"REMOVE-ME"
    assert not (install / "_internal/newfile.dat").exists()


def test_apply_plan_retries_locked_files(tmp_path):
    install, staging = _setup(tmp_path)
    real = os.replace
    fails = {"n": 0}

    def flaky_once(src, dst):
        if "NEW-EXE" == Path(src).read_bytes().decode(errors="ignore") \
                and fails["n"] == 0 and str(src).endswith("SM64Trainer.exe"):
            fails["n"] = 1
            raise PermissionError("AV lock")
        return real(src, dst)

    apply_plan(install, staging, replace=["SM64Trainer.exe"], delete=[],
               os_replace=flaky_once, retries=3, sleep=lambda s: None)
    assert (install / "SM64Trainer.exe").read_bytes() == b"NEW-EXE"


def test_startup_repair_none_without_journal(tmp_path):
    install, _ = _setup(tmp_path)
    assert startup_repair(install) == "none"


def test_startup_repair_rolls_back_interrupted_apply(tmp_path):
    """Simulate a crash mid-swap: journal says applying, exe already swapped,
    delete backed up, the add was never placed."""
    install, staging = _setup(tmp_path)
    backup = install / BACKUP_DIR
    _tree(install, {})
    # manual mid-state: exe swapped (old in backup), gone.pyd backed up
    (backup / "_internal").mkdir(parents=True)
    os.replace(install / "SM64Trainer.exe", backup / "SM64Trainer.exe")
    (install / "SM64Trainer.exe").write_bytes(b"NEW-EXE")
    os.replace(install / "_internal/gone.pyd", backup / "_internal/gone.pyd")
    (install / JOURNAL_NAME).write_text(json.dumps({
        "state": "applying",
        "replace": ["SM64Trainer.exe", "_internal/newfile.dat"],
        "delete": ["_internal/gone.pyd"],
        "added": ["_internal/newfile.dat"]}))
    assert startup_repair(install) == "rolled_back"
    assert (install / "SM64Trainer.exe").read_bytes() == b"OLD-EXE"
    assert (install / "_internal/gone.pyd").read_bytes() == b"REMOVE-ME"
    assert read_journal(install)["state"] == "rolled_back"
    # repair is terminal: a second call cleans, never re-rolls
    assert startup_repair(install) == "cleaned"


def test_startup_repair_removes_placed_adds(tmp_path):
    install, _ = _setup(tmp_path)
    (install / "_internal/newfile.dat").write_bytes(b"HALF-PLACED")
    (install / JOURNAL_NAME).write_text(json.dumps({
        "state": "applying", "replace": ["_internal/newfile.dat"],
        "delete": [], "added": ["_internal/newfile.dat"]}))
    assert startup_repair(install) == "rolled_back"
    assert not (install / "_internal/newfile.dat").exists()


def test_sweep_backup_removes_backup_and_done_journal(tmp_path):
    install, staging = _setup(tmp_path)
    apply_plan(install, staging, replace=["SM64Trainer.exe"], delete=[])
    assert sweep_backup(install, attempts=1) is True
    assert not (install / BACKUP_DIR).exists()
    assert read_journal(install) is None


def test_sweep_backup_never_touches_an_applying_journal(tmp_path):
    install, _ = _setup(tmp_path)
    (install / JOURNAL_NAME).write_text(json.dumps({
        "state": "applying", "replace": [], "delete": [], "added": []}))
    assert sweep_backup(install, attempts=1) is False
    assert read_journal(install)["state"] == "applying"
