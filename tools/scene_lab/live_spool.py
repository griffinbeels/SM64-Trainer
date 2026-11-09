# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""The live spool: the scene tap recording every graphics task while the Trainer's lab runs.

    live_spool.py status [--lab-root DIR]          the newest session's health and task span
    live_spool.py lease [--seconds N] [--lab-root DIR]   hold a lease (a manual check)

Intent: open the scene of an attempt right after playing it. The Trainer holds a Lease
(lease.txt beside the spool files, renewed every 2 s while the scene lab's recording is on;
the Scene viewer shows that it runs and has the switch, server/scene_lab_api.py); the
wrapper's scene tap (plugin/gfxwrap/scene_tap.cpp) checks it at most once a second, records
continuously while it is fresh and stops once it is stale (8 s without renewal) or gone.
Nothing else starts it. Its writer (scene_spool.cpp) writes segments over a fixed set of
spool files in <lab>/live/ (format 2: spool-00 to spool-63, .tap and .idx, reused in turn
and never deleted; spool-table.bin says which segment each holds): each one starts with keys
from the writer's own shadows, so Spool.source() decodes any recent span from that
segment's first byte, and an append-only index names every task, its global_timer, level,
area and time. A reader sees a task once the next one arrives or 100 ms later. One session
(one set of lease lines in one ROM run) at a time: a new one takes over the files, and a
reader of the old one sees its segments leave.
Spool.follow() keeps a scene's captures coming while he plays (a cutter writes the
package as the attempt goes and finishes at its end); Spool.source() cuts a finished span.
The lease asks for tap.py arm's identity and scene-state lines and the per-VI audio spans
with SP DMEM (tap.audio_state_lines: the stem renderer's input, stems_live.py; audio=False
leaves them out), never pictures or full copies.

