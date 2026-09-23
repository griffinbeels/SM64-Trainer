"""Portable native ghost files; byte layout is plugin/gfxwrap/ghost_clip.h.

Capture refusals remain records. A clip never fills a gap from a nearby pose,
and its captured camera is evidence rather than the playback camera. Each
record may also carry the shadow the game drew under Mario, the sound
requests Mario issued since the previous frame (`Frame.sounds`) and the
particles the game drew for him (bubbles, splashes, water rings, dust), each
with its own validity bit.

A star run's clip may end with a DANCE TAIL (header flag `DANCE_TAIL`): the
frames after the attempt closed, through Mario's closing star grab and dance,
so the ghost celebrates before it fades. The 100-coin star's clip carries one
too although its clock is its exit segment's (it closes on the exit star). The
attempt still closes on its own frame; the manifest's `tail_records` counts
the rows after it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import math
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
from uuid import UUID, uuid4

MAGIC, VERSION, SAMPLE_VERSION = b"SM64GHO5", 5, 6
HEADER_BYTES, RECORD_BYTES, SAMPLE_BYTES = 160, 1232, 1216
MAX_RECORDS, WORLD_READY = 100000, 3
CLOCK, POSE, CAMERA, ACTION, WARP, SHADOW, SOUND, PARTICLES = 1, 2, 4, 8, 16, 32, 64, 128
REQUIRED, ALL_VALID = 31, 255
SHADOW_AT, SOUND_AT, PARTICLES_AT = 16 + 308, 16 + 600, 16 + 636  # record offsets of the optional blocks
SHADOW_BYTES, SOUND_BYTES, SOUND_EVENTS, SOUND_OVERFLOW = 292, 36, 8, 1
PARTICLE_BYTES, PARTICLE_EVENTS, PARTICLE_OVERFLOW = 580, 16, 1
OLDER_MAGICS = (b"SM64GHO1", b"SM64GHO2", b"SM64GHO3")
# GCLIP4/GC5, before particles: still read, as the same clip without them.
V4_MAGIC, V4_VERSION, V4_SAMPLE_VERSION, V4_RECORD_BYTES, V4_SAMPLE_BYTES = b"SM64GHO4", 4, 5, 652, 636
RAW_VALID, PROGRESS_VALID, ELAPSED_VALID = 1, 2, 4
STAR_CLOCK, SEGMENT_CLOCK = 1, 2
DANCE_TAIL = 1  # header flag: rows after the closing frame are the closing star's dance
CLOCK_KINDS = {"star": STAR_CLOCK, "segment": SEGMENT_CLOCK}
_HEADER = struct.Struct("<8s8I16sQqd2Q2I2i4I3Q2I")
_PREFIX = struct.Struct("<4I")
_SAMPLE = struct.Struct("<4I2QqI2H2h2I")
_WARP = struct.Struct("<Hh")
_CLOCK = struct.Struct("<4I6Q")
_SOUND = struct.Struct("<4B8I")
REFUSAL_AT = 16 + 60 + 77  # record offset of gc_pose.refusal
# gc_refusal (ghost_capture.h), by value: the one check that refused the pose.
# With a valid pose, "not_drawn" means the game culled Mario and the root was
# carried from the last frame that proved it, or composed as his own TRS.
REFUSALS = ("", "object", "area", "held", "billboard", "throw_matrix", "floor", "head",
            "transform", "animation", "animation_range", "animation_stale", "body_state",
            "camera", "camera_to_object", "shared_model", "graph", "body_graph", "material",
            "not_drawn", "not_drawn_floor", "body_mismatch", "duplicate", "incomplete")
PRODUCER_FIELDS = ("pid", "created", "generation", "rom_generation", "configuration",
                   "epoch", "qpc_frequency", "qpc_anchor", "utc_anchor")


class ClipError(ValueError):
    """The recording cannot establish the requested native state."""


OLDER_POSE_FORMAT_TEXT = "This Mario recording uses an older pose format. Record a new attempt to use it."


class OlderPoseFormat(ClipError):
    """A preserved recording predates effective graphics-root capture.

    Its bytes stay on disk and keep pruning protection; only playback refuses.
    """

    def __init__(self, message: str = OLDER_POSE_FORMAT_TEXT):
        super().__init__(message)


def attempt_origin(attempt) -> dict:
    """One exact owner identity for export, durable lookup and cheap cache keys."""
    origin = {name: getattr(attempt, name, None) for name in (
        "native_clock_frame", "native_clock_utc", "native_clock_kind", "closed_frame")}
    start, end, kind = origin["native_clock_frame"], origin["closed_frame"], origin["native_clock_kind"]
    if (type(start) is not int or type(end) is not int or not 0 <= start <= end < 2**32
            or not isinstance(kind, str) or kind not in CLOCK_KINDS):
        raise ClipError("attempt has no exact native frame endpoints or owner kind")
    try:
        utc = datetime.fromisoformat(origin["native_clock_utc"])
        if utc.tzinfo is None or not math.isfinite(utc.timestamp()):
            raise ValueError("missing timezone")
    except (TypeError, ValueError, OverflowError) as error:
        raise ClipError("attempt has no valid native origin UTC") from error
    return origin


@dataclass(frozen=True)
class Clock:
    valid: int
    refusal: int
    transition: int
    raw_igt: int
    banked_frames: int
    progress_frames: int
    origin_id: int
    leg_id: int
    elapsed_frames: int
    continuity_id: int


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
    pending_warp_op: int
    delayed_warp_timer: int
    clock: Clock
    sounds: tuple[int, ...] = ()  # Mario's play_sound bits this frame, in order
    sound_overflow: bool = False  # more than SOUND_EVENTS were issued
    refusal: int = 0  # gc_refusal: why this frame's own lists did not prove the pose
    particles: int = 0  # Mario's particles the game drew this frame

    @property
    def refusal_name(self) -> str:
        return REFUSALS[self.refusal]

    @property
    def carried(self) -> bool:
        """Mario was culled: the root came from memory or TRS, not this frame's lists."""
        return bool(self.valid_fields & POSE) and self.refusal != 0

    @property
    def has_shadow(self) -> bool:
        return bool(self.valid_fields & SHADOW)

    @property
    def has_sound(self) -> bool:
        return bool(self.valid_fields & SOUND)

    @property
    def playable(self) -> bool:
        # Stored renderer qualification is diagnostic. Playback uses the live
        # world/camera, so a historical framebuffer refusal cannot erase a pose.
        return self.valid_fields & (CLOCK | POSE) == CLOCK | POSE


