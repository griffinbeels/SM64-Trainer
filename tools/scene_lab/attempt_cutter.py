"""The attempt-scene cutter (M4 B4, lab only): one hidden child process that writes each attempt's
scene package while he plays.

    cutter = CutterProcess(rom, folder, on_event)        # the server's handle; the child starts now
    cutter.send({"op": "start", "kind": "anchor", "frame": 1234, "utc": "2026-10-26T20:16:05+00:00"})
    cutter.send({"op": "complete", "attempt": AttemptMark.of(attempt).as_json()})
    cutter.close()                                       # EOF: the child abandons what it writes and exits

The child (serve) follows the scene recording (live_spool.open_spool: the spool the server's
live_spool.Lease keeps the wrapper's tap writing) and writes one JSON line per change: {"ready":
true} once it can cut, {"live": {"recording", "reason"}} when the recording's state changes, and per
attempt {"id", "state": "cutting" | "ready" | "unavailable", ...}. Notes the server sends: "start"
(an anchor, practice_reset or state_loaded, or a segment_armed notice: where an attempt may begin),
"stop" (a segment_disarmed notice), "complete" (an attempt finished: its AttemptMark) and "forget"
(the server no longer keeps that attempt).

Why while he plays. A package written after the attempt ends is ready seconds to minutes late:
measured 2026-10-26 on a 20 s span (600 tasks) of his BoB minute (tap-20261025-184022), one
write_package took 27.4 s with lzma (2.9 MB), 12.2 s with zlib (4.0 MB) and 7.6 s stored
(120.5 MB), ~1 s of each the ROM's MIO0 decode, which this child does once at start. So each
start the event stream announces begins a take: a package.PackageWriter fed by spool.follow from
that start's task as the recording grows, with zlib (16 to 19 ms a frame against the game's 33;
lzma's ~45 would fall behind). Measured on that minute replayed at the game's pace through
tests/scene_spool_double.py: ready 0.11 s after the fade-out's last picture reached the
recording, 0.26 s after a reset's, and the server's 250 Hz poll loop ticked as without a cut
(p99 4.4 ms, max 5.2 ms in three runs; one run had a single 43 ms gap). When an
attempt completes, attempt_span decides its scene, the take that began at its first task is told
to stop at the last, and finish() writes only the frames still pending and the index. An attempt
with no take (it began before the recording, at a start the stream did not announce, or shares
its start with an attempt that claimed the take) gets a take that starts behind the live edge and
stays "cutting" for as long as that takes. A take nobody claims is abandoned: an anchor's take
GRACE_SECONDS after a newer anchor's take began, a segment's take at its disarm, any take past
attempt_span.MAX_SCENE_FRAMES; at most MAX_TAKES unclaimed takes write at once.

Why a process: the server's live poll loop shares Python's lock with every thread of its process
(a thread offload still shares the GIL), and a take is CPU work for as long as he plays. The child
runs below normal priority, as the replay's background shrink does, so the emulator and the
Trainer win any contention; on a loaded machine it falls behind and catches up, which shows as a
longer "cutting".
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import attempt_span  # noqa: E402

PACKAGE_SUFFIX = ".sm64scene"
TICK_SECONDS = 0.1             # how often the child re-reads the recording while anything waits
IDLE_TICK_SECONDS = 0.5        # and when nothing does (the recording's state for the probe)
GRACE_SECONDS = 1.0            # an anchor's take outlives a newer anchor's this long for its completion
MAX_TAKES = 2                  # unclaimed takes writing at once (~30 MB each: two RDRAM-sized images)
MAX_BEHIND = 2                 # takes cutting from behind the live edge at once; the rest queue
LAG_FRAMES = 3                 # a live take this far behind the recording pauses the behind takes
HOLD_SECONDS = 0.5             # a take holds a later start's first frame this long for a completion
MAX_LAG_FRAMES = 150           # an unclaimed take this far (5 s) behind the recording gives up
SOUNDS_KEPT = 32               # finished scenes whose presents wait for the server's sound
PUBLISH_SECONDS = 0.25         # how often a growing package is published (what an open scene can show)
TAKE_ZLIB_LEVEL = 6            # package.compress: about 6x faster than level 9 for 6 % more bytes
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
_SERIAL = itertools.count(1)   # takes from one start (a behind take, an abandoned one) never share a file


# ---------------------------------------------------------------- the server's handle

class CutterProcess:
    """The child process and its two pipe threads. on_event(dict) runs on the reader thread for
    every line the child writes, then once with {"exited": code} when it is gone."""

    def __init__(self, rom: Path, folder: Path, on_event, *, lab_root: Path | None = None,
                 spool_factory: str | None = None, extra_path: tuple = ()):
        from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
        args = ["--rom", str(rom), "--folder", str(folder)]
        if lab_root is not None:
            args += ["--lab-root", str(lab_root)]
        if spool_factory:
            args += ["--spool-factory", spool_factory]
        argv, env = python_child("attempt_cutter", "--scene-lab-cutter", args, site_packages=True)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (*map(str, extra_path), str(HERE), env.get("PYTHONPATH"))))
        spawn = quiet_spawn_kwargs()
        spawn["creationflags"] = spawn.get("creationflags", 0) | _BELOW_NORMAL
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env=env, **spawn)
        self.on_event = on_event
        self.outgoing: queue.SimpleQueue = queue.SimpleQueue()
        threading.Thread(target=self._write, name="scene-cutter-notes", daemon=True).start()
        threading.Thread(target=self._read, name="scene-cutter-events", daemon=True).start()

    @property
    def pid(self) -> int:
        return self.process.pid

    def send(self, note: dict) -> None:
        """Queues one note; never blocks (the poll loop calls this)."""
        self.outgoing.put(note)

    def _write(self) -> None:
        while True:
            note = self.outgoing.get()
            try:
                if note is None:
                    self.process.stdin.close()
                    return
                self.process.stdin.write((json.dumps(note) + "\n").encode("utf-8"))
                self.process.stdin.flush()
            except (OSError, ValueError):
                return

    def _read(self) -> None:
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                try:
                    self.on_event(event)
                except Exception:  # noqa: BLE001 - one bad event must not stop the events after it
                    logging.getLogger("sm64.scene_lab").warning("A scene cutter event failed: %r", event,
                                                                exc_info=True)
        self.on_event({"exited": self.process.wait()})

    def close(self, timeout: float = 10.0) -> int | None:
        self.outgoing.put(None)
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait(timeout=5)


# ---------------------------------------------------------------- the child

class Take(threading.Thread):
    """One package written from one start: frames as the recording yields them, until it is told
    where to stop (claim) or to give up (abandon)."""

    def __init__(self, brain: Brain, first, kind: str, segment_id=None, last: int | None = None):
        super().__init__(name=f"take-{first.index}", daemon=True)
        self.brain, self.first, self.kind, self.segment_id = brain, first, kind, segment_id
        self.spool = brain.spool               # the recording its indexes belong to, even after the brain moves on
        self.attempt_id = None                 # who claimed it; None while unclaimed
        self.reserved = None                   # an attempt waiting for its end that will claim it
        self.cut: attempt_span.Cut | None = None
        self.decided_at: float | None = None
        self.abandon_at: float | None = None
        self.source = None
        self.written = 0
        self.dropped = threading.Event()
        self.ended = False
        self.stop_index = last
        self.limit: int | None = None          # write no further than this until claimed (Waiting.through)
        # The package is written in place under its final name, and published beside it as it
        # grows (package.PackageWriter.publish): a growing scene opens from the two.
        self.path = Path(brain.folder) / f"take-{first.index}-{next(_SERIAL)}{PACKAGE_SUFFIX}"
        self.grow = self.path.with_suffix(".grow")
        self.published = 0                     # frames the newest publication covers
        self.presents: list = []               # per frame written: (present sequence, us after the origin) or None
        self.published_at = 0.0
        self.add_ms: list[float] = []          # each frame's write, for the cost the ready event reports
        self.cpu_started = 0.0
        self.writer = None

    def announce(self) -> dict:
        """The child's "take" event: where this take began and where its package grows."""
        return {"take": {"path": str(self.path), "grow": str(self.grow), "frame": self.first.global_timer,
                         "utc": self.first.utc.isoformat(), "first": self.first.index, "kind": self.kind}}

    @property
    def open(self) -> bool:
        """Unclaimed and still writing: a completion starting at its first task may claim it."""
        return self.attempt_id is None and not self.dropped.is_set() and not self.ended

    @property
    def position(self) -> int:
        """The newest task index written."""
        return self.first.index + self.written - 1

    def claim(self, attempt_id: int, cut: attempt_span.Cut, decided_at: float) -> None:
        self.attempt_id, self.cut, self.decided_at, self.reserved = attempt_id, cut, decided_at, None
        self.abandon_at = None
        self.stop_index = cut.last.index
        if self.source is not None:
            self.source.stop_at(cut.last.index)

    def abandon(self) -> None:
        self.dropped.set()
        if self.source is not None:
            self.source.stop_at(-1)

    def cost(self) -> dict:
        """What this take cost the child's core: its thread's CPU a frame (writing, publishing,
        reading the recording), and each frame's write, mean and p95, in ms."""
        writes = sorted(self.add_ms)
        if not writes:
            return {}
        return {"cpu_ms_per_frame": round((time.thread_time() - self.cpu_started) * 1000 / len(writes), 2),
                "write_ms_mean": round(sum(writes) / len(writes), 2),
                "write_ms_p95": round(writes[min(len(writes) - 1, int(len(writes) * 0.95))], 2)}

    def absolute_presents(self, frames: int) -> list[tuple[int, int]]:
        """(present sequence, QPC) of each shown frame of the first `frames`, as live_audio's
        attach_span takes them; empty when the reader gives no origin (live_spool.LiveSource's
        origin_qpc: a capture's times count from it)."""
        origin, frequency = getattr(self.source, "origin_qpc", None), self.brain.spool_frequency
        if origin is None or not frequency:
            return []
        return [(sequence, origin + time_us * frequency // 1_000_000)
                for sequence, time_us in filter(None, self.presents[:frames])]

    def _hold(self, capture, timer) -> None:
        """Waits before writing a frame that may lie past this take's span: past `limit` (the
        newest task the span rule placed inside it while the end of the fade-out is still to
        come), or the first frame of a start announced after this take began (the reset that
        ends this attempt starts the next). A level load's first frame costs ~0.9 s to write
        (a checkpoint over a new level, measured 2026-10-26), which would otherwise come
        between his reset and "ready". A start that ends something else releases after HOLD_SECONDS."""
        index = self.position + 1
        started = time.monotonic()
        while not self.dropped.is_set() and self.cut is None:
            self._publish()                      # what it holds back from is shown meanwhile
            if self.limit is not None and index > self.limit:
                time.sleep(0.005)
                continue
            if timer is None or time.monotonic() - started > HOLD_SECONDS:
                return
            frame = self.brain.package.tap.word_value(capture.extras, *timer)
            if not any(boundary.frame == frame and boundary.utc > self.first.utc
                       for boundary in list(self.brain.boundaries)):
                return
            time.sleep(0.005)

    def run(self) -> None:
        brain = self.brain
        writer = None
        try:
            if hasattr(self.spool, "follow"):
                self.source = self.spool.follow(self.first.index, states=True)
                if self.stop_index is not None or self.dropped.is_set():
                    self.source.stop_at(-1 if self.dropped.is_set() else self.stop_index)
            else:                               # a reader without follow(): only cuts with a known end
                self.source = self.spool.source(self.first.index, self.stop_index, states=True)
                self.source.stop_at = lambda last: None
            writer = brain.package.PackageWriter(self.source, brain.rom, self.path, brain.package.ZLIB,
                                                 level=TAKE_ZLIB_LEVEL, shared=brain.seed(self.source.rdram_size),
                                                 in_place=True, frequency=brain.spool_frequency)
            self.writer = writer
            self.cpu_started = time.thread_time()
            timer = dict((name, (address, size)) for name, address, size in self.source.identity).get("global_timer")
            for capture in self.source.captures:
                self._hold(capture, timer)
                while brain.should_wait(self) and not self.dropped.is_set():
                    time.sleep(0.005)
                if self.dropped.is_set() or (self.cut is not None and self.position >= self.cut.last.index):
                    break
                began = time.perf_counter()
                writer.add(capture)
                self.add_ms.append((time.perf_counter() - began) * 1000)
                self.written += 1
                self.presents.append((capture.present_sequence, capture.present_time_us)
                                     if capture.shown and capture.present_time_us >= 0 else None)
                self._publish()
            # A take that follows the live edge may have written a frame or two past the end it
            # learned later (the reset's own picture can reach the recording before its event
            # reaches the server); finish keeps the span and leaves those unreferenced.
            frames = None if self.cut is None else self.cut.last.index - self.first.index + 1
            if self.dropped.is_set() or frames is None or self.written < frames:
                writer.abandon()
                writer = None
                if self.cut is not None and not self.dropped.is_set():
                    brain.finished(self, None, getattr(self.source, "ended", None)     # live_spool says why
                                   or "The scene recording ended before this attempt's scene was complete.")
                return
            stats = writer.finish(frames)
            writer = None
            self.grow.unlink(missing_ok=True)   # the file is whole: a growing session reads it now
            brain.finished(self, stats, None)
        except Exception as error:  # noqa: BLE001 - a take's failure is that attempt's reason, never the child's end
            if writer is not None:
                writer.abandon()
            if self.cut is not None:
                brain.finished(self, None, f"The scene could not be cut: {error}")
        finally:
            if not self.path.is_file():         # abandoned: its publication goes with it
                self.grow.unlink(missing_ok=True)
            self.ended = True
            brain.emit({"take_ended": str(self.path)})

    def _publish(self) -> None:
        """Publishes the frames written so far (never past a claimed span's last) once
        PUBLISH_SECONDS passed since the last publication and it would cover more; a take cut
        from behind the edge publishes nothing (nobody opens it before it is whole)."""
        writer = self.writer
        if writer is None or self.kind == "behind" or time.monotonic() - self.published_at < PUBLISH_SECONDS:
            return
        written = self.written - (1 if writer.states else 0)        # the state runs one frame behind
        limit = None if self.cut is None else self.cut.last.index - self.first.index + 1
        if min(written, limit or written) <= self.published:
            return
        self.published = writer.publish(self.grow, limit) or self.published
        self.published_at = time.monotonic()


class Brain:
    """The child's state: the starts and completions the server sent, the takes writing, the
    recording. Everything but the takes' own threads runs on the main thread (note, tick); a take
    reports back through finished()."""

    def __init__(self, rom: bytes, rom_loads: list, folder: Path, opener, emit, clock=time.monotonic):
        import package
        self.package = package
        self.rom, self.rom_loads, self.folder, self.opener, self.emit, self.clock = (
            rom, rom_loads, Path(folder), opener, emit, clock)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.spool = None
        self.starts: deque[dict] = deque(maxlen=64)
        self.boundaries: deque[attempt_span.Boundary] = deque(maxlen=256)
        self.pending: dict[int, dict] = {}           # attempt id -> {"mark", "said"}
        self.takes: list[Take] = []                  # writing, claimed or not; tick drops ended ones
        self.queued: deque[Take] = deque()           # behind takes waiting for a slot (MAX_BEHIND)
        self.forgotten: set[int] = set()
        self.live: dict | None = None
        self.edge = None                             # the newest TaskRef the recording held at the last tick
        self.seeds: dict[int, object] = {}           # RDRAM size -> the attributor every take shares
        self.sounds: dict[int, tuple] = {}           # attempt id -> (package, absolute presents) awaiting sound
        self.spool_frequency: int | None = None      # the recording's QPC ticks a second

    def seed(self, rdram_size: int):
        """The attributor whose ROM candidates, discovered windows and misses every take uses."""
        if rdram_size not in self.seeds:
            self.seeds[rdram_size] = self.package.Attributor(self.rom, rdram_size, self.rom_loads)
        return self.seeds[rdram_size]

    def should_wait(self, take: Take) -> bool:
        """The child's one core goes first to a live take finishing its claimed span (what he is
        waiting for), then to live takes behind the recording, last to takes cut from behind it.
        Measured 2026-10-26 on the BoB minute: a reset starts the next attempt's take, whose
        first frame costs ~0.8 s (a checkpoint over the whole level) and, written beside the
        finishing take, held "ready" back 0.87 s."""
        if take.kind != "behind" and take.cut is not None:
            return False
        others = [other for other in list(self.takes) if other is not take and not other.ended
                  and not other.dropped.is_set() and other.kind != "behind"]
        if any(other.cut is not None for other in others):
            return True
        edge = self.edge
        return take.kind == "behind" and edge is not None and any(
            other.position < edge.index - LAG_FRAMES for other in others)

    # notes from the server
    def note(self, note: dict) -> None:
        op = note.get("op")
        if op == "start":
            self.starts.append(note)
            if note.get("kind") == "anchor":
                self.boundaries.append(attempt_span.Boundary(int(note["frame"]), attempt_span.utc(note["utc"])))
        elif op == "stop":
            for take in self.takes:
                if take.kind == "segment" and take.segment_id == note.get("segment_id") and take.open:
                    take.abandon_at = take.abandon_at or self.clock() + GRACE_SECONDS
        elif op == "complete":
            mark = attempt_span.AttemptMark.from_json(note["attempt"])
            self.forgotten.discard(mark.id)
            self.pending[mark.id] = {"mark": mark, "said": None}
            if mark.anchor_frame is not None:
                self.boundaries.append(attempt_span.Boundary(mark.anchor_frame, mark.started_utc))
        elif op == "sound":
            threading.Thread(target=self._sound, args=(int(note["id"]), Path(note["capture"])),
                             name="cutter-sound", daemon=True).start()
        elif op == "forget":
            attempt_id = int(note["id"])
            self.forgotten.add(attempt_id)
            self.pending.pop(attempt_id, None)
            self.queued = deque(take for take in self.queued if take.attempt_id != attempt_id)
            for take in self.takes:
                if attempt_id in (take.attempt_id, take.reserved):
                    take.abandon()

    def tick(self) -> bool:
        """One look at the recording; True while anything waits on it."""
        self.takes = [take for take in self.takes if not take.ended]
        while self.queued and sum(take.kind == "behind" for take in self.takes) < MAX_BEHIND and self.spool:
            self._begin(self.queued.popleft())
        if self.spool is None:
            self.spool = self.opener()
            self.spool_frequency = getattr(self.spool, "frequency", None) or self.spool_frequency
            if self.spool is None:
                self._live({"recording": False, "reason": "No scene recording is running."})
                for attempt_id, entry in list(self.pending.items()):
                    self._say(attempt_id, entry, "unavailable", reason=attempt_span.NOT_RECORDING)
                    self.pending.pop(attempt_id)
                return bool(self.takes)
        self.spool.refresh()
        health = self.spool.health()
        self._live({"recording": bool(health.get("recording")), "reason": health.get("reason")})
        if self.starts or self.pending:
            oldest = [attempt_span.utc(start["utc"]) for start in self.starts]
            oldest += [entry["mark"].started_utc for entry in self.pending.values()]
            tasks = self.spool.tasks(min(oldest) - timedelta(seconds=attempt_span.MATCH_SECONDS + 1))
            self.edge = tasks[-1] if tasks else self.edge
            now = self.clock()
            self._resolve_starts(tasks, now)
            self._decide(tasks, health, now)
        elif self.edge is not None and self.takes:
            recent = self.spool.tasks(self.edge.utc)
            self.edge = recent[-1] if recent else self.edge
        now = self.clock()
        for take in self.takes:
            if take.open and take.reserved is None and (
                    (take.abandon_at is not None and now >= take.abandon_at)
                    or take.written > attempt_span.MAX_SCENE_FRAMES
                    or (self.edge is not None and take.written and self.edge.index - take.position > MAX_LAG_FRAMES)):
                take.abandon()              # nobody waits for it, or it fell too far behind to catch up
        if not health.get("recording"):
            self.spool = self.edge = None      # a newer session, when one starts, is opened next tick
        return bool(self.starts or self.pending or self.takes)

    def _open_take(self, first_index: int) -> Take | None:
        return next((take for take in self.takes if take.open and take.first.index == first_index), None)

    def _resolve_starts(self, tasks: list, now: float) -> None:
        newest = tasks[-1] if tasks else None
        for start in list(self.starts):
            when = attempt_span.utc(start["utc"])
            task = attempt_span.find(tasks, int(start["frame"]), when)
            if task is None:
                if newest is not None and (newest.utc - when).total_seconds() > attempt_span.MATCH_SECONDS:
                    self.starts.remove(start)                # never recorded: nothing to take
                continue
            self.starts.remove(start)
            if self._open_take(task.index) is not None or not hasattr(self.spool, "follow"):
                continue                                     # without follow() every cut waits for its end
            kind = start.get("kind", "anchor")
            for take in self.takes:
                superseded = take.kind == "anchor" if kind == "anchor" else (
                    take.kind == "segment" and take.segment_id == start.get("segment_id"))
                if superseded and take.open and take.abandon_at is None:
                    take.abandon_at = now + GRACE_SECONDS
            if sum(take.open for take in self.takes) >= MAX_TAKES:
                continue                                     # its attempt, if any, is cut behind the edge
            self._begin(Take(self, task, kind, start.get("segment_id")))

    def _decide(self, tasks: list, health: dict, now: float) -> None:
        for attempt_id, entry in list(self.pending.items()):
            found = attempt_span.span(entry["mark"], tasks, list(self.boundaries), health)
            if isinstance(found, attempt_span.Unavailable):
                self.pending.pop(attempt_id)
                self._release(attempt_id)
                self._say(attempt_id, entry, "unavailable", reason=found.reason)
            elif isinstance(found, attempt_span.Waiting):
                take = found.first is not None and self._open_take(found.first.index)
                if take and take.reserved in (None, attempt_id):
                    take.reserved = attempt_id
                    if found.through is not None:
                        take.limit = found.through.index
                    self._say(attempt_id, entry, "ready", growing=True, path=str(take.path), grow=str(take.grow))
                else:
                    self._say(attempt_id, entry, "cutting", reason=found.reason)
            else:
                self.pending.pop(attempt_id)
                take = self._open_take(found.first.index)
                if take is None or take.reserved not in (None, attempt_id):
                    take = Take(self, found.first, "behind", last=found.last.index)
                    take.claim(attempt_id, found, now)          # before it starts: a failure has its attempt
                    self._begin(take)
                else:
                    take.claim(attempt_id, found, now)
                if take.kind == "behind":
                    self._say(attempt_id, entry, "cutting", seconds=found.seconds, reason="Writing the scene.")
                else:
                    self._say(attempt_id, entry, "ready", growing=True, seconds=found.seconds,
                              path=str(take.path), grow=str(take.grow))

    def _begin(self, take: Take) -> None:
        if take.kind == "behind" and sum(other.kind == "behind" for other in self.takes) >= MAX_BEHIND:
            self.queued.append(take)                         # tick starts it when one ends
            return
        self.takes.append(take)
        if take.kind != "behind":
            self.emit(take.announce())
        take.start()

    def _release(self, attempt_id: int) -> None:
        for take in self.takes:
            if take.reserved == attempt_id:
                take.reserved = None
                take.abandon_at = self.clock() + GRACE_SECONDS

    def finished(self, take: Take, stats: dict | None, reason: str | None) -> None:
        """On the take's thread, once its package is complete or cannot be."""
        if take.attempt_id in self.forgotten:
            if stats is not None:
                take.path.unlink(missing_ok=True)
            return
        if stats is None:
            self.emit({"id": take.attempt_id, "state": "unavailable", "reason": reason})
            return
        cut = take.cut
        presents = take.absolute_presents(stats["frames"])
        if presents:
            self.sounds[take.attempt_id] = (take.path, presents)
            while len(self.sounds) > SOUNDS_KEPT:
                self.sounds.pop(next(iter(self.sounds)))
        sound = {"presents_qpc": [presents[0][1], presents[-1][1]], "qpc_frequency": self.spool_frequency} \
            if presents else {"silent": "The scene recording gives no time for this attempt's pictures."}
        self.emit({"id": take.attempt_id, "state": "ready", "growing": False, "seconds": cut.seconds, **sound,
                   "path": str(take.path), "why": cut.why,
                   "first": cut.first.index, "last": cut.last.index, "frames": stats["frames"],
                   "bytes": stats["package_bytes"], "write_seconds": stats["write_seconds"],
                   "finish_seconds": stats["finish_seconds"], "take_kind": take.kind, **take.cost(),
                   "ready_after_decided_ms": round((self.clock() - take.decided_at) * 1000, 1),
                   "ready_after_last_ms": round((datetime.now(timezone.utc) - cut.last.utc).total_seconds() * 1000, 1)})

    def _sound(self, attempt_id: int, capture: Path) -> None:
        """Adds the sound the recorder heard (the server wrote it from live_audio's ring) to a
        finished attempt scene, off the main thread: {"id", "sound": None} once the package
        carries it, else the plain sentence why the scene stays silent."""
        path, presents = self.sounds.get(attempt_id, (None, None))
        if path is None:
            reason = "This attempt's scene is no longer kept."
        else:
            try:
                reason = __import__("live_audio").attach_span(path, capture, presents, self.spool_frequency, self.rom)
            except Exception as error:  # noqa: BLE001 - a silent scene, never a stopped cutter
                reason = f"This attempt's sound could not be added: {error}"
        self.emit({"id": attempt_id, "sound": reason})

    def _say(self, attempt_id: int, entry: dict, state: str, **fields) -> None:
        said = (state, fields.get("reason"))
        if entry["said"] != said:
            entry["said"] = said
            self.emit({"id": attempt_id, "state": state, **fields})

    def _live(self, live: dict) -> None:
        if live != self.live:
            self.live = live
            self.emit({"live": live})

    def close(self) -> None:
        for take in self.takes:
            take.abandon()
        for take in self.takes:
            take.join(timeout=10)


def _opener(spec: str | None, lab_root: Path | None):
    """open_spool from live_spool, or `module:function` (tests inject a recorded-session double)."""
    if spec:
        module, _, name = spec.partition(":")
        factory = getattr(__import__(module), name)
    else:
        factory = __import__("live_spool").open_spool
    return lambda: factory(lab_root)


def serve(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="attempt_cutter")
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--folder", required=True, type=Path)
    parser.add_argument("--lab-root", type=Path)
    parser.add_argument("--spool-factory")
    args = parser.parse_args(argv)
    sink, sink_lock = sys.stdout, threading.Lock()

    def emit(event: dict) -> None:
        with sink_lock:
            sink.write(json.dumps(event) + "\n")
            sink.flush()

    import package
    started = time.perf_counter()
    rom = args.rom.read_bytes()
    rom_loads = package.provenance.segment_loads(rom)
    args.folder.mkdir(parents=True, exist_ok=True)
    brain = Brain(rom, rom_loads, args.folder, _opener(args.spool_factory, args.lab_root), emit)
    emit({"ready": True, "rom_seconds": round(time.perf_counter() - started, 3)})
    notes: queue.SimpleQueue = queue.SimpleQueue()
    threading.Thread(target=_read_notes, args=(sys.stdin, notes), name="cutter-notes", daemon=True).start()
    try:
        return _run(brain, notes)
    finally:
        brain.close()


def _read_notes(stream, notes: queue.SimpleQueue) -> None:
    """The server's notes, one JSON object a line; None once the pipe closes."""
    for line in stream:
        try:
            note = json.loads(line)
        except ValueError:
            continue
        if isinstance(note, dict):
            notes.put(note)
    notes.put(None)


def _run(brain: Brain, notes: queue.SimpleQueue) -> int:
    """Every note as it arrives, then a look at the recording; until the server's pipe closes."""
    busy = False
    while True:
        try:
            note = notes.get(timeout=TICK_SECONDS if busy else IDLE_TICK_SECONDS)
        except queue.Empty:
            note = False
        while note:
            brain.note(note)
            try:
                note = notes.get_nowait()
            except queue.Empty:
                note = False
        if note is None:
            return 0
        try:
            busy = brain.tick()
        except Exception as error:  # noqa: BLE001 - a broken read of the recording is reported, then retried
            brain.spool = brain.edge = None
            brain._live({"recording": False, "reason": f"The scene recording could not be read: {error}"})
            busy = True


if __name__ == "__main__":
    raise SystemExit(serve())
