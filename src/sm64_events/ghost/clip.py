"""Portable native ghost files; byte layout is plugin/gfxwrap/ghost_clip.h.

Capture refusals remain records. A clip never fills a gap from a nearby pose,
and its captured camera is evidence rather than the playback camera.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
from uuid import UUID, uuid4

MAGIC, VERSION, SAMPLE_VERSION = b"SM64GHO1", 1, 2
HEADER_BYTES, RECORD_BYTES, SAMPLE_BYTES = 128, 192, 176
MAX_RECORDS, WORLD_READY = 262144, 3
CLOCK, POSE, CAMERA, ACTION = 1, 2, 4, 8
_HEADER = struct.Struct("<8s8I16sQqd2Q2I2i4I")
_PREFIX = struct.Struct("<4I")
_SAMPLE = struct.Struct("<4I2QqI2H2h2I")
PRODUCER_FIELDS = ("pid", "created", "generation", "rom_generation", "configuration",
                   "epoch", "qpc_frequency", "qpc_anchor", "utc_anchor")


class ClipError(ValueError):
    """The recording cannot establish the requested native state."""


@dataclass(frozen=True)
class Frame:
    raw: bytes
    configuration: int
    world_status: int
    epoch: int
    valid_fields: int
    sequence: int
    dlist_serial: int
    qpc: int
    global_timer: int
    area_update_counter: int
    igt: int
    level: int
    area: int
    status: int
    live_action: int

    @property
    def playable(self) -> bool:
        return self.world_status == WORLD_READY and self.valid_fields & (CLOCK | POSE) == CLOCK | POSE


def frame_from_bytes(data: bytes) -> Frame:
    if len(data) != RECORD_BYTES:
        raise ClipError("native ghost record has the wrong size")
    raw = bytes(data)
    size, version, configuration, world = _PREFIX.unpack_from(raw)
    values = _SAMPLE.unpack_from(raw, _PREFIX.size)
    sample_size, sample_version, epoch, valid = values[:4]
    if ((size, version, sample_size, sample_version) !=
            (RECORD_BYTES, VERSION, SAMPLE_BYTES, SAMPLE_VERSION)
            or not configuration or not epoch or valid & ~15 or world > 11):
        raise ClipError("native ghost record schema is unsupported")
    frame = Frame(raw, configuration, world, epoch, valid, *values[4:])
    if not frame.sequence or not frame.dlist_serial or frame.qpc < 0 or frame.status > 13:
        raise ClipError("native ghost occurrence is invalid")
    if valid & CLOCK and not (4 <= frame.level <= 36 and 1 <= frame.area <= 8):
        raise ClipError("native ghost place is invalid")
    if bool(valid & ACTION) != bool(frame.live_action):
        raise ClipError("native ghost action validity disagrees with its value")
    for bit, lo, hi in ((POSE, 76, 156), (CAMERA, 156, 192)):
        if not valid & bit and any(raw[lo:hi]):
            raise ClipError("native ghost refusal retains invalid component bytes")
        if valid & bit:
            floats = struct.unpack_from("<6f", raw, lo)
            if not all(math.isfinite(value) for value in floats):
                raise ClipError("native ghost component contains a nonfinite value")
    if valid & CAMERA and not math.isfinite(struct.unpack_from("<f", raw, 184)[0]):
        raise ClipError("native ghost camera contains a nonfinite value")
    if frame.status == 0 and valid != 15:
        raise ClipError("native ghost success is missing a component")
    return frame


def producer_values(producer) -> dict:
    values = {name: getattr(producer, name) for name in PRODUCER_FIELDS}
    for name, value in values.items():
        if name == "utc_anchor":
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ClipError("native ghost UTC anchor is invalid")
        elif type(value) is not int or value < (0 if name == "qpc_anchor" else 1):
            raise ClipError(f"native ghost producer {name} is invalid")
    for name in ("pid", "generation", "rom_generation", "configuration", "epoch"):
        if values[name] >= 2**32:
            raise ClipError(f"native ghost producer {name} exceeds its field")
    if values["created"] >= 2**64 or values["qpc_frequency"] >= 2**64 or values["qpc_anchor"] >= 2**63:
        raise ClipError("native ghost producer clock exceeds its field")
    return values


@dataclass(frozen=True)
class Clip:
    clip_id: str
    configuration: int
    epoch: int
    qpc_frequency: int
    qpc_anchor: int
    utc_anchor: float
    level: int
    area: int
    frames: tuple[Frame, ...]

    def canonical_frames(self) -> dict[int, Frame]:
        """First occurrence per counter, including a refused first occurrence."""
        result = {}
        for frame in self.frames:
            if frame.valid_fields & CLOCK:
                result.setdefault(frame.igt, frame)
        return result


def _checked_frames(frames: tuple[Frame, ...], configuration: int, epoch: int) -> tuple[int, int]:
    if not 1 <= len(frames) <= MAX_RECORDS:
        raise ClipError("native ghost record count is outside its bound")
    last = None
    clock = None
    place = None
    for frame in frames:
        if (frame.configuration, frame.epoch) != (configuration, epoch):
            raise ClipError("native ghost spans more than one capture epoch")
        if last and (frame.sequence <= last.sequence or frame.dlist_serial <= last.dlist_serial
                     or frame.qpc < last.qpc):
            raise ClipError("native ghost occurrences are not ordered")
        if frame.valid_fields & CLOCK:
            current_place = (frame.level, frame.area)
            if place is not None and current_place != place:
                raise ClipError("native ghost spans more than one level or area")
            if clock and (frame.global_timer < clock.global_timer or frame.igt < clock.igt):
                raise ClipError("native ghost clock restarts inside the attempt")
            place, clock = current_place, frame
        last = frame
    if not frames[0].valid_fields & CLOCK or not frames[-1].valid_fields & CLOCK:
        raise ClipError("native ghost endpoints have no exact clock")
    if not any(frame.playable for frame in frames):
        raise ClipError("native ghost has no supported recorded poses")
    return place


def decode_clip(data: bytes) -> Clip:
    if not HEADER_BYTES <= len(data) <= HEADER_BYTES + MAX_RECORDS * RECORD_BYTES:
        raise ClipError("native ghost file size is outside its bound")
    values = _HEADER.unpack_from(data)
    (magic, version, size, record_size, count, rom, sample_version, configuration,
     epoch, clip_id, frequency, anchor, utc, first, last, first_timer, last_timer,
     level, area, *reserved) = values
    if ((magic, version, size, record_size, rom, sample_version) !=
            (MAGIC, VERSION, HEADER_BYTES, RECORD_BYTES, 1, SAMPLE_VERSION)
            or not configuration or not epoch or not frequency or anchor < 0
            or not math.isfinite(utc) or utc <= 0 or not any(clip_id) or any(reserved)
            or not 1 <= count <= MAX_RECORDS or len(data) != HEADER_BYTES + count * RECORD_BYTES):
        raise ClipError("native ghost file header is invalid")
    frames = tuple(frame_from_bytes(data[at:at + RECORD_BYTES])
                   for at in range(HEADER_BYTES, len(data), RECORD_BYTES))
    place = _checked_frames(frames, configuration, epoch)
    if ((level, area) != place or (first, last, first_timer, last_timer) !=
            (frames[0].sequence, frames[-1].sequence, frames[0].global_timer, frames[-1].global_timer)):
        raise ClipError("native ghost header disagrees with its occurrences")
    return Clip(clip_id.hex(), configuration, epoch, frequency, anchor, utc, level, area, frames)


def encode_clip(frames, producer, *, clip_id: str | None = None) -> bytes:
    source = producer_values(producer)
    checked = []
    for frame in frames:
        if len(checked) == MAX_RECORDS:
            raise ClipError("native ghost record count is outside its bound")
        checked.append(frame_from_bytes(frame.raw if isinstance(frame, Frame) else frame))
    records = tuple(checked)
    level, area = _checked_frames(records, source["configuration"], source["epoch"])
    identity = UUID(clip_id).bytes if clip_id else uuid4().bytes
    if not any(identity):
        raise ClipError("native ghost clip identity is empty")
    header = _HEADER.pack(MAGIC, VERSION, HEADER_BYTES, RECORD_BYTES, len(records), 1,
                          SAMPLE_VERSION, source["configuration"], source["epoch"], identity,
                          source["qpc_frequency"], source["qpc_anchor"], source["utc_anchor"],
                          records[0].sequence, records[-1].sequence,
                          records[0].global_timer, records[-1].global_timer, level, area, 0, 0, 0, 0)
    return header + b"".join(frame.raw for frame in records)


def read_clip(path: Path, expected_sha256: str | None = None) -> Clip:
    with Path(path).open("rb") as stream:
        data = stream.read(HEADER_BYTES + MAX_RECORDS * RECORD_BYTES + 1)
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ClipError("native ghost content hash does not match")
    return decode_clip(data)


def check_manifest(path: Path, manifest: dict, *, attempt_id: int,
                   session_id: int, target: dict) -> Clip:
    """Bind durable bytes to their current attempt, rather than a filename alone."""
    if (not isinstance(manifest, dict) or manifest.get("attempt_id") != attempt_id
            or manifest.get("session_id") != session_id or manifest.get("target") != target
            or manifest.get("file") != path.name):
        raise ClipError("native ghost manifest belongs to another attempt")
    digest = manifest.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ClipError("native ghost manifest has no content hash")
    source = producer_values(SimpleNamespace(**manifest["source"]))
    clip = read_clip(path, digest)
    first, last = clip.frames[0], clip.frames[-1]
    expected = dict(version=VERSION, clip_id=clip.clip_id, bytes=path.stat().st_size,
                    records=len(clip.frames), record_bytes=RECORD_BYTES, sample_version=SAMPLE_VERSION,
                    configuration=clip.configuration, epoch=clip.epoch, level=clip.level, area=clip.area,
                    first_sequence=first.sequence, last_sequence=last.sequence,
                    first_global_timer=first.global_timer, last_global_timer=last.global_timer,
                    first_igt=first.igt, last_igt=last.igt)
    if (any(manifest.get(key) != value for key, value in expected.items())
            or any(source[key] != getattr(clip, key)
                   for key in ("configuration", "epoch", "qpc_frequency", "qpc_anchor", "utc_anchor"))):
        raise ClipError("native ghost manifest disagrees with its captured bytes")
    return clip


def write_clip(path: Path, frames, producer, *, clip_id: str | None = None) -> dict:
    """Atomically write one clip; the replay publisher owns its durable commit."""
    data = encode_clip(frames, producer, clip_id=clip_id)
    clip = decode_clip(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    first, last = clip.frames[0], clip.frames[-1]
    return {"version": VERSION, "file": path.name, "clip_id": clip.clip_id,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "records": len(clip.frames), "record_bytes": RECORD_BYTES,
            "sample_version": SAMPLE_VERSION, "configuration": clip.configuration,
            "epoch": clip.epoch, "first_sequence": first.sequence, "last_sequence": last.sequence,
            "first_global_timer": first.global_timer, "last_global_timer": last.global_timer,
            "first_igt": first.igt, "last_igt": last.igt, "level": clip.level, "area": clip.area,
            "invalid_records": sum(not frame.playable for frame in clip.frames),
            "missing_occurrences": last.sequence - first.sequence + 1 - len(clip.frames),
            "source": producer_values(producer)}
