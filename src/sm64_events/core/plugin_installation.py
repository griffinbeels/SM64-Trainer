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

from sm64_events.core.setup_files import restore_on_failure

log = logging.getLogger("sm64.capturelayer")

# The capture layer's files inside Project64's Plugin folder; the setup
# screen (core/capturelayer.py) and the UAC helper write the same names.
WRAPPER_DLL = "sm64_trainer_gfx.dll"
WRAPPER_INI = "sm64_trainer_gfx.ini"
#: THE RENDERER as installed. Never a .dll: Project64 lists every *.dll in
#: Plugin\ as a graphics plugin, and picking the renderer itself skips
#: capture. The wrapper loads it by full path, so Windows appends nothing.
#: Its extension must not start with "dll" ("*.dll" also matches 8.3 names).
RENDERER_FILE = "sm64_trainer_gfx.renderer"
#: Where installs before 2026-10-24 put the renderer (its bundled name,
#: core/paths.py::RENDERER_DLL_NAME). Install and refresh move it.
LEGACY_RENDERER_DLL = "GLideN64_SM64Trainer.dll"


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


# Every shipped native id: wrapper, encoder helper, renderer (core/capturelayer.py
# compares by the same ids; receipts record them for diagnostics).
_BUILD = re.compile(rb"(?<![a-f0-9])[a-f0-9]{64}(?:-gpu-runtime|-gpu-encoder|-renderer)\x00")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


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
