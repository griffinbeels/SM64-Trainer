"""Recorder-owned pose history, independent of video selection and encoding.

Every row retains its immutable renderer occurrence. UTC narrows the possible
capture epoch; exact game-frame endpoints bind an attempt, never a nearest row.
A star attempt's export also keeps its dance tail (`dance_tail`).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
import threading
import time
from types import SimpleNamespace
import zlib

from sm64_events.detectors.anchors import NEAR_ZERO_IGT
from sm64_events.ghost.clip import (
    ELAPSED_VALID, RAW_VALID, STAR_CLOCK, CLOCK_KINDS, RECORD_BYTES,
    CLOSING_TRANSITION_MAX, ClipError, Frame, attempt_origin, frame_from_bytes, producer_values, write_clip,
)
from sm64_events.ghost.native_layout import POSE_AT, VALID_POSE
from sm64_events.memory.addresses import ACT_JUMBO_STAR_CUTSCENE, STAR_GRAB_ACTIONS

# His ask, 2026-09-23: the ghost stays on screen through its own star grab and
# dance, then fades. A star attempt closes at x-cam (the dance's first frame)
# of the grab that completes it -- for the 100-coin star, the EXIT star after
# it -- so its clip keeps the frames after that through the dance's end:
# display only, the attempt's origin and closing frame are unchanged. Measured dances
# run about 100-125 frames past x-cam (the exit dance, and the longer no-exit
# one); the cap keeps a stuck or unusual tail bounded.
#
# A Bowser 1/2 key ends its segment with the same dance (his ruling,
# 2026-09-28: "The key should work the same way as a normal star"; his
# 2026-09-28 archive: every key grab entered ACT_STAR_DANCE_EXIT from idle, a
# frame after the segment's close). The Bowser 3 grand star plays the game's
# own sequence instead, all in ACT_JUMBO_STAR_CUTSCENE (n64decomp
# mario_actions_cutscene.c act_jumbo_star_cutscene): he falls to the arena's
# centre, lands (MARIO_ANIM_GENERAL_LAND, frames 9-23), raises the star
# (MARIO_ANIM_FINAL_BOWSER_RAISE_HAND_SPIN, 100 frames) and takes off
# (MARIO_ANIM_FINAL_BOWSER_WING_CAP_TAKE_OFF, 94 frames); then a scripted
# spline flight (MARIO_ANIM_WING_CAP_FLY) carries him into the credits. Its
# tail stops at the flight, so the cap covers the fall, landing, raise and
# take-off with room to spare.
TAIL_ACTIONS = STAR_GRAB_ACTIONS | {ACT_JUMBO_STAR_CUTSCENE}
MARIO_ANIM_WING_CAP_FLY = 0x2A  # n64decomp include/mario_animation_ids.h
DANCE_TAIL_MAX_FRAMES = 300
# Export can run the moment the attempt closes, before the dance is recorded;
# it waits for the dance to end, for the recording to stall or close, or for
# the cap's own duration plus a second, whichever comes first.
DANCE_TAIL_WAIT_S = DANCE_TAIL_MAX_FRAMES / 30 + 1
DANCE_TAIL_STALL_S = 1.0
# A grab straight into the dance (on the ground, or swimming) can close its
# attempt a frame BEFORE the first recorded dance frame: the star-grab detector
# back-computes the entry from Mario's action timer, which the dance counts
# from 1 on its own first frame (n64decomp general_star_dance_handler:
# `switch (++m->actionTimer)`), and a snapshot may read a frame stale. His SSL
# "Shining Atop the Pyramid" PB (attempt 5386, 2026-09-28) closed on walking
# frame 821 with the dance at 822, and so did his DDD "The Manta Ray's Reward"
# (swimming, 13957/13958): export required the closing row itself to dance and
# dropped both dances. A close the tracker timed at the x-cam may now precede
# the dance by this many rows.
DANCE_ENTRY_FRAMES = 2
# The archive's own ceiling, by bytes, not the clip format's per-clip cap: a
# longer record (GC6's particles) must not shorten how far back a late save
# can reach. Halving the clip cap to 100000 once cut this window to about 55
# minutes (review, 2026-09-23). 288 MiB holds the default two-hour retention
# at 30 Hz (216000 rows) with room for the record to grow; the time bound
# decides what goes, and this only stops a runaway producer.
ARCHIVE_MAX_BYTES = 288 * 1024 * 1024
ARCHIVE_MAX_RECORDS = ARCHIVE_MAX_BYTES // RECORD_BYTES
# Always applied: the recorder's video retention may be "whole session", and
# before 2026-09-23 that None reached here too, so only the record ceiling
# (~499 MiB of rows) ever bounded a long session.
ARCHIVE_RETENTION_S = 7200

# WRITE POLICY (optimization pass, 2026-09-23; measured on his archive, 3000
# real rows): one fsync'd rollback-journal commit per ~1-row batch cost 14 ms
# wall per ingest (the pose pump blocked ~430 ms of every second) and ~48 KB
# written per row. Rows now go into one open WAL transaction (synchronous=
# NORMAL) that commits every COMMIT_ROWS rows or COMMIT_S seconds, and on
# close. Readers share the owning connection, so export, the dance-tail wait
# and status see uncommitted rows without a flush; a crash loses at most
# COMMIT_S of session scratch.
COMMIT_ROWS = 30
COMMIT_S = 1.0
# Rows are session scratch in a per-recorder file: a file from another schema
# version is dropped and recreated, never migrated. v2: integer producer and
# sequence keys and zlib level-1 records (2204 -> ~614 B/row on disk).
SCHEMA_VERSION = 2
_SEQUENCE_BIAS = 1 << 63  # u64 occurrence -> signed INTEGER, order preserved
_ZLIB_LEVEL = 1


def stored_record(blob: bytes) -> bytes:
    """The native record a poses row holds (rows are zlib-compressed)."""
    return zlib.decompress(blob)


def dance_tail(close: Frame, candidates) -> tuple[list[bytes], bool]:
    """Rows after the closing frame through the end of Mario's star dance,
    and whether that end was seen. The grab or dance begins at the close or
    within DANCE_ENTRY_FRAMES after it; a close that no dance follows has no
    tail. The dance ends at the first row that leaves the grab/dance actions,
    stops the game clock (the exit warp freezes it; so does a pause), breaks
    the clip's timing (reset, state load, new star), changes scene, or passes
    DANCE_TAIL_MAX_FRAMES. A repeated occurrence of the same frame is kept, as
    inside the attempt."""
    tail, last = [], close
    danced = close.live_action in TAIL_ACTIONS
    for raw in candidates:
        frame = frame_from_bytes(raw)
        repeat = (frame.global_timer, frame.clock.elapsed_frames) == (last.global_timer, last.clock.elapsed_frames)
        if (frame.global_timer > close.global_timer + DANCE_TAIL_MAX_FRAMES
                or (frame.global_timer > close.global_timer + DANCE_ENTRY_FRAMES if not danced
                    else frame.live_action not in TAIL_ACTIONS)
                or _grand_star_flight(frame, raw)
                or frame.clock.valid & (RAW_VALID | ELAPSED_VALID) != RAW_VALID | ELAPSED_VALID
                or (frame.clock.continuity_id, frame.clock.origin_id, frame.level, frame.area)
                != (close.clock.continuity_id, close.clock.origin_id, close.level, close.area)
                or frame.qpc < last.qpc
                or not (repeat or (frame.global_timer > last.global_timer
                                   and frame.clock.elapsed_frames > last.clock.elapsed_frames))):
            return (tail if danced else []), True
        tail.append(raw)
        last = frame
        danced = danced or frame.live_action in TAIL_ACTIONS
    return (tail if danced else []), False


def _grand_star_flight(frame: Frame, raw: bytes) -> bool:
    """The grand star's scripted flight into the credits, where its tail ends."""
    return (frame.live_action == ACT_JUMBO_STAR_CUTSCENE and frame.valid_fields & VALID_POSE
            and struct.unpack_from("<h", raw, POSE_AT + 30)[0] == MARIO_ANIM_WING_CAP_FLY)


