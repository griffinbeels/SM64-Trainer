"""Load the encoder helper from a private copy, never the shipped file.

The helper process maps its DLL for as long as a recording runs. Loading the
shipped `data/plugin/SM64GpuEncoderV1.dll` itself held that file open, so a
new build could not replace it while the trainer was running: a merge on a
live checkout failed halfway (2026-09-23) and an update would fail the same
way. The helper now loads a content-addressed copy under the replay scratch
directory, so the shipped file is only ever read.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from sm64_events.core.paths import ENCODER_DLL_NAME, replay_scratch_dir

log = logging.getLogger("sm64.replay")


def staged_encoder_dll(shipped: Path, root: Path | None = None) -> Path:
    """An absolute path to a byte-identical copy of `shipped`, one folder per
    content hash (the helper requires the file name to stay unchanged).
    Copies from older builds are removed when nothing holds them."""
    data = shipped.read_bytes()
    digest = hashlib.sha256(data).hexdigest()[:16]
    base = Path(root if root is not None else replay_scratch_dir()).resolve() / "encoder"
    target = base / digest / ENCODER_DLL_NAME
    if not (target.is_file() and target.read_bytes() == data):
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)
    for other in base.iterdir():
        if other.is_dir() and other.name != digest:
            try:
                (other / ENCODER_DLL_NAME).unlink(missing_ok=True)
                other.rmdir()
            except OSError:
                log.debug("an older encoder copy is still loaded: %s", other)
    return target
