"""DLL identity and verified installation; no emulator or capture operations."""
from contextlib import contextmanager
from datetime import UTC, datetime
import errno
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil

from sm64_events.core.paths import PLUGIN_DLL_NAME
from sm64_events.core.setup_files import restore_on_failure

log = logging.getLogger("sm64.capturelayer")

#: THE GRAPHICS PLUGIN as installed in Project64's Plugin folder: the one file
#: the setup screen (core/capturelayer.py) and the UAC helper write there,
#: under the bundled name, so Project64's `Graphics Dll` choice of it holds
#: through every update.
PLUGIN_DLL = PLUGIN_DLL_NAME
#: What older installs left beside it. Until 2026-11-07 the trainer installed
#: a wrapper under PLUGIN_DLL that loaded the renderer named by its ini:
#: `sm64_trainer_gfx.renderer` (from 2026-10-24; not a .dll, so Project64's
#: picker listed one plugin) or, before that, `GLideN64_SM64Trainer.dll`.
#: Install and refresh delete these (`our_leftovers`), in this order.
LEGACY_WRAPPER_INI = "sm64_trainer_gfx.ini"
LEGACY_RENDERER_FILE = "sm64_trainer_gfx.renderer"
LEGACY_RENDERER_DLL = "GLideN64_SM64Trainer.dll"
LEFTOVERS = (LEGACY_WRAPPER_INI, LEGACY_RENDERER_FILE, LEGACY_RENDERER_DLL)


class PluginInstallError(OSError):
    """A terminal install failure with the original operation and Windows code."""

    def __init__(self, operation, path, error, *, reason="io_error"):
        super().__init__(getattr(error, "errno", None),
                         getattr(error, "strerror", None) or str(error), str(path))
        self.operation = operation
        if getattr(error, "winerror", None) is not None:
            self.winerror = error.winerror
        self.reason = reason


class ElevationCancelled(PluginInstallError):
    """The player declined Windows' administrator permission prompt."""


@contextmanager
def file_operation(operation: str, path: Path):
    try:
        yield
    except (DestinationInUse, PluginInstallError):
        raise
    except OSError as exc:
        if (isinstance(exc, PermissionError) and os.name == "nt"
                and getattr(exc, "winerror", None) is None and path.is_file()):
            from sm64_events.core.plugin_elevation_win import write_access_error
            exc.winerror = write_access_error(path) or None
        reason = "access_denied" if isinstance(exc, PermissionError) else "io_error"
        raise PluginInstallError(operation, path, exc, reason=reason) from exc


class DestinationInUse(OSError):
    """Another process holds the destination, so the copy wrote nothing.

    Project64 maps its plugin DLLs while it runs, and Windows refuses to open
    a mapped file for writing. For an automatic refresh that is the normal
    "not now" (it retries once Project64 closes), never a failed install."""


def in_use(path: Path) -> bool:
    """Positive sharing/loaded-module evidence, rather than an ACL denial.

    A mapped DLL can fail with ACCESS_DENIED too. Restart Manager distinguishes
    that case without guessing that every unwritable file is running Project64.
    """
    try:
        with path.open("r+b"):
            return False
    except FileNotFoundError:
        return False
    except PermissionError as exc:
        if os.name == "nt":
            from sm64_events.core.plugin_elevation_win import file_users, write_access_error
            if (getattr(exc, "winerror", None) or write_access_error(path)) in (32, 33):
                return True
            return bool(file_users(path))
        return False


# Every native id, by role: the graphics plugin (-gpu-runtime), the encoder
# helper (-gpu-encoder) and an older install's renderer (-renderer).
# core/capturelayer.py compares by the same ids; receipts record them.
_BUILD = re.compile(rb"(?<![a-f0-9])[a-f0-9]{64}(?:-gpu-runtime|-gpu-encoder|-renderer)\x00")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_id(path: Path) -> bytes | None:
    """The one build id a native embeds (without its NUL), else None."""
    ids = {match[:-1] for match in _BUILD.findall(path.read_bytes())}
    return ids.pop() if len(ids) == 1 else None


def _wrapper_config(path: Path) -> bool:
    """The old wrapper's ini as the trainer wrote it: only `wrapped=` lines."""
    try:
        if path.stat().st_size > 4096:
            return False
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, UnicodeError):
        return False
    lines = [line for line in lines if line]
    return bool(lines) and all(line.startswith("wrapped=") for line in lines)


