"""One shared test budget across checkouts, including direct pytest invocations.

Two kinds of run. A MERGE CHECK (a whole lane) takes one of two slots before
any worker or browser exists, so at most two stand up at once and a third
waits. A FOCUSED run (explicit targets, `--changed` selection) never queues:
it is the inner loop, and waiting behind somebody's merge check is exactly the
lost time this design removes. Both kinds are confined to the same CPU mask.

Slot 0 is the lock file every earlier runner takes, so a runner from an older
worktree still excludes -- and is excluded by -- the first merge check. A
crashed owner releases its slot automatically (an OS lock, not a lockfile).

The machine budget is 16 workers on 20 of 32 CPUs, or 8 workers on a quarter
of the CPUs while OBS is open; each run gets half of it, so two concurrent
merge checks together fill it and never exceed it. OBS opening mid-run
tightens the whole tree within two seconds and stays latched until that run
ends. On a dedicated CI machine there is no desktop to protect: every CPU,
a worker per CPU at most, and the job names its lane's count. See docs/testing.md for the measurements and limits.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

import psutil

from sm64_events.storage.instance_lock import acquire_instance_lock
if __package__:
    from .test_activity import competing_runs
else:
    from test_activity import competing_runs

LOCK_PATH = Path(tempfile.gettempdir()) / "SM64Trainer_tests.lock"
OWNER_ENV = "SM64_TEST_OWNER"
WORKERS_ENV = "SM64_TEST_WORKERS"
POLL_SECONDS = 2.0
# The watcher runs on the controller, and while it runs the test thread waits
# for the GIL. Pinning children and the OBS check cost ~15 ms a tick; the scan
# for outside test runs cost 0.6-1.2 s a tick with 36-61 Python processes up
# (w1 and here, 2026-09-22), which ran a serial run's tests at 65-89% of their
# solo speed. So that scan, which only reports, runs every half minute: a run
# started mid-way is still named, within 30 s.
SCAN_SECONDS = 30.0
SLOTS = 2
MACHINE_WORKERS = {False: 16, True: 8}   # every concurrent run together; key: OBS open


def slot_paths(path: Path = LOCK_PATH) -> list[Path]:
    """Slot 0 IS the legacy lock; later slots sit beside it."""
    return [path] + [path.with_name(f"{path.stem}.slot{index}{path.suffix}")
                     for index in range(1, SLOTS)]


def obs_is_open() -> bool:
    return any((proc.info["name"] or "").lower() in {"obs64.exe", "obs32.exe", "obs.exe", "obs"}
               for proc in psutil.process_iter(["name"]))


def dedicated_machine() -> bool:
    """A CI runner: nobody is using the desktop the reserve protects."""
    return os.environ.get("GITHUB_ACTIONS") == "true"


def budget(eligible: list[int], obs: bool, workers: int | None = None,
           reserve: int | None = None, *, dedicated: bool = False) -> tuple[int, list[int]]:
    """Return one run's worker ceiling and a subset of CPUs the caller owns.

    Explicit workers/reserve may tighten the policy, never loosen it.
    Zero workers means serial pytest; an affinity mask is never empty.
    """
    available = len(eligible)
    if dedicated:
        # The workflow names each lane's workers (tools/test_lanes.py
        # LANE_WORKERS); the machine only caps them at one per CPU.
        count = available
        ceiling = available
    else:
        count = max(1, available // 4 if obs else available - min(12, available // 2))
        ceiling = max(1, min(MACHINE_WORKERS[obs], count) // SLOTS)
    if reserve is not None:
        count = min(count, max(1, available - reserve))
        ceiling = min(ceiling, count)
    return min(ceiling, workers) if workers is not None else ceiling, eligible[:count]


def inherited_owner() -> bool:
    """Only a live ancestor with the matching birth time can lend its budget.

    Stale environment variables and recycled PIDs cannot bypass admission.
    Nested test harnesses inherit the same allocation instead of deadlocking.
    """
    token = os.environ.get(OWNER_ENV, "")
    return any(token == f"{parent.pid}:{parent.create_time()}"
               for parent in psutil.Process().parents())


def effective_workers(transports: list[str]) -> int:
    """Count xdist's already-limited local transports, including N*popen.

    xdist applies --maxprocesses before configure; re-reading numprocesses
    would discard that tighter request. Remote/custom transports cannot be
    accounted for by this local resource controller.
    """
    count = 0
    for transport in transports:
        amount, separator, spec = transport.partition("*")
        if not separator:
            amount, spec = "1", amount
        if not amount.isdecimal() or spec != "popen":
            raise ValueError("test budget supports local popen workers only; use --workers through tools/run_tests.py")
        count += int(amount)
    return count


class TestResources:
    """Hold a slot (merge check) and the affinity monitor until all owned work is finished."""

    __test__ = False

    def __init__(self, workers: int | None = None, reserve: int | None = None,
                 *, admit: bool = True, path: Path = LOCK_PATH):
        self.requested_workers = workers
        self.reserve = reserve
        self.admit = admit
        self.path = path
        self.slot = None
        self.workers = 0
        self.handle = None
        self.process = psutil.Process()
        self.eligible = self.process.cpu_affinity()
        self.cpus = self.eligible
        self.obs = False
        self.dedicated = dedicated_machine()
        self._stop = threading.Event()
        self._thread = None
        self._previous_owner = os.environ.get(OWNER_ENV)
        self._previous_workers = os.environ.get(WORKERS_ENV)
        self.registry = self.path.with_name(self.path.name + ".runners")
        self.ticket = self.registry / f"{self.process.pid}-{self.process.create_time()}.runner"
        self.competitors: dict[int, dict] = {}
        self.clock = time.monotonic
        self._next_scan = 0.0

    def __enter__(self):
        started = time.monotonic()
        # Registered before anything else, focused runs included: an
        # unregistered pytest is what a merge check waits for.
        self.registry.mkdir(parents=True, exist_ok=True)
        self.ticket.touch()
        try:
            if self.admit:
                self._take_a_slot()
                self._wait_for_older_runners()
            self.obs = obs_is_open()
            self.workers, self.cpus = budget(self.eligible, self.obs, self.requested_workers,
                                             self.reserve, dedicated=self.dedicated)
            # Before Popen/xdist, not a sweep two seconds after the spawn storm.
            self.process.cpu_affinity(self.cpus)
            os.environ[OWNER_ENV] = f"{self.process.pid}:{self.process.create_time()}"
            os.environ[WORKERS_ENV] = str(self.workers)
            kind = "dedicated" if self.dedicated else "OBS open" if self.obs else "normal"
            entry = (f"admitted after {time.monotonic() - started:.1f}s to slot {self.slot + 1}/{SLOTS}"
                     if self.admit else "focused run, no queue")
            print(f"tests: {entry}; {kind} budget: "
                  f"{self.workers or 'serial'} workers, {len(self.cpus)}/{len(self.eligible)} CPUs", flush=True)
            self._thread = threading.Thread(target=self._watch, daemon=True)
            self._thread.start()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def _take_a_slot(self):
        announced = False
        while self.handle is None:
            # Slot 0 first, always: while any new merge check runs it holds
            # the legacy lock, which is the only one an older runner reads.
            for index, slot in enumerate(slot_paths(self.path)):
                self.handle = acquire_instance_lock(slot)
                if self.handle is not None:
                    self.slot = index
                    return
            if not announced:
                print(f"tests: queued behind {SLOTS} merge checks; no workers or browsers started", flush=True)
                announced = True
            time.sleep(0.25)

    def _wait_for_older_runners(self):
        reported = set()
        while other := competing_runs(self.registry):
            for run in other:
                if run["pid"] not in reported:
                    print(f"tests: waiting for uncoordinated test PID {run['pid']} "
                          f"in {run['checkout']}; no workers started", flush=True)
                    reported.add(run["pid"])
            time.sleep(POLL_SECONDS)

    def _watch(self):
        while not self._stop.wait(POLL_SECONDS):
            self.refresh()

    def refresh(self):
        """OBS can appear after admission; re-pin existing descendants as well."""
        if self.clock() >= self._next_scan:
            self._next_scan = self.clock() + SCAN_SECONDS
            for run in competing_runs(self.registry):
                if run["pid"] not in self.competitors:
                    self.competitors[run["pid"]] = run
                    print(f"tests: PERFORMANCE COMPARISON CONTAMINATED by outside test "
                          f"PID {run['pid']} in {run['checkout']}; do not tune from this run", flush=True)
        if not self.obs and not self.dedicated and obs_is_open():
            self.obs = True
            _, self.cpus = budget(self.eligible, True, self.requested_workers, self.reserve)
            print(f"tests: OBS opened; limiting this run to {len(self.cpus)} CPUs "
                  "(worker count stays fixed until the next run)", flush=True)
        try:
            family = [self.process, *self.process.children(recursive=True)]
        except psutil.NoSuchProcess:
            return
        for proc in family:
            try:
                current = proc.cpu_affinity()
                if not set(current) <= set(self.cpus):
                    # A nested probe may voluntarily use fewer CPUs. The
                    # outer monitor must never widen that child's allocation.
                    proc.cpu_affinity(sorted(set(current) & set(self.cpus)) or self.cpus)
            except psutil.NoSuchProcess:
                continue  # Normal race with a finished test child.
            except (psutil.AccessDenied, OSError) as error:
                print(f"tests: could not limit owned PID {proc.pid}: {error}", flush=True)

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        try:
            self.process.cpu_affinity(self.eligible)
        finally:
            if self._previous_owner is None:
                os.environ.pop(OWNER_ENV, None)
            else:
                os.environ[OWNER_ENV] = self._previous_owner
            if self._previous_workers is None:
                os.environ.pop(WORKERS_ENV, None)
            else:
                os.environ[WORKERS_ENV] = self._previous_workers
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            self.ticket.unlink(missing_ok=True)
