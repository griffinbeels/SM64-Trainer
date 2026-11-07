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
"stop" (a segment_disarmed notice), "complete" (an attempt finished: its AttemptMark), "forget"
(the server no longer keeps that attempt), "sound" (add a finished scene's sound) and "fit" (fit
a growing scene's sound to its pictures once, for scene_audio.LiveSound).

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
to stop at the last, and finish() writes only the frames still pending and the index. Each take
reads Mario in every task it writes (attempt_span.MarioReader on the task's scene state), so a
star's scene ends where its dance does (attempt_span.dance): the span rule reads the take's
states, and once the take knows its attempt's end it writes nothing past that dance. Every
attempt that starts at a take's first task gets a package of its own from that take (a Seat), so
the star and the segment anchored on one reset are both ready at once, however far apart they
end. An attempt with no take (it began before the recording, or at a start the stream did not
announce) gets a take that starts behind the live edge and stays "cutting" for as long as that
takes. A take nobody claims is abandoned: an anchor's or a segment's take GRACE_SECONDS after a
newer anchor (a segment's also at its disarm or re-arm), any take past
attempt_span.MAX_SCENE_FRAMES; at most MAX_TAKES unclaimed takes nothing has superseded write at
once. Segments arm on a level change, not only on an anchor (26 of 28 arms in his session of
2026-10-28), and keep their arm through each reset, where their next attempt starts. Modelled
on that session's journal: segment takes living until their disarm would have written 1,106 s,
none claimed, and held back the new take of 28 of his 34 resets at MAX_TAKES; ended at the next
anchor, they write 19 s in 79 min and hold back none.

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
import tap  # noqa: E402

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
SAME_START = timedelta(seconds=1 / attempt_span.FPS)   # an attempt still open began no later than this one
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
_SERIAL = itertools.count(1)   # takes from one start (a behind take, an abandoned one) never share a file
TASK_INDEX = tap.TASK_FIELDS.index("task_index")   # a capture's task index (a live_spool.TaskRef's index)


# ---------------------------------------------------------------- the server's handle

class CutterProcess:
    """The child process and its two pipe threads. on_event(dict) runs on the reader thread for
    every line the child writes, then once with {"exited": code} when it is gone."""

    def __init__(self, rom: Path, folder: Path, on_event, *, lab_root: Path | None = None,
                 spool_factory: str | None = None, extra_path: tuple = (), symbol_map: Path | None = None):
        from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
        args = ["--rom", str(rom), "--folder", str(folder)]
        if lab_root is not None:
            args += ["--lab-root", str(lab_root)]
        if symbol_map is not None:
            args += ["--map", str(symbol_map)]
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

class Seat:
    """One attempt's package from a take: its file and growth sidecar, the attempt that claimed it
    (or waits to), and where its scene stops. Attempts that start on the same frame share their
    take (his BitDW star and the "8 Red Coins -> Pipe" segment, both anchored on one reset, close
    ~150 frames apart): the first to complete takes the file the take began, each other one a
    seat whose file starts as a copy of what the take wrote so far (package.PackageWriter.branch)
    and is written on from there. Measured 2026-10-28 on his log: the segment's scene, cut again
    from behind the recording, was whole 23.6 and 26.5 s after its end was known, its star's 0.5 s."""

    def __init__(self, path: Path, number: int):
        self.path, self.grow, self.number = path, path.with_suffix(".grow"), number
        self.attempt_id = None                 # who claimed it; None while unclaimed
        self.reserved = None                   # an attempt waiting for its end that will claim it
        self.cut: attempt_span.Cut | None = None
        self.decided_at: float | None = None
        self.limit: int | None = None          # write no further than this until claimed (Waiting.through)
        self.end: int | None = None            # its attempt's end task: past that end's star dance nothing is its
        self.refs: dict | None = None          # a behind take's tasks, end to last: where its dance stops
        self.published = 0                     # frames the newest publication covers
        self.done = False                      # its package is whole, or nobody will claim it

    @property
    def open(self) -> bool:
        """Nobody holds it: a completion starting at its take's first task may."""
        return self.attempt_id is None and self.reserved is None and not self.done


class Take(threading.Thread):
    """Frames from one start as the recording yields them, written into one package per attempt
    that starts there (its seats), until each is told where it stops (claim) or nobody waits for
    one any more (give_up, abandon)."""

    def __init__(self, brain: Brain, first, kind: str, segment_id=None, last: int | None = None):
        super().__init__(name=f"take-{first.index}", daemon=True)
        self.brain, self.first, self.kind, self.segment_id = brain, first, kind, segment_id
        self.spool = brain.spool               # the recording its indexes belong to, even after the brain moves on
        # Seats change on the brain's thread while this one writes, under the lock; only this
        # thread touches the writer (a level load's first frame takes ~1 s, which must never hold
        # the brain), so a seat's file is made or deleted here between frames (_apply).
        self.lock = threading.Lock()
        self.seats: list[Seat] = []
        self.branching: list[Seat] = []        # new seats whose file this thread has yet to copy
        self.dropping: list[Seat] = []         # seats given up whose file this thread has yet to delete
        # The newest attempt to take a seat completed while another that began no later was still
        # open (Brain.note): the take writes on past every claimed span for that one's seat.
        self.expecting = False
        self.closing = False                   # it writes nothing more, so no seat can be added
        self.stop: int | None = None           # the follower's last task, once every seat is claimed
        self.abandon_at: float | None = None
        self.source = None
        self.written = 0
        self.dropped = threading.Event()
        self.ended = False
        self.stop_index = last
        # Mario in each task read (attempt_span.dance's input; None while the recording's states
        # cannot show him): past a seat's end's star dance nothing is that seat's.
        self.marios: dict[int, attempt_span.Mario | None] | None = None
        self.presents: list = []               # per frame written: (present sequence, us after the origin) or None
        self.published_at = 0.0
        self.add_ms: list[float] = []          # each frame's write, for the cost the ready event reports
        self.cpu_started = 0.0
        self.writer = None
        self.sealed: Path | None = None        # the newest seat finished: a later seat's copy when no file is open
        # Each package is written in place under its final name, and published beside it as it
        # grows (package.PackageWriter.publish): a growing scene opens from the two.
        self.seats.append(self._new_seat())

    def _new_seat(self) -> Seat:
        return Seat(Path(self.brain.folder) / f"take-{self.first.index}-{next(_SERIAL)}{PACKAGE_SUFFIX}",
                    len(self.seats))

    def _seat(self) -> Seat:
        """Under the lock: a further seat, whose file begins as a copy of the body written so far
        (package.PackageWriter.branch, made by _apply)."""
        seat = self._new_seat()
        self.seats.append(seat)
        self.branching.append(seat)
        return seat

    def _apply(self) -> None:
        """On this thread, between frames: makes the files of new seats and deletes those of
        seats given up."""
        with self.lock:
            branching, dropping, self.branching, self.dropping = self.branching, self.dropping, [], []
        writer = self.writer
        for seat in branching:
            if not seat.done:
                writer.branch(seat.path, None if writer.outputs.files else self.sealed)
        for seat in dropping:
            writer.drop(seat.path)
            seat.grow.unlink(missing_ok=True)
            self.brain.emit({"take_ended": str(seat.path)})

    def announce(self, seat: Seat) -> dict:
        """The child's "take" event: where this take began and where the package a completion
        from that start would get grows."""
        return {"take": {"path": str(seat.path), "grow": str(seat.grow), "frame": self.first.global_timer,
                         "utc": self.first.utc.isoformat(), "first": self.first.index, "kind": self.kind}}

    @property
    def open(self) -> bool:
        """Has a seat nobody holds and still writes: a completion starting at its first task may take it."""
        return (not self.dropped.is_set() and not self.ended and not self.closing
                and any(seat.open for seat in self.seats))

    @property
    def finishing(self) -> bool:
        """Writes a span an attempt claimed: what he is waiting for."""
        return any(seat.cut is not None and not seat.done for seat in self.seats)

    @property
    def position(self) -> int:
        """The newest task index written."""
        return self.first.index + self.written - 1

    def seat_for(self, attempt_id: int, companion: bool) -> Seat | None:
        """Under the lock: the seat for this attempt, which starts at this take's first task: the
        one it holds, the open one, else a new one; None once the take can serve no more."""
        seat = next((seat for seat in self.seats if not seat.done and attempt_id in (seat.attempt_id, seat.reserved)),
                    None)
        if seat is not None:
            return seat
        if (self.kind == "behind" or self.closing or self.ended or self.dropped.is_set() or self.stop is not None
                or (self.writer is not None and not self.writer.outputs.files
                    and not (self.sealed and self.sealed.is_file()))):
            return None
        seat = next((seat for seat in self.seats if seat.open), None) or self._seat()
        self.expecting = companion
        return seat

    def claim(self, seat: Seat, attempt_id: int, cut: attempt_span.Cut, decided_at: float) -> None:
        seat.attempt_id, seat.cut, seat.decided_at, seat.reserved = attempt_id, cut, decided_at, None
        seat.end = cut.end.index
        self.abandon_at = None

    def settle(self) -> None:
        """Under the lock, once the brain's notes are applied: when every seat is claimed and no
        other attempt from this start is expected, the follower ends after the last task a seat
        needs, promptly even while it waits for the next."""
        seats = [seat for seat in self.seats if not seat.done]
        if (self.source is None or self.stop is not None or self.expecting or not seats
                or any(seat.cut is None for seat in seats)):
            return
        self.stop = max(seat.cut.last.index for seat in seats)
        self.source.stop_at(self.stop)

    def give_up(self, seat: Seat) -> None:
        """Under the lock: a seat nobody will claim; its file and publication go (_apply). The
        take stops once it has no seat left."""
        seat.done = True
        self.dropping.append(seat)
        if all(other.done for other in self.seats):
            self.abandon()

    def release(self, seat: Seat, abandon_at: float) -> None:
        """Under the lock: the attempt waiting for this seat will not claim it after all. The
        take's only seat waits for another completion until `abandon_at`, as an unclaimed take
        does; any other is given up."""
        if any(other is not seat and not other.done for other in self.seats):
            self.give_up(seat)
        else:
            seat.reserved = seat.limit = seat.end = None
            self.abandon_at = abandon_at

    def forget(self, attempt_id: int) -> None:
        """Under the lock: the server keeps that attempt no more."""
        for seat in self.seats:
            if not seat.done and attempt_id in (seat.attempt_id, seat.reserved):
                self.give_up(seat)

    def _mario_note(self, timer):
        """note(capture) -> its task index, keeping Mario in that task in `marios` when the
        recording's states can show him (its task regions, at its version's layout)."""
        reader = attempt_span.MarioReader.of(self.source.identity)
        states = getattr(self.source, "states", None)
        if reader is not None and states is not None:
            self.marios = {}

        def note(capture) -> int:
            index = capture.fields[TASK_INDEX]
            if self.marios is not None:
                frame = None if timer is None else tap.word_value(capture.extras, *timer)
                self.marios[index] = reader.read(states.task.get(capture.task_sequence), frame)
            return index
        return note

    def _dance_last(self, end: int | None) -> int | None:
        """The last task of the star dance after the end task `end` (attempt_span.dance over the
        tasks this take read), once they show it ended; else None."""
        if end is None or not self.marios:
            return None
        found = attempt_span.dance(end, self.marios)
        return None if found is None else found.last

    def _past_dance(self, index: int, end: int | None) -> int | None:
        """The star dance's last task when task `index` lies past it, else None."""
        last = self._dance_last(end)
        return last if last is not None and index > last else None

    def _shorten(self, index: int) -> None:
        """A take cut from behind the recording learns its dance as it reads it: a claimed span
        that runs past the dance's last task stops there (the span rule decided it without the
        states)."""
        seat = self.seats[0]
        last = self._past_dance(index, seat.end)
        cut = seat.cut
        if last is not None and cut is not None and seat.refs and last in seat.refs and last < cut.last.index:
            seat.cut = attempt_span.Cut(cut.first, seat.refs[last], cut.end, "dance")
            self.stop = self.stop_index = last
            if self.source is not None:
                self.source.stop_at(last)

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

    def _turn(self, index: int, capture, timer) -> bool:
        """Waits until this take writes task `index`: a seat needs it (_need, _released) and the
        child's core is its turn (Brain.should_wait). False once no seat ever will: the take then
        writes nothing more. What it holds back from is published meanwhile."""
        started = time.monotonic()
        while not self.dropped.is_set():
            self._apply()
            self._shorten(index)
            with self.lock:
                need = self._need(index)
                if need is None:
                    self.closing = True
                    return False
            if (need == "claimed" or need == "open" and self._released(capture, timer, started)) \
                    and not self.brain.should_wait(self):
                return True
            self._publish()
            time.sleep(0.005)
        return False

    def _need(self, index: int) -> str | None:
        """Under the lock: who needs task `index`. "claimed": a claimed span holds it. "open": a
        seat nobody holds yet, a seat waiting within what the span rule placed in its span, or
        the seat of an attempt still expected once every claimed one is whole. "wait": only a
        seat whose attempt waits for its end, past `limit` (the newest task the span rule placed
        inside it while the end of the fade-out is still to come) or past the star dance after
        its end (the span rule ends there once it sees the same states). None: no seat ever will."""
        need = None
        for seat in self.seats:
            if seat.done:
                continue
            if seat.cut is not None:
                if index <= seat.cut.last.index:
                    return "claimed"
            elif seat.reserved is not None and ((seat.limit is not None and index > seat.limit)
                                                or self._past_dance(index, seat.end) is not None):
                need = need or "wait"
            else:
                need = "open"
        return "open" if need is None and self.expecting else need

    def _released(self, capture, timer, started: float) -> bool:
        """A frame no claimed span holds waits while it is the first frame of a start announced
        after this take began (the reset that ends its attempt starts the next), at most
        HOLD_SECONDS: a level load's first frame costs ~0.9 s to write (a checkpoint over a new
        level, measured 2026-10-26), which would otherwise come between his reset and "ready". A
        start that ends something else releases then."""
        if timer is None or time.monotonic() - started > HOLD_SECONDS:
            return True
        frame = self.brain.package.tap.word_value(capture.extras, *timer)
        return not any(boundary.frame == frame and boundary.utc > self.first.utc
                       for boundary in list(self.brain.boundaries))

    def run(self) -> None:
        brain = self.brain
        try:
            if hasattr(self.spool, "follow"):
                self.source = self.spool.follow(self.first.index, states=True)
            else:                               # a reader without follow(): only cuts with a known end
                self.source = self.spool.source(self.first.index, self.stop_index, states=True)
                self.source.stop_at = lambda last: None
            writer = brain.package.PackageWriter(self.source, brain.rom, self.seats[0].path, brain.package.ZLIB,
                                                 level=TAKE_ZLIB_LEVEL, shared=brain.seed(self.source.rdram_size),
                                                 in_place=True, frequency=brain.spool_frequency)
            self.writer = writer
            with self.lock:
                if self.dropped.is_set():
                    self.source.stop_at(-1)
                self.settle()
            self._apply()                       # seats taken or given up before the first frame
            self.cpu_started = time.thread_time()
            timer = dict((name, (address, size)) for name, address, size in self.source.identity).get("global_timer")
            note = self._mario_note(timer)
            for capture in self.source.captures:
                index = note(capture)
                if not self._turn(index, capture, timer):
                    break
                began = time.perf_counter()
                writer.add(capture)
                self.add_ms.append((time.perf_counter() - began) * 1000)
                self.written += 1
                self.presents.append((capture.present_sequence, capture.present_time_us)
                                     if capture.shown and capture.present_time_us >= 0 else None)
                self._seal_whole()
                self._publish()
            self._finish()
        except Exception as error:  # noqa: BLE001 - a take's failure is that attempt's reason, never the child's end
            with self.lock:
                self.closing = True
                failed = [seat for seat in self.seats if not seat.done]
                for seat in failed:
                    seat.done = True
            if self.writer is not None:
                self.writer.abandon()
            for seat in failed:
                if seat.cut is not None:
                    brain.finished(self, seat, None, f"The scene could not be cut: {error}")
        finally:
            for seat in self.seats:
                if not seat.path.is_file():     # given up: its publication goes with it
                    seat.grow.unlink(missing_ok=True)
            self.ended = True
            for seat in self.seats:
                brain.emit({"take_ended": str(seat.path)})

    def _finish(self) -> None:
        """Once the take writes nothing more: every claimed seat whose frames were all written
        is finished, one the recording stopped short of says why, the rest are given up. A take
        that follows the live edge may have written a frame or two past the end it learned later
        (the reset's own picture can reach the recording before its event reaches the server);
        each package keeps its span and leaves those unreferenced."""
        with self.lock:
            self.closing, dropped = True, self.dropped.is_set()
            whole, short = [], []
            for seat in self.seats:
                frames = None if seat.done or seat.cut is None else seat.cut.last.index - self.first.index + 1
                if frames is not None and not dropped and self.written >= frames:
                    seat.done = True
                    whole.append((seat, frames))
                elif not seat.done:
                    self.give_up(seat)
                    if frames is not None and not dropped:
                        short.append(seat)
        self._apply()
        for seat, frames in whole:
            self._seal(seat, frames, final=True)
        for seat in short:
            self.brain.finished(self, seat, None, getattr(self.source, "ended", None)   # live_spool says why
                                or "The scene recording ended before this attempt's scene was complete.")

    def _seal_whole(self) -> None:
        """After each frame: finishes every claimed seat whose frames, scene state included, are
        all written, while the take writes on for the others. When that leaves no seat but
        another attempt from this start was still open, the take goes on in a new seat nobody
        holds yet, its file a copy of the last one's body, announced like a take's first so that
        attempt is ready the moment it completes."""
        self._apply()
        written = self.writer.state_frames
        with self.lock:
            whole = []
            for seat in self.seats:
                frames = None if seat.done or seat.cut is None else seat.cut.last.index - self.first.index + 1
                if frames is not None and written >= frames and seat not in self.branching:
                    seat.done = True
                    whole.append((seat, frames))
        for seat, frames in whole:
            self._seal(seat, frames, final=False)
        with self.lock:
            going_on = bool(whole) and self.expecting and all(seat.done for seat in self.seats)
            seat = self._seat() if going_on else None
        self._apply()
        if seat is not None:
            self.brain.emit(self.announce(seat))

    def _seal(self, seat: Seat, frames: int, *, final: bool) -> None:
        """The seat's package is whole (its attempt said so through the brain)."""
        stats = self.writer.seal(seat.path, frames, final=final)
        self.sealed = seat.path
        seat.grow.unlink(missing_ok=True)       # the file is whole: a growing session reads it now
        self.brain.finished(self, seat, stats, None)
        self.brain.emit({"take_ended": str(seat.path)})

    def _bound(self, seat: Seat) -> int | None:
        """How many frames a seat's publication may show: its claimed span; while its attempt
        waits for its end, what the span rule placed in its span, never past its star dance; all
        written for a seat nobody holds."""
        if seat.cut is not None:
            return seat.cut.last.index - self.first.index + 1
        if seat.reserved is None:
            return None
        lasts = [last for last in (seat.limit, self._dance_last(seat.end)) if last is not None]
        return min(lasts) - self.first.index + 1 if lasts else None

    def _publish(self) -> None:
        """Publishes each seat's frames written so far (_bound) once PUBLISH_SECONDS passed since
        the last publication and it would cover more; a take cut from behind the edge publishes
        nothing (nobody opens it before it is whole)."""
        writer = self.writer
        if writer is None or self.kind == "behind" or time.monotonic() - self.published_at < PUBLISH_SECONDS:
            return
        written = writer.state_frames                             # the state runs one frame behind
        with self.lock:
            due = [(seat, self._bound(seat)) for seat in self.seats if not seat.done]
        for seat, limit in due:
            if min(written, limit or written) > seat.published and seat.path in writer.outputs.files:
                try:
                    seat.published = writer.publish(seat.grow, limit) or seat.published
                except PermissionError:         # an open scene was reading the last one: next time
                    continue
                self.published_at = time.monotonic()


class Brain:
    """The child's state: the starts and completions the server sent, the takes writing, the
    recording. Everything but the takes' own threads runs on the main thread (note, tick); a take
    reports back through finished()."""

    def __init__(self, rom: bytes, rom_loads: list, folder: Path, opener, emit, clock=time.monotonic,
                 stems=None, stems_problem: str | None = None):
        import package
        self.package = package
        self.stems, self.stems_problem = stems, stems_problem    # stems_live.StemsProcess: music and effects
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
        if take.kind != "behind" and take.finishing:
            return False
        others = [other for other in list(self.takes) if other is not take and not other.ended
                  and not other.dropped.is_set() and other.kind != "behind"]
        if any(other.finishing for other in others):
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
            # open_since: the start of the oldest attempt the tracker still has open after this
            # one closed (its completion hook's active_start). One that began no later may share
            # this one's start (a star and the segment that runs on to the pipe: the same reset),
            # so its take writes on past this one's end until that one completes too.
            since = note.get("open_since")
            self.pending[mark.id] = {"mark": mark, "said": None, "companion": since is not None and (
                attempt_span.utc(since) <= mark.started_utc + SAME_START)}
            if mark.anchor_frame is not None:
                self.boundaries.append(attempt_span.Boundary(mark.anchor_frame, mark.started_utc))
        elif op == "sound":
            threading.Thread(target=self._sound, args=(int(note["id"]), Path(note["capture"])),
                             name="cutter-sound", daemon=True).start()
        elif op == "fit":
            threading.Thread(target=self._fit, args=(note,), name="cutter-fit", daemon=True).start()
        elif op == "forget":
            attempt_id = int(note["id"])
            self.forgotten.add(attempt_id)
            self.pending.pop(attempt_id, None)
            self.queued = deque(take for take in self.queued if take.seats[0].attempt_id != attempt_id)
            for take in self.takes:
                with take.lock:
                    take.forget(attempt_id)

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
            if take.open and (
                    (take.abandon_at is not None and now >= take.abandon_at)
                    or take.written > attempt_span.MAX_SCENE_FRAMES
                    or (self.edge is not None and take.written and self.edge.index - take.position > MAX_LAG_FRAMES)):
                with take.lock:             # nobody waits for its open seat, or it fell too far behind to catch up
                    for seat in take.seats:
                        if seat.open:
                            take.give_up(seat)
                    take.settle()
        if not health.get("recording"):
            self.spool = self.edge = None      # a newer session, when one starts, is opened next tick
        return bool(self.starts or self.pending or self.takes)

    def _seat(self, first_index: int, attempt_id: int, companion: bool) -> tuple[Take, Seat] | None:
        """The take writing from that task and this attempt's seat in it (Take.seat_for), else None."""
        for take in self.takes:
            if take.first.index == first_index and not take.ended:
                with take.lock:
                    seat = take.seat_for(attempt_id, companion)
                if seat is not None:
                    return take, seat
        return None

    def _marios(self, first_index: int) -> dict | None:
        """Mario in each task a take writing from that task has read (attempt_span.span's
        `marios`), or None when none is: the span rule then decides without the dance."""
        take = next((take for take in self.takes if take.first.index == first_index and not take.dropped.is_set()
                     and not take.ended and take.kind != "behind"), None)
        marios = None if take is None else take.marios
        return None if marios is None else dict(marios)

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
            if not hasattr(self.spool, "follow") or any(     # without follow() every cut waits for its end
                    take.first.index == task.index and take.kind != "behind" and not take.ended
                    and not take.closing and not take.dropped.is_set() for take in self.takes):
                continue                                     # a take writes from there: it serves every attempt
            kind = start.get("kind", "anchor")
            for take in self.takes:
                # A reset or load ends every attempt that began before it: an armed segment's too,
                # which the tracker re-arms on the anchor's own frame (tracking/segments.py) or
                # disarms. So an anchor supersedes segment takes as well as anchor takes.
                superseded = kind == "anchor" and take.kind in ("anchor", "segment") or (
                    take.kind == "segment" and take.segment_id == start.get("segment_id"))
                if superseded and (take.open or take.expecting) and take.abandon_at is None:
                    take.abandon_at = now + GRACE_SECONDS            # (going on for one still open: its seat too)
            if sum(take.open and take.abandon_at is None for take in self.takes) >= MAX_TAKES:
                continue                                     # its attempt, if any, is cut behind the edge
            self._begin(Take(self, task, kind, start.get("segment_id")))

    def _decide(self, tasks: list, health: dict, now: float) -> None:
        for attempt_id, entry in list(self.pending.items()):
            found = attempt_span.span(entry["mark"], tasks, list(self.boundaries), health, self._marios)
            if isinstance(found, attempt_span.Unavailable):
                self.pending.pop(attempt_id)
                self._release(attempt_id)
                self._say(attempt_id, entry, "unavailable", reason=found.reason)
            elif isinstance(found, attempt_span.Waiting):
                held = found.first is not None and self._seat(found.first.index, attempt_id, entry["companion"])
                if held:
                    take, seat = held
                    with take.lock:
                        seat.reserved = attempt_id
                        if found.through is not None:
                            seat.limit = found.through.index
                        if found.end is not None:
                            seat.end = found.end.index
                    self._say(attempt_id, entry, "ready", growing=True, path=str(seat.path), grow=str(seat.grow))
                else:
                    self._say(attempt_id, entry, "cutting", reason=found.reason)
            else:
                self.pending.pop(attempt_id)
                held = self._seat(found.first.index, attempt_id, entry["companion"])
                if held is None:
                    take = Take(self, found.first, "behind", last=found.last.index)
                    seat = take.seats[0]
                    seat.refs = {task.index: task for task in tasks if found.end.index <= task.index <= found.last.index}
                    take.claim(seat, attempt_id, found, now)    # before it starts: a failure has its attempt
                    self._begin(take)
                else:
                    take, seat = held
                    with take.lock:
                        take.claim(seat, attempt_id, found, now)
                if take.kind == "behind":
                    self._say(attempt_id, entry, "cutting", seconds=found.seconds, reason="Writing the scene.")
                else:
                    self._say(attempt_id, entry, "ready", growing=True, seconds=found.seconds,
                              path=str(seat.path), grow=str(seat.grow))
        for take in self.takes:                     # after every claim this tick: a later one may share a take
            with take.lock:
                take.settle()

    def _begin(self, take: Take) -> None:
        if take.kind == "behind" and sum(other.kind == "behind" for other in self.takes) >= MAX_BEHIND:
            self.queued.append(take)                         # tick starts it when one ends
            return
        self.takes.append(take)
        if take.kind != "behind":
            self.emit(take.announce(take.seats[0]))
        take.start()

    def _release(self, attempt_id: int) -> None:
        for take in self.takes:
            with take.lock:
                for seat in take.seats:
                    if seat.reserved == attempt_id and not seat.done:
                        take.release(seat, self.clock() + GRACE_SECONDS)

    def finished(self, take: Take, seat: Seat, stats: dict | None, reason: str | None) -> None:
        """On the take's thread, once a seat's package is complete or cannot be."""
        if seat.attempt_id in self.forgotten:
            if stats is not None:
                seat.path.unlink(missing_ok=True)
            return
        if stats is None:
            self.emit({"id": seat.attempt_id, "state": "unavailable", "reason": reason})
            return
        cut = seat.cut
        presents = take.absolute_presents(stats["frames"])
        if presents:
            # The session's name (live_spool.Spool.name; a recorded double's folder name): the stem rows' tag.
            session = getattr(take.spool, "name", None) or getattr(getattr(take.spool, "session", None), "name", "")
            self.sounds[seat.attempt_id] = (seat.path, presents, session)
            while len(self.sounds) > SOUNDS_KEPT:
                self.sounds.pop(next(iter(self.sounds)))
        sound = {"presents_qpc": [presents[0][1], presents[-1][1]], "qpc_frequency": self.spool_frequency} \
            if presents else {"silent": "The scene recording gives no time for this attempt's pictures."}
        self.emit({"id": seat.attempt_id, "state": "ready", "growing": False, "seconds": cut.seconds, **sound,
                   "path": str(seat.path), "why": cut.why,
                   "first": cut.first.index, "last": cut.last.index, "frames": stats["frames"],
                   "bytes": stats["package_bytes"], "write_seconds": stats["write_seconds"],
                   "finish_seconds": stats["finish_seconds"], "take_kind": take.kind, "seat": seat.number,
                   **take.cost(), "ready_after_decided_ms": round((self.clock() - seat.decided_at) * 1000, 1),
                   "ready_after_last_ms": round((datetime.now(timezone.utc) - cut.last.utc).total_seconds() * 1000, 1)})

    def _sound(self, attempt_id: int, capture: Path) -> None:
        """Adds the sound the recorder heard (the server wrote it from live_audio's ring) to a
        finished attempt scene, off the main thread: {"id", "sound": None} once the package
        carries it, else the plain sentence why the scene stays silent."""
        path, presents, session = self.sounds.get(attempt_id, (None, None, None))
        if path is None:
            reason = "This attempt's scene is no longer kept."
        else:
            try:
                reason = __import__("live_audio").attach_span(path, capture, presents, self.spool_frequency, self.rom,
                                                              more=self._with_stems(presents, session))
            except Exception as error:  # noqa: BLE001 - a silent scene, never a stopped cutter
                reason = f"This attempt's sound could not be added: {error}"
        self.emit({"id": attempt_id, "sound": reason})

    def _with_stems(self, presents: list, session: str):
        """attach_span's last step: the scene's music and effects beside its sound, in the same
        write (stems_live.attach: the stems child's layer, or one saying why there is none)."""
        import stems_live
        return lambda data, payload: stems_live.attach(data, self.rom, payload, self.stems, self.stems_problem,
                                                       presents[0][1], session, self.folder)

    def _fit(self, note: dict) -> None:
        """A growing scene's one sound fit (scene_audio.LiveSound), off the main thread: the
        audio clock fit to the ring's chunk stamps the server wrote, here rather than in the
        server, whose poll loop shares its process's lock. {"fit": token, "segments", "losses",
        "total", "fit_ms"}, or {"fit": token, "reason"} with the plain sentence."""
        started = time.perf_counter()
        try:
            import numpy as np
            fit = __import__("live_audio").fit_stamps(np.load(note["stamps"]), int(note["origin"]),
                                                      int(note["frequency"]))
        except Exception as error:  # noqa: BLE001 - a silent scene, never a stopped cutter
            fit = {"reason": f"This scene's sound could not be lined up with its pictures ({error})."}
        self.emit({"fit": note["token"], **fit, "fit_ms": round((time.perf_counter() - started) * 1000, 1)})

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
        if self.stems is not None:
            self.stems.close()


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
    parser.add_argument("--map", type=Path, help="the symbol map: music and effects split beside each scene's sound")
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
    stems, stems_problem = None, "The scene lab has no symbol map (lab.json \"map\"), so music and sound effects cannot be split."
    if args.map is not None:
        import stems_live
        stems = stems_live.StemsProcess(args.rom, args.map, lab_root=args.lab_root,
                                        on_event=lambda event: emit({"stems_child": event}))
    brain = Brain(rom, rom_loads, args.folder, _opener(args.spool_factory, args.lab_root), emit,
                  stems=stems, stems_problem=stems_problem)
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