def upgrade_record(data: bytes) -> bytes:
    """A GCLIP4/GC5 record read as GCLIP5/GC6: the same bytes, its sizes and
    versions moved on and an empty particle block (native ghost_clip_upgrade.h).
    Anything else is returned unchanged for the ordinary checks."""
    if (len(data) != V4_RECORD_BYTES or struct.unpack_from("<2I", data) != (V4_RECORD_BYTES, V4_VERSION)
            or struct.unpack_from("<2I", data, 16) != (V4_SAMPLE_BYTES, V4_SAMPLE_VERSION)):
        return data
    raw = bytearray(RECORD_BYTES)
    raw[:V4_RECORD_BYTES] = data
    struct.pack_into("<2I", raw, 0, RECORD_BYTES, VERSION)
    struct.pack_into("<2I", raw, 16, SAMPLE_BYTES, SAMPLE_VERSION)
    return bytes(raw)


def frame_from_bytes(data: bytes) -> Frame:
    if ((len(data) == 260 and struct.unpack_from("<2I", data) == (260, 2)
            and struct.unpack_from("<2I", data, 16) == (244, 3))
            or (len(data) == 324 and struct.unpack_from("<2I", data) == (324, 3)
                and struct.unpack_from("<2I", data, 16) == (308, 4))):
        raise OlderPoseFormat()
    data = upgrade_record(data)
    if len(data) != RECORD_BYTES:
        raise ClipError("native ghost record has the wrong size")
    raw = bytes(data)
    size, version, configuration, world = _PREFIX.unpack_from(raw)
    values = _SAMPLE.unpack_from(raw, _PREFIX.size)
    sample_size, sample_version, epoch, valid = values[:4]
    if ((size, version, sample_size, sample_version) !=
            (RECORD_BYTES, VERSION, SAMPLE_BYTES, SAMPLE_VERSION)
            or not configuration or not epoch or valid & ~ALL_VALID or world > 11):
        raise ClipError("native ghost record schema is unsupported")
    count, flags, _producer, _consumer, *bits = _SOUND.unpack_from(raw, SOUND_AT)
    frame = Frame(raw, configuration, world, epoch, valid, *values[4:],
                  *_WARP.unpack_from(raw, 256), Clock(*_CLOCK.unpack_from(raw, 260)),
                  tuple(bits[:count]) if valid & SOUND else (), bool(flags & SOUND_OVERFLOW),
                  raw[REFUSAL_AT], raw[PARTICLES_AT] if valid & PARTICLES else 0)
    if frame.refusal >= len(REFUSALS):
        raise ClipError("native ghost pose refusal is unknown")
    if not frame.sequence or not frame.dlist_serial or frame.qpc < 0 or frame.status > 13:
        raise ClipError("native ghost occurrence is invalid")
    if valid & CLOCK and not (4 <= frame.level <= 36 and 1 <= frame.area <= 8):
        raise ClipError("native ghost place is invalid")
    if bool(valid & ACTION) != bool(frame.live_action):
        raise ClipError("native ghost action validity disagrees with its value")
    for bit, lo, hi in ((POSE, 76, 220), (CAMERA, 220, 256)):
        component = raw[lo:hi] if bit != POSE else raw[lo:REFUSAL_AT] + raw[REFUSAL_AT + 1:hi]
        if not valid & bit and any(component):
            raise ClipError("native ghost refusal retains invalid component bytes")
        if valid & bit:
            floats = struct.unpack_from("<6f", raw, lo)
            if not all(math.isfinite(value) for value in floats):
                raise ClipError("native ghost component contains a nonfinite value")
    if valid & CAMERA and not math.isfinite(struct.unpack_from("<f", raw, 248)[0]):
        raise ClipError("native ghost camera contains a nonfinite value")
    _validate_root(raw, valid)
    if not valid & WARP and (frame.pending_warp_op or frame.delayed_warp_timer):
        raise ClipError("native ghost refusal retains invalid warp bytes")
    if frame.status == 0 and valid & REQUIRED != REQUIRED:
        raise ClipError("native ghost success is missing a component")
    _validate_shadow(raw, valid)
    _validate_sound(count, flags, bits, raw, valid)
    _validate_particles(raw, valid)
    _validate_clock(frame)
    return frame


