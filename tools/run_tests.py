"""Run the suite the fast way and the honest way -- the one door for both.

    uv run python tools/run_tests.py             # the merge gate: everything, on workers, refreshing the coverage map
    uv run python tools/run_tests.py --changed   # the inner loop: only what the last full run says your Python change touched
    uv run python tools/run_tests.py -x -k rank  # anything else goes straight to pytest

Two instruments, and why each is shaped as it is (all numbers 2026-09-01,
8,763 tests):

* **Workers.** 19m 29s serial; 4m 11s on 16 workers grouped by file, identical
  outcomes. `tests/conftest.py::pytest_collection_modifyitems` gives every
  test a worker group -- its file, or itself when marked `spread` -- and
  `--dist loadgroup` schedules by that. 79% of the suite's time is browser
  tests waiting on Chrome, which is why the workers overlap so well; 16
  rather than 32 because each browser test is several Chrome processes plus
  a server, and the sweep floor made 32 pointless until the sweep was split.

* **Rerun of only-failed** (pytest-rerunfailures, `--reruns 2`). Sixteen
  browsers saturate the CPU, and a render check with a fixed settle window
  misses it under that load: one worker runs the browser modules green,
  16 flaked 9 render assertions on pages that render fine unloaded
  (2026-09-01). A rerun retries only the tests that failed, on the same
  worker, so a flake passes on the retry and a real failure fails all
  three attempts and is still reported. This is not a fixed constant to
  tune -- it is the standard answer to browser flake under parallelism,
  and it replaces the repo's standing "one browser test flakes per full
  run" tax.

* **Selection** (pytest-testmon). A run records, per test, the source lines it
  executed; the next `--changed` run reruns only tests that executed a block
  that has since changed, plus tests whose own file changed or that failed
  last time. A leaf module's change selected 405 tests, a hub's 600 to 1,072,
  an unchanged tree none in two seconds. It is BLIND to everything that is
  not Python executed in-process: a JS, HTML, CSS, seed-data or docs edit
  selects nothing, and so does Python that only runs inside a spawned
  subprocess. So `--changed` is not the gate. It never replaces the full run;
  the full run is what refreshes the map, and it is the one command that
  says "this merges".

  **testmon reorders, and the suite puts the order back.** `--testmon-
  noselect` is documented as "reorder and prioritize the tests most likely
  to fail first", and it does, once a map exists -- a `trylast` collection
  hook that sorts every module's tests by recorded failures and duration.
  A module that shares one browser page across its tests (the fixture-
  reach file, the collapse story, every `scope="module"` page) then runs
  its practice-tab tests after its story tests, on a page left on the
  Segments tab, and its two viewport params interleave so the page is
  rebuilt dozens of times. Measured 2026-09-02, one worker, no other
  load: 38 page builds and 3 failed + 7 reruns under the flag; 2 builds
  and 81 green without it. The first full run in a fresh worktree has no
  map and keeps file order, which is why the first gate in a worktree was
  green and every later one red on a different set -- it read as load
  flake for a day. `tests/conftest.py`'s collection hookwrapper restores
  raw order plus pytest's own param grouping after every plugin, and
  `tests/test_worker_groups.py` checks the live session against that
  recipe on every run.

The door closes the blind spot it can: after every full run it records a
content hash of every non-Python file git knows about (tracked or untracked,
not ignored), and `--changed` refuses to select when any of them differs --
it runs everything instead and says which files made it. Without that, a JS
edit reads as green on zero tests, which is the failure `CLAUDE.md` names as
"a guard nobody has seen fail is green forever".

State: `.testmondata` (testmon's map) and `.run_tests.json` (the last full
run's hash), both per checkout, both gitignored; a fresh worktree's first
run is a full one because it has neither.

**What the run does to the machine, during and after.** Sixteen workers each
launching Chrome saturate the CPU -- summed test time inflates 2.2x under
load -- and he reported the desktop lagging while a run was on (2026-09-01).
The first answer was below-normal priority for the whole tree, and it was
wrong: two full runs at that class went red (6 failed + 51 errors, then 11 +
13) where the same tree at normal priority ran green, because this machine
always carries normal-priority load beside a run (sibling Claude sessions
and their servers) and a starved worker times out its browsers.

The two levers that DO work are measured, by `tools/measure_run_load.py`,
against what lag actually is: how long a normal-priority thread waits for a
core after it is ready to run (its wake-latency probe; p95 of that wait, in
ms, because a stutter is a tail event and the median never sees one). Three
full green runs, 2026-09-02, with sibling sessions live as usual:

    idle, no run                                p95  0.6 ms   p99  0.7 ms
    16 workers, nothing reserved      216 s     p95 10.3 ms   p99 29.4 ms
    16 workers, 8 cores reserved      234 s     p95  1.3 ms   p99 17.7 ms
    8 workers, nothing reserved       326 s     p95  0.7 ms   p99  1.2 ms

**`--reserve` is on by default at 8, and that is why.** It is CPU affinity,
not priority: the whole pytest tree is fenced off 8 of this machine's 32
logical processors -- the top indices, so sibling threads go together and 4
whole physical cores come free -- and the desktop always has somewhere to
run rather than waiting behind a worker. It costs 18 s (8%) and takes the
typical stall from 10.3 ms, which is dropped frames, to 1.3 ms, which is
idle. Affinity is inherited at spawn and xdist's workers and their browsers
appear over the run's first seconds, so it is re-applied to every new
descendant on a 2 s sweep rather than set once. `--reserve 0` turns it off.

`--workers 8` remains the lever for when he is actively using the machine
and wants it untouched: idle-grade at both percentiles, for 110 s (51%)
more wall time. Reserving cores does not substitute for it -- the p99 stays
at 17.7 ms, so an occasional hitch survives -- and it does not need to,
because the two compose.

And every run ends with a sweep that reports what it left behind, so "sludge" is a number
on screen rather than a feeling: orphaned headless browsers (parent gone),
orphaned workers and ffmpeg from THIS checkout, and Playwright profile dirs
in TEMP that no live browser references. Measured before this existed: ten
full runs in a day left zero processes and zero profile dirs; the 69 empty
profile dirs in TEMP dated from March to August, from browsers killed
mid-run in earlier sessions. Only orphans are touched -- a sibling session's
live run has live parents and referenced dirs, and is never a target.

**Two tells worth knowing when a run goes red under load.** `net::
ERR_NO_BUFFER_SPACE` on a goto means Windows ran out of socket buffers --
seen once when two extra pytest collections ran beside the door -- and every
later test in that worker then fails with "asyncio.run() cannot be called
from a running event loop", because the browser's loop was left running in
the worker's thread. That cascade is environmental: rerun the door alone.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from find_uilab import find_uilab

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".run_tests.json"
DEFAULT_WORKERS = 16
# Logical processors kept OFF the test tree so the desktop always has one.
# 8 of the 32 here -- measured at 8% more wall time for an 8x cut in the
# typical stall; the docstring carries the table.
DEFAULT_RESERVED_CORES = 8
AFFINITY_SWEEP_SECONDS = 2.0
# pytest exit codes that mean the run COMPLETED and the coverage map is whole:
# 0 all passed, 1 some failed. 2 is an interruption, 3/4 are pytest's own
# errors, 5 collected nothing -- none of those leave a map worth stamping.
COMPLETED_EXIT_CODES = (0, 1)
# The door's own state never enters its fingerprint. testmon keeps its map
# in SQLite, whose `-wal`/`-shm` side files appear and vanish between runs;
# with them counted, every `--changed` read as "non-Python changed" and ran
# everything, forever -- the safe failure, and a useless door (2026-09-01).
OWN_STATE_PREFIXES = (".testmondata", ".run_tests.json")


def base_args(workers: int) -> list[str]:
    """The flags every run through this door shares. One place, so the merge
    gate and the inner loop can never drift onto different schedulers -- the
    nodeids testmon keys its map by carry the worker group as a suffix under
    loadgroup, so a run without it would look like 8,763 brand-new tests."""
    # `--reruns 2` reruns ONLY the tests that failed, on the same worker: a
    # browser render that missed its settle window under 16-way CPU contention
    # passes on the retry, while a real failure fails all three attempts and
    # is still reported failed. Measured 2026-09-01: a single worker runs the
    # browser modules green, 16 workers flaked 9 render checks (0 elements
    # found on a page that renders fine unloaded) -- load, not breakage. The
    # 1 s delay gives the machine a breath before the retry.
    return ["-n", str(workers), "--dist", "loadgroup",
            "--reruns", "2", "--reruns-delay", "1", "-q"]


def pytest_args(mode: str, workers: int, extra: list[str]) -> list[str]:
    if mode == "full":
        return [*base_args(workers), "--testmon-noselect", *extra]
    if mode == "select":
        return [*base_args(workers), "--testmon", *extra]
    raise ValueError(mode)


def reserved_affinity(reserve: int, total: int | None = None) -> list[int]:
    """The logical processors the test tree may use when `reserve` are kept for
    the desktop -- empty when nothing is reserved, which is how the caller knows
    to leave affinity alone. The TOP indices are the ones handed back: Windows
    numbers a core's two threads adjacently, so taking them off the end frees
    whole physical cores rather than one thread of twice as many."""
    if reserve <= 0:
        return []
    total = total or psutil.cpu_count()
    return list(range(max(1, total - reserve)))


def apply_affinity(root_pid: int, cpus: list[int], pinned: set[int]) -> None:
    """Fence the run's whole process tree onto `cpus`, skipping what is already
    fenced. Silent on a process that exited mid-walk or refuses -- a finished
    worker is the normal case, and a run must never die of its own courtesy."""
    try:
        root = psutil.Process(root_pid)
        family = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return
    for proc in family:
        if proc.pid in pinned:
            continue
        pinned.add(proc.pid)
        try:
            proc.cpu_affinity(cpus)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass


def is_python(path: str) -> bool:
    return path.endswith(".py")


def git_known_files() -> list[str]:
    """Every path git tracks plus every untracked path it does not ignore --
    the set a commit could carry, which is the set a merge gate answers for."""
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                             capture_output=True).stdout
    untracked = subprocess.run(["git", "ls-files", "-z", "--others", "--exclude-standard"],
                               cwd=ROOT, check=True, capture_output=True).stdout
    paths = (tracked + untracked).decode("utf-8").split("\0")
    return sorted(path for path in paths if path)


def fingerprint(paths: list[str], read=lambda path: (ROOT / path).read_bytes()) -> dict[str, str]:
    """path -> sha1 of contents, for every non-Python file that still exists.
    Content, not mtime: a checkout or a merge touches mtimes without changing
    a byte, and a spurious full run is safe but costs four minutes."""
    prints = {}
    for path in paths:
        if is_python(path) or path.startswith(OWN_STATE_PREFIXES):
            continue
        try:
            data = read(path)
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            continue
        prints[path] = hashlib.sha1(data).hexdigest()
    return prints


def decide(saved: dict[str, str] | None, current: dict[str, str]) -> tuple[str, str]:
    """('full' | 'select', why). Pure, so the rule is testable without git."""
    if saved is None:
        return "full", "no record of a full run in this checkout yet"
    changed = sorted(path for path in set(saved) | set(current)
                     if saved.get(path) != current.get(path))
    if changed:
        shown = ", ".join(changed[:5]) + (f" (+{len(changed) - 5} more)" if len(changed) > 5 else "")
        return "full", (f"{len(changed)} non-Python file(s) changed since the last full run, "
                        f"and nothing can see what they affect: {shown}")
    return "select", "only Python changed since the last full run; testmon picks the tests"


def records_full_run(mode: str, extra: list[str], exit_code: int) -> bool:
    """Only a full run over EVERYTHING that completed earns the stamp: a `-k`
    or a path narrows the run, and stamping it would let `--changed` select
    against a map that never covered the rest."""
    return mode == "full" and not extra and exit_code in COMPLETED_EXIT_CODES


def is_orphan(created_at: float, parent: dict | None) -> bool:
    """A process whose parent is gone, or whose parent pid was reused by a
    process YOUNGER than it, belongs to nobody. A live sibling session's
    workers and browsers have live, older parents and never match."""
    return parent is None or parent["create_time"] > created_at


def stray_processes(procs: list[dict], root: Path) -> list[dict]:
    """Which of these process records a finished run should not have left.

    Each record: name, pid, cmdline (list), create_time, cwd (or None), parent
    (a record or None). Orphaned HEADLESS chrome; orphaned ffmpeg; orphaned
    python whose cwd is under this checkout (an xdist worker or a spawned
    server whose controller died). Never a process with a live parent."""
    strays = []
    for proc in procs:
        name = proc["name"].lower()
        cmdline = " ".join(proc.get("cmdline") or [])
        orphan = is_orphan(proc["create_time"], proc.get("parent"))
        if not orphan:
            continue
        if name == "chrome.exe" and "--headless" in cmdline:
            strays.append(proc)
        elif name == "ffmpeg.exe":
            strays.append(proc)
        elif name == "python.exe" and proc.get("cwd") and _under(Path(proc["cwd"]), root):
            strays.append(proc)
    return strays


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def stray_profile_dirs(temp_dirs: list[Path], live_cmdlines: list[str]) -> list[Path]:
    """Playwright makes one `playwright*` profile dir per browser in TEMP and
    removes it on close; a browser killed mid-run leaves it. A dir no live
    browser names in its command line is nobody's."""
    referenced = " ".join(live_cmdlines).lower()
    return [path for path in temp_dirs
            if path.name.lower().startswith("playwright") and str(path).lower() not in referenced]


