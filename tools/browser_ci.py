"""Read the browser run on GitHub in a few lines instead of a raw log.

    uv run python tools/browser_ci.py status    [--sha SHA | --ref BRANCH]
    uv run python tools/browser_ci.py wait      [--sha SHA | --ref BRANCH] [--timeout-minutes 60]
    uv run python tools/browser_ci.py failures  [--sha SHA | --ref BRANCH | --run ID]
    uv run python tools/browser_ci.py durations [--sha SHA | --ref BRANCH | --run ID]

The browser run is the half of the suite that starts a UI fixture server or a
browser (tools/test_lanes.py). It runs on GitHub Actions on every push to
main and on demand (`gh workflow run browser.yml --ref <branch>`), split into
jobs, and gates a release (tools/release.py). With no target, these read the
newest run for this checkout's HEAD commit.

`failures` downloads the run's artifacts (JUnit XML per job, the rerun list)
to a temp folder and prints only each failing test with its first error line.
`durations` rewrites tests/browser_durations.json from a run's JUnit times,
which is what balances the jobs. Exit codes: 0 passed, 1 failed, 2 no run or
still running (status/wait), 3 `gh` unavailable.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from test_lanes import DURATIONS_PATH, shard_unit  # noqa: E402

WORKFLOW = "browser.yml"
RUN_FIELDS = "databaseId,status,conclusion,headSha,headBranch,event,createdAt,updatedAt,url"
DOWNLOADS = Path(tempfile.gettempdir()) / "sm64-browser-run"
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
    """The newest browser run for a commit or branch (a re-run of the same
    commit supersedes an older verdict), or the named run."""
    if run_id:
        return json.loads(run("run", "view", str(run_id), "--json", RUN_FIELDS))
    query = ["run", "list", "--workflow", WORKFLOW, "--json", RUN_FIELDS, "--limit", "20"]
    query += ["--commit", sha] if sha else ["--branch", ref] if ref else []
    runs = json.loads(run(*query) or "[]")
    return max(runs, key=lambda item: item["createdAt"]) if runs else None


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
        return (f"no browser run for {target or 'this commit'}. A push to main starts one; "
                f"for a branch: gh workflow run {WORKFLOW} --ref <branch>")
    state = verdict(found)
    head = (f"browser run {found['databaseId']} for {found['headSha'][:10]} "
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
                 "`tools/browser_ci.py failures` names the tests")
    lines = [head, found["url"]]
    lines += [f"  {job['name']}: {job.get('conclusion')} "
              f"({_minutes(job.get('startedAt'), job.get('completedAt'))})" for job in bad]
    return "\n".join(lines)


def jobs_of(found: dict, run=gh) -> list[dict]:
    return json.loads(run("run", "view", str(found["databaseId"]), "--json", "jobs"))["jobs"]


def release_gate(sha: str, *, wait: bool = True, timeout_minutes: float = 60,
                 poll_seconds: float = 30, run=gh, sleep=time.sleep,
                 clock=time.monotonic, say=print) -> tuple[bool, str]:
    """(allowed, why) for releasing `sha`: its newest browser run must have
    passed. A run still going is waited for; a missing one is a refusal that
    says how to start it."""
    deadline = clock() + timeout_minutes * 60
    found = find_run(sha=sha, run=run)
    while verdict(found) == PENDING:
        if not wait or clock() >= deadline:
            return False, f"browser run {found['databaseId']} for {sha[:10]} is still {found['status']}: {found['url']}"
        say(f"waiting for browser run {found['databaseId']} ({found['status']}) {found['url']}")
        sleep(poll_seconds)
        found = find_run(sha=sha, run=run)
    state = verdict(found)
    if state == PASSED:
        return True, f"browser run passed for {sha[:10]}: {found['url']}"
    if state == MISSING:
        return False, (f"no browser run on GitHub for {sha[:10]}. Push this commit to main "
                       f"(a push starts the run), or `gh workflow run {WORKFLOW} --ref <branch>`, "
                       "then release again")
    return False, (f"the browser run for {sha[:10]} {found['conclusion']}: {found['url']}\n"
                   f"`uv run python tools/browser_ci.py failures --sha {sha}` names the tests")


# --- artifacts ----------------------------------------------------------------

def nodeid_of(classname: str, name: str, root: Path = ROOT) -> str:
    """JUnit's dotted classname back to a pytest nodeid: the longest prefix
    that is a file, then any class names."""
    parts = classname.split(".")
    for cut in range(len(parts), 0, -1):
        path = "/".join(parts[:cut]) + ".py"
        if (root / path).is_file():
            return "::".join([path, *parts[cut:], name])
    return "::".join([classname.replace(".", "/") + ".py", name])


def junit_cases(folder: Path) -> list[dict]:
    cases = []
    for report in sorted(folder.rglob("*.xml")):
        try:
            tree = ET.parse(report)
        except ET.ParseError:
            continue
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
    lines = []
    for case in junit_cases(folder):
        if case["outcome"] in ("failure", "error"):
            lines.append(f"  {case['outcome'].upper()} {case['nodeid']} -- {case['first_line']}")
    for reruns in sorted(folder.rglob("reruns.json")):
        for row in json.loads(reruns.read_text(encoding="utf-8")):
            if row["flaky"]:
                lines.append(f"  FLAKY {row['nodeid']} -- {row['cause']}")
    return lines


def durations_from(folder: Path) -> dict[str, float]:
    units: dict[str, float] = {}
    for case in junit_cases(folder):
        unit = shard_unit(case["nodeid"])
        units[unit] = units.get(unit, 0.0) + case["time"]
    return units


def write_durations(measured: dict[str, float], found: dict, path: Path = DURATIONS_PATH) -> None:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))["units"]
    except (OSError, ValueError, KeyError):
        existing = {}
    units = {**existing, **{unit: round(seconds, 1) for unit, seconds in measured.items()}}
    path.write_text(json.dumps({
        "source": (f"GitHub browser run {found['databaseId']} on {found['headSha'][:10]}, "
                   f"{datetime.now(timezone.utc):%Y-%m-%d}; refresh with tools/browser_ci.py durations"),
        "units": dict(sorted(units.items()))}, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("status", "wait", "failures", "durations"))
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--sha")
    target.add_argument("--ref")
    target.add_argument("--run")
    parser.add_argument("--timeout-minutes", type=float, default=60)
    args = parser.parse_args(argv)
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
    except GhUnavailable as error:
        print(f"browser_ci: {error}", file=sys.stderr)
        return 3
    return {PASSED: 0, FAILED: 1}.get(state, 2)


if __name__ == "__main__":
    sys.exit(main())
