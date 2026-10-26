"""The live spool: the scene tap recording every graphics task while the Trainer's lab runs.

    live_spool.py status [--lab-root DIR]          the newest session's health and task span
    live_spool.py lease [--seconds N] [--lab-root DIR]   hold a lease (a manual check)

Intent: open the scene of an attempt right after playing it. The Trainer holds a Lease
(lease.txt beside the sessions, renewed every 2 s); the wrapper's scene tap
(plugin/gfxwrap/scene_tap.cpp) checks it at most once a second, records continuously
while it is fresh and stops once it is stale (8 s without renewal) or gone. Its writer
(scene_spool.cpp) keeps rolling segments under <lab>/live/<session>/: each one starts
with keys from the writer's own shadows, so Spool.source() decodes any recent span from
that segment's first byte, and an append-only index names every task, its global_timer,
level, area and time. A reader sees a task once the next one arrives or 100 ms later.
Spool.follow() keeps a scene's captures coming while he plays (a cutter writes the
package as the attempt goes and finishes at its end); Spool.source() cuts a finished span.
The lease asks for tap.py arm's identity and scene-state lines, never pictures, full
copies or the per-VI audio spans unless its extra lines name them
(tap.audio_state_lines).

Evidence (2026-10-26, this machine). Disk: his BoB session (tap-20261025-184022, 90 s,
29 tasks/s) through the real writer (tap_offline_host --spool-bench), 15 s segments,
MB = 10^6 bytes, writer = its busy time on one thread:
    raw records          675 MB/min   writer 0.3% of a core
    256-byte dedupe      139 MB/min   0.2%
    XPRESS alone         247 MB/min   2.0%
    dedupe + XPRESS       42 MB/min   0.45%   (the default; keys included)
plus the index (0.06 MB/min) and boot.bin (4 MiB once per session). The default bound is
15 minutes or 2 GiB (2,147,483,648 bytes) of segments, whichever is less, plus the open
segment (at most 15 s); at 42 MB/min the window holds about 630 MB. Producer, savestates
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

Limits:
- Lab only. The lease file lives under %LOCALAPPDATA% of the Project64 process; a
  lab_root elsewhere is seen only by a test host that sets LOCALAPPDATA to its parent.
- One Trainer per machine: Lease.stop() deletes the file whoever else holds it.
- Only Project64.exe takes the lease (a test host must opt in).
- A full ring still drops records: health()["holes"] names the tasks left incomplete,
  and a consumer must not cut a scene across one.
- A segment's first task carries a whole key (1.4 MB of level state before XPRESS), so
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
import os
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

ROW = struct.Struct("<IIIIqQ")          # kind, a, b, c, d, e (scene_spool.cpp's header comment)
ROW_OPEN, ROW_TASK, ROW_SHOWN, ROW_HOLE, ROW_BEAT, ROW_CLOSE = 1, 2, 3, 4, 5, 6
TASK_SHOWN, TASK_REFUSED = 1, 2
CLOSE_ROLLOVER = 1
REASONS = {2: "The Trainer ended the lease.",
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


def resolve_lab_root(override: Path | None = None) -> Path:
    return Path(override) if override is not None else tap.lab_root()


def lease_text(version: str = "us", extra_lines: Sequence[str] = (), *, window_seconds: float = 900,
               max_bytes: int = 2 << 30, segment_seconds: float = 15) -> str:
    """The lease's lines: tap.py arm's identity and scene-state lines (never pictures or
    audio), the live settings, the boot span, then the caller's extra lines (which win)."""
    arming = tap.arming_text(SimpleNamespace(version=version, seconds=600, no_reference=True, reference_every=1,
                                             full_every=0, no_scene_state=False, no_audio_state=True))
    live = [f"lease_ms={LEASE_MS}", f"window_ms={int(window_seconds * 1000)}", f"max_bytes={int(max_bytes)}",
            f"segment_ms={int(segment_seconds * 1000)}",
            f"boot_region={provenance.EXPANSION_RAM:x}:{(8 << 20) - provenance.EXPANSION_RAM}"]
    return "# scene-lab live lease (tools/scene_lab/live_spool.py)\n" + arming + "\n".join([*live, *extra_lines]) + "\n"


