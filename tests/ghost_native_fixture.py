"""Independent byte fixture for portable v5/capture v6, including source timing,
Mario's recorded shadow, his sound requests and his particles."""
from datetime import datetime, timezone
import struct
from types import SimpleNamespace


def producer(**changes):
    return SimpleNamespace(**{**dict(pid=123, created=456, generation=2, rom_generation=3,
        configuration=4, epoch=5, qpc_frequency=30, qpc_anchor=1000, utc_anchor=1800000000.0), **changes})


IDENTITY = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


def frame(sequence=1, *, timer=None, igt=None, valid=31, world=3, configuration=4,
          epoch=5, qpc=None, level=24, area=1, elapsed=None, continuity=9,
          origin=1, clock_valid=None, refusal=None, transition=1, banked=0,
          shadow=False, sounds=None, overflow=False, action=None, pose_refusal=0, particles=None):
    raw_igt = sequence if igt is None else igt
    valid |= (32 if shadow else 0) | (64 if sounds is not None else 0) | (128 if particles is not None else 0)
    raw = bytearray(1232)
    struct.pack_into("<4I", raw, 0, 1232, 5, configuration, world)
    struct.pack_into("<4I2QqI2H2h2I", raw, 16, 1216, 6, epoch, valid,
        sequence, sequence + 100, 1000 + sequence if qpc is None else qpc,
        100 + sequence if timer is None else timer, sequence & 0xffff,
        raw_igt, level, area, 0 if valid & 31 == 31 else 9,
        (0x0c400201 if action is None else action) if valid & 8 else 0)
    if valid & 2:
        struct.pack_into("<6f", raw, 76, 10, 20, 30, 1, 1, 1)
        struct.pack_into("<3h3h2H4I", raw, 100, 0, 1234, 0, 197, 7, 189, 1, 1,
                         0x0c400201, 0x0c400201, 0, 1)
    raw[153] = pose_refusal  # gc_pose.refusal: the one pose byte a refusal keeps
    if valid & 4:
        struct.pack_into("<6f2hf2h", raw, 220, 0, 100, 500, 0, 0, 0, 0, 0, 45, 100, 20000)
    if clock_valid is None:
        clock_valid = 5 if valid & 25 == 25 else 0
    raw_valid, progress_valid, elapsed_valid = clock_valid & 1, clock_valid & 2, clock_valid & 4
    if refusal is None:
        refusal = 0 if progress_valid else 1 if raw_valid else 2
    struct.pack_into("<4I6Q", raw, 260, clock_valid, refusal, transition,
                     raw_igt if raw_valid else 0, banked if progress_valid else 0,
                     banked + raw_igt if progress_valid else 0, origin if raw_valid else 0,
                     1 if raw_valid else 0, (sequence if elapsed is None else elapsed) if elapsed_valid else 0,
                     continuity if elapsed_valid else 0)
    if shadow:  # an executed root, and the shadow matrix drawn beside it
        struct.pack_into("<I", raw, 128, 2)
        struct.pack_into("<16f", raw, 156, *IDENTITY)
        struct.pack_into("<16f", raw, 324, *IDENTITY[:12], 5, -2, 7, 1)
        struct.pack_into("<3h", raw, 324 + 64, -25, -3, 40)
        raw[324 + 154:324 + 163] = bytes(range(100, 109))
        raw[324 + 164:324 + 292] = bytes(range(128))
    if sounds is not None:
        struct.pack_into("<4B", raw, 616, len(sounds), 1 if overflow else 0, 7, 5)
        if sounds:
            struct.pack_into(f"<{len(sounds)}I", raw, 620, *sounds)
    if particles is not None:  # (display list, view-space translation) per particle
        raw[652] = len(particles)
        for index, (dl, translation) in enumerate(particles):
            at = 656 + 36 * index
            struct.pack_into("<I9hh3f", raw, at, dl, 4096, 0, 0, 0, 4096, 0, 0, 0, 4096, 0, *translation)
    return bytes(raw)


def attempt(*, start=101, end=103, start_utc=1800000000.0, end_utc=1800000001.0, kind="star"):
    utc = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat()
    return SimpleNamespace(id=77, session_id=9, anchor_frame=start, rta_frames=end-start,
                           native_clock_frame=start, native_clock_utc=utc(start_utc),
                           native_clock_kind=kind, closed_frame=end,
                           started_utc=utc(start_utc), ended_utc=utc(end_utc))
