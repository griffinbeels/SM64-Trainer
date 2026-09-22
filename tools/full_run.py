"""Read the full run on GitHub in a few lines instead of a raw log.

    uv run python tools/full_run.py status    [--sha SHA | --ref BRANCH | --run ID]
    uv run python tools/full_run.py wait      [... ] [--timeout-minutes 60]
    uv run python tools/full_run.py failures  [...]   # failing tests, first error line, rerun command
    uv run python tools/full_run.py durations [...]   # rebalance the jobs from a run's JUnit times
    uv run python tools/full_run.py retry <junit.xml>  # a job's one retry (the workflow runs it)

The full run is the whole test suite, browser tests included, on GitHub
Actions for every push to main and on demand (`gh workflow run full.yml --ref
<branch>`), split into parallel jobs. It gates a release (tools/release.py),
and its newest green run on main is the baseline the local merge check diffs
against (tools/blast_radius.py). With no target these read the newest run for
this checkout's HEAD commit.

`failures` downloads the run's artifacts (JUnit XML per job, the flaky list,
failure screenshots) to a temp folder and prints each failing test with its
first error line, then the command that reruns exactly those tests here.
`status` and `wait` also name the run's FLAKY tests, and any test FLAKY in
two or more of the branch's last ten runs as needing a fix.
Exit codes: 0 passed, 1 failed, 2 no run or still running, 3 `gh` unavailable.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from test_lanes import DURATIONS_PATH, file_totals, shard_unit  # noqa: E402

WORKFLOW = "full.yml"
RUN_FIELDS = "databaseId,status,conclusion,headSha,headBranch,event,createdAt,updatedAt,url"
DOWNLOADS = Path(tempfile.gettempdir()) / "sm64-full-run"
PASSED, FAILED, PENDING, MISSING = "passed", "failed", "running", "missing"


class GhUnavailable(RuntimeError):
    pass


def gh(*args: str) -> str:
    try:
        result = subprocess.run(["gh", *args], cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GhUnavailable(f"gh could not run: {error}") from error
    if result.returncode != 0:
        raise GhUnavailable(result.stderr.strip() or f"gh {' '.join(args)} exited {result.returncode}")
    return result.stdout


def head_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def find_run(*, sha: str | None = None, ref: str | None = None, run_id: str | None = None,
             run=gh) -> dict | None:
    """The newest full run for a commit or branch (a re-run of the same
    commit supersedes an older verdict), or the named run."""
    if run_id:
        return json.loads(run("run", "view", str(run_id), "--json", RUN_FIELDS))
    query = ["run", "list", "--workflow", WORKFLOW, "--json", RUN_FIELDS, "--limit", "20"]
    query += ["--commit", sha] if sha else ["--branch", ref] if ref else []
    runs = json.loads(run(*query) or "[]")
    return max(runs, key=lambda item: item["createdAt"]) if runs else None


def green_runs_on_main(run=gh) -> list[tuple[str, int]]:
    """(commit, run id) of every passed full run on main, newest first."""
    runs = json.loads(run("run", "list", "--workflow", WORKFLOW, "--branch", "main",
                          "--status", "success", "--json", "headSha,databaseId,createdAt",
                          "--limit", "50") or "[]")
    return [(item["headSha"], item["databaseId"])
            for item in sorted(runs, key=lambda item: item["createdAt"], reverse=True)]


def verdict(found: dict | None) -> str:
    if found is None:
        return MISSING
    if found["status"] != "completed":
        return PENDING
    return PASSED if found["conclusion"] == "success" else FAILED


def _minutes(start: str | None, end: str | None) -> str:
    if not start or not end or end.startswith("0001"):
        return "-"
    seconds = (datetime.fromisoformat(end.replace("Z", "+00:00"))
               - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds()
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def describe(found: dict | None, jobs: list[dict] | None = None, target: str = "") -> str:
    """One line for the run, one short line per job that is not green."""
    if found is None:
        return (f"no full run for {target or 'this commit'}. A push to main starts one; "
                f"for a branch: gh workflow run {WORKFLOW} --ref <branch>")
    state = verdict(found)
    head = (f"full run {found['databaseId']} for {found['headSha'][:10]} "
            f"({found['headBranch']}, {found['event']}): ")
    jobs = jobs or []
    done = [job for job in jobs if job.get("status") == "completed"]
    bad = [job for job in done if job.get("conclusion") not in ("success", "skipped")]
    if state == PENDING:
        head += f"{found['status']}, {len(done)}/{len(jobs) or '?'} jobs finished"
    elif state == PASSED:
        head += f"passed, {len(jobs)} jobs in {_minutes(found['createdAt'], found['updatedAt'])}"
    else:
        head += (f"{found['conclusion']}, {len(bad)} of {len(jobs)} jobs not green; "
                 "`tools/full_run.py failures` names the tests")
    lines = [head, found["url"]]
    lines += [f"  {job['name']}: {job.get('conclusion')} "
              f"({_minutes(job.get('startedAt'), job.get('completedAt'))})" for job in bad]
    return "\n".join(lines)


def jobs_of(found: dict, run=gh) -> list[dict]:
    return json.loads(run("run", "view", str(found["databaseId"]), "--json", "jobs"))["jobs"]


def release_gate(sha: str, *, wait: bool = True, timeout_minutes: float = 60,
                 poll_seconds: float = 30, run=gh, sleep=time.sleep,
                 clock=time.monotonic, say=print) -> tuple[bool, str]:
    """(allowed, why) for releasing `sha`: its newest full run must have
    passed. A run still going is waited for; a missing one is a refusal that
    says how to start it."""
    deadline = clock() + timeout_minutes * 60
    found = find_run(sha=sha, run=run)
    while verdict(found) == PENDING:
        if not wait or clock() >= deadline:
            return False, f"full run {found['databaseId']} for {sha[:10]} is still {found['status']}: {found['url']}"
        say(f"waiting for full run {found['databaseId']} ({found['status']}) {found['url']}")
        sleep(poll_seconds)
        found = find_run(sha=sha, run=run)
    state = verdict(found)
    if state == PASSED:
        return True, f"full run passed for {sha[:10]}: {found['url']}"
    if state == MISSING:
        return False, (f"no full run on GitHub for {sha[:10]}. Push this commit to main "
                       f"(a push starts the run), or `gh workflow run {WORKFLOW} --ref <branch>`, "
                       "then release again")
    return False, (f"the full run for {sha[:10]} {found['conclusion']}: {found['url']}\n"
                   f"`uv run python tools/full_run.py failures --sha {sha}` names the tests")


# --- artifacts ----------------------------------------------------------------

def nodeid_of(classname: str, name: str, root: Path = ROOT) -> str:
    """JUnit's dotted classname back to a pytest nodeid: the longest prefix
    that is a file, then any class names. xdist's `@<worker group>` suffix is
    dropped: a test's shard unit and rerun command use the plain nodeid."""
    name = name.rsplit("@", 1)[0]
    parts = classname.split(".")
    for cut in range(len(parts), 0, -1):
        path = "/".join(parts[:cut]) + ".py"
        if (root / path).is_file():
            return "::".join([path, *parts[cut:], name])
    return "::".join([classname.replace(".", "/") + ".py", name])


