"""Crash-recoverable publication of explicitly preserved replay media.

The MP4 filename is the public commit point. Metadata is installed first;
incomplete copies never acquire that name. A same-volume hard link keeps the
immutable recording bytes without another full file write. Recovery only
touches validated destinations recorded beneath this save root.
"""
import json
import os
from pathlib import Path
import shutil
import tempfile


def atomic_json(path: Path, document: dict, *, allow_nan: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, allow_nan=allow_nan)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _destination(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if (not path.is_relative_to(root.resolve()) or path == root.resolve()
            or path.suffix != ".mp4" or not path.name.startswith("attempt_")):
        raise ValueError("invalid replay publication destination")
    return path


def _finish(root: Path, pending: Path) -> Path:
    intent = json.loads((pending / "intent.json").read_text(encoding="utf-8"))
    if intent.get("version") != 1:
        raise ValueError("unknown replay publication version")
    destination = _destination(root, intent["destination"])
    staged = pending / "media.mp4"
    # A valid public name exists only after both metadata files were installed.
    if not destination.exists():
        if not staged.is_file() or staged.stat().st_size != intent["media_bytes"]:
            raise ValueError("replay publication has no complete staged media")
        destination.parent.mkdir(parents=True, exist_ok=True)
        for suffix, filename in ((".json", "metadata.json"), (".review.json", "review.json")):
            source = pending / filename
            target = destination.with_suffix(suffix)
            if source.is_file():
                os.replace(source, target)
            elif not target.is_file():
                raise ValueError("replay publication is missing metadata")
        os.replace(staged, destination)
    # All paths are explicit children of a validated pending directory.
    for filename in ("media.mp4", "metadata.json", "review.json", "media.copying", "intent.json"):
        (pending / filename).unlink(missing_ok=True)
    pending.rmdir()
    return destination


def resume(root: Path, attempt_id: int) -> Path | None:
    """Finish a complete staged save without requiring its scratch source."""
    pending = root.resolve() / ".pending" / str(int(attempt_id))
    if not (pending / "intent.json").is_file() or not (pending / "media.mp4").is_file():
        return None
    return _finish(root.resolve(), pending)


def publish(root: Path, attempt_id: int, source, destination: Path,
            metadata: dict, review: dict, *, reserve_bytes: int = 5 * 1024**3) -> Path:
    """Publish one serialized Save/PB. Retry/recovery resumes a staged copy."""
    root = root.resolve()
    destination = _destination(root, str(destination.resolve().relative_to(root)))
    pending = root / ".pending" / str(int(attempt_id))
    resumed = resume(root, attempt_id)
    if resumed is not None:
        return resumed
    pending.mkdir(parents=True, exist_ok=True)
    # Archival evidence may contain non-finite values from old sidecars. Keep
    # those bytes interpretable; the service validates them before HTTP output.
    atomic_json(pending / "metadata.json", metadata, allow_nan=True)
    atomic_json(pending / "review.json", {"version": 1, "state": review})
    atomic_json(pending / "intent.json", {"version": 1,
        "destination": str(destination.relative_to(root)), "media_bytes": source.stat().st_size if isinstance(source, Path) else source.size})
    staged = pending / "media.mp4"
    if not staged.exists():
        try:
            if not isinstance(source, Path):
                raise OSError("fragment selection requires one permanent media write")
            os.link(source, staged)
        except OSError as error:
            required = source.stat().st_size if isinstance(source, Path) else source.size
            if shutil.disk_usage(pending).free - required < reserve_bytes:
                raise OSError("Not enough free storage to save this replay; free space and retry Save replay.") from error
            copying = pending / "media.copying"
            try:
                if isinstance(source, Path):
                    shutil.copyfile(source, copying)
                else:
                    from contextlib import closing
                    with copying.open("wb") as target, closing(source.chunks()) as chunks:
                        for chunk in chunks:
                            target.write(chunk)
                    if copying.stat().st_size != required:
                        raise OSError("incomplete replay media publication")
                with copying.open("r+b") as stream:
                    os.fsync(stream.fileno())
                os.replace(copying, staged)
            finally:
                copying.unlink(missing_ok=True)
    return _finish(root, pending)


def recover(root: Path) -> list[str]:
    """Finish complete explicit saves before scratch cleanup; return failures."""
    directory = root.resolve() / ".pending"
    if not directory.exists():
        return []
    failures = []
    for pending in directory.iterdir():
        if not pending.is_dir() or not pending.name.isdecimal() or pending.is_symlink():
            continue
        if not (pending / "intent.json").is_file():
            # An interrupted staging copy is not proof of complete metadata.
            failures.append(f"Replay {pending.name} save was interrupted before publication; retry Save replay.")
            continue
        try:
            _finish(root, pending)
        except (OSError, ValueError, KeyError, TypeError) as error:
            failures.append(f"Replay {pending.name}: {error}")
    return failures
