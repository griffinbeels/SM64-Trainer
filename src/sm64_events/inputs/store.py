# src/sm64_events/inputs/store.py
"""Captured input, in run-length chunks beside the journal.

A chunk is one contiguous stretch of capture. It ends when the emulator goes
away, when the frame counter jumps backward (a console reset restarting it),
or when the writer flushes on its own count.

**Nothing here is keyed by attempt id.** Attempts are re-derived from the
journal on every reprojection, so a row keyed to one orphans itself. A chunk
is found by the wall-clock span it covers and read by frame number within it —
and wall clock is also the only TOTAL order, because the frame counter repeats
within a session every time the console is reset.

Each run carries the frame it STARTS on rather than only its length. That is
what makes a capture hole survive the round trip as a hole: a decoder given
only lengths would have to assume the frames were contiguous, which is the one
thing a hole means they are not.

Size: 45 s of real play compresses from 1,348 frames to 289 runs
(`tools/probe_inputs.py`, 2026-08-20) — about 3 KB, a rounding error beside
the clip ring.
"""
import struct
from datetime import datetime, timezone

from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.runs import collapse, same_state

_HEADER = struct.Struct("<II")        # first frame number | run count
# v1: start_frame u32 | run_length u16 | buttons u16 | stick_x s8 | stick_y s8
_RUN_V1 = struct.Struct("<IHHbb")
# v2 adds what MARIO was doing while that was held (round 32): his action id,
# his face-angle yaw and his forward speed. All three are OFFSETS off an
# address the layout already carries, so the capture cost is one more read
# inside the same window.
_RUN_V2 = struct.Struct("<IHHbbIhf")
_RUNS = {1: _RUN_V1, 2: _RUN_V2}
FORMAT = 2                            # what new chunks are written as
_MAX_RUN = 0xFFFF


def encode_runs(frames: list[tuple[int, InputFrame]]) -> bytes:
    """Collapse consecutive frames with identical state into runs.

    `pressed` is not stored: it is derivable from consecutive frames, and a
    second copy of one fact is a second thing that can disagree.
    """
    # Mario's own state counts: an action change with the pad unmoved is
    # exactly the transition the action row exists to show, so it breaks a run.
    runs = collapse(frames, same_state, max_length=_MAX_RUN)
    out = bytearray(_HEADER.pack(runs[0].start if runs else 0, len(runs)))
    for start_number, length, frame in runs:
        out += _RUN_V2.pack(start_number, length, frame.buttons,
                            frame.stick_x, frame.stick_y,
                            frame.action & 0xFFFFFFFF, frame.yaw, frame.speed)
    return bytes(out)


def decode_runs(blob: bytes, chunk_format: int = FORMAT
                ) -> list[tuple[int, InputFrame]]:
    """`chunk_format` says which run layout the blob holds.

    Stored per chunk rather than guessed from the byte length: a v1 chunk and
    a v2 chunk can be the same size at different run counts, so length is not
    a discriminator, and treating it as one decodes one as the other and
    returns plausible nonsense.
    """
    layout = _RUNS.get(chunk_format)
    if layout is None:
        raise ValueError(f"unknown input-chunk format {chunk_format}")
    _first, count = _HEADER.unpack_from(blob, 0)
    at = _HEADER.size
    out: list[tuple[int, InputFrame]] = []
    for _ in range(count):
        fields = layout.unpack_from(blob, at)
        at += layout.size
        start_number, length, buttons, stick_x, stick_y = fields[:5]
        action, yaw, speed = (fields[5], fields[6], fields[7]) \
            if chunk_format >= 2 else (0, 0, 0.0)
        for step in range(length):
            out.append((start_number + step,
                        InputFrame(buttons, 0, stick_x, stick_y,
                                   action, yaw, speed)))
    return out


class InputStore:
    """Chunk rows over the journal's own connection and lock."""

    def __init__(self, conn, lock):
        self._conn = conn
        self._lock = lock

    def append(self, session_id: int, frames: list[tuple[int, InputFrame]],
               started_utc: str, ended_utc: str) -> None:
        if not frames:
            return
        blob = encode_runs(frames)
        with self._lock:
            self._conn.execute(
                "INSERT INTO input_chunks (session_id, start_frame, end_frame,"
                " started_utc, ended_utc, runs, format)"
                " VALUES (?,?,?,?,?,?,?)",
                (session_id, frames[0][0], frames[-1][0], started_utc,
                 ended_utc, blob, FORMAT))
            self._conn.commit()

    def frames_between(self, started_utc: str,
                       ended_utc: str) -> list[tuple[int, InputFrame]]:
        """Every captured frame in chunks OVERLAPPING that span, in capture
        order — by started_utc then id, never by frame number."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT runs, format FROM input_chunks"
                " WHERE started_utc <= ? AND ended_utc >= ?"
                " ORDER BY started_utc, id", (ended_utc, started_utc)
            ).fetchall()
        out: list[tuple[int, InputFrame]] = []
        for row in rows:
            out.extend(decode_runs(row["runs"], row["format"]))
        return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChunkWriter:
    """Buffers frames from the sampler and writes a chunk at a time.

    Flushes every `FLUSH_FRAMES` (10 seconds of play), so a crash loses
    seconds rather than a session, and immediately when the frame counter
    jumps backward — a console reset restarts it, and one chunk cannot hold
    both sides of that seam and still decode as a monotonic run.
    """

    FLUSH_FRAMES = 300

    def __init__(self, store: InputStore, session_id, clock=_now):
        """`session_id` is an int or a CALLABLE returning one.

        The composition root builds this before the tracker has opened a
        session, so the id cannot be captured at build time. A callable that
        answers None means there is no session yet, and whatever is buffered
        belongs to nothing — it is dropped rather than filed under a session
        that did not exist while it was played.
        """
        self._store = store
        self._session_id = session_id
        self._clock = clock
        self._buffer: list[tuple[int, InputFrame]] = []
        self._started: str | None = None

    def _session(self) -> int | None:
        return (self._session_id() if callable(self._session_id)
                else self._session_id)

    def add(self, number: int, frame: InputFrame) -> None:
        if self._buffer and number < self._buffer[-1][0]:
            self.close()
        if not self._buffer:
            self._started = self._clock()
        self._buffer.append((number, frame))
        if len(self._buffer) >= self.FLUSH_FRAMES:
            self.close()

    def close(self) -> None:
        if not self._buffer:
            return
        session = self._session()
        if session is not None:
            self._store.append(session, self._buffer,
                               self._started, self._clock())
        self._buffer = []
        self._started = None