def _process_records() -> list[dict]:
    records = {}
    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
        info = proc.info
        if not info["name"]:
            continue
        record = {"pid": info["pid"], "ppid": info["ppid"], "name": info["name"],
                  "cmdline": info["cmdline"] or [], "create_time": info["create_time"],
                  "cwd": None, "parent": None, "_proc": proc}
        if info["name"].lower() == "python.exe":
            try:
                record["cwd"] = proc.cwd()
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                pass
        records[info["pid"]] = record
    for record in records.values():
        parent = records.get(record["ppid"])
        record["parent"] = ({"create_time": parent["create_time"]}
                            if parent and parent["pid"] != record["pid"] else None)
    return list(records.values())


def sweep_leftovers(label: str) -> None:
    """Kill orphaned test processes and delete unreferenced profile dirs, then
    say what was found -- every run, so a clean machine is a printed fact."""
    records = _process_records()
    strays = stray_processes(records, ROOT)
    for stray in strays:
        try:
            stray["_proc"].kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    live_chrome = [" ".join(rec["cmdline"]) for rec in records
                   if rec["name"].lower() == "chrome.exe" and rec not in strays]
    temp = Path(tempfile.gettempdir())
    dirs = [path for path in temp.iterdir() if path.is_dir()] if temp.is_dir() else []
    orphan_dirs = stray_profile_dirs(dirs, live_chrome)
    for path in orphan_dirs:
        shutil.rmtree(path, ignore_errors=True)
    browsers = sum(1 for stray in strays if stray["name"].lower() == "chrome.exe")
    others = len(strays) - browsers
    if strays or orphan_dirs:
        print(f"run_tests: {label} -- swept {browsers} orphaned headless browser(s), "
              f"{others} orphaned worker/ffmpeg process(es), {len(orphan_dirs)} unreferenced "
              f"Playwright profile dir(s)", flush=True)
    elif label == "after":
        print("run_tests: nothing left behind (orphaned browsers, workers, ffmpeg, "
              "profile dirs all zero)", flush=True)