def junit_cases(folder: Path) -> list[dict]:
    """Every case of the suite steps' reports (`junit-*.xml`); a retry's own
    report (`retry-*.xml`) is read by the retry, never counted twice."""
    return [case for report in sorted(folder.rglob("junit-*.xml")) for case in junit_cases_in(report)]


def junit_cases_in(report: Path) -> list[dict]:
    try:
        tree = ET.parse(report)
    except (ET.ParseError, OSError):
        return []
    cases = []
    for case in tree.iter("testcase"):
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        message = "" if problem is None else (problem.get("message") or problem.text or "")
        cases.append({"nodeid": nodeid_of(case.get("classname", ""), case.get("name", "")),
                      "time": float(case.get("time") or 0),
                      "outcome": "passed" if problem is None else problem.tag,
                      "skipped": case.find("skipped") is not None,
                      "first_line": message.strip().splitlines()[0] if message.strip() else ""})
    return cases


def download(found: dict, run=gh) -> Path:
    target = DOWNLOADS / str(found["databaseId"])
    if not target.exists():
        target.mkdir(parents=True)
        run("run", "download", str(found["databaseId"]), "--dir", str(target))
    return target


def failures_report(folder: Path) -> list[str]:
    lines, failed = [], []
    flaky = flaky_rows(folder)
    passed_on_retry = {row["nodeid"] for row in flaky}
    for case in junit_cases(folder):
        if case["outcome"] in ("failure", "error") and case["nodeid"] not in passed_on_retry:
            lines.append(f"  {case['outcome'].upper()} {case['nodeid']} -- {case['first_line']}")
            failed.append(case["nodeid"])
    lines += [f"  FLAKY {row['nodeid']} -- {row['first_line']}" for row in flaky]
    for skips in sorted(folder.rglob("undocumented-skips.json")):
        for row in json.loads(skips.read_text(encoding="utf-8")):
            lines.append(f"  UNDOCUMENTED SKIP {row['nodeid']} -- {row['reason'][:120]}")
    if failed:
        lines.append(rerun_command(failed))
    return lines


