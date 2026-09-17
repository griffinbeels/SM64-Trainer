"""DLL identity and verified installation; no emulator or capture operations."""
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil

from sm64_events.core.setup_files import restore_on_failure

log = logging.getLogger("sm64.capturelayer")
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
            shutil.copyfile(source, destination)
            receipt["after"] = file_identity(destination)
            if receipt["after"]["sha256"] != expected:
                raise OSError("capture layer copy verification failed")
            yield receipt
    except OSError:
        receipt["restored"] = file_identity(destination)
        log.exception("capture layer installation failed: %s", json.dumps(receipt, sort_keys=True))
        raise
    log.info("capture layer installation committed: %s", json.dumps(receipt, sort_keys=True))
