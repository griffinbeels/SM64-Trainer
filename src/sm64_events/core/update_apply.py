"""Crash-safe application of a staged update to a live onedir install.

Windows forbids DELETING a running exe or a loaded DLL but ALLOWS renaming
them — the same trick the old single-exe updater used, generalized to N
files: each file is RENAMED into .update_backup\\ (never deleted) and its
staged replacement moved in IMMEDIATELY after (per-file pairs, so the window
in which any one file is absent from the live tree is two back-to-back
renames — a crash can strand at most one file). A journal written BEFORE the
first file op — via temp+fsync+rename, so it survives power loss, not just
process death — records exactly what was planned. Rollback restores every
backup and removes half-placed additions, driven purely by the journal +
what exists in the backup tree: idempotent, safe to re-run, and able to
restore even the RUNNING exe (it can't be overwritten, but it CAN be renamed
aside — the displaced copy parks in the backup tree and is swept later).

startup_repair() runs at every launch BEFORE anything else loads: an
'applying' journal means a swap was interrupted — roll it back and tell the
caller to relaunch once (the journal flips to 'rolled_back' + records
rollback_failures FIRST, so a crash loop is impossible and a partial
rollback is never silently reported clean). The one unrecoverable corner:
a crash inside a single file-pair of a BOOT-critical file (the exe /
python DLL) can leave the app unable to start at all — recovery is
re-running the SM64Trainer.exe installer, which is an idempotent repair.
The backup tree + finished journal are swept in the background after a
successful start (the OLD process may still hold its exe/DLLs for a moment —
bounded retries, like the old cleanup_old)."""
import json
import logging
import os
import shutil
import time
from pathlib import Path

from sm64_events.core.update_plan import (BACKUP_DIR, JOURNAL_NAME,
                                          STAGING_DIR)

log = logging.getLogger("sm64.updater")

__all__ = ["BACKUP_DIR", "STAGING_DIR", "JOURNAL_NAME", "apply_plan",
           "read_journal", "startup_repair", "sweep_backup"]


def _local(root: Path, rel: str) -> Path:
    return root.joinpath(*rel.split("/"))


def _write_journal(root: Path, doc: dict) -> None:
    """Temp + fsync + rename: the journal IS the crash-recovery record, so a
    power loss between journal write and file renames must not lose it
    (wave-1 review I-4)."""
    path = root / JOURNAL_NAME
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


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


def _rollback(root: Path, journal: dict, *, os_replace=os.replace) -> int:
    """Idempotent: restores whatever backups exist, removes half-placed adds.
    Returns the number of files that could NOT be restored (0 = clean)."""
    failures = 0
    backup = root / BACKUP_DIR
    for rel in journal.get("added", []):
        try:
            _local(root, rel).unlink(missing_ok=True)
        except OSError:
            failures += 1
            log.exception("rollback: could not remove added file %s", rel)
    for rel in list(journal.get("replace", [])) + list(journal.get("delete", [])):
        bak = _local(backup, rel)
        if not bak.exists():
            continue
        live = _local(root, rel)
        live.parent.mkdir(parents=True, exist_ok=True)
        try:
            os_replace(bak, live)
        except PermissionError:
            # live is in use — typically the RUNNING exe (Windows refuses to
            # replace-over it but allows RENAMING it). Displace it into the
            # backup tree, restore the original, sweep the displaced copy
            # with the rest of the backup later (wave-1 review I-3).
            try:
                displaced = bak.with_name(bak.name + ".displaced")
                os_replace(live, displaced)
                os_replace(bak, live)
            except OSError:
                failures += 1
                log.exception("rollback failed for %s", rel)
        except OSError:
            failures += 1
            log.exception("rollback failed for %s", rel)
    return failures


def apply_plan(install_root: Path, staging: Path, *, replace, delete,
               os_replace=os.replace, retries: int = 5,
               sleep=time.sleep) -> None:
    """Swap staged files into the install. `replace` paths must exist under
    `staging`; `delete` paths are renamed into the backup tree. Raises on
    failure AFTER rolling back every completed step."""
    replace = list(replace)
    delete = list(delete)
    backup = install_root / BACKUP_DIR
    added = set(p for p in replace if not _local(install_root, p).exists())
    journal = {"state": "applying", "replace": replace, "delete": delete,
               "added": sorted(added)}
    _write_journal(install_root, journal)
    try:
        for rel in delete:
            bak = _local(backup, rel)
            bak.parent.mkdir(parents=True, exist_ok=True)
            _replace_retry(_local(install_root, rel), bak,
                           os_replace=os_replace, retries=retries, sleep=sleep)
        # Per-file backup+place PAIRS (not backup-all-then-place-all): the
        # window where any one file is absent from the live tree is two
        # back-to-back renames, so a crash strands at most ONE file (I-3).
        for rel in replace:
            live = _local(install_root, rel)
            if rel not in added:
                bak = _local(backup, rel)
                bak.parent.mkdir(parents=True, exist_ok=True)
                _replace_retry(live, bak,
                               os_replace=os_replace, retries=retries,
                               sleep=sleep)
            live.parent.mkdir(parents=True, exist_ok=True)
            _replace_retry(_local(staging, rel), live,
                           os_replace=os_replace, retries=retries, sleep=sleep)
        journal["state"] = "done"
        _write_journal(install_root, journal)
    except Exception:
        journal["rollback_failures"] = _rollback(install_root, journal,
                                                 os_replace=os_replace)
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
        failures = _rollback(install_root, journal, os_replace=os_replace)
        journal["state"] = "rolled_back"
        journal["rollback_failures"] = failures
        _write_journal(install_root, journal)
        if failures:
            log.error("rollback could not restore %d file(s); if the app "
                      "misbehaves, re-run the SM64Trainer.exe installer "
                      "(idempotent repair)", failures)
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