def our_leftovers(plugin_dir: Path, *, keep: str | None = None, identity=build_id) -> list[Path]:
    """The older install's files in `plugin_dir` that this trainer wrote: a
    renderer whose build id carries the -renderer role, and the wrapper's ini.
    A user's own file under one of these names stays, and so do a link and
    `keep`, the file Project64's `Graphics Dll` names: deleting it would leave
    Project64 selecting a missing plugin."""
    found = []
    for name in LEFTOVERS:
        path = plugin_dir / name
        if keep is not None and keep.lower() == name.lower():
            continue
        try:
            stat = path.lstat()
            if (getattr(stat, "st_file_attributes", 0) & 0x400 or stat.st_nlink != 1
                    or not path.is_file() or path.is_symlink()):
                continue
            ours = (_wrapper_config(path) if name == LEGACY_WRAPPER_INI
                    else (identity(path) or b"").endswith(b"-renderer"))
        except OSError:
            continue
        if ours:
            found.append(path)
    return found


def remove_files(paths) -> None:
    """Delete every path or none: a refused delete puts back the ones already
    gone (never over a file that appeared since) and raises. A file a
    running Project64 has loaded raises DestinationInUse, the normal "not now"."""
    removed = []
    try:
        for path in paths:
            if in_use(path):
                raise DestinationInUse(errno.EACCES, "in use by another process", str(path))
            data = path.read_bytes()
            with file_operation("remove an older capture layer file", path):
                path.unlink()
            removed.append((path, data))
    except OSError:
        for path, data in reversed(removed):
            try:
                with path.open("xb") as stream:
                    stream.write(data)
            except OSError:
                log.exception("could not put back %s", path)
        raise


def file_identity(path: Path) -> dict:
    """Build labels are source identities, never substitutes for binary hashes."""
    try:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            size = os.fstat(stream.fileno()).st_size
            stream.seek(0)
            labels = sorted({m[:-1].decode("ascii") for m in _BUILD.findall(stream.read(16 * 1024 * 1024))})
        return {"path": str(path.resolve()), "sha256": digest, "bytes": size,
                "build_ids": labels, "error": None}
    except OSError as exc:
        return {"path": str(path), "sha256": None, "build_ids": [], "error": str(exc)}


@contextmanager
def verified_copy(source: Path, destination: Path, *, reason: str, rollback=()):
    """Commit a verified DLL and caller's settings together, or restore both."""
    receipt = {"utc": datetime.now(UTC).isoformat(), "server_pid": os.getpid(),
               "reason": reason, "source": file_identity(source),
               "before": file_identity(destination), "destination": str(destination.resolve())}
    expected = receipt["source"]["sha256"]
    if expected is None:
        raise PluginInstallError("read bundled plugin", source,
                                 OSError(f"Source verification failed: {receipt['source']['error']}"))
    try:
        with restore_on_failure([destination, *rollback]):
            try:
                shutil.copyfile(source, destination)
            except PermissionError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) is None:
                    from sm64_events.core.plugin_elevation_win import write_access_error
                    exc.winerror = write_access_error(destination) or None
                if in_use(destination):
                    error = DestinationInUse(errno.EACCES, "in use by another process",
                                             str(destination))
                    error.winerror = getattr(exc, "winerror", None)
                    raise error from exc
                raise PluginInstallError("copy plugin", destination, exc,
                                         reason="access_denied") from exc
            except OSError as exc:
                raise PluginInstallError("copy plugin", destination, exc) from exc
            receipt["after"] = file_identity(destination)
            if receipt["after"]["sha256"] != expected:
                raise PluginInstallError("verify plugin copy", destination,
                                         OSError("Plugin copy verification failed: bytes did not match this build"))
            yield receipt
    except DestinationInUse:
        raise  # Whatever was written is put back; the caller says "not now".
    except OSError as exc:
        receipt["restored"] = file_identity(destination)
        if (isinstance(exc, PluginInstallError) and exc.reason == "access_denied"
                and reason == "explicit_install"):
            log.info("capture layer needs administrator permission: %s", json.dumps(receipt, sort_keys=True))
            raise
        log.exception("capture layer installation failed: %s", json.dumps(receipt, sort_keys=True))
        raise
    log.info("capture layer installation committed: %s", json.dumps(receipt, sort_keys=True))
