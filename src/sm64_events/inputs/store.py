# src/sm64_events/inputs/store.py
"""Captured input, in run-length chunks beside the journal.

A chunk is one contiguous stretch of capture. It ends when the emulator goes
away, when the frame counter jumps backward (a console reset restarting it),
or when the writer flushes on its own count.

**Nothing here is keyed by attempt id.** Attempts are re-derived from the
journal on every reprojection, so a row keyed to one orphans itself. A chunk
is found by the wall-clock span it covers and read by frame number within it —
and its insertion order survives counter resets and wall-clock corrections.
New sampled chunks retain each observation's UTC time and local source ordinal;
legacy chunks only have their emission/flush bounds.

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
from time import monotonic
from typing import NamedTuple

from sm64_events.inputs.frame import InputFrame, valid_raw_stick
from sm64_events.inputs.observation import (InputObservation, decode_observations,
                                           encode_observations, utc_time)
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
FORMAT = 2                            # legacy / unattributed run format
OBSERVED_FORMAT = 3                   # v2 runs followed by compressed provenance
_MAX_RUN = 0xFFFF
_CURRENT_SESSION = object()


class InputChunk(NamedTuple):
    id: int
    session_id: int
    started_utc: str
    ended_utc: str
    frames: list[tuple[int, InputFrame]]
    observations: list[InputObservation] | None = None


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
    layout = _RUNS.get(2 if chunk_format == OBSERVED_FORMAT else chunk_format)
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
               started_utc: str, ended_utc: str, *,
               observations: list[InputObservation] | None = None) -> None:
        if not frames:
            return
        blob = encode_runs(frames)
        chunk_format = FORMAT
        if observations is not None:
            blob += encode_observations(observations, len(frames))
            chunk_format = OBSERVED_FORMAT
            moments = [stamp for o in observations
                       for stamp in (o.lower_utc, o.upper_utc)]
            started_utc, ended_utc = min(moments), max(moments)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO input_chunks (session_id, start_frame, end_frame,"
                " started_utc, ended_utc, runs, format)"
                " VALUES (?,?,?,?,?,?,?)",
                (session_id, frames[0][0], frames[-1][0],
                 _canonical_utc(utc_time(started_utc)),
                 _canonical_utc(utc_time(ended_utc)), blob, chunk_format))
            self._conn.commit()

    def frames_between(self, started_utc: str,
                       ended_utc: str) -> list[tuple[int, InputFrame]]:
        """Every captured frame in chunks OVERLAPPING that span, in capture
        order — by chunk id, never by wall clock or game frame number."""
        return [frame for chunk in self.chunks_between(started_utc, ended_utc)
                for frame in chunk.frames]

    def chunks_between(self, started_utc: str, ended_utc: str,
                       session_id: int | None = None) -> list[InputChunk]:
        """Retain provenance while resolving repeated game counters.

        Observed chunks bound actual sample times and carry per-frame records.
        Legacy bounds delimit emission/flush. Neither permits interpolation.
        """
        owner = " AND session_id = ?" if session_id is not None else ""
        start, end = utc_time(started_utc), utc_time(ended_utc)
        # Rows are written by `_now`/isoformat in one canonical spelling
        # (microseconds, +00:00), so the same spelling of the bounds compares
        # correctly as text and the (ended_utc, started_utc) index answers
        # the range. `julianday()` on the columns defeated every index and
        # scanned the whole table on each replay open (round 48: 14.9 ms
        # for 3 rows, growing ~0.5 ms per hour of play forever).
        params = (_canonical_utc(start), _canonical_utc(end))  # ended >= start, started <= end
        if session_id is not None:
            params += (session_id,)
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, session_id, started_utc, ended_utc, runs, format FROM input_chunks"
                " INDEXED BY idx_input_chunks_ended"
                " WHERE ended_utc >= ? AND started_utc <= ?"
                + owner + " ORDER BY id", params
            ).fetchall()
        # Text order only selects candidates; exact microsecond overlap in
        # Python decides.
        return [_chunk(row) for row in rows
                if utc_time(row["started_utc"]) <= end
                and utc_time(row["ended_utc"]) >= start]


def _canonical_utc(moment) -> str:
    """The one spelling `input_chunks` rows use, so text order is time order."""
    return moment.isoformat(timespec="microseconds")


def _chunk(row) -> InputChunk:
    blob, chunk_format = row["runs"], row["format"]
    frames = decode_runs(blob, chunk_format)
    observations = None
    if chunk_format == OBSERVED_FORMAT:
        _, runs = _HEADER.unpack_from(blob)
        at = _HEADER.size + runs * _RUN_V2.size
        observations = decode_observations(blob[at:], len(frames))
    return InputChunk(row["id"], row["session_id"], row["started_utc"],
                      row["ended_utc"], frames, observations)


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

    def __init__(self, store: InputStore, session_id, clock=_now, *, retry_clock=monotonic):
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
        self._buffer_session: int | None = None
        self._observations: list[InputObservation] = []
        self._retry_clock = retry_clock
        self._retry_at = 0.0
        self._retry_delay = 0.25
        self._write_error: str | None = None
        self._write_failures = 0
        self._rejected_frames = 0

    def health(self) -> dict:
        return {"pending_frames": len(self._buffer), "failures": self._write_failures,
                "rejected_frames": self._rejected_frames, "error": self._write_error,
                "retry_in_s": max(0.0, self._retry_at - self._retry_clock())}

    def retry(self) -> None:
        """Retry a failed chunk when due, including while gameplay is paused."""
        if self._write_error is not None and self._retry_clock() >= self._retry_at:
            self.close()

    def _session(self) -> int | None:
        return (self._session_id() if callable(self._session_id)
                else self._session_id)

    def add(self, number: int, frame: InputFrame, *, session_id=_CURRENT_SESSION,
            observation: InputObservation | None = None) -> None:
        if not valid_raw_stick(frame.stick_x, frame.stick_y):
            raise ValueError(f"invalid raw stick on frame {number}: "
                             f"{frame.stick_x}, {frame.stick_y}")
        if self._write_error is not None:
            try:
                self.close()
            except Exception:
                self._rejected_frames += 1
                raise
        # The sampler supplies the owner observed with the pending frame.
        # Direct callers capture ownership here, never later during close().
        session = self._session() if session_id is _CURRENT_SESSION else session_id
        changed_source = (bool(self._observations) != (observation is not None)
                          or (observation is not None and self._observations
                              and (observation.source_id != self._observations[-1].source_id
                                   or observation.sequence <= self._observations[-1].sequence)))
        if self._buffer and (session != self._buffer_session
                             or number <= self._buffer[-1][0] or changed_source):
            self.close()
        if session is None:
            return
        # Failed writes retain the valid chunk for a later retry. Do not
        # keep growing that buffer on every new frame while storage is down.
        if len(self._buffer) >= self.FLUSH_FRAMES:
            self.close()
        if not self._buffer:
            self._started = observation.observed_utc if observation else self._clock()
            self._buffer_session = session
        self._buffer.append((number, frame))
        if observation is not None:
            self._observations.append(observation)
        if len(self._buffer) >= self.FLUSH_FRAMES:
            self.close()

    def close(self) -> None:
        if not self._buffer:
            return
        if self._retry_clock() < self._retry_at:
            raise OSError(self._write_error or "input storage retry deferred")
        session = self._buffer_session
        try:
            if session is not None:
                if self._observations:
                    self._store.append(session, self._buffer, self._started,
                                       self._observations[-1].observed_utc,
                                       observations=self._observations)
                else:
                    self._store.append(session, self._buffer,
                                       self._started, self._clock())
        except Exception as error:
            self._write_failures += 1
            self._write_error = f"{type(error).__name__}: {error}"[:512]
            self._retry_at = self._retry_clock() + self._retry_delay
            self._retry_delay = min(10.0, self._retry_delay * 2)
            raise
        self._write_error = None
        self._retry_at = 0.0
        self._retry_delay = 0.25
        self._buffer = []
        self._started = None
        self._buffer_session = None
        self._observations = []