def rerun_command(nodeids: list[str]) -> str:
    """Exactly these tests, here: a focused run, which never queues."""
    return "rerun: uv run python tools/run_tests.py " + " ".join(f'"{n}"' for n in dict.fromkeys(nodeids))


# --- the full run's one retry ------------------------------------------------
# A job's suite runs with no retries. If it fails, exactly the tests that
# failed run again ALONE, one at a time, after the suite has finished: a test
# that fails only under the job's load passes there and the job is green,
# with that test reported FLAKY; a real break fails twice and stays red.
# Planner decision 2026-09-22, after four runs in a row (35684991413 to
# 35687167501) each went red on ONE different browser wait, about one in 700
# per run, which the old list of retryable setup errors did not cover.
RETRY_LIMIT = 20   # more failures than this in one job is a break, not flakiness
FLAKY_FILE = "flaky.json"
FLAKY_HISTORY = 10
NEEDS_A_FIX = 2


def _run_focused(nodeids: list[str], junit: Path) -> int:
    """Exactly these tests through the runner's focused door: no queue, one
    worker, no retry of its own."""
    return subprocess.run([sys.executable, str(ROOT / "tools" / "run_tests.py"), *nodeids,
                           "--junitxml", str(junit)], cwd=ROOT, check=False).returncode


def retry_failures(junit: Path, *, run_focused=_run_focused, say=print,
                   summary: str | None = None) -> int:
    """A job's one retry, read from its suite's JUnit report. 0 when every
    failed test passed alone (those go to flaky.json beside the report, the
    job summary and a warning annotation); 1 otherwise."""
    cases = [case for case in junit_cases_in(junit) if case["outcome"] in ("failure", "error")]
    failed = list(dict.fromkeys(case["nodeid"] for case in cases))
    if not failed:
        say("retry: the suite failed with no failing test in its report (an undocumented skip, "
            "a collection error or a timeout): nothing to retry; read the suite step's log")
        return 1
    if len(failed) > RETRY_LIMIT:
        say(f"retry: {len(failed)} tests failed, more than {RETRY_LIMIT}: a break, not flakiness")
        return 1
    say(f"retry: {len(failed)} failed test(s) again, alone, after the suite")
    retried = junit.with_name(junit.name.replace("junit-", "retry-", 1))
    code = run_focused(failed, retried)
    after = {case["nodeid"]: case for case in junit_cases_in(retried)}
    first_line = {case["nodeid"]: case["first_line"] for case in cases}
    flaky = [{"nodeid": nodeid, "first_line": first_line[nodeid]} for nodeid in failed
             if after.get(nodeid, {}).get("outcome") == "passed" and not after[nodeid]["skipped"]]
    if flaky:
        (junit.parent / FLAKY_FILE).write_bytes(json.dumps(flaky, indent=1).encode("utf-8"))
        lines = [f"FLAKY {row['nodeid']} -- failed in the suite, passed alone: {row['first_line']}"
                 for row in flaky]
        for line in lines:
            say(f"::warning title=FLAKY::{line}")
        summary = summary or os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as out:
                out.write("".join(f"- {line}\n" for line in lines))
    still = [nodeid for nodeid in failed if nodeid not in {row["nodeid"] for row in flaky}]
    for nodeid in still:
        say(f"retry: FAILED TWICE {nodeid}")
    return 0 if code == 0 and not still else 1


def flaky_rows(folder: Path) -> list[dict]:
    """One row per test: a run's whole download holds each job's list twice
    (in its report artifact and its own `flaky-*` one)."""
    rows = {row["nodeid"]: row for path in sorted(folder.rglob(FLAKY_FILE))
            for row in json.loads(path.read_text(encoding="utf-8"))}
    return list(rows.values())


