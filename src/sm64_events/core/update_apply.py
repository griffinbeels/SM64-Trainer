"""Crash-safe application of a staged update to a live onedir install.

Windows forbids DELETING a running exe or a loaded DLL but ALLOWS renaming
them — the same trick the old single-exe updater used, generalized to N
files: every replaced/deleted file is RENAMED into .update_backup\\ (never
deleted) before its staged replacement moves in, and a journal written
BEFORE the first file op records exactly what was planned. Any failure —
including a hard crash mid-swap — is recoverable: rollback restores every
backup and removes half-placed additions, driven purely by the journal +
what exists in the backup tree, so it is idempotent and safe to re-run.

startup_repair() runs at every launch BEFORE anything else loads: an
'applying' journal means a swap was interrupted — roll it back and tell the
caller to relaunch once (the relaunch runs the restored code; the journal
flips to 'rolled_back' first so a crash loop is impossible). The backup
tree + finished journal are swept in the background after a successful
start (the OLD process may still hold its exe/DLLs for a moment — bounded
retries, like the old cleanup_old)."""
import json
import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger("sm64.updater")

BACKUP_DIR = ".update_backup"
STAGING_DIR = ".update_staging"
JOURNAL_NAME = "update_journal.json"


def _local(root: Path, rel: str) -> Path:
    return root.joinpath(*rel.split("/"))


def _write_journal(root: Path, doc: dict) -> None:
    (root / JOURNAL_NAME).write_text(json.dumps(doc))


def read_journal(root: Path) -> "dict | None":
    try:
        return json.loads((root / JOURNAL_NAME).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _replace_retry(src: Path, dst: Path, *, os_replace, retries, sleep) -> None:
    for attempt in range(retries):
        try:
            os_replace(src, dst)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            sleep(0.5)


def _rollback(root: Path, journal: dict, *, os_replace=os.replace) -> None:
    """Idempotent: restores whatever backups exist, removes half-placed adds."""
    backup = root / BACKUP_DIR
    for rel in journal.get("added", []):
        _local(root, rel).unlink(missing_ok=True)
    for rel in list(journal.get("replace", [])) + list(journal.get("delete", [])):
        bak = _local(backup, rel)
        if bak.exists():
            live = _local(root, rel)
            live.parent.mkdir(parents=True, exist_ok=True)
            try:
                os_replace(bak, live)
            except OSError:
                log.exception("rollback failed for %s", rel)


def apply_plan(install_root: Path, staging: Path, *, replace, delete,
               os_replace=os.replace, retries: int = 5,
               sleep=time.sleep) -> None:
    """Swap staged files into the install. `replace` paths must exist under
    `staging`; `delete` paths are renamed into the backup tree. Raises on
    failure AFTER rolling back every completed step."""
    replace = list(replace)
    delete = list(delete)
    backup = install_root / BACKUP_DIR
    added = [p for p in replace if not _local(install_root, p).exists()]
    journal = {"state": "applying", "replace": replace, "delete": delete,
               "added": added}
    _write_journal(install_root, journal)
    try:
        for rel in delete + [p for p in replace if p not in added]:
            bak = _local(backup, rel)
            bak.parent.mkdir(parents=True, exist_ok=True)
            _replace_retry(_local(install_root, rel), bak,
                           os_replace=os_replace, retries=retries, sleep=sleep)
        for rel in replace:
            live = _local(install_root, rel)
            live.parent.mkdir(parents=True, exist_ok=True)
            _replace_retry(_local(staging, rel), live,
                           os_replace=os_replace, retries=retries, sleep=sleep)
        journal["state"] = "done"
        _write_journal(install_root, journal)
    except Exception:
        _rollback(install_root, journal, os_replace=os_replace)
        journal["state"] = "rolled_back"
        _write_journal(install_root, journal)
        raise


def startup_repair(install_root: Path, *, os_replace=os.replace) -> str:
    """Run at launch BEFORE the server starts. 'rolled_back' => caller must
    relaunch once (the journal is already terminal — no restart loop)."""
    journal = read_journal(install_root)
    if journal is None:
        return "none"
    if journal.get("state") == "applying":
        log.warning("interrupted update detected — rolling back")
        _rollback(install_root, journal, os_replace=os_replace)
        journal["state"] = "rolled_back"
        _write_journal(install_root, journal)
        return "rolled_back"
    return "cleaned"    # 'done'/'rolled_back' journals are swept later


def sweep_backup(install_root: Path, *, attempts: int = 60,
                 sleep=time.sleep) -> bool:
    """Remove the backup tree + a FINISHED journal. Never touches an
    'applying' journal (that is startup_repair's job)."""
    journal = read_journal(install_root)
    if journal is not None and journal.get("state") == "applying":
        return False
    backup = install_root / BACKUP_DIR
    for attempt in range(attempts):
        shutil.rmtree(backup, ignore_errors=True)
        if not backup.exists():
            break
        if attempt < attempts - 1:
            sleep(1.0)
    if backup.exists():
        return False
    (install_root / JOURNAL_NAME).unlink(missing_ok=True)
    return True