Evidence (2026-10-26, this machine). Disk: his BoB session (tap-20261025-184022, 90 s,
29 tasks/s) through the real writer (tap_offline_host --spool-bench), 15 s segments,
MB = 10^6 bytes, writer = its busy time on one thread:
    raw records          675 MB/min   writer 0.3% of a core
    256-byte dedupe      139 MB/min   0.2%
    XPRESS alone         247 MB/min   2.0%
    dedupe + XPRESS       42 MB/min   0.45%   (format 1's default; keys included)
plus the index (0.06 MB/min) and boot.bin (4 MiB once per session). Format 2 (2026-11-06)
compresses with XPRESS_HUFF through the Windows Compression API instead of ntdll's XPRESS
found by name. The same BoB session through both real writers (--spool-bench, dedupe on):
62,981,216 bytes (42.0 MB/min, writer 0.40 s busy, 0.44% of a core) became 55,625,088
(37.1 MB/min, -11.7%; writer 0.72 s, 0.80% of a core, on the tap thread, never the
emulation thread). On 8 segments (2 minutes) of his 2026-11-05 session's own packed
streams: 57.9 MB became 51.5 MB (-11%); MSZIP and zlib were smaller still but 6 to 47
times slower than XPRESS_HUFF. The bound is the 64 spool files, each at most max_bytes / 64
(2 GiB / 64 = 32 MiB): at most 2 GiB, which 15 s segments fill only past about 128 MiB a
minute; at 42 MB/min the files hold the last 16 minutes in about 670 MB. Producer, savestates
at 30 Hz through tap_offline_host: 212 us p50 and 384 us p99 per task live, the same as
the armed tap before this change (211/388); 0.4 us per call with no lease. Reader at
30 Hz: a task is listed 50 ms p50, 67 ms p99 and max after the game made it. Lease:
capture started 19 ms and resumed 165 ms after Lease.start() (a check runs every second),
stopped 176 ms after Lease.stop() and 8.06 s after a crashed Trainer's last renewal.
His session read back from the spool: tasks 1000-1300 (from mid-segment) and 1700-1800
(across a segment start) match the one-shot tap.bin's closures and scene states, and the
package's code row (97,100 bytes at ROM 0x7cc6c0); Spool.source decodes 537 tasks/s.
Spool.follow at 30 Hz on savestate frames: a capture 35 ms p50, 45 ms p99, 79 ms max after
the game made the task, at 7.6% of a core for the follower.

The audio spans (2026-10-27, two round-1 savestates replayed through the real writer at one
VI each, 1199 VIs and 600 tasks each, music playing and the probe's coin and voice
requests): a VI delta is 21.4 KB raw and 13.6 KB on disk after XPRESS on Whomp's Fortress
(48.9 MB a minute at 59.94 VIs a second), 5.4 KB and 3.1 KB on the quieter one (11.0 MB a
minute); each segment's VI key adds 95 to 134 KB. With the 42 MB a minute of tasks above,
the window holds 0.8 to 1.4 GB, so the 15 minutes still bind before the 2 GiB cap (which
binds only past 143 MB a minute). The producer's cost per VI rose from 18 to 35 us p50
(58 us p99, 239 us max); the writer's busy time was 0.9% of a core.

Limits:
- Lab only. The lease file lives under %LOCALAPPDATA% of the Project64 process; a
  lab_root elsewhere is seen only by a test host that sets LOCALAPPDATA to its parent.
- One Trainer per machine: Lease.stop() deletes the file whoever else holds it. One
  Project64 records at a time: the writer holds writer.lock open, and a second one waits.
- A new session (new lease lines, or Project64 reopening the game) ends the last one's
  segments at once: its readers end with a reason, and its recording cannot be cut after.
- Only Project64.exe takes the lease (a test host must opt in).
- A full ring still drops records: health()["holes"] names the tasks left incomplete,
  and a consumer must not cut a scene across one.
- A segment's first task carries a whole key (1.4 MB of level state before compression), so
  the segment length trades disk (a key per segment) against how far a reader decodes
  to reach a task (at most one segment, under a second).
- Task indexes count the tasks the tap saw: a lapse leaves no gap in them; time and
  global_timer tell when a task was drawn.
- Without boot.bin (its copy was dropped), a package lacks the act selector's code row.
- captures() is the capture loop package.tap_source still repeats inline (package.py is
  m4-cut's); a test holds the two equal until tap_source calls it.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import struct
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package  # noqa: E402
import provenance  # noqa: E402
import tap  # noqa: E402

log = logging.getLogger("sm64.scene_lab")

ROW = struct.Struct("<IIIIqQ")          # kind, a, b, c, d, e (scene_spool.cpp's header comment)
ROW_OPEN, ROW_TASK, ROW_SHOWN, ROW_HOLE, ROW_BEAT, ROW_CLOSE, ROW_SESSION = 1, 2, 3, 4, 5, 6, 7
TASK_SHOWN, TASK_REFUSED = 1, 2
CLOSE_ROLLOVER = 1
FORMAT = 2                              # spool-table.bin and the SESSION row (scene_spool.h kFormat)
TABLE = "spool-table.bin"
TABLE_HEAD = struct.Struct("<IIQIIII")  # magic, format, session, files, 0, 0, 0
TABLE_MAGIC = 0x32534C53                # "SLS2"
MAX_FILES = 64                          # spool-00 .. spool-63 (scene_spool.h kMaxFiles)
ENTRY = struct.Struct("<II")            # the segment a file holds, and its complement
REASONS = {2: "The Trainer stopped the scene recording.",
           3: "The lease lapsed: the Trainer stopped renewing it.",
           4: "Project64 closed the game.",
           5: "The spool could not be written.",
           6: "The lease's settings changed.",
           7: "The capture layer was unloaded.",
           8: "The capture layer could not allocate the capture's memory."}
END_REASONS = {"lease_stopped": 2, "lease_lapsed": 3, "rom_closed": 4, "write_failed": 5, "rearmed": 6,
               "unloaded": 7, "no_memory": 8}
FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
RENEW_SECONDS = 2.0
LEASE_MS = 8000
STALE_SECONDS = 3.0                     # an open segment with no row this long: the writer stopped
FOLLOW_POLL = 0.02                      # a follower at the live edge re-reads this often
MAX_BYTES = 2 << 30                     # every spool file at most MAX_BYTES / 64; 15 s segments usually bind first


def resolve_lab_root(override: Path | None = None) -> Path:
    return Path(override) if override is not None else tap.lab_root()


def lease_text(version: str = "us", extra_lines: Sequence[str] = (), *, audio: bool = True,
               max_bytes: int = MAX_BYTES, segment_seconds: float = 15) -> str:
    """The lease's lines: tap.arming_text's identity and scene-state lines, the per-VI audio
    spans unless `audio` is off (never pictures), the live settings, the boot span, then the
    caller's extra lines (which win; spool_files=<2..64> makes a test's spool wrap sooner)."""
    arming = tap.arming_text(SimpleNamespace(version=version, seconds=600, no_reference=True, reference_every=1,
                                             full_every=0, no_scene_state=False, no_audio_state=not audio))
    live = [f"lease_ms={LEASE_MS}", f"max_bytes={int(max_bytes)}", f"segment_ms={int(segment_seconds * 1000)}",
            f"boot_region={provenance.EXPANSION_RAM:x}:{(8 << 20) - provenance.EXPANSION_RAM}"]
    return "# scene-lab live lease (tools/scene_lab/live_spool.py)\n" + arming + "\n".join([*live, *extra_lines]) + "\n"


class Lease:
    """The Trainer's side of continuous capture: lease.txt, renewed while it runs."""

    def __init__(self, lab_root: Path | None = None, *, extra_lines: Sequence[str] = (), version: str = "us",
                 audio: bool = True, max_bytes: int = MAX_BYTES, segment_seconds: float = 15) -> None:
        self.lab_root = resolve_lab_root(lab_root)
        self.path = self.lab_root / "live" / "lease.txt"
        self.text = lease_text(version, extra_lines, audio=audio, max_bytes=max_bytes,
                               segment_seconds=segment_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Arm with these lines (the wrapper notices within a second) and keep renewing. The
        first start in a lab moves format 1's folders out (clear_old_layout)."""
        clear_old_layout(self.lab_root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write()
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._renew, name="scene-lease", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Disarm: the wrapper closes its segment within a second of noticing."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        for _ in range(50):
            try:
                self.path.unlink(missing_ok=True)
                return
            except PermissionError:   # the wrapper is reading it this instant
                time.sleep(0.02)

    def _write(self) -> None:
        partial = self.path.with_name(self.path.name + ".partial")
        partial.write_text(self.text, encoding="ascii")
        for _ in range(50):
            try:
                os.replace(partial, self.path)
                return
            except PermissionError:
                time.sleep(0.02)
        raise PermissionError(f"could not write {self.path}")

    def _renew(self) -> None:
        while not self._stop.wait(RENEW_SECONDS):
            try:
                os.utime(self.path)
            except FileNotFoundError:
                self._write()
            except PermissionError:
                pass   # renewed on the next pass, long before it lapses


@dataclass(frozen=True)
class TaskRef:
    index: int
    global_timer: int
    level: int
    area: int
    utc: datetime
    has_present: bool
    refused: bool = False               # the walker refused its list: source() skips it, follow() ends there


@dataclass
class LiveSource(package.Source):
    """Spool.source's Source. A capture's times count from origin_qpc, the producer QPC of the
    first task the captures reached (set when they reach it): its present showed at
    origin_qpc + present_time_us * Spool.frequency // 1_000_000."""
    origin_qpc: int | None = None


@dataclass
class FollowSource(LiveSource):
    """Spool.follow's Source: its captures keep coming as the writer flushes tasks."""
    ended: str | None = None            # why the captures stopped short (a plain sentence), or None
    last: int | None = None             # the newest task the captures yielded
    stop: int | None = None

    def stop_at(self, last: int) -> None:
        """End once the next task would pass `last`, promptly even while waiting; -1 abandons."""
        self.stop = last


@dataclass
class _Segment:
    number: int
    file: int                                           # the spool file holding it (spool-KK)
    read: int = 0                                       # index bytes consumed
    verified: bool = False                              # its SESSION row named this session
    opened: tuple[int, int] | None = None               # (qpc, FILETIME)
    tasks: dict = field(default_factory=dict)           # index -> [timer, place, qpc, offset, flags]
    holes: list = field(default_factory=list)
    beat: tuple | None = None                           # (lost, refused, tasks, qpc, FILETIME)
    newest_qpc: int = 0
    closed: tuple | None = None                         # (reason, last task, bytes)
    lost: int = 0


def _filetime(value: int) -> datetime:
    return FILETIME_EPOCH + timedelta(microseconds=value // 10)


@dataclass(frozen=True)
class SpoolTable:
    """spool-table.bin as read: the session the files hold now (0 while none) and which
    segment each spool file holds."""
    session: int
    files: dict                         # segment number -> spool file number


def read_table(folder: Path) -> SpoolTable | None:
    """The spool table, or None without one (no format 2 writer has run here)."""
    try:
        data = (Path(folder) / TABLE).read_bytes()
    except OSError:
        return None
    if len(data) < TABLE_HEAD.size + ENTRY.size * MAX_FILES:
        return None
    magic, version, session, _files, *_ = TABLE_HEAD.unpack_from(data)
    if magic != TABLE_MAGIC or version != FORMAT:
        return None
    files = {}
    for number in range(MAX_FILES):
        segment, check = ENTRY.unpack_from(data, TABLE_HEAD.size + ENTRY.size * number)
        if segment and check == segment ^ 0xFFFFFFFF:     # 0 while reused; a torn entry fails the check
            files[segment] = number
    return SpoolTable(session, files)


def spool_file(folder: Path, number: int, suffix: str) -> Path:
    return Path(folder) / f"spool-{number:02d}.{suffix}"


def session_name(session: int) -> str:
    """A session's name (the stem renderer and the cutter tag its rows with it): its local
    start time, then the start's 100 ns ticks within that second."""
    started = _filetime(session).astimezone()
    return f"live-{started:%Y%m%d-%H%M%S}-{session % 10_000_000:07d}"


def open_spool(lab_root: Path | None = None) -> Spool | None:
    """The session the spool files hold now; None when there is none (or one is starting)."""
    folder = resolve_lab_root(lab_root) / "live"
    table = read_table(folder)
    if table is None or not table.session:
        return None
    try:
        spool = Spool(folder)
    except (OSError, KeyError, ValueError):
        return None                     # session.txt is being rewritten: the next look finds it
    return spool if spool.id == table.session else None


class Spool:
    """One live session as its segments stand now; refresh() reads only new index rows.
    `session` is the spool folder (where the session's files are), `id` the session and
    `name` its session_name."""

    def __init__(self, session: Path):
        self.session = Path(session)
        self.header = tap.session_header(self.session)
        self.id = int(self.header["session"])
        self.name = session_name(self.id)
        self.frequency = int(self.header.get("qpc_frequency") or 0) or 1
        self.rdram_size = int(self.header["rdram_size"])
        self._segments: dict[int, _Segment] = {}
        self.refresh()

    def _files(self) -> dict:
        """segment -> spool file for this session now; {} once another session took the files."""
        table = read_table(self.session)
        return table.files if table is not None and table.session == self.id else {}

    def holds(self, number: int, file: int) -> bool:
        """The spool table still names segment `number` in spool file `file` (checked after a read)."""
        return self._files().get(number) == file

    def refresh(self) -> None:
        """New index rows of every segment not yet closed. The table is read before and after
        the index files: a file whose turn came meanwhile is dropped, not misread."""
        files = self._files()
        for number in [n for n, segment in self._segments.items() if files.get(n) != segment.file]:
            del self._segments[number]
        reads = []
        for number, file in sorted(files.items()):
            segment = self._segments.setdefault(number, _Segment(number, file))
            if segment.closed:
                continue   # a closed segment's rows are all read
            try:
                with spool_file(self.session, file, "idx").open("rb") as handle:
                    handle.seek(segment.read)
                    reads.append((segment, handle.read()))
            except FileNotFoundError:
                self._segments.pop(number, None)
        after = self._files()
        for segment, data in reads:
            if after.get(segment.number) != segment.file:
                self._segments.pop(segment.number, None)
                continue
            whole = len(data) - len(data) % ROW.size
            for at in range(0, whole, ROW.size):
                verdict = self._row(segment, ROW.unpack_from(data, at))
                if verdict is None:          # not this session's or not this segment's rows
                    self._segments.pop(segment.number, None)
                    break
                if not verdict:
                    whole = at
                    break
            segment.read += whole

    def _row(self, segment: _Segment, row: tuple) -> bool | None:
        """Takes one index row: True, False past the last whole row, None when the file does not
        hold this session's segment (its first rows: SESSION for this session, OPEN for it)."""
        kind, a, b, c, d, e = row
        if not segment.verified:
            if kind == 0:
                return False
            segment.verified = kind == ROW_SESSION and e == self.id and b == FORMAT
            return True if segment.verified else None
        if kind == ROW_OPEN and a != segment.number:
            return None
        if kind == ROW_TASK:
            segment.tasks[a] = [b, c, d, e & ((1 << 48) - 1), e >> 48]
        elif kind == ROW_SHOWN and a in segment.tasks:
            segment.tasks[a][4] |= TASK_SHOWN
        elif kind == ROW_OPEN:
            segment.opened = (d, e)
        elif kind == ROW_HOLE:
            segment.holes.append([a, b])
            segment.lost = max(segment.lost, c)
        elif kind == ROW_BEAT:
            segment.beat = (a, b, c, d, e)
        elif kind == ROW_CLOSE:
            segment.closed = (a, b, e)
        else:
            return False   # zeros past the last whole row: not written yet
        segment.newest_qpc = max(segment.newest_qpc, d)
        return True

    def _utc(self, segment: _Segment, qpc: int) -> datetime:
        base_qpc, base = segment.opened or (qpc, 0)
        return _filetime(base) + timedelta(seconds=(qpc - base_qpc) / self.frequency)

    def tasks(self, since_utc: datetime | None = None) -> list[TaskRef]:
        """Flushed tasks only, oldest first (a segment older than since_utc is not visited)."""
        refs = []
        for number in sorted(self._segments):
            segment = self._segments[number]
            if since_utc is not None and self._utc(segment, segment.newest_qpc) < since_utc:
                continue
            for index, (timer, place, qpc, _offset, flags) in segment.tasks.items():
                utc = self._utc(segment, qpc)
                if since_utc is None or utc >= since_utc:
                    refs.append(TaskRef(index, timer, place & 0xFFFF, place >> 16, utc, bool(flags & TASK_SHOWN),
                                        bool(flags & TASK_REFUSED)))
        return sorted(refs, key=lambda ref: ref.index)

    def source(self, first: int, last: int, *, states: bool = True) -> package.Source:
        """Tasks first..last inclusive as a package.Source (tap_source's type), decoded from the
        first byte of the segment holding `first`; nothing earlier is read."""
        self.refresh()
        numbers = sorted(self._segments)
        start = next((n for n in numbers if first in self._segments[n].tasks), None)
        if start is None:
            raise LookupError(f"task {first} is not in the spool {self.session.name}")
        chain = [n for n in numbers if n >= start]
        source = self._new_source(LiveSource, states)
        source.captures = captures(source, self._records(chain, last), self.frequency, first=first, last=last)
        return source

    def follow(self, first: int, *, states: bool = True) -> FollowSource:
        """Tasks first, first+1, ... as the writer flushes them, decoded once from the start of
        the segment holding `first` and on across segment starts with the scene state continuous
        (source()'s pairing; a task waits for its present or the next task). At the live edge the
        captures wait, re-reading only the open segment's new bytes and holding no file open, so
        the writer still prunes. They end after source.stop_at(n)'s task n, and end early,
        without raising, with source.ended saying why: the next task is missing, in a hole or
        refused, capture stopped, or the follower fell behind the spool's window. A follower
        reads through its own Spool, so this one stays free for any thread."""
        source = self._new_source(FollowSource, states)
        source.captures = Spool(self.session)._follow(source, first)
        return source

    def _new_source(self, kind, states: bool):
        source = kind("live-spool", self.rdram_size, self.header.get("rom_header", ""),
                      tap.identity_spec(self.session), self.header.get("wrapper_build"))
        source.states = package.TapStates() if states and package.has_state(self.session) else None
        if source.states is not None:
            source.states.full = self._boot()
        return source

    def _follow(self, source: FollowSource, first: int):
        origin = None
        records = self._follow_records(source, first)
        for task, present in tap.paired(source.states.watch(records) if source.states else records, "next"):
            index = task.fields["task_index"]
            if index < first:
                if source.states is not None:
                    source.states.task.pop(task.sequence, None)
                continue
            expected = first if source.last is None else source.last + 1
            if source.stop is not None and index > source.stop:
                return
            if any(low <= expected <= high for segment in self._segments.values() for low, high in segment.holes):
                source.ended = f"Task {expected} lost records when the capture ring was full."
                return
            if index != expected:
                source.ended = f"Task {expected} is not in the spool."
                return
            if task.fields["refusals"]:
                source.ended = f"Task {index}'s display list could not be read whole."
                return
            if origin is None:
                origin = source.origin_qpc = task.qpc
            yield _capture(task, present, origin, self.frequency)
            source.last = index
            if source.stop is not None and index >= source.stop:
                return

    def _follow_records(self, source: FollowSource, first: int):
        """The records from the start of the segment holding `first` on, waiting at the live
        edge; it returns (source.ended set unless stopped) when following cannot go on."""
        number, position = self._first_segment(source, first), 0
        while number is not None:
            if (yield from self._segment_records(source, first, number, position)):
                return
            number, position = self._next_segment(source, first, number + 1), position + 1

    @staticmethod
    def _stopping(source: FollowSource, first: int) -> bool:
        return source.stop is not None and source.stop < (first if source.last is None else source.last + 1)

    def _first_segment(self, source: FollowSource, first: int) -> int | None:
        """The segment holding `first`, waiting while it is still to come."""
        while not self._stopping(source, first):
            self.refresh()
            number = next((n for n in sorted(self._segments) if first in self._segments[n].tasks), None)
            if number is not None:
                return number
            listed = [index for segment in self._segments.values() for index in segment.tasks]
            if listed and first <= max(listed):
                source.ended = (f"Task {first} already left the spool's window." if first < min(listed)
                                else f"Task {first} is not in the spool.")
                return None
            recording, reason = self._state()
            if not recording:
                source.ended = reason
                return None
            time.sleep(FOLLOW_POLL)
        return None

    def _segment_records(self, source: FollowSource, first: int, number: int, position: int):
        """One segment's records as it grows (a later segment's keys skipped: the state
        continues); returns True when following ends, False once a rollover closed it."""
        held = self._segments.get(number)
        file = held.file if held is not None else -1
        reader, path = tap.SegmentReader(self.rdram_size), spool_file(self.session, file, "tap")
        while not self._stopping(source, first):
            data = _read_from(path, reader.read)
            self.refresh()                             # after the bytes: a hole they reveal is listed now
            segment = self._segments.get(number)
            if segment is not None and segment.file != file:
                segment = None                         # its file's turn came: the bytes read are another's
            if data is None or segment is None:
                source.ended = (f"Segment {number} left the spool before it was read: the scene "
                                "fell behind the spool's window.")
                return True
            for _offset, flags, record in reader.feed(data):
                if not (position and flags & tap.WRITER_KEY):
                    yield record
            if segment.closed and reader.read >= segment.closed[2]:
                if reader.pending or segment.closed[0] != CLOSE_ROLLOVER:
                    source.ended = (f"Segment {number} ends inside a record." if reader.pending
                                    else REASONS.get(segment.closed[0], "Capture stopped."))
                    return True
                return False
            if not data:
                recording, reason = self._state()
                if not recording and not segment.closed:
                    source.ended = reason
                    return True
                time.sleep(FOLLOW_POLL)
        return True

    def _next_segment(self, source: FollowSource, first: int, number: int) -> int | None:
        """The segment after a rollover: the writer opens it right after the close."""
        while number not in self._segments:
            if self._stopping(source, first):
                return None
            if any(n > number for n in self._segments) or not self._state()[0]:
                source.ended = self._state()[1] or f"Segment {number} is not in the spool."
                return None
            time.sleep(FOLLOW_POLL)
            self.refresh()
        return number

    def _records(self, chain: list[int], last: int):
        """The chain's records in order, the keys of every segment after the first skipped
        (the state continues), stopping before the first task past `last`."""
        for position, number in enumerate(chain):
            file = self._segments[number].file
            try:
                data = spool_file(self.session, file, "tap").read_bytes()
            except FileNotFoundError:
                data = None
            if data is None or not self.holds(number, file):
                raise LookupError(f"segment {number} left the spool while it was read")
            for _offset, flags, record in tap.read_segment(data, self.rdram_size):
                if position and flags & tap.WRITER_KEY:
                    continue
                if isinstance(record, tap.Task) and record.fields["task_index"] > last:
                    return
                yield record

    def _boot(self) -> bytes | None:
        """RDRAM-sized bytes with the boot copy in place (package.code_load reads only
        from the expansion base, which the boot span starts at), or None without one."""
        try:   # read whole, never mapped: the writer empties this file in place at a new session
            data = (self.session / "boot.bin").read_bytes()
        except OSError:
            return None
        for at, kind, size, sequence, qpc, _flags in tap._walk(data, torn_ok=True):
            record = tap._parse_record(kind, sequence, qpc, memoryview(data)[at + 32:at + size])
            if isinstance(record, tap.Boot) and record.begin + len(record.data) <= self.rdram_size:
                table = read_table(self.session)
                if table is None or table.session != self.id:
                    return None             # a newer session's copy, or its emptied file
                return bytes(record.begin) + record.data + bytes(self.rdram_size - record.begin - len(record.data))
        return None

    def health(self) -> dict:
        self.refresh()
        totals = {"lost": 0, "refused": 0}
        holes, newest = [], None
        for number in sorted(self._segments):
            segment = self._segments[number]
            if segment.beat:
                totals["lost"], totals["refused"] = max(totals["lost"], segment.beat[0]), segment.beat[1]
            totals["lost"] = max(totals["lost"], segment.lost)
            holes += segment.holes
            if segment.tasks:
                index = max(segment.tasks)
                newest = self._utc(segment, segment.tasks[index][2])
        recording, reason = self._state()
        merged: list[list[int]] = []
        for first, last in sorted(holes):
            if merged and first <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], last)
            else:
                merged.append([first, last])
        return {"recording": recording, "reason": reason, "lost": totals["lost"], "refused": totals["refused"],
                "newest_utc": newest.isoformat() if newest else None, "holes": merged}

    def _state(self) -> tuple[bool, str | None]:
        ended = _summary(self.session)
        if ended.get("session") == str(self.id):        # the session's last write names it
            return False, REASONS.get(END_REASONS.get(ended.get("end_reason", ""), 0),
                                      "This capture session ended.")
        table = read_table(self.session)
        if table is None or table.session != self.id:
            return False, "A newer scene recording session took over the spool."
        if not self._segments:
            return False, "The spool holds no capture yet."
        segment = self._segments[max(self._segments)]
        if segment.closed and segment.closed[0] != CLOSE_ROLLOVER:
            return False, REASONS.get(segment.closed[0], "Capture stopped.")
        age = (datetime.now(UTC) - self._utc(segment, segment.newest_qpc)).total_seconds()
        if age > STALE_SECONDS:
            return False, "Project64 stopped writing the spool (it closed, froze or crashed)."
        return True, None


def _summary(folder: Path) -> dict:
    """summary.txt as key=value strings ({} without one); its session= says whose it is."""
    try:
        text = (Path(folder) / "summary.txt").read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return {}
    return dict(line.partition("=")[::2] for line in text.splitlines())


# Format 1's names: a folder per session under live/ (and the stem renderer's under stems/),
# holding only these. clear_old_layout removes such a folder whole, never anything else.
OLD_SPOOL_FILES = re.compile(r"seg-\d{6}\.(tap|idx)|boot\.bin|session\.txt|armed\.txt|summary\.txt|writer\.lock")
OLD_STEM_FILES = re.compile(r"vi-\d{8}-\d{20}\.stm")
OLD_LAYOUT_LIMIT = 64                   # folders looked at per call: the bound on one migration
OLD_LAYOUT_LOG = "old-layout-removed.txt"


def clear_old_layout(lab_root: Path | None = None) -> dict:
    """Moves format 1 out of the lab, once: each live/live-* and stems/live-* folder that holds
    only format 1's files is removed, at most OLD_LAYOUT_LIMIT folders a call; a folder whose
    writer.lock an older wrapper still holds open, or with anything else in it, stays. What it
    removed is logged and appended to live/old-layout-removed.txt. Format 2 creates no such
    folder, so once they are gone every later call only looks and finds none."""
    root = resolve_lab_root(lab_root)
    removed = {"folders": 0, "files": 0, "bytes": 0, "kept": []}
    candidates = [(folder, OLD_SPOOL_FILES) for folder in sorted((root / "live").glob("live-*"))]
    candidates += [(folder, OLD_STEM_FILES) for folder in sorted((root / "stems").glob("live-*"))]
    for folder, names in candidates[:OLD_LAYOUT_LIMIT]:
        try:
            files = list(folder.iterdir()) if folder.is_dir() else None
        except OSError:
            files = None
        if files is None or any(not path.is_file() or not names.fullmatch(path.name) for path in files):
            removed["kept"].append(folder.name)
            continue
        sizes = {}
        for path in files:
            try:
                sizes[path] = path.stat().st_size
            except OSError:
                continue
        try:
            (folder / "writer.lock").unlink(missing_ok=True)   # held open by a wrapper still writing it
        except OSError:
            removed["kept"].append(folder.name)
            continue
        for path, size in sizes.items():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            removed["files"] += 1
            removed["bytes"] += size
        try:
            folder.rmdir()
            removed["folders"] += 1
        except OSError:
            removed["kept"].append(folder.name)
    if removed["folders"] or removed["files"]:
        line = (f"{datetime.now(UTC).isoformat(timespec='seconds')} removed {removed['folders']} format 1 "
                f"folders, {removed['files']} files, {removed['bytes']} bytes; kept {removed['kept']}")
        log.info("The scene recording moved to fixed spool files: %s", line)
        try:
            (root / "live").mkdir(parents=True, exist_ok=True)
            with (root / "live" / OLD_LAYOUT_LOG).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass
    return removed


def _read_from(path: Path, offset: int) -> bytes | None:
    """The file's bytes from `offset` (opened and closed again, so the writer can reuse it), or None."""
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            return handle.read()
    except FileNotFoundError:
        return None


def captures(source: package.Source, records, frequency: int, *, first: int = 0, last: int | None = None,
             pair: str = "next"):
    """A tap record stream's captures, the one loop package.tap_source's captures repeat (a
    tap_source that called it with tap.read_records would share it; package.py stays
    m4-cut's): records through source.states when kept, tap.paired, and every task
    first..last packaged except a refused one (counted in source.refused). Tasks before
    `first` still pass through pairing and the scene states, never out. Times count from the
    first task reached, refused or not; source.origin_qpc names it."""
    origin = None
    stream = source.states.watch(records) if source.states else records
    for task, present in tap.paired(stream, pair):
        index = task.fields["task_index"]
        if index < first:
            if source.states is not None:
                source.states.task.pop(task.sequence, None)   # its level records still count
            continue
        if last is not None and index > last:
            return
        if origin is None:
            origin = source.origin_qpc = task.qpc
        if task.fields["refusals"]:
            source.refused += 1
            continue
        yield _capture(task, present, origin, frequency)


def _capture(task: tap.Task, present: tap.Present | None, origin: int, frequency: int) -> package.Capture:
    """package.tap_source's Capture for one paired task, its times from `origin`'s qpc."""
    return package.Capture(
        task.sequence, tuple(task.task), [tuple(run) for run in task.runs], task.run_bytes,
        tuple(task.fields[name] for name in tap.TASK_FIELDS),
        [(address, data) for address, data in task.extras],
        tuple(present.vi[name] for name in tap.VI_NAMES) if present else None,
        present is not None, present.sequence if present else None,
        (task.qpc - origin) * 1_000_000 // frequency,
        (present.qpc - origin) * 1_000_000 // frequency if present else -1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "lease"):
        p = sub.add_parser(name)
        p.add_argument("--lab-root", type=Path, default=None)
        if name == "lease":
            p.add_argument("--seconds", type=float, default=60)
    args = parser.parse_args()
    if args.command == "lease":
        lease = Lease(args.lab_root)
        lease.start()
        try:
            time.sleep(args.seconds)
        finally:
            lease.stop()
        return 0
    spool = open_spool(args.lab_root)
    if spool is None:
        print(json.dumps({"session": None}))
        return 0
    refs = spool.tasks()
    print(json.dumps({"session": spool.session.name, "health": spool.health(), "tasks": len(refs),
                      "first": refs[0].index if refs else None, "last": refs[-1].index if refs else None},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
