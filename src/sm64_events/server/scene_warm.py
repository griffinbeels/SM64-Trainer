"""Stages made ahead, in the background, so a ghost's scene starts at once (lab only; scene_resim.py uses it).

A stage from the cartridge alone costs ~6 s the first time (the cold boot, then the practice reset); a cached one
~0.2 s. The warmer makes them before he asks, one stage (level, area, act) at a time, in this order (the lead's,
2026-11-08):

    CURRENT  the star he is looking at (GET /stage)
    LIST     every target on a list he is looking at (GET /ghosts, GET /ghost: Top ghosts, the Library)
    ROUTE    his practice route's stars (the tracker's active route)
    REST     every other stage: courses 1-15 x acts 1-6, then the stages without an act (the Bowser courses, the
             slide, the cap courses, Wing Mario over the Rainbow, the aquarium, the three Bowser arenas): 102 in all

The castle's own levels (the lobby, upstairs, the basement, the grounds, the courtyard) are not made ahead: the
recipe's painting entry (warp node 0x0A) is no entry there (in the lobby it leads on into the slide, the basement
has none and the game stops, the grounds drop him into the moat) and Usamune's practice reset is not shown to be one
in a hub, so a castle secret star's or a castle movement's ghost starts from his own scene, a start file or another
take of his (measured 2026-11-08).

His PB ghosts with no recorded take go in the same queue, each after its own stage (ask_ghost; 2026-11-09, his
words: "I want to be able to see all of my PBs ready immediately as ghosts when I open the app"): the Practice log's
ask (scene_lab_api.SceneLab.attempt_states) queues the ghost's whole scene, built by ghost_resim's `build` command at
this same idle pace, so a click then reads the made scene. Measured 2026-11-09 on his 10"10 Wild Blue PB (303
frames, his US ROM, the fake renderer host), click to the first picture: 11.2 s with nothing made ahead, 1.9 s with
its stage made ahead, 0.6 s with its scene made ahead; ~3 MB per scene under the lab's resim folder.

Each stage once per server run. One child at a time (ghost_resim's `warm` command), at idle priority pinned to one
core, and suspended while his game runs slow (GamePace: the poller's own reads of the game clock, in process). A
build he clicked joins a warm of its own stage (join) instead of making it again. Disk: ~1.2 MB per stage (~125 MB
for all 102), the boot RAM 1.2 MB, the packer's seed 23 MB per ROM, under the lab's resim/stages folder; not
counted in GHOST FILES (ghost/storage.py) and never purged by it.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import subprocess
import threading
import time
from collections import deque

log = logging.getLogger("sm64.scene_lab")

CURRENT, LIST, ROUTE, REST = 0, 1, 2, 3
WARM_SECONDS = 120.0               # a stuck warm is ended (a cold one takes ~8-10 s on one idle core)
JOIN_SECONDS = 60.0                # how long a click waits for the warm of its own stage
CHECK_SECONDS = 0.5                # how often a running warm looks at his game's pace
MAIN_COURSES, ACTS = range(1, 16), range(1, 7)
ARENAS = (30, 33, 34)              # the Bowser fights' levels (memory/addresses.py leaves them without a course)


def stage_of(course: int | None, star: int | None) -> tuple[int, int, int] | None:
    """A star target's stage (level, area, act): a main course's stars 1-6 are its acts; its 100-coin star, and every
    star of a course without an act selector, take act 1 (what a ghost there builds from). None for a castle star
    (course 0) and anything else without a course level (memory/addresses.py COURSE_BY_LEVEL)."""
    from sm64_events.memory.addresses import COURSE_BY_LEVEL
    level = next((level for level, owner in COURSE_BY_LEVEL.items() if owner == course), None)
    if level is None or star is None:
        return None
    return level, 1, star + 1 if 1 <= course <= 15 and 0 <= star <= 5 else 1


def rest() -> list[tuple[int, int, int]]:
    """Every stage the warmer makes after the ones he is looking at, in order: courses 1-15 x acts 1-6, then the
    stages without an act (courses 16-24, then the arenas)."""
    from sm64_events.memory.addresses import COURSE_BY_LEVEL
    by_course = {course: level for level, course in COURSE_BY_LEVEL.items()}
    stages = [(by_course[course], 1, act) for course in MAIN_COURSES for act in ACTS]
    stages += [(by_course[course], 1, 1) for course in sorted(by_course) if course > max(MAIN_COURSES)]
    return stages + [(level, 1, 1) for level in ARENAS]


class GamePace:
    """Is his game keeping its pace right now? From `timer()`, the game clock the poller last read (global_timer, in
    process; None while no game is attached): the game's frames per second over the last WINDOW_S. A running game
    below SLOW_FPS (it runs at 30) hurts; a paused, reset or absent game says nothing, so it never holds work back."""

    WINDOW_S, SLOW_FPS, LEAST_S = 2.0, 27.0, 1.0

    def __init__(self, timer, clock=time.monotonic):
        self.timer, self.clock = timer, clock
        self.samples: deque[tuple[float, int]] = deque()

    def hurts(self) -> bool:
        now = self.clock()
        try:
            value = self.timer()
        except Exception:  # noqa: BLE001 - a missing signal never stops the work
            value = None
        if value is None:
            self.samples.clear()
            return False
        if self.samples and value < self.samples[-1][1]:          # a reset: start again
            self.samples.clear()
        self.samples.append((now, value))
        while self.samples and now - self.samples[0][0] > self.WINDOW_S:
            self.samples.popleft()
        (began, first), (ended, last) = self.samples[0], self.samples[-1]
        if ended - began < self.LEAST_S or last == first:          # too soon to say, or paused
            return False
        return (last - first) / (ended - began) < self.SLOW_FPS


def _suspend(child: subprocess.Popen, suspended: bool) -> None:
    """Suspends or resumes a child this owns (NtSuspendProcess / NtResumeProcess; Windows only)."""
    handle = getattr(child, "_handle", None)
    if handle is None or os.name != "nt":
        return
    import ctypes
    ntdll = ctypes.windll.ntdll
    (ntdll.NtSuspendProcess if suspended else ntdll.NtResumeProcess)(ctypes.c_void_p(int(handle)))


def _priority(child: subprocess.Popen, below_normal: bool) -> None:
    """A joined warm runs at below normal priority on every core: he is waiting on it now."""
    handle = getattr(child, "_handle", None)
    if handle is None or os.name != "nt":
        return
    import ctypes
    kernel32 = ctypes.windll.kernel32
    kernel32.SetPriorityClass(ctypes.c_void_p(int(handle)), subprocess.BELOW_NORMAL_PRIORITY_CLASS if below_normal
                              else subprocess.IDLE_PRIORITY_CLASS)
    if below_normal:
        every = (1 << min(os.cpu_count() or 1, 64)) - 1
        kernel32.SetProcessAffinityMask(ctypes.c_void_p(int(handle)), ctypes.c_size_t(every))


class StageWarmer:
    """The ordered queue and its one background child. `spawn(level, area, act)` starts the child (scene_resim builds
    its arguments); `pace` is a GamePace or None; `route()` gives his route's "course:star" keys."""

    def __init__(self, spawn, *, pace: GamePace | None = None, route=None, spawn_ghost=None, finished=None):
        """`spawn_ghost(payload)` starts a ghost's scene made ahead (ask_ghost; scene_resim.py builds it) and
        `finished(key)` hears each item end, so its owner can clean up after it."""
        self.spawn, self.pace, self.route = spawn, pace, route
        self.spawn_ghost, self.on_finished = spawn_ghost, finished
        self.payloads: dict[tuple, object] = {}                          # ("ghost", sha16) -> what spawn_ghost takes
        self.lock = threading.Lock()
        self.queued: dict[tuple[int, int, int], tuple[int, int]] = {}   # stage -> (tier, order)
        self.done: set[tuple[int, int, int]] = set()                    # warmed or tried this run (or a build's)
        self.order = itertools.count()
        self.running: tuple[int, int, int] | None = None
        self.child: subprocess.Popen | None = None
        self.joined = threading.Event()
        self.finished = threading.Condition(self.lock)
        self.worker: threading.Thread | None = None
        self.stopped = threading.Event()
        self.started_rest = False
        self.log: list[tuple[int, int, int]] = []                       # the order warms ran in (tests, the report)

    def ask(self, stage: tuple[int, int, int] | None, tier: int = CURRENT, *, then_rest: bool = True) -> None:
        """Queues that stage at `tier` (a better tier moves it forward); starts the worker. `then_rest`: the
        first such ask also queues his route and every other stage (the Ghost Lab's own asks)."""
        if stage is not None:
            self.ask_many([stage], tier, then_rest=then_rest)

    def ask_many(self, stages, tier: int, *, then_rest: bool = True) -> None:
        with self.lock:
            for key in stages:
                if key in self.done or key == self.running:
                    continue
                known = self.queued.get(key)
                if known is None or tier < known[0]:
                    self.queued[key] = (tier, next(self.order))
            if then_rest and not self.started_rest:     # the first ask also queues his route, then everything else
                self.started_rest = True
                for key in self._route():
                    self.queued.setdefault(key, (ROUTE, next(self.order)))
                for key in rest():
                    self.queued.setdefault(key, (REST, next(self.order)))
                for key in self.done:
                    self.queued.pop(key, None)
            if self.queued and (self.worker is None or not self.worker.is_alive()) and not self.stopped.is_set():
                self.worker = threading.Thread(target=self._work, name="resim-warm", daemon=True)
                self.worker.start()

    def ask_ghost(self, key: tuple, payload, tier: int = LIST) -> None:
        """Queues one ghost's scene, made ahead at the same idle pace (his PB ghosts on the Practice log, 2026-11-09:
        "I want to be able to see all of my PBs ready immediately as ghosts"). Asked after its stage, at the same
        tier, so the stage is made first. `key` is ("ghost", the clip's sha256[:16]); once per server run. It never
        starts the route and the rest (the Practice log opening is not a visit to the Ghost Lab)."""
        with self.lock:
            if key in self.done or key == self.running:
                return
            self.payloads[key] = payload
        self.ask_many([key], tier, then_rest=False)

    def _route(self) -> list[tuple[int, int, int]]:
        try:
            keys = self.route() if self.route is not None else []
        except Exception:  # noqa: BLE001 - no route, no route tier
            return []
        stages = []
        for key in keys or []:
            try:
                course, star = (int(part) for part in str(key).split(":"))
            except ValueError:
                continue
            stage = stage_of(course, star)
            if stage is not None:
                stages.append(stage)
        return stages

    def made(self, key: tuple[int, int, int]) -> None:
        """A build made that stage itself: nothing to warm."""
        with self.lock:
            self.done.add(key)
            self.queued.pop(key, None)

    def join(self, key: tuple[int, int, int] | None) -> bool:
        """A click on a ghost of `key`: a running warm of it is raised to his priority and waited for (True); a
        queued one is dropped (the build makes the stage). False when there was nothing to join."""
        if key is None:
            return False
        with self.lock:
            self.queued.pop(key, None)
            if self.running != key:
                self.done.add(key)
                return False
            child = self.child
        if child is not None:
            _suspend(child, False)
            _priority(child, True)
        self.joined.set()
        deadline = time.monotonic() + JOIN_SECONDS
        with self.lock:
            while self.running == key and time.monotonic() < deadline:
                self.finished.wait(timeout=0.5)
        return True

    def stop(self) -> None:
        self.stopped.set()
        with self.lock:
            child = self.child
        if child is not None and child.poll() is None:
            child.kill()

    def _next(self) -> tuple[int, int, int] | None:
        with self.lock:
            if not self.queued or self.stopped.is_set():
                self.running = None
                return None
            key = min(self.queued, key=self.queued.get)
            del self.queued[key]
            self.done.add(key)
            self.running = key
            return key

    def _work(self) -> None:
        while not self.stopped.is_set():
            while self.pace is not None and self.pace.hurts() and not self.stopped.is_set():
                time.sleep(CHECK_SECONDS)                    # his game runs slow: nothing starts now
            key = self._next()
            if key is None:
                return
            self.log.append(key)
            try:
                if key[0] == "ghost":
                    with self.lock:
                        payload = self.payloads.pop(key, None)
                    child = self.spawn_ghost(payload) if self.spawn_ghost is not None and payload is not None else None
                else:
                    child = self.spawn(*key)
            except Exception as error:  # noqa: BLE001 - a warm that cannot start is skipped
                log.info("%s could not be made ahead: %s", _named(key), error)
                child = None
            with self.lock:
                self.child = child
            if child is not None:
                self._watch(child, key)
            if self.on_finished is not None:
                try:
                    self.on_finished(key)
                except Exception:  # noqa: BLE001 - clean-up never stops the queue
                    log.warning("Cleaning up after %s failed.", _named(key), exc_info=True)
            with self.lock:
                self.child, self.running = None, None
                self.finished.notify_all()

    def _watch(self, child: subprocess.Popen, key: tuple[int, int, int]) -> None:
        """Waits for the child (its pipes read on a thread of their own, so it never blocks on them), suspended
        while his game runs slow, unless a click joined it."""
        said: list = []
        reader = threading.Thread(target=lambda: said.append(child.communicate()), daemon=True)
        reader.start()
        began, suspended = time.monotonic(), False
        while reader.is_alive():
            if time.monotonic() - began > WARM_SECONDS or self.stopped.is_set():
                child.kill()
                break
            slow = self.pace is not None and not self.joined.is_set() and self.pace.hurts()
            if slow != suspended:
                _suspend(child, slow)
                suspended = slow
            reader.join(timeout=CHECK_SECONDS)
        reader.join(timeout=5)
        self.joined.clear()
        output, errors = said[0] if said else ("", "")
        line = (output or "").strip().splitlines()[-1:] or [(errors or "").strip()[-300:]]
        try:
            answer = json.loads(line[0]) if line[0].startswith("{") else {"error": line[0]}
        except ValueError:
            answer = {"error": line[0]}
        log.info("%s made ahead: %s", _named(key), answer)


def _named(key: tuple) -> str:
    """A queue item as the log names it: a stage by level, area and act, a ghost's scene by its clip's hash."""
    return f"ghost scene {key[1]}" if key[0] == "ghost" else "stage (level {} area {} act {})".format(*key)
