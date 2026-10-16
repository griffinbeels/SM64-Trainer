"""Offline private-beta installer. Program files and practice data stay separate."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import zipfile

from sm64_events.bootstrap.beta_payload import checked_members, extract_payload, verify_payload
from sm64_events.bootstrap.installer import ConsoleUI, TkUI, _ps_quote, launch_app
from sm64_events.core.build_channel import BETA_APP_DIR, CONFIG_FILENAME, read_metadata
from sm64_events.core.childproc import quiet_spawn_kwargs

APP_EXE = BETA_APP_DIR + ".exe"
RECEIPT = "beta-install.json"
log = logging.getLogger("sm64.bootstrap")


def default_install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / BETA_APP_DIR


def _write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _safe_sibling(target, name):
    if not isinstance(name, str) or not name.startswith(".sm64-beta-") or Path(name).name != name:
        raise ValueError("private installer recovery record is invalid")
    path = target.parent / name
    if path.is_symlink() or not path.resolve().is_relative_to(target.parent.resolve()):
        raise ValueError("private installer recovery path is unsafe")
    return path


def _complete_install(target, files, digest):
    receipt = target / RECEIPT
    if not receipt.is_file() or receipt.is_symlink() or receipt.stat().st_size > 4096:
        return False
    try:
        previous = json.loads(receipt.read_text(encoding="utf-8"))
        return (isinstance(previous, dict) and previous.get("payload_sha256") == digest
                and all((target / name).is_file() for name in files)
                and read_metadata(target / "_internal" / CONFIG_FILENAME).channel == "beta")
    except ValueError:
        log.info("Invalid installed beta receipt or metadata; repairing program files")
        return False


def _repair(target, journal):
    if not journal.exists():
        return
    if journal.is_symlink() or journal.stat().st_size > 4096:
        raise ValueError("private installer recovery record is invalid")
    record = json.loads(journal.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise ValueError("private installer recovery record is invalid")
    staging, previous = (_safe_sibling(target, record.get(key)) for key in ("staging", "previous"))
    if previous.exists() and not target.exists():
        os.replace(previous, target)
    for path in (staging, previous):
        if path.exists():
            shutil.rmtree(path)
    journal.unlink()


@contextmanager
def _install_lock(target):
    """Kernel-released lock: an interrupted installer does not leave a stale owner."""
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / ("." + BETA_APP_DIR + ".install.lock")
    if lock.is_symlink():
        raise ValueError("private installer lock is unsafe")
    with lock.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError("Another beta installer is running. Wait for it to finish.") from None
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def install(executable: Path, target: Path, ui) -> Path:
    """Verify, stage and atomically replace only the beta program directory."""
    if not target.is_absolute() or target.name != BETA_APP_DIR or target.is_symlink():
        raise ValueError("Choose an absolute beta program directory named SM64TrainerBeta.")
    ui.status("Checking the beta application…")
    digest = verify_payload(executable, ui.progress)
    with zipfile.ZipFile(executable) as archive:
        members = checked_members(archive)
        unpacked = sum(member.file_size for member in members)
        files = tuple(member.filename for member in members if not member.is_dir())
    with _install_lock(target):
        journal = target.parent / ("." + BETA_APP_DIR + ".install.json")
        _repair(target, journal)
        # Rerunning the same download launches the installed app without a full
        # extraction. A missing program file falls through to repair.
        if _complete_install(target, files, digest):
            return target / APP_EXE
        if shutil.disk_usage(target.parent).free < unpacked + (32 << 20):
            raise RuntimeError("There is not enough free disk space to install this beta.")
        staging = Path(tempfile.mkdtemp(prefix=".sm64-beta-new-", dir=target.parent))
        previous = Path(tempfile.mkdtemp(prefix=".sm64-beta-old-", dir=target.parent))
        previous.rmdir()
        try:
            ui.status("Installing SM64 Trainer Beta…")
            extract_payload(executable, staging, ui.progress)
            build = read_metadata(staging / "_internal" / CONFIG_FILENAME)
            required = (APP_EXE, "_internal/ffmpeg.exe", "_internal/" + CONFIG_FILENAME,
                        "_internal/sm64_events/ui/index.html", *(
                            "_internal/sm64_events/data/plugin/" + name for name in
                            ("sm64_trainer_gfx.dll", "GLideN64_SM64Trainer.dll", "SM64GpuEncoderV1.dll")))
            if build.channel != "beta" or not all((staging / path).is_file() for path in required):
                raise ValueError("The installer does not contain a complete beta application.")
            _write_json(staging / RECEIPT, {"schema": 1, "build_id": build.build_id,
                                          "payload_sha256": digest})
            _write_json(journal, {"schema": 1, "staging": staging.name, "previous": previous.name})
            if target.exists():
                os.replace(target, previous)
            os.replace(staging, target)
        except OSError as failure:
            _repair(target, journal)
            raise RuntimeError("Close SM64 Trainer Beta and run this file again to finish installation.") from failure
        finally:
            _repair(target, journal)
            if staging.exists():
                shutil.rmtree(staging)
        return target / APP_EXE


def create_shortcut(exe: Path) -> bool:
    """Create a distinct beta shortcut; never replace the public trainer shortcut."""
    import subprocess
    script = ("$desktop=[Environment]::GetFolderPath('Desktop');"
              "$ws=New-Object -ComObject WScript.Shell;"
              "$s=$ws.CreateShortcut((Join-Path $desktop 'SM64 Trainer Beta.lnk'));"
              f"$s.TargetPath='{_ps_quote(str(exe))}';"
              f"$s.WorkingDirectory='{_ps_quote(str(exe.parent))}';"
              f"$s.IconLocation='{_ps_quote(str(exe))},0';$s.Save()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       check=True, capture_output=True, timeout=30, **quiet_spawn_kwargs())
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def run_install(executable, ui, *, target=None, launch=True, shortcut=True):
    try:
        exe = install(executable, target or default_install_dir(), ui)
        if shortcut:
            create_shortcut(exe)
        if launch:
            ui.status("Opening SM64 Trainer Beta…")
            launch_app(exe, None)  # User clicked this installer; the app owns shared onboarding.
        ui.done(exe)
        return True
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as failure:
        ui.error(str(failure))
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--no-launch", action="store_true", help="maintainer installation probe")
    parser.add_argument("--no-shortcut", action="store_true", help="maintainer installation probe")
    parser.add_argument("--install-dir", type=Path, help="absolute maintainer disposable program directory")
    args = parser.parse_args(argv)
    if not getattr(sys, "frozen", False):
        return 2
    ui = ConsoleUI() if args.silent else TkUI()
    if not args.silent:
        ui.root.title("SM64 Trainer Beta Setup")
    result = {"ok": False}

    def work():
        result["ok"] = run_install(Path(sys.executable), ui, target=args.install_dir,
                                   launch=not args.no_launch, shortcut=not args.no_shortcut)

    if args.silent:
        work()
    else:
        threading.Thread(target=work, daemon=True).start()
        ui.root.mainloop()
    return 0 if result["ok"] else 1