def _validate_root(raw: bytes, valid: int) -> None:
    if not valid & POSE:
        return
    root_kind = struct.unpack_from("<I", raw, 128)[0]
    matrix = struct.unpack_from("<16f", raw, 156)
    if root_kind == 1:
        if any(raw[156:220]):
            raise ClipError("native ghost TRS root retains matrix bytes")
    elif root_kind == 2:
        if not valid & CAMERA or not all(math.isfinite(value) for value in matrix):
            raise ClipError("native ghost matrix root has no valid recorded camera")
        if (matrix[3], matrix[7], matrix[11], matrix[15]) != (0, 0, 0, 1):
            raise ClipError("native ghost root matrix is not affine")
    else:
        raise ClipError("native ghost root kind is unsupported")


def _validate_shadow(raw: bytes, valid: int) -> None:
    block = raw[SHADOW_AT:SHADOW_AT + SHADOW_BYTES]
    if not valid & SHADOW:
        if any(block):
            raise ClipError("native ghost shadow refusal retains bytes")
        return
    matrix = struct.unpack_from("<16f", block)
    if (not valid & POSE or struct.unpack_from("<I", raw, 128)[0] != 2 or block[163]
            or not all(math.isfinite(value) for value in matrix)
            or (matrix[3], matrix[7], matrix[11], matrix[15]) != (0, 0, 0, 1)):
        raise ClipError("native ghost shadow has no executed root or an invalid matrix")


def _validate_sound(count: int, flags: int, bits: list[int], raw: bytes, valid: int) -> None:
    if not valid & SOUND:
        if any(raw[SOUND_AT:SOUND_AT + SOUND_BYTES]):
            raise ClipError("native ghost sound refusal retains bytes")
        return
    if (count > SOUND_EVENTS or flags & ~SOUND_OVERFLOW or any(bits[count:])
            or (flags & SOUND_OVERFLOW and count != SOUND_EVENTS)):
        raise ClipError("native ghost sound events are inconsistent")


def _validate_particles(raw: bytes, valid: int) -> None:
    block = raw[PARTICLES_AT:PARTICLES_AT + PARTICLE_BYTES]
    if not valid & PARTICLES:
        if any(block):
            raise ClipError("native ghost particle refusal retains bytes")
        return
    count, flags = block[0], block[1]
    if (count > PARTICLE_EVENTS or flags & ~PARTICLE_OVERFLOW or any(block[2:4])
            or (flags & PARTICLE_OVERFLOW and count != PARTICLE_EVENTS) or any(block[4 + 36 * count:])):
        raise ClipError("native ghost particles are inconsistent")
    for at in range(4, 4 + 36 * count, 36):
        opacity = struct.unpack_from("<H", block, at + 22)[0]
        if (opacity and not 0x100 <= opacity <= 0x1FF) or not all(
                math.isfinite(value) for value in struct.unpack_from("<3f", block, at + 24)):
            raise ClipError("native ghost particle entry is invalid")


