"""Independent byte fixture for portable v2/capture v3, including source timing."""
from datetime import datetime, timezone
import struct
from types import SimpleNamespace


def producer(**changes):
    return SimpleNamespace(**{**dict(pid=123, created=456, generation=2, rom_generation=3,
        configuration=4, epoch=5, qpc_frequency=30, qpc_anchor=1000, utc_anchor=1800000000.0), **changes})


def frame(sequence=1, *, timer=None, igt=None, valid=31, world=3, configuration=4,
          epoch=5, qpc=None, level=24, area=1, elapsed=None, continuity=9,
          origin=1, clock_valid=None, refusal=None, transition=1, banked=0):
    raw_igt = sequence if igt is None else igt
    raw = bytearray(260)
    struct.pack_into("<4I", raw, 0, 260, 2, configuration, world)
    struct.pack_into("<4I2QqI2H2h2I", raw, 16, 244, 3, epoch, valid,
        sequence, sequence + 100, 1000 + sequence if qpc is None else qpc,
        100 + sequence if timer is None else timer, sequence & 0xffff,
        raw_igt, level, area, 0 if valid == 31 else 9,
        0x0c400201 if valid & 8 else 0)
    if valid & 2:
        struct.pack_into("<6f", raw, 76, 10, 20, 30, 1, 1, 1)
        struct.pack_into("<3h3h2H4I", raw, 100, 0, 1234, 0, 197, 7, 189, 1, 1,
                         0x0c400201, 0x0c400201, 0, 1)
    if valid & 4:
        struct.pack_into("<6f2hf2h", raw, 156, 0, 100, 500, 0, 0, 0, 0, 0, 45, 100, 20000)
    if clock_valid is None:
        clock_valid = 5 if valid & 25 == 25 else 0
    raw_valid, progress_valid, elapsed_valid = clock_valid & 1, clock_valid & 2, clock_valid & 4
    if refusal is None:
        refusal = 0 if progress_valid else 1 if raw_valid else 2
    struct.pack_into("<4I6Q", raw, 196, clock_valid, refusal, transition,
                     raw_igt if raw_valid else 0, banked if progress_valid else 0,
                     banked + raw_igt if progress_valid else 0, origin if raw_valid else 0,
                     1 if raw_valid else 0, (sequence if elapsed is None else elapsed) if elapsed_valid else 0,
                     continuity if elapsed_valid else 0)
    return bytes(raw)


def attempt(*, start=101, end=103, start_utc=1800000000.0, end_utc=1800000001.0, kind="star"):
    utc = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat()
    return SimpleNamespace(id=77, session_id=9, anchor_frame=start, rta_frames=end-start,
                           native_clock_frame=start, native_clock_utc=utc(start_utc),
                           native_clock_kind=kind, closed_frame=end,
                           started_utc=utc(start_utc), ended_utc=utc(end_utc))
