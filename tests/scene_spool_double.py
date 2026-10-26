"""A test double with live_spool's interface A, over a recorded scene-tap session (tests only).

    spool = RecordedSpool(session, through=2300)       # tasks 0..2300 flushed
    spool = RecordedSpool(session, pace=1.0, start=1940, started_at=time.time())   # the edge moves in real time
    spool.tasks(since_utc), spool.source(first, last), spool.follow(first), spool.health()
    open_recorded(lab_root)                            # attempt_cutter.py --spool-factory: lab_root/spool-double.json

The real reader is m4-spool's tools/scene_lab/live_spool.py; the server swaps it in. This one reads
a tap session folder (tap.py's format) and yields package.Source objects the way package.tap_source
does, starting at the newest level key at or before the first task asked for (what a spool
segment's key gives the real reader), so no capture depends on anything read before it. A task's
index is its ordinal in the session; its utc is the session folder's local start time plus its QPC
offset. `holes` drops task ranges as if the recording had lost them.
"""
from __future__ import annotations

import bisect
import itertools
import json
import mmap
import struct
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import package  # noqa: E402
import tap  # noqa: E402

CONFIG = "spool-double.json"
KEY_EVERY = 450                # a writer-side key every 15 s of tasks, as m4-spool's segments start


@dataclass(frozen=True)
class TaskRef:
    index: int
    global_timer: int
    level: int
    area: int
    utc: datetime
    has_present: bool


def session_start(session: Path) -> datetime:
    """tap-YYYYmmdd-HHMMSS, the arming machine's local time, as UTC (a fixed origin otherwise)."""
    try:
        return datetime.strptime(session.name.removeprefix("tap-")[:15], "%Y%m%d-%H%M%S").astimezone(timezone.utc)
    except ValueError:
        return datetime(2026, 10, 26, tzinfo=timezone.utc)


