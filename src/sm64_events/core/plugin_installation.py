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


class DestinationInUse(OSError):
    """Another process holds the destination, so the copy wrote nothing.

    Project64 maps its plugin DLLs while it runs, and Windows refuses to open
    a mapped file for writing. For an automatic refresh that is the normal
    "not now" (it retries once Project64 closes), never a failed install."""


def in_use(path: Path) -> bool:
    """Whether `path` cannot be opened for writing right now. Opening for
    update writes nothing; the copy asks for the same access."""
    try:
        with path.open("r+b"):
            return False
    except FileNotFoundError:
        return False
    except PermissionError:
        return True


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
        raise OSError(f"capture layer source verification failed: {receipt['source']['error']}")
    try:
        with restore_on_failure([destination, *rollback]):
            try:
                shutil.copyfile(source, destination)
            except PermissionError as exc:
                # Refused at open, before a byte was written: something (Project64
                # starting between the caller's check and this copy) holds it.
                if file_identity(destination)["sha256"] == receipt["before"]["sha256"]:
                    raise DestinationInUse(errno.EACCES, "in use by another process",
                                           str(destination)) from exc
                raise
            receipt["after"] = file_identity(destination)
            if receipt["after"]["sha256"] != expected:
                raise OSError("capture layer copy verification failed")
            yield receipt
    except DestinationInUse:
        raise  # Whatever was written is put back; the caller says "not now".
    except OSError:
        receipt["restored"] = file_identity(destination)
        log.exception("capture layer installation failed: %s", json.dumps(receipt, sort_keys=True))
        raise
    log.info("capture layer installation committed: %s", json.dumps(receipt, sort_keys=True))