def _utc(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
        return parsed.timestamp()
    except (TypeError, AttributeError, ValueError) as error:
        raise ClipError("attempt has no valid UTC interval") from error


class PoseArchive:
    def __init__(self, path: Path, *, max_records: int = ARCHIVE_MAX_RECORDS,
                 retention_s: float = ARCHIVE_RETENTION_S, boundary_slack_s: float = 1,
                 dance_wait_s: float = DANCE_TAIL_WAIT_S, dance_stall_s: float = DANCE_TAIL_STALL_S,
                 clock=time.monotonic, sleep=time.sleep):
        if type(max_records) is not int or not 1 <= max_records <= ARCHIVE_MAX_RECORDS:
            raise ValueError("pose archive record bound is invalid")
        if (retention_s is None or not math.isfinite(retention_s) or retention_s <= 0
                or not math.isfinite(boundary_slack_s) or boundary_slack_s < 0):
            raise ValueError("pose archive time bound is invalid")
        self.path = Path(path)
        self.max_records, self.retention_s = max_records, retention_s
        self.boundary_slack_s = boundary_slack_s
        self.dance_wait_s, self.dance_stall_s = dance_wait_s, dance_stall_s
        self._clock, self._sleep = clock, sleep  # injectable: tests run on a fake clock
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        if self._db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            self._db.executescript(f"""
                DROP TABLE IF EXISTS poses; DROP TABLE IF EXISTS producers;
                PRAGMA user_version={SCHEMA_VERSION};""")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS producers (
                id INTEGER PRIMARY KEY, identity TEXT NOT NULL UNIQUE, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS poses (
                id INTEGER PRIMARY KEY, producer INTEGER NOT NULL, sequence INTEGER NOT NULL,
                captured REAL NOT NULL, timer INTEGER NOT NULL, data BLOB NOT NULL,
                UNIQUE(producer, sequence));
            CREATE INDEX IF NOT EXISTS poses_time ON poses(captured);
            CREATE INDEX IF NOT EXISTS poses_timer ON poses(timer, captured);
        """)
        self._count, newest = self._db.execute("SELECT COUNT(*), MAX(captured) FROM poses").fetchone()
        self._newest = newest if newest is not None else -math.inf
        self._producers: dict[str, int] = {}  # identity -> producer id
        self._last: dict[int, Frame] = {}     # producer id -> its newest archived frame
        self._uncommitted, self._since = 0, None
        self._new_producer = False

    def ingest(self, producer, frames: tuple[bytes, ...]) -> bool:
        """Archive one bounded batch, or nothing. Closed owners reject late input."""
        if not 0 < len(frames) <= 512:
            if not frames:
                return False
            raise ClipError("native ghost batch exceeds channel capacity")
        source = producer_values(producer)
        document = json.dumps(source, sort_keys=True, separators=(",", ":"))
        identity = hashlib.sha256(document.encode()).hexdigest()
        records = tuple(frame_from_bytes(raw) for raw in frames)
        for frame in records:
            if (frame.configuration, frame.epoch) != (source["configuration"], source["epoch"]):
                raise ClipError("native ghost batch disagrees with its producer identity")
        with self._lock:
            if self._db is None:
                return False
            known = self._producers.get(identity)
            last = self._last.get(known) if known is not None else self._stored_last(identity)
            for frame in records:
                if last and (frame.sequence <= last.sequence or frame.dlist_serial <= last.dlist_serial
                             or frame.qpc < last.qpc):
                    raise ClipError("native ghost archive received a stale occurrence")
                last = frame
            captured = [source["utc_anchor"] + (frame.qpc - source["qpc_anchor"]) / source["qpc_frequency"]
                        for frame in records]
            if not self._db.in_transaction:
                self._db.execute("BEGIN")
            self._db.execute("SAVEPOINT batch")  # this batch, or nothing; earlier batches stay
            try:
                key = known if known is not None else self._producer_id(identity, document)
                self._db.executemany(
                    "INSERT INTO poses(producer,sequence,captured,timer,data) VALUES (?,?,?,?,?)",
                    [(key, frame.sequence - _SEQUENCE_BIAS, at, frame.global_timer,
                      zlib.compress(frame.raw, _ZLIB_LEVEL)) for frame, at in zip(records, captured, strict=True)])
                newest = max(self._newest, *captured)
                removed = self._db.execute("DELETE FROM poses WHERE captured < ?",
                                           (newest - self.retention_s,)).rowcount
                count = self._count + len(records) - removed
                if count > self.max_records:
                    count -= self._db.execute("""DELETE FROM poses WHERE id IN
                        (SELECT id FROM poses ORDER BY id LIMIT ?)""", (count - self.max_records,)).rowcount
                self._db.execute("RELEASE batch")
            except BaseException:
                self._db.execute("ROLLBACK TO batch")
                self._db.execute("RELEASE batch")
                raise
            self._producers[identity], self._last[key] = key, last
            self._count, self._newest = count, newest
            self._uncommitted += len(records)
            self._since = self._clock() if self._since is None else self._since
            if self._uncommitted >= COMMIT_ROWS or self._clock() - self._since >= COMMIT_S:
                self._commit()
            return True

    def _stored_last(self, identity: str) -> Frame | None:
        """A producer this owner has not written yet may already have rows."""
        row = self._db.execute("""SELECT data FROM poses WHERE producer=
            (SELECT id FROM producers WHERE identity=?) ORDER BY sequence DESC LIMIT 1""",
            (identity,)).fetchone()
        return frame_from_bytes(stored_record(row[0])) if row else None

    def _producer_id(self, identity: str, document: str) -> int:
        self._new_producer |= bool(self._db.execute(
            "INSERT OR IGNORE INTO producers(identity,data) VALUES (?,?)", (identity, document)).rowcount)
        return self._db.execute("SELECT id FROM producers WHERE identity=?", (identity,)).fetchone()[0]

    def _commit(self) -> None:
        if self._new_producer:
            # A producer whose rows all aged out is forgotten with them.
            for (gone,) in self._db.execute("""DELETE FROM producers
                    WHERE id NOT IN (SELECT DISTINCT producer FROM poses) RETURNING id""").fetchall():
                self._last.pop(gone, None)
                self._producers = {name: key for name, key in self._producers.items() if key != gone}
            self._new_producer = False
        self._db.commit()
        self._uncommitted, self._since = 0, None

    @contextmanager
    def _reader(self):
        # The owning connection reads its own uncommitted rows: no flush.
        with self._lock:
            owned = self._db
            connection = owned or sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                yield connection
            finally:
                if owned is None:
                    connection.close()

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._commit()
                self._db.close()  # the last close checkpoints the WAL into the file
                self._db = None

    def export_attempt(self, attempt, destination: Path, *, target: dict | None = None,
                       wait_tail: bool = True) -> dict:
        """The attempt's clip. ``wait_tail=False`` takes one look for the star
        dance instead of waiting for it: the clip is ready the moment the run
        closes, and ``tail_pending`` in the manifest says a later export over
        the same path should complete the dance (his rule, round 47)."""
        origin = attempt_origin(attempt)
        start, end_frame = origin["native_clock_frame"], origin["closed_frame"]
        kind = CLOCK_KINDS[origin["native_clock_kind"]]
        lo, hi = _utc(origin["native_clock_utc"]), _utc(attempt.ended_utc)
        if hi < lo:
            raise ClipError("attempt UTC interval is reversed")

        def clocked(frame):
            return frame.clock.valid & (RAW_VALID | ELAPSED_VALID) == RAW_VALID | ELAPSED_VALID

        def endpoint(rows):
            frames = [frame_from_bytes(stored_record(row[1])) for row in rows]
            if not all(clocked(frame) for frame in frames):
                raise ClipError("attempt endpoint has no exact native elapsed clock")
            coordinates = {(frame.epoch, frame.clock.continuity_id, frame.clock.elapsed_frames,
                            frame.igt, frame.clock.origin_id, frame.clock.valid)
                           for frame in frames}
            if len(coordinates) != 1:
                raise ClipError("attempt has ambiguous native endpoint coordinates")
            return rows[0][0]  # first source occurrence, including a refused pose

        with self._reader() as db:
            candidates = db.execute("""SELECT DISTINCT producer FROM poses
                WHERE timer=? AND captured BETWEEN ? AND ?""",
                (start, lo - self.boundary_slack_s, lo + self.boundary_slack_s)).fetchall()
            matches = []
            for (identity,) in candidates:
                ends = db.execute("""SELECT sequence,data FROM poses WHERE producer=? AND timer=?
                    AND captured BETWEEN ? AND ? ORDER BY sequence""",
                    (identity, end_frame, hi - self.boundary_slack_s, hi + self.boundary_slack_s)).fetchall()
                if not ends:
                    continue
                starts = db.execute("""SELECT sequence,data FROM poses WHERE producer=? AND timer=?
                    AND captured BETWEEN ? AND ? ORDER BY sequence""",
                    (identity, start, lo - self.boundary_slack_s, lo + self.boundary_slack_s)).fetchall()
                first = endpoint(starts)
                ends, transition = self._before_transition(db, identity, ends, end_frame, clocked)
                last = endpoint(ends)
                if first <= last:
                    matches.append((identity, first, last, transition))
            if len(matches) != 1:
                raise ClipError("attempt has ambiguous native epochs" if matches else
                                "attempt is missing exact native frame endpoints")
            identity, first, last, transition = matches[0]
            source = json.loads(db.execute("SELECT data FROM producers WHERE id=?", (identity,)).fetchone()[0])
            rows = [(stored_record(data),) for (data,) in db.execute("""SELECT data FROM poses
                WHERE producer=? AND sequence BETWEEN ? AND ? ORDER BY sequence""", (identity, first, last))]
        # The immutable copy survives pruning or recorder detach during publication.
        if kind == STAR_CLOCK and frame_from_bytes(rows[0][0]).igt > NEAR_ZERO_IGT:
            raise ClipError("star origin has no canonical near-zero native clock witness")
        close = frame_from_bytes(rows[-1][0])
        # A star run, whatever owns its clock: the 100-coin star is timed by
        # its exit segment and closes on the exit star's grab. Segments keep
        # ending where they close.
        # A key segment (Bowser 1/2 key, Bowser 3 grand star) ends like one.
        star_run = (getattr(attempt, "segment_id", None) is None
                    or getattr(attempt, "closed_by", None) == "key_grabbed")
        # The dance follows a close that already dances, or one the tracker
        # timed at the x-cam (a grab straight into the dance can close a frame
        # before it: DANCE_ENTRY_FRAMES).
        dances = (close.live_action in TAIL_ACTIONS or getattr(attempt, "timed_at", None) == "xcam"
                  or getattr(attempt, "closed_by", None) == "key_grabbed")
        tail, pending = (self._dance_tail(identity, last, close, wait=wait_tail)
                         if star_run and dances else ([], False))
        manifest = write_clip(destination, tuple(row[0] for row in rows) + tuple(tail),
                              SimpleNamespace(**source), clock_kind=kind, tail_records=len(tail))
        manifest["tail_pending"] = pending
        if transition:
            manifest["closing_transition"] = transition
        return {**manifest, "attempt_id": attempt.id, "session_id": attempt.session_id,
                **origin,
                "target": dict(target) if target else None}

    @staticmethod
    def _before_transition(db, identity, ends, end_frame, clocked):
        """The closing rows, or, when the close fell inside a level load with no
        clock, the last clocked occurrence at most CLOSING_TRANSITION_MAX frames
        before it and how far before (see clip.CLOSING_TRANSITION_MAX)."""
        if all(clocked(frame_from_bytes(stored_record(row[1]))) for row in ends):
            return ends, 0
        for sequence, timer, data in db.execute("""SELECT sequence,timer,data FROM poses
                WHERE producer=? AND sequence<? AND timer BETWEEN ? AND ? ORDER BY sequence DESC""",
                (identity, ends[0][0], end_frame - CLOSING_TRANSITION_MAX, end_frame)):
            if clocked(frame_from_bytes(stored_record(data))):
                return [(sequence, data)], end_frame - timer
        return ends, 0  # no clock close enough: the endpoint refuses as before

    def _dance_tail(self, identity: int, after: int, close: Frame, *, wait: bool = True) -> tuple[list[bytes], bool]:
        """Wait, bounded, for the recorder to capture the dance; outside the
        lock between looks so capture keeps committing. Returns the tail and
        whether it is still pending: with ``wait=False`` one look, pending while
        the recorder is open and the dance has not ended; with the wait, never."""
        deadline = self._clock() + self.dance_wait_s
        seen, moved = -1, self._clock()
        while True:
            with self._reader() as db:
                recording = self._db is not None
                candidates = [stored_record(row[0]) for row in db.execute(
                    "SELECT data FROM poses WHERE producer=? AND sequence>? ORDER BY sequence LIMIT ?",
                    (identity, after, 4 * DANCE_TAIL_MAX_FRAMES))]
            tail, ended = dance_tail(close, candidates)
            now = self._clock()
            if len(candidates) != seen:
                seen, moved = len(candidates), now
            if not wait:
                return tail, bool(recording and not ended)
            if ended or not recording or now >= deadline or now - moved >= self.dance_stall_s:
                return tail, False
            self._sleep(0.1)

    def status(self) -> dict:
        with self._reader() as db:
            count, first, last = db.execute("SELECT COUNT(*),MIN(captured),MAX(captured) FROM poses").fetchone()
        return {"records": count, "first_utc": first, "last_utc": last, "closed": self._db is None}
