"""Stages made ahead, in the background, so a ghost's scene starts at once (lab only; scene_resim.py uses it).

A stage from the cartridge alone costs ~6 s the first time (the cold boot, then the practice reset); a cached one
~0.2 s. The warmer makes them before he asks, one course and act at a time, in this order (the lead's, 2026-11-08):

    CURRENT  the star he is looking at (GET /stage)
    LIST     every target on a list he is looking at (GET /ghosts, GET /ghost: Top ghosts, the Library)
    ROUTE    his practice route's stars (the tracker's active route)
    REST     every other course and act (courses 1-15, acts 1-6)

Each course and act once per server run. One child at a time (ghost_resim's `warm` command), at idle priority pinned
to one core, and suspended while his game runs slow (GamePace: the poller's own reads of the game clock, in
process). A build he clicked joins a warm of its own course and act (join) instead of making the stage again.
Disk: ~1.2 MB per course and act (90 of them ~110 MB), the boot RAM 1.2 MB, the packer's seed 23 MB per ROM, under
the lab's resim/stages folder; not counted in GHOST FILES (ghost/storage.py) and never purged by it.
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
    """The ordered queue and its one background child. `spawn(course, act)` starts the child (scene_resim builds
    its arguments); `pace` is a GamePace or None."""

    def __init__(self, spawn, *, pace: GamePace | None = None, route=None):
        self.spawn, self.pace, self.route = spawn, pace, route
        self.lock = threading.Lock()
        self.queued: dict[tuple[int, int], tuple[int, int]] = {}   # (course, act) -> (tier, order)
        self.done: set[tuple[int, int]] = set()                    # warmed or tried this run (or made by a build)
        self.order = itertools.count()
        self.running: tuple[int, int] | None = None
        self.child: subprocess.Popen | None = None
        self.joined = threading.Event()
        self.finished = threading.Condition(self.lock)
        self.worker: threading.Thread | None = None
        self.stopped = threading.Event()
        self.started_rest = False
        self.log: list[tuple[int, int]] = []                       # the order warms ran in (tests, the report)

    def ask(self, course: int | None, act: int | None, tier: int = CURRENT) -> None:
        """Queues that course and act at `tier` (a better tier moves it forward); starts the worker."""
        if course is None or act is None:
            return
        self.ask_many([(course, act)], tier)

    def ask_many(self, pairs, tier: int) -> None:
        with self.lock:
            for key in pairs:
                if key in self.done or key == self.running:
                    continue
                known = self.queued.get(key)
                if known is None or tier < known[0]:
                    self.queued[key] = (tier, next(self.order))
            if not self.started_rest:          # the first ask also queues his route's stars, then everything else
                self.started_rest = True
                for key in self._route():
                    self.queued.setdefault(key, (ROUTE, next(self.order)))
                for key in ((course, act) for course in MAIN_COURSES for act in ACTS):
                    self.queued.setdefault(key, (REST, next(self.order)))
                for key in self.done:
                    self.queued.pop(key, None)
            if self.queued and (self.worker is None or not self.worker.is_alive()) and not self.stopped.is_set():
                self.worker = threading.Thread(target=self._work, name="resim-warm", daemon=True)
                self.worker.start()

    def _route(self) -> list[tuple[int, int]]:
        try:
            keys = self.route() if self.route is not None else []
        except Exception:  # noqa: BLE001 - no route, no route tier
            return []
        pairs = []
        for key in keys or []:
            try:
                course, star = (int(part) for part in str(key).split(":"))
            except ValueError:
                continue
            if 1 <= course <= 15 and 0 <= star <= 5:
                pairs.append((course, star + 1))
        return pairs

    def made(self, key: tuple[int, int]) -> None:
        """A build made that stage itself: nothing to warm."""
        with self.lock:
            self.done.add(key)
            self.queued.pop(key, None)

    def join(self, key: tuple[int, int] | None) -> bool:
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

    def _next(self) -> tuple[int, int] | None:
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
                child = self.spawn(*key)
            except Exception as error:  # noqa: BLE001 - a warm that cannot start is skipped
                log.info("The stage of course %s act %s could not be warmed: %s", *key, error)
                child = None
            with self.lock:
                self.child = child
            if child is not None:
                self._watch(child, key)
            with self.lock:
                self.child, self.running = None, None
                self.finished.notify_all()

    def _watch(self, child: subprocess.Popen, key: tuple[int, int]) -> None:
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
        log.info("stage warmed (course %s act %s): %s", *key, answer)
