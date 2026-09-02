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
and their servers) and a starved worker times out its browsers. So the lag
lever is `--workers`: 16 is the measured sweet spot on an idle machine, and
`--workers 8` while he is actively using it. And every run ends with a sweep that reports what it left behind, so "sludge" is a number
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
from datetime import datetime, timezone
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".run_tests.json"
DEFAULT_WORKERS = 16
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--changed", action="store_true",
                        help="run only tests the last full run's map says your Python "
                             "change touched; runs everything if a non-Python file changed")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"pytest-xdist workers (default {DEFAULT_WORKERS})")
    args, extra = parser.parse_known_args(argv)

    current = fingerprint(git_known_files())
    if args.changed:
        state = load_state()
        mode, why = decide(state.get("fingerprint") if state else None, current)
    else:
        mode, why = "full", "the merge gate: every test, and the coverage map refreshed"
    print(f"run_tests: {mode} -- {why}", flush=True)

    sweep_leftovers("before")
    command = [sys.executable, "-m", "pytest", *pytest_args(mode, args.workers, extra)]
    # NOT below-normal priority, though that was the first answer to the lag:
    # two full runs at BELOW_NORMAL_PRIORITY_CLASS went red (6+51 and 11+13)
    # where the same tree at normal priority ran green, because this machine
    # always has normal-priority load beside a run (sibling Claude sessions,
    # their servers) and a starved worker times out its browsers. Lag is
    # answered with `--workers` instead; see the docstring.
    exit_code = subprocess.run(command, cwd=ROOT).returncode
    sweep_leftovers("after")
    if records_full_run(mode, extra, exit_code):
        save_state(current, args.workers, exit_code)
        print(f"run_tests: recorded a full run against {len(current)} non-Python files "
              f"(pytest exit {exit_code})", flush=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