def load_state() -> dict | None:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_state(prints: dict[str, str], workers: int, exit_code: int) -> None:
    STATE_PATH.write_text(json.dumps({
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "workers": workers,
        "pytest_exit": exit_code,
        "fingerprint": prints,
    }, indent=0), encoding="utf-8")


def say_if_the_rendered_gates_cannot_run() -> None:
    """Announce a missing uilab, because a run without it looks GREEN.

    Every layout, contact-sheet and rendered-behaviour gate skips when uilab
    is unreachable, and a skip is not a failure: the run reports `8413 passed,
    75 skipped` in 68 seconds and exits 0. Measured 2026-09-03, building a
    baseline to compare a branch against -- a `git worktree` outside
    `.claude/worktrees/` resolves the sibling checkout relative to ITSELF, so
    the 62 browser tests silently left the run and the comparison was about to
    be believed. `find_uilab`'s own docstring covers the other way this
    vanishes (a `uv sync` pruning an editable install).

    Prints and returns; it never blocks. The point is that a run missing its
    rendered half cannot look identical to one that has it."""
    if find_uilab() is not None:
        print("run_tests: !! uilab NOT FOUND -- every rendered gate will SKIP, "
              "and the run will still exit 0. Set UILAB_PATH to your uilab "
              "checkout before trusting this result.", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--changed", action="store_true",
                        help="run only tests the last full run's map says your Python "
                             "change touched; runs everything if a non-Python file changed")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"pytest-xdist workers (default {DEFAULT_WORKERS})")
    parser.add_argument("--reserve", type=int, default=DEFAULT_RESERVED_CORES,
                        help=f"logical processors kept free of the test tree so the desktop "
                             f"stays responsive (default {DEFAULT_RESERVED_CORES}; 0 disables)")
    args, extra = parser.parse_known_args(argv)

    current = fingerprint(git_known_files())
    if args.changed:
        state = load_state()
        mode, why = decide(state.get("fingerprint") if state else None, current)
    else:
        mode, why = "full", "the merge gate: every test, and the coverage map refreshed"
    print(f"run_tests: {mode} -- {why}", flush=True)
    say_if_the_rendered_gates_cannot_run()

    sweep_leftovers("before")
    command = [sys.executable, "-m", "pytest", *pytest_args(mode, args.workers, extra)]
    # NOT below-normal priority, though that was the first answer to the lag:
    # two full runs at BELOW_NORMAL_PRIORITY_CLASS went red (6+51 and 11+13)
    # where the same tree at normal priority ran green, because this machine
    # always has normal-priority load beside a run (sibling Claude sessions,
    # their servers) and a starved worker times out its browsers. Lag is
    # answered with `--workers` instead; see the docstring.
    child = subprocess.Popen(command, cwd=ROOT)
    cpus = reserved_affinity(args.reserve)
    if cpus:
        pinned: set[int] = set()
        while child.poll() is None:
            apply_affinity(child.pid, cpus, pinned)
            time.sleep(AFFINITY_SWEEP_SECONDS)
    exit_code = child.wait()
    sweep_leftovers("after")
    if records_full_run(mode, extra, exit_code):
        save_state(current, args.workers, exit_code)
        print(f"run_tests: recorded a full run against {len(current)} non-Python files "
              f"(pytest exit {exit_code})", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