def _validate_clock(frame: Frame) -> None:
    valid = frame.valid_fields
    clock = frame.clock
    if clock.valid & ~7 or clock.refusal & ~1023 or clock.transition > 6:
        raise ClipError("native ghost clock schema is unsupported")
    if clock.valid & RAW_VALID:
        if (valid & (CLOCK | ACTION | WARP) != CLOCK | ACTION | WARP
                or clock.raw_igt != frame.igt or not clock.leg_id):
            raise ClipError("native ghost derived clock disagrees with its raw evidence")
    elif (clock.valid or clock.raw_igt or clock.origin_id or clock.leg_id):
        raise ClipError("native ghost clock retains invalid raw evidence")
    if clock.valid & PROGRESS_VALID:
        if (clock.refusal or not clock.origin_id
                or clock.progress_frames != clock.banked_frames + clock.raw_igt):
            raise ClipError("native ghost banked progress disagrees with its evidence")
    elif clock.banked_frames or clock.progress_frames:
        raise ClipError("native ghost clock retains invalid progress")
    if clock.valid & ELAPSED_VALID:
        if not clock.continuity_id:
            raise ClipError("native ghost elapsed clock has no continuity identity")
    elif clock.elapsed_frames or clock.continuity_id:
        raise ClipError("native ghost clock retains invalid elapsed evidence")


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
    clock_kind: int
    origin_global_timer: int
    origin_raw: int
    origin_sequence: int
    origin_elapsed: int
    origin_continuity: int
    frames: tuple[Frame, ...]
    flags: int = 0
    file_version: int = VERSION  # 4: saved before particles, read without them

    @property
    def dance_tail(self) -> bool:
        return bool(self.flags & DANCE_TAIL)

    def canonical_frames(self) -> dict[int, Frame]:
        """First exact owner-coordinate occurrence, including pose refusals."""
        result = {}
        for frame in self.frames:
            if self.clock_kind == STAR_CLOCK and frame.clock.origin_id != self.frames[0].clock.origin_id:
                break
            coordinate = frame.clock.elapsed_frames - self.origin_elapsed
            if self.clock_kind == STAR_CLOCK:
                coordinate += self.origin_raw
            result.setdefault(coordinate, frame)
        return result


def _checked_frames(frames: tuple[Frame, ...], configuration: int, epoch: int,
                    clock_kind: int) -> tuple[int, int]:
    if not 1 <= len(frames) <= MAX_RECORDS:
        raise ClipError("native ghost record count is outside its bound")
    if type(clock_kind) is not int or clock_kind not in (STAR_CLOCK, SEGMENT_CLOCK):
        raise ClipError("native ghost has no supported owner clock kind")
    first = frames[0]
    last = None
    indexing, playable = True, False
    for frame in frames:
        if (frame.configuration, frame.epoch) != (configuration, epoch):
            raise ClipError("native ghost spans more than one capture epoch")
        if last and (frame.sequence <= last.sequence or frame.dlist_serial <= last.dlist_serial
                     or frame.qpc < last.qpc):
            raise ClipError("native ghost occurrences are not ordered")
        if frame.clock.valid & (RAW_VALID | ELAPSED_VALID) != RAW_VALID | ELAPSED_VALID:
            raise ClipError("native ghost occurrence has no exact elapsed clock")
        if frame.clock.continuity_id != first.clock.continuity_id:
            raise ClipError("native ghost clock continuity changes inside the attempt")
        if last and (frame.global_timer < last.global_timer
                     or frame.clock.elapsed_frames < last.clock.elapsed_frames):
            raise ClipError("native ghost clock moves backward inside the attempt")
        coordinate = frame.clock.elapsed_frames - first.clock.elapsed_frames
        if clock_kind == STAR_CLOCK:
            coordinate += first.igt
            if frame.clock.origin_id != first.clock.origin_id:
                indexing = False
        if not 0 <= coordinate < 2**64:
            raise ClipError("native ghost relative coordinate exceeds its field")
        playable |= indexing and frame.playable
        last = frame
    if not playable:
        raise ClipError("native ghost has no supported recorded poses")
    return first.level, first.area