class Lease:
    """The Trainer's side of continuous capture: lease.txt, renewed while it runs."""

    def __init__(self, lab_root: Path | None = None, *, extra_lines: Sequence[str] = (), version: str = "us",
                 window_seconds: float = 900, max_bytes: int = 2 << 30, segment_seconds: float = 15) -> None:
        self.path = resolve_lab_root(lab_root) / "live" / "lease.txt"
        self.text = lease_text(version, extra_lines, window_seconds=window_seconds, max_bytes=max_bytes,
                               segment_seconds=segment_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Arm with these lines (the wrapper notices within a second) and keep renewing."""
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
    read: int = 0                                       # index bytes consumed
    opened: tuple[int, int] | None = None               # (qpc, FILETIME)
    tasks: dict = field(default_factory=dict)           # index -> [timer, place, qpc, offset, flags]
    holes: list = field(default_factory=list)
    beat: tuple | None = None                           # (lost, refused, tasks, qpc, FILETIME)
    newest_qpc: int = 0
    closed: tuple | None = None                         # (reason, last task, bytes)
    lost: int = 0


def _filetime(value: int) -> datetime:
    return FILETIME_EPOCH + timedelta(microseconds=value // 10)


def _sessions(root: Path) -> list[Path]:
    def order(path: Path):
        parts = path.name.split("-")
        return (parts[1:3], int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1)
    live = root / "live"
    found = [path for path in live.glob("live-*") if (path / "session.txt").is_file()] if live.is_dir() else []
    return sorted(found, key=order)


def open_spool(lab_root: Path | None = None, *, session: str | Path | None = None) -> Spool | None:
    """The newest live session, or the named one (a Spool.session folder or its name); None
    when there is none."""
    root = resolve_lab_root(lab_root)
    if session is not None:
        folder = Path(session) if Path(session).is_absolute() else root / "live" / session
        return Spool(folder) if (folder / "session.txt").is_file() else None
    sessions = _sessions(root)
    return Spool(sessions[-1]) if sessions else None


class Spool:
    """One live session as its segments stand now; refresh() reads only new index rows."""

    def __init__(self, session: Path):
        self.session = Path(session)
        self.header = tap.session_header(self.session)
        self.frequency = int(self.header.get("qpc_frequency") or 0) or 1
        self.rdram_size = int(self.header["rdram_size"])
        self._segments: dict[int, _Segment] = {}
        self.refresh()

    def refresh(self) -> None:
        present = {}
        for path in self.session.glob("seg-*.idx"):
            try:
                present[int(path.stem.removeprefix("seg-"))] = path
            except ValueError:
                continue
        for number in [number for number in self._segments if number not in present]:
            del self._segments[number]
        for number, path in sorted(present.items()):
            segment = self._segments.setdefault(number, _Segment(number))
            if segment.closed:
                continue   # a closed segment's rows are all read
            try:
                with path.open("rb") as handle:
                    handle.seek(segment.read)
                    data = handle.read()
            except FileNotFoundError:
                self._segments.pop(number, None)
                continue
            whole = len(data) - len(data) % ROW.size
            for at in range(0, whole, ROW.size):
                if not self._row(segment, ROW.unpack_from(data, at)):
                    whole = at
                    break
            segment.read += whole

    @staticmethod
    def _row(segment: _Segment, row: tuple) -> bool:
        kind, a, b, c, d, e = row
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
        reader, path = tap.SegmentReader(self.rdram_size), self.session / f"seg-{number:06d}.tap"
        while not self._stopping(source, first):
            data = _read_from(path, reader.read)
            self.refresh()                             # after the bytes: a hole they reveal is listed now
            segment = self._segments.get(number)
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
            path = self.session / f"seg-{number:06d}.tap"
            try:
                data = path.read_bytes()
            except FileNotFoundError as error:
                raise LookupError(f"segment {number} left the spool while it was read") from error
            for _offset, flags, record in tap.read_segment(data, self.rdram_size):
                if position and flags & tap.WRITER_KEY:
                    continue
                if isinstance(record, tap.Task) and record.fields["task_index"] > last:
                    return
                yield record

    def _boot(self) -> bytes | None:
        """RDRAM-sized bytes with the boot copy in place (package.code_load reads only
        from the expansion base, which the boot span starts at), or None without one."""
        path = self.session / "boot.bin"
        if not path.is_file():
            return None
        for record in tap.read_records(path):
            if isinstance(record, tap.Boot) and record.begin + len(record.data) <= self.rdram_size:
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
        summary = self.session / "summary.txt"
        if summary.is_file():
            ended = dict(line.partition("=")[::2] for line in summary.read_text(encoding="ascii").splitlines())
            return False, REASONS.get(END_REASONS.get(ended.get("end_reason", ""), 0),
                                      "This capture session ended.")
        if not self._segments:
            return False, "The spool holds no capture yet."
        segment = self._segments[max(self._segments)]
        if segment.closed and segment.closed[0] != CLOSE_ROLLOVER:
            return False, REASONS.get(segment.closed[0], "Capture stopped.")
        age = (datetime.now(UTC) - self._utc(segment, segment.newest_qpc)).total_seconds()
        if age > STALE_SECONDS:
            return False, "Project64 stopped writing the spool (it closed, froze or crashed)."
        return True, None


def _read_from(path: Path, offset: int) -> bytes | None:
    """The file's bytes from `offset` (opened and closed again: the writer may delete it), or None."""
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
