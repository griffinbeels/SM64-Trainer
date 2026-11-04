"""Small, fixed ownership boundaries for disposable local debug artifacts.

Cleanup is best-effort and never traverses links. These names are debug output,
not practice data: recordings, databases and arbitrary user files are excluded.
"""
from __future__ import annotations

from pathlib import Path
import stat
import time

from sm64_events.core.identity_privacy import purge

MAX_DEBUG_AGE = 86400
REPORT_KEEP = 3
REPORT_MAX_BYTES = 1024 * 1024
SERVER_LOG_MAX_BYTES = 1024 * 1024
PERF_LOG_MAX_BYTES = 1024 * 1024


def _directory(path: Path) -> Path | None:
    try:
        path = path.absolute()
        if any(item.is_symlink() or item.is_junction() for item in (path, *path.parents)):
            return None
        return path.resolve(strict=True) if path.is_dir() else None
    except OSError:
        return None


def _metadata(path: Path):
    try:
        result = path.lstat()
        if stat.S_ISREG(result.st_mode) and not getattr(result, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            return result
    except OSError:
        pass
    return None


def _remove(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # A denied cleanup cannot break practice or report export.


def prune_reports(directory: Path, *, now: float | None = None, keep: int = REPORT_KEEP) -> None:
    """Keep three recent bounded reports; only app-owned report names qualify."""
    root = _directory(Path(directory))
    if root is None:
        return
    cutoff = (time.time() if now is None else now) - MAX_DEBUG_AGE
    try:
        reports = []
        for path in root.glob("debug-report-*.md"):
            metadata = _metadata(path)
            if metadata is None:
                continue
            if metadata.st_mtime <= cutoff or metadata.st_size > REPORT_MAX_BYTES:
                _remove(path)
            else:
                reports.append((metadata.st_mtime, path.name, path))
        for _, _, path in sorted(reports, reverse=True)[max(0, min(keep, REPORT_KEEP)):]:
            _remove(path)
    except OSError:
        pass


def bounded_report(markdown: str, limit: int = REPORT_MAX_BYTES) -> bytes:
    """One cut for exported files and error attachments; it names its own limit.
    Both leave the machine, so this machine's account and computer names go first."""
    raw = purge(markdown).encode("utf-8")
    if len(raw) <= limit:
        return raw
    suffix = f"\n\n[Report truncated at the {limit // 1024} KiB size limit.]\n".encode()
    return raw[:limit - len(suffix)].decode("utf-8", "ignore").encode("utf-8") + suffix


def report_directory(directory: Path, *, keep: int = REPORT_KEEP) -> Path:
    """Reserve a slot through the same guarded cleanup path before writing."""
    path = Path(directory).absolute()
    if any(item.is_symlink() or item.is_junction() for item in (path, *path.parents)):
        raise OSError("Debug report folder contains a redirected path.")
    path.mkdir(parents=True, exist_ok=True)
    root = _directory(path)
    if root is None:
        raise OSError("Debug report folder is unavailable.")
    prune_reports(root, keep=max(0, min(keep, REPORT_KEEP) - 1))
    if sum(_metadata(item) is not None for item in root.glob("debug-report-*.md")) >= keep:
        raise OSError("The debug-report limit is full; old exports could not be removed.")
    return root


def prune_debug_logs(log_directory: Path, *, data_directory: Path | None = None,
                     now: float | None = None) -> None:
    """Expire known debug logs on startup, never recurse through a data folder."""
    log_root = _directory(Path(log_directory))
    if log_root is None:
        return
    cutoff = (time.time() if now is None else now) - MAX_DEBUG_AGE
    candidates = [(log_root / name, SERVER_LOG_MAX_BYTES) for name in
                  ("sm64_events.log", "sm64_events.log.1")]
    data_root = _directory(data_directory) if data_directory is not None else None
    if data_root is not None:
        candidates.extend((data_root / name, limit) for name, limit in (
            ("ui_log.jsonl", 2_000_000), ("perf_log.jsonl", PERF_LOG_MAX_BYTES),
            ("perf_log.jsonl.prev", PERF_LOG_MAX_BYTES)))
    for path, limit in candidates:
        metadata = _metadata(path)
        if metadata is not None and (metadata.st_mtime <= cutoff or
                                     (limit is not None and metadata.st_size > limit)):
            _remove(path)