def _upgrade_clip(data: bytes) -> bytes:
    """A structurally sized GCLIP4 file as GCLIP5 (ghost_clip_upgrade.h);
    anything else unchanged, for the ordinary header checks to judge."""
    if len(data) < HEADER_BYTES or data[:8] != V4_MAGIC:
        return data
    version, size, record_size, count, _rom, sample_version = struct.unpack_from("<6I", data, 8)
    if ((version, size, record_size, sample_version) != (V4_VERSION, HEADER_BYTES, V4_RECORD_BYTES, V4_SAMPLE_VERSION)
            or not 1 <= count <= MAX_RECORDS or len(data) != HEADER_BYTES + count * V4_RECORD_BYTES):
        return data
    header = bytearray(data[:HEADER_BYTES])
    header[:8] = MAGIC
    struct.pack_into("<3I", header, 8, VERSION, HEADER_BYTES, RECORD_BYTES)
    struct.pack_into("<I", header, 28, SAMPLE_VERSION)
    return bytes(header) + b"".join(upgrade_record(data[at:at + V4_RECORD_BYTES])
                                    for at in range(HEADER_BYTES, len(data), V4_RECORD_BYTES))


def decode_clip(data: bytes) -> Clip:
    if data[:8] in OLDER_MAGICS:
        raise OlderPoseFormat()
    file_version = V4_VERSION if data[:8] == V4_MAGIC else VERSION
    data = _upgrade_clip(data)
    if not HEADER_BYTES <= len(data) <= HEADER_BYTES + MAX_RECORDS * RECORD_BYTES:
        raise ClipError("native ghost file size is outside its bound")
    values = _HEADER.unpack_from(data)
    (magic, version, size, record_size, count, rom, sample_version, configuration,
     epoch, clip_id, frequency, anchor, utc, first, last, first_timer, last_timer,
     level, area, kind, origin_timer, origin_raw, flags, origin_sequence,
     origin_elapsed, origin_continuity, *reserved) = values
    if ((magic, version, size, record_size, rom, sample_version) !=
            (MAGIC, VERSION, HEADER_BYTES, RECORD_BYTES, 1, SAMPLE_VERSION)
            or not configuration or not epoch or not frequency or anchor < 0
            or not math.isfinite(utc) or utc <= 0 or not any(clip_id) or any(reserved)
            or flags & ~DANCE_TAIL
            or not 1 <= count <= MAX_RECORDS or len(data) != HEADER_BYTES + count * RECORD_BYTES):
        raise ClipError("native ghost file header is invalid")
    frames = tuple(frame_from_bytes(data[at:at + RECORD_BYTES])
                   for at in range(HEADER_BYTES, len(data), RECORD_BYTES))
    place = _checked_frames(frames, configuration, epoch, kind)
    if ((level, area) != place or (first, last, first_timer, last_timer) !=
            (frames[0].sequence, frames[-1].sequence, frames[0].global_timer, frames[-1].global_timer)):
        raise ClipError("native ghost header disagrees with its occurrences")
    origin = frames[0]
    if (origin_timer, origin_raw, origin_sequence, origin_elapsed, origin_continuity) != (
            origin.global_timer, origin.igt, origin.sequence, origin.clock.elapsed_frames,
            origin.clock.continuity_id):
        raise ClipError("native ghost header disagrees with its exact origin")
    return Clip(clip_id.hex(), configuration, epoch, frequency, anchor, utc, level, area,
                kind, origin_timer, origin_raw, origin_sequence, origin_elapsed, origin_continuity, frames,
                flags, file_version)


def encode_clip(frames, producer, *, clock_kind: int, clip_id: str | None = None,
                dance_tail: bool = False) -> bytes:
    source = producer_values(producer)
    checked = []
    for frame in frames:
        if len(checked) == MAX_RECORDS:
            raise ClipError("native ghost record count is outside its bound")
        checked.append(frame_from_bytes(frame.raw if isinstance(frame, Frame) else frame))
    records = tuple(checked)
    level, area = _checked_frames(records, source["configuration"], source["epoch"], clock_kind)
    identity = UUID(clip_id).bytes if clip_id else uuid4().bytes
    if not any(identity):
        raise ClipError("native ghost clip identity is empty")
    header = _HEADER.pack(MAGIC, VERSION, HEADER_BYTES, RECORD_BYTES, len(records), 1,
                          SAMPLE_VERSION, source["configuration"], source["epoch"], identity,
                          source["qpc_frequency"], source["qpc_anchor"], source["utc_anchor"],
                          records[0].sequence, records[-1].sequence,
                          records[0].global_timer, records[-1].global_timer, level, area,
                          clock_kind, records[0].global_timer, records[0].igt, DANCE_TAIL if dance_tail else 0,
                          records[0].sequence, records[0].clock.elapsed_frames,
                          records[0].clock.continuity_id, 0, 0)
    return header + b"".join(frame.raw for frame in records)


