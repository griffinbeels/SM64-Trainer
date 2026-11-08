"""A ghost's start frame: the game state its recording began from, saved beside the ghost so the scene lab
can re-simulate the run exactly from the ghost's own pads after the attempt's scene is gone.

Kept apart from the clip on purpose: the clip format is what the race and the community exchange, and
this file never leaves his machine. One file per clip, named by the clip's sha256, under the ghost
population's folder (`starts/<sha256>.sm64start`). A ghost without one still reads and still re-simulates
from his own scene or a simulated reset (tools/scene_lab/ghost_resim.py).

Format v1 (little-endian): magic SM64STRT, u32 version, u32 JSON length, the JSON, then xz of the
payload. The JSON: {"version", "clip_sha256", "global_timer", "level", "area", "act", "regions":
[[physical begin, bytes], ...], "pool": null or {"base", "base_sha256", "runs"}, "made_utc"}. The
payload: the regions' bytes in order (the scene tap's task regions of the start frame, big-endian as the
game holds them), then the main pool's delta against the simulated practice reset named by "base"
(stage_sim.py): "runs" runs of u32 offset, u32 length, bytes. Measured on his Whomp's Fortress takes
(2026-11-08): regions alone make Mario and every object exact; with the pool delta the start is byte-exact
(25.5-26.1 KB in all). A reader refuses another version, a mismatched name and anything oversized.
"""
from __future__ import annotations

import hashlib
import json
import lzma
import os
import struct
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MAGIC = b"SM64STRT"
VERSION = 1
SUFFIX = ".sm64start"
MAX_FILE_BYTES = 4 << 20
MAX_PAYLOAD_BYTES = 8 << 20
_HEAD = struct.Struct("<8sII")
_RUN = struct.Struct("<II")
RDRAM_BYTES = 8 << 20


class StartError(ValueError):
    """This start file cannot be used; the message says why."""


@dataclass
class StartFrame:
    clip_sha256: str
    global_timer: int
    level: int
    area: int
    act: int
    regions: list = field(default_factory=list)        # [(physical begin, bytes)]
    pool_base: str | None = None                       # the simulated reset the pool delta was made against
    pool_base_sha256: str | None = None
    pool_runs: list = field(default_factory=list)      # [(physical begin, bytes)]
    made_utc: str = ""


def start_path(root: Path, clip_sha256: str) -> Path:
    if len(clip_sha256) != 64 or any(c not in "0123456789abcdef" for c in clip_sha256):
        raise StartError("A start file is named by its clip's sha256.")
    return Path(root) / "starts" / f"{clip_sha256}{SUFFIX}"


def encode(start: StartFrame) -> bytes:
    regions = [(int(begin), bytes(data)) for begin, data in start.regions]
    runs = [(int(begin), bytes(data)) for begin, data in start.pool_runs]
    for begin, data in regions + runs:
        if begin < 0 or begin + len(data) > RDRAM_BYTES:
            raise StartError("A start region lies outside RDRAM.")
    meta = {"version": VERSION, "clip_sha256": start.clip_sha256, "global_timer": start.global_timer,
            "level": start.level, "area": start.area, "act": start.act,
            "regions": [[begin, len(data)] for begin, data in regions],
            "pool": None if start.pool_base is None else {"base": start.pool_base, "base_sha256": start.pool_base_sha256,
                                                          "runs": len(runs)},
            "made_utc": start.made_utc or datetime.now(timezone.utc).isoformat()}
    payload = b"".join(data for _begin, data in regions) + b"".join(
        _RUN.pack(begin, len(data)) + data for begin, data in runs)
    text = json.dumps(meta, separators=(",", ":")).encode()
    return _HEAD.pack(MAGIC, VERSION, len(text)) + text + lzma.compress(payload, preset=9 | lzma.PRESET_EXTREME)


def decode(data: bytes, clip_sha256: str | None = None) -> StartFrame:
    if len(data) > MAX_FILE_BYTES or len(data) < _HEAD.size:
        raise StartError("This start file is the wrong size.")
    magic, version, length = _HEAD.unpack_from(data)
    if magic != MAGIC:
        raise StartError("This is not a ghost start file.")
    if version != VERSION:
        raise StartError(f"This start file is version {version}; this Trainer reads version {VERSION}.")
    try:
        meta = json.loads(data[_HEAD.size:_HEAD.size + length])
        decompressor = lzma.LZMADecompressor()
        payload = decompressor.decompress(data[_HEAD.size + length:], MAX_PAYLOAD_BYTES + 1)
    except (ValueError, lzma.LZMAError) as error:
        raise StartError(f"This start file is damaged: {error}") from None
    if len(payload) > MAX_PAYLOAD_BYTES or not decompressor.eof or decompressor.unused_data:
        raise StartError("This start file is larger than any real start.")
    if clip_sha256 is not None and meta.get("clip_sha256") != clip_sha256:
        raise StartError("This start file belongs to another ghost.")
    at, regions = 0, []
    for begin, size in meta["regions"]:
        if begin < 0 or size < 0 or begin + size > RDRAM_BYTES or at + size > len(payload):
            raise StartError("A start region lies outside RDRAM or the file.")
        regions.append((begin, payload[at:at + size]))
        at += size
    runs = []
    pool = meta.get("pool")
    for _ in range(pool["runs"] if pool else 0):
        if at + _RUN.size > len(payload):
            raise StartError("The start file's pool delta is cut short.")
        begin, size = _RUN.unpack_from(payload, at)
        at += _RUN.size
        if begin + size > RDRAM_BYTES or at + size > len(payload):
            raise StartError("A pool run lies outside RDRAM or the file.")
        runs.append((begin, payload[at:at + size]))
        at += size
    if at != len(payload):
        raise StartError("The start file has bytes no table names.")
    return StartFrame(meta["clip_sha256"], meta["global_timer"], meta["level"], meta["area"], meta["act"], regions,
                      pool["base"] if pool else None, pool["base_sha256"] if pool else None, runs, meta.get("made_utc", ""))


def write_start(root: Path, start: StartFrame) -> Path:
    """Atomically: a reader sees the old file or the whole new one."""
    path = start_path(root, start.clip_sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = encode(start)
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def read_start(root: Path, clip_sha256: str) -> StartFrame | None:
    """The clip's start, None when it has none; StartError when the file cannot be used."""
    path = start_path(root, clip_sha256)
    if not path.is_file():
        return None
    return decode(path.read_bytes(), clip_sha256)


def remove_start(root: Path, clip_sha256: str) -> None:
    """With its clip: whoever deletes a ghost recording deletes its start too."""
    start_path(root, clip_sha256).unlink(missing_ok=True)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