def scan(session: Path) -> tuple[list[TaskRef], list[int], list[tuple], int | None]:
    """(task refs, each task's record offset, keys, the full RDRAM copy's offset). A key is
    (offset to read from, the task index it precedes, a synthetic level_key record or None): the
    tap's own level keys, and every KEY_EVERY tasks the level regions as they stood before that
    task, as a spool segment's writer-side key carries them."""
    identity = {name: (address, size) for name, address, size in tap.identity_spec(session)}
    header = tap.session_header(session)
    frequency = int(header.get("qpc_frequency") or 1)
    origin = session_start(session)
    rows, offsets, keys, full = [], [], [], None
    first_qpc = None
    level = np.zeros(int(header["rdram_size"]), np.uint8)
    level_runs: list = []
    segments = (0,) * 16
    with (session / "tap.bin").open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        at = 0
        while at + 32 <= len(data):
            _magic, kind, size, sequence, qpc = struct.unpack_from("<IIIIq", data, at)
            body = at + 32
            if kind == tap.REC_TASK and rows and len(rows) % KEY_EVERY == 0 and level_runs:
                spans = [(begin, end - begin) for begin, end in level_runs]
                keys.append((at, len(rows), tap.Regions(
                    sequence, qpc, {"task_index": 0, "kind": 1, "count": len(spans)}, "level_key", segments, spans,
                    b"".join(level[begin:end].tobytes() for begin, end in level_runs))))
            if kind == tap.REC_TASK:
                fields = dict(zip(tap.TASK_FIELDS, struct.unpack_from("<12I", data, body), strict=True))
                offset = body + 112 + 8 * fields["runs"]
                spec = [struct.unpack_from("<II", data, offset + 8 * i) for i in range(fields["extras"])]
                offset += 8 * fields["extras"] + fields["closure_bytes"]
                extras = []
                for address, length in spec:
                    padded = (length + 3) & ~3
                    extras.append((address & ~3, bytes(data[offset:offset + padded])))
                    offset += padded

                def value(name, extras=extras):
                    address, size = identity.get(name, (0, 0))
                    return tap.word_value(extras, address, size) if size else 0

                first_qpc = qpc if first_qpc is None else first_qpc
                utc = origin + timedelta(microseconds=(qpc - first_qpc) * 1_000_000 // frequency)
                rows.append([len(rows), value("global_timer") or 0, value("curr_level") or 0,
                             value("curr_area") or 0, utc, False])
                offsets.append(at)
            elif kind == tap.REC_PRESENT and rows:
                rows[-1][5] = True
            elif kind == tap.REC_REGIONS and tap.REGION_KINDS[struct.unpack_from("<8I", data, body)[1]] != "task":
                with memoryview(data)[body:at + size] as view:
                    record = tap._parse_record(kind, sequence, qpc, view)
                if record.kind == "level_key":
                    keys.append((at, len(rows), None))
                runs = [(begin, begin + length) for begin, length in record.spans]
                level_runs = package.merge_runs(runs if record.kind == "level_key" else level_runs + runs)
                cursor = 0
                for begin, length in record.spans:
                    level[begin:begin + length] = np.frombuffer(record.data, np.uint8, length, cursor)
                    cursor += length
                segments = record.segments if any(record.segments) else segments
            elif kind == tap.REC_FULL and full is None:
                full = at
            at += size
    return [TaskRef(*row) for row in rows], offsets, keys, full


def _records_from(path: Path, starts: list[int], limit: int | None = None):
    """The records from the first offset in `starts` on (at most `limit`)."""
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        at, count = starts[0], 0
        while at + 32 <= len(data) and (limit is None or count < limit):
            _magic, kind, size, sequence, qpc = struct.unpack_from("<IIIIq", data, at)
            with memoryview(data)[at + 32:at + size] as body:
                record = tap._parse_record(kind, sequence, qpc, body)
            if record is not None:
                yield record
                count += 1
            at += size


class FollowSource(package.Source):
    """package.Source whose captures stop once the index stop_at named was yielded."""
    stop: int | None = None

    def stop_at(self, last: int) -> None:
        self.stop = last


class RecordedSpool:
    def __init__(self, session: Path, *, through: int | None = None, pace: float | None = None,
                 start: int = 0, started_at: float | None = None, recording: bool = True,
                 holes: list | None = None, reason: str | None = None):
        self.session = Path(session)
        self.refs, self.offsets, self.keys, self.full = scan(self.session)
        self.through, self.pace, self.start = through, pace, start
        self.started_at = time.time() if started_at is None else started_at
        self.recording, self.reason = recording, reason
        self.holes = [tuple(hole) for hole in holes or ()]
        self.lock = threading.Lock()

    def edge(self) -> int:
        """The newest flushed task index."""
        if self.pace is None:
            return len(self.refs) - 1 if self.through is None else min(self.through, len(self.refs) - 1)
        origin = self.refs[self.start].utc
        elapsed = (time.time() - self.started_at) * self.pace
        with self.lock:
            newest = max(self.start, getattr(self, "_newest", self.start))
            while newest + 1 < len(self.refs) and (self.refs[newest + 1].utc - origin).total_seconds() <= elapsed:
                newest += 1
            self._newest = newest
        return newest

    def _lost(self, index: int) -> bool:
        return any(a <= index <= b for a, b in self.holes)

    def refresh(self) -> None:
        pass

    def tasks(self, since_utc: datetime | None = None) -> list[TaskRef]:
        edge = self.edge()
        return [ref for ref in self.refs[:edge + 1]
                if not self._lost(ref.index) and (since_utc is None or ref.utc >= since_utc)]

    def health(self) -> dict:
        edge = self.edge()
        ended = edge == len(self.refs) - 1 and self.pace is not None
        return {"recording": self.recording and not ended, "reason": self.reason if self.recording and not ended
                else (self.reason or "Project64 closed the ROM."), "lost": 0, "refused": 0,
                "newest_utc": self.refs[edge].utc.isoformat(), "holes": [list(hole) for hole in self.holes]}

    def source(self, first: int, last: int, *, states: bool = True) -> package.Source:
        if last > self.edge() or any(a <= last and first <= b for a, b in self.holes):
            raise LookupError("those tasks are not in the spool")
        source = self.follow(first, states=states)
        source.stop_at(last)
        return source

    def follow(self, first: int, *, states: bool = True) -> FollowSource:
        header = tap.session_header(self.session)
        source = FollowSource("scene-tap", int(header["rdram_size"]), header.get("rom_header", ""),
                              tap.identity_spec(self.session), header.get("wrapper_build"))
        source.states = package.TapStates() if states and package.has_state(self.session) else None
        key, _before, synthetic = max(((at, before, record) for at, before, record in self.keys if before <= first),
                                      key=lambda item: item[0], default=(self.offsets[0], 0, None))
        head = list(_records_from(self.session / "tap.bin", [self.full], 1)) if self.full is not None \
            and self.full < key else []
        head += [synthetic] if synthetic is not None else []
        frequency = int(header.get("qpc_frequency") or 0) or 1

        def captures():
            records = itertools.chain(head, _records_from(self.session / "tap.bin", [key]))
            index, origin = bisect.bisect_left(self.offsets, key) - 1, None
            pairs = tap.paired(source.states.watch(records) if source.states else records, "next")
            for task, present in pairs:
                index += 1
                if index < first:
                    if source.states:
                        source.states.task.pop(task.sequence, None)
                    continue
                while index > self.edge() and (source.stop is None or index <= source.stop):
                    time.sleep(0.005)                          # the live edge: wait for the flush
                if (source.stop is not None and index > source.stop) or self._lost(index):
                    return
                origin = task.qpc if origin is None else origin
                if task.fields["refusals"]:
                    source.refused += 1
                    continue
                yield package.Capture(
                    task.sequence, tuple(task.task), [tuple(run) for run in task.runs], task.run_bytes,
                    tuple(task.fields[name] for name in tap.TASK_FIELDS),
                    [(address, data) for address, data in task.extras],
                    tuple(present.vi[name] for name in tap.VI_NAMES) if present else None,
                    present is not None, present.sequence if present else None,
                    (task.qpc - origin) * 1_000_000 // frequency,
                    (present.qpc - origin) * 1_000_000 // frequency if present else -1)
                if source.stop is not None and index >= source.stop:
                    return

        source.captures = captures()
        return source


def open_recorded(lab_root: Path | None) -> RecordedSpool | None:
    """attempt_cutter.py's --spool-factory for tests: the double lab_root/spool-double.json describes."""
    path = Path(lab_root) / CONFIG
    if not path.is_file():
        return None
    config = json.loads(path.read_text(encoding="utf-8"))
    session = Path(config.pop("session"))
    return RecordedSpool(session, **config)