def read_clip(path: Path, expected_sha256: str | None = None) -> Clip:
    with Path(path).open("rb") as stream:
        data = stream.read(HEADER_BYTES + MAX_RECORDS * RECORD_BYTES + 1)
    if expected_sha256 is not None and hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ClipError("native ghost content hash does not match")
    return decode_clip(data)


def _origin_metadata(clip: Clip) -> dict:
    names = ("clock_kind", "origin_global_timer", "origin_raw", "origin_sequence",
             "origin_elapsed", "origin_continuity")
    return {**{name: getattr(clip, name) for name in names},
            "origin_generation": clip.frames[0].clock.origin_id}


def check_manifest(path: Path, manifest: dict, *, attempt, target: dict) -> Clip:
    """Bind durable bytes to their current attempt, rather than a filename alone."""
    owner = {"attempt_id": attempt.id, "session_id": attempt.session_id, **attempt_origin(attempt)}
    if (not isinstance(manifest, dict) or any(manifest.get(key) != value for key, value in owner.items())
            or manifest.get("target") != target
            or manifest.get("file") != path.name):
        raise ClipError("native ghost manifest belongs to another attempt")
    if type(manifest.get("version")) is int and manifest["version"] in (1, 2, 3):
        raise OlderPoseFormat()
    digest = manifest.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ClipError("native ghost manifest has no content hash")
    source = producer_values(SimpleNamespace(**manifest["source"]))
    clip = read_clip(path, digest)
    first, last = clip.frames[0], clip.frames[-1]
    tail = manifest.get("tail_records", 0)  # absent before dance tails: none
    if type(tail) is not int or not 0 <= tail < len(clip.frames) or bool(tail) != clip.dance_tail:
        raise ClipError("native ghost manifest disagrees with its dance tail")
    expected_kind = CLOCK_KINDS[owner["native_clock_kind"]]
    if (clip.clock_kind != expected_kind or clip.origin_global_timer != owner["native_clock_frame"]
            or clip.frames[-1 - tail].global_timer != owner["closed_frame"]):
        raise ClipError("native ghost manifest disagrees with the attempt's effective origin")
    saved_before_particles = clip.file_version == V4_VERSION
    expected = dict(version=clip.file_version, clip_id=clip.clip_id, bytes=path.stat().st_size,
                    records=len(clip.frames),
                    record_bytes=V4_RECORD_BYTES if saved_before_particles else RECORD_BYTES,
                    sample_version=V4_SAMPLE_VERSION if saved_before_particles else SAMPLE_VERSION,
                    configuration=clip.configuration, epoch=clip.epoch, level=clip.level, area=clip.area,
                    first_sequence=first.sequence, last_sequence=last.sequence,
                    first_global_timer=first.global_timer, last_global_timer=last.global_timer,
                    first_igt=first.igt, last_igt=last.igt, **_origin_metadata(clip))
    if (any(manifest.get(key) != value for key, value in expected.items())
            or any(source[key] != getattr(clip, key)
                   for key in ("configuration", "epoch", "qpc_frequency", "qpc_anchor", "utc_anchor"))):
        raise ClipError("native ghost manifest disagrees with its captured bytes")
    return clip


def pose_gaps(manifest) -> int:
    """Frames inside a recording where Mario's position was not extracted:
    refused poses plus capture occurrences that never arrived. Every later
    ghost feature assumes this is zero (his ask, 2026-09-22: "future features
    hinge on correct extraction"), so it is counted on every commit."""
    if not isinstance(manifest, dict):
        return 0
    return int(manifest.get("invalid_records") or 0) + int(manifest.get("missing_occurrences") or 0)


def write_clip(path: Path, frames, producer, *, clock_kind: int, clip_id: str | None = None,
               tail_records: int = 0) -> dict:
    """Atomically write one clip; the replay publisher owns its durable commit.
    The last `tail_records` frames are a star clip's dance tail."""
    data = encode_clip(frames, producer, clock_kind=clock_kind, clip_id=clip_id,
                       dance_tail=tail_records > 0)
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
            "tail_records": tail_records,
            **_origin_metadata(clip),
            "source": producer_values(producer)}