def flaky_of(run_id: int, run=gh) -> list[dict]:
    """The run's FLAKY tests, from its small `flaky-*` artifacts (only a job
    that needed the retry uploads one). Cached per run once it is known."""
    folder = DOWNLOADS / str(run_id) / "flaky"
    if not folder.exists():
        folder.mkdir(parents=True)
        try:
            run("run", "download", str(run_id), "--dir", str(folder), "--pattern", "flaky-*")
        except GhUnavailable as error:
            if "no artifact matches" not in str(error):
                folder.rmdir()   # not known yet: ask again next time
                return []
    return flaky_rows(folder)


def flaky_report(found: dict, run=gh) -> list[str]:
    """This run's FLAKY tests, then every test FLAKY in NEEDS_A_FIX or more of
    the branch's last FLAKY_HISTORY completed runs: a retry that keeps being
    needed is a defect, not weather."""
    lines = [f"  FLAKY {row['nodeid']} -- {row['first_line']}" for row in flaky_of(found["databaseId"], run)]
    runs = json.loads(run("run", "list", "--workflow", WORKFLOW, "--branch", found["headBranch"],
                          "--status", "completed", "--json", "databaseId,createdAt",
                          "--limit", str(FLAKY_HISTORY)) or "[]")
    seen = collections.Counter(nodeid for item in runs for nodeid in
                               {row["nodeid"] for row in flaky_of(item["databaseId"], run)})
    lines += [f"  NEEDS A FIX {nodeid} -- FLAKY in {count} of the last {len(runs)} runs on "
              f"{found['headBranch']}" for nodeid, count in sorted(seen.items()) if count >= NEEDS_A_FIX]
    return lines


# --- the coverage map the full run publishes -------------------------------------

def export_coverage(testmon_db: Path, target: Path, label: str) -> int:
    """One job's .testmondata as the portable map tools/blast_radius.py reads:
    test names plus, per source file, the tests that executed it. Relative
    paths and no environment, so a map made on a runner serves any checkout."""
    from blast_radius import coverage_from_testmon
    covered = coverage_from_testmon(testmon_db)
    tests = sorted({test for entries in covered.values() for _, names in entries for test in names})
    index = {test: position for position, test in enumerate(tests)}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(json.dumps({
        "source": label, "tests": tests,
        # path -> [[checksums of the executed blocks], [the tests that executed exactly those]]
        "files": {path: [[list(checksums), sorted(index[t] for t in names)] for checksums, names in entries]
                  for path, entries in sorted(covered.items())},
    }, separators=(",", ":")).encode("utf-8"))
    return len(tests)


def newest_coverage_map(run=gh, tries: int = 3) -> tuple[Path, int] | None:
    """The newest map main published: the nightly run records one (a push
    does not; recording doubles a job's time). Any conclusion will do -- a
    red test still recorded what it executed."""
    runs = json.loads(run("run", "list", "--workflow", WORKFLOW, "--branch", "main",
                          "--status", "completed", "--json", "databaseId,event,createdAt",
                          "--limit", "30") or "[]")
    candidates = [item for item in sorted(runs, key=lambda item: item["createdAt"], reverse=True)
                  if item["event"] in ("schedule", "workflow_dispatch")]
    for item in candidates[:tries]:
        found = coverage_map(item["databaseId"], run=run)
        if found is not None:
            return found, item["databaseId"]
    return None


def coverage_map(run_id: int, run=gh) -> Path | None:
    """The run's jobs' map parts merged into one file (cached per run), or
    None when that run published none."""
    folder = DOWNLOADS / str(run_id)
    merged = folder / "coverage-map.json"
    if merged.is_file():
        return merged
    parts_folder = folder / "coverage"
    if not parts_folder.exists():
        parts_folder.mkdir(parents=True)
        try:
            run("run", "download", str(run_id), "--dir", str(parts_folder), "--pattern", "coverage-*")
        except GhUnavailable:
            return None
    tests: list[str] = []
    files: dict[str, list] = {}
    for part in sorted(parts_folder.rglob("coverage-map-*.json")):
        data = json.loads(part.read_text(encoding="utf-8"))
        offset = len(tests)
        tests += data["tests"]
        for path, entries in data["files"].items():
            files.setdefault(path, []).extend(
                [checksums, [offset + i for i in indexes]] for checksums, indexes in entries)
    if not tests:
        return None
    merged.write_bytes(json.dumps({"source": f"full run {run_id}", "tests": tests, "files": files},
                                  separators=(",", ":")).encode("utf-8"))
    return merged


def durations_from(folder: Path, root: Path = ROOT) -> dict[str, float]:
    """Seconds per shard unit. A file's own total this run decides whether
    it is timed whole or as its tests, as the next run will split it."""
    cases = list(junit_cases(folder))
    totals = file_totals({case["nodeid"]: case["time"] for case in cases})
    units: dict[str, float] = {}
    for case in cases:
        unit = shard_unit(case["nodeid"], totals, root)
        units[unit] = units.get(unit, 0.0) + case["time"]
    return units


def write_durations(measured: dict[str, float], found: dict, path: Path = DURATIONS_PATH,
                    root: Path = ROOT) -> None:
    """Measured files replace every entry they had (whole or as tests); a file
    this run did not time keeps its old ones, unless it no longer exists."""
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))["units"]
    except (OSError, ValueError, KeyError):
        existing = {}
    measured_files = {unit.split("::")[0] for unit in measured}
    kept = {unit: seconds for unit, seconds in existing.items()
            if (file := unit.split("::")[0]) not in measured_files and (root / file).is_file()}
    units = {**kept, **{unit: round(seconds, 1) for unit, seconds in measured.items()}}
    text = json.dumps({
        "source": (f"GitHub full run {found['databaseId']} on {found['headSha'][:10]}, "
                   f"{datetime.now(timezone.utc):%Y-%m-%d}; refresh with tools/full_run.py durations"),
        "units": dict(sorted(units.items()))}, indent=1) + "\n"
    path.write_bytes(text.encode("utf-8"))   # LF: it is tracked (tests/test_line_endings.py)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("status", "wait", "failures", "durations",
                                            "export-coverage", "retry"))
    parser.add_argument("paths", nargs="*", help="export-coverage: <.testmondata> <map.json> <label>; "
                                                 "retry: <the suite's junit.xml>")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--sha")
    target.add_argument("--ref")
    target.add_argument("--run")
    parser.add_argument("--timeout-minutes", type=float, default=60)
    args = parser.parse_args(argv)
    if args.command == "export-coverage":
        db, target, label = args.paths
        print(f"coverage map: {export_coverage(Path(db), Path(target), label)} tests -> {target}")
        return 0
    if args.command == "retry":
        return retry_failures(Path(args.paths[0]))
    sha = args.sha or (None if args.ref or args.run else head_sha())
    label = args.ref or args.run or sha[:10]
    try:
        found = find_run(sha=sha, ref=args.ref, run_id=args.run)
        if args.command == "wait":
            started = time.monotonic()
            # A push registers its run within seconds; give a missing one two minutes.
            while (verdict(found) == PENDING or (found is None and time.monotonic() - started < 120)) \
                    and time.monotonic() - started < args.timeout_minutes * 60:
                time.sleep(30)
                found = find_run(sha=sha, ref=args.ref,
                                 run_id=args.run or (found["databaseId"] if found else None))
        state = verdict(found)
        jobs = jobs_of(found) if found else []
        print(describe(found, jobs, label))
        if found and state in (PASSED, FAILED) and args.command in ("status", "wait"):
            print("\n".join(flaky_report(found)) or "  no FLAKY test in this run or its branch's recent runs")
        if args.command == "failures" and state == FAILED:
            folder = download(found)
            print("\n".join(failures_report(folder)) or "  no failing test in the JUnit reports: "
                  "a job failed before or after pytest -- read its log with gh run view --log-failed")
            print(f"artifacts: {folder}")
        if args.command == "durations" and found:
            folder = download(found)
            measured = durations_from(folder)
            write_durations(measured, found)
            print(f"{len(measured)} units timed; wrote {DURATIONS_PATH.relative_to(ROOT)}")
            return 0  # timings are worth keeping from a red run too
    except GhUnavailable as error:
        print(f"full_run: {error}", file=sys.stderr)
        return 3
    return {PASSED: 0, FAILED: 1}.get(state, 2)


if __name__ == "__main__":
    sys.exit(main())
