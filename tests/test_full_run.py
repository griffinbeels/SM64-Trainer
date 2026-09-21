"""Reading the GitHub full run, and the release gate built on it, with `gh`
stubbed. The real `gh` is exercised by dispatching the workflow; these pin
the decisions an agent and a release act on."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import full_run  # noqa: E402

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def _run(status="completed", conclusion="success", created="2026-09-21T10:00:00Z", run_id=7):
    return {"databaseId": run_id, "status": status, "conclusion": conclusion, "headSha": SHA,
            "headBranch": "main", "event": "push", "createdAt": created,
            "updatedAt": "2026-09-21T10:18:30Z", "url": f"https://github.com/x/y/actions/runs/{run_id}"}


class Gh:
    """Answers `gh run list` from a queue of snapshots, one per call."""

    def __init__(self, *snapshots):
        self.snapshots = list(snapshots)
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("run", "list"):
            current = self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]
            return json.dumps(current)
        raise AssertionError(f"unexpected gh call {args}")


def test_the_newest_run_for_a_commit_is_the_verdict():
    """A re-run supersedes the red run before it."""
    gh = Gh([_run(conclusion="failure", created="2026-09-21T09:00:00Z", run_id=1),
             _run(created="2026-09-21T10:00:00Z", run_id=2)])
    found = full_run.find_run(sha=SHA, run=gh)
    assert found["databaseId"] == 2 and full_run.verdict(found) == full_run.PASSED
    assert ("--commit", SHA) == gh.calls[0][-2:]


def test_a_release_proceeds_on_a_green_run():
    allowed, why = full_run.release_gate(SHA, run=Gh([_run()]), sleep=lambda _: None)
    assert allowed and "passed" in why


def test_a_release_refuses_a_red_run_and_says_how_to_read_it():
    allowed, why = full_run.release_gate(SHA, run=Gh([_run(conclusion="failure")]),
                                           sleep=lambda _: None)
    assert not allowed
    assert "failures --sha" in why and SHA[:10] in why


def test_a_release_refuses_a_commit_github_never_ran_and_says_how_to_start_one():
    allowed, why = full_run.release_gate(SHA, run=Gh([]), sleep=lambda _: None)
    assert not allowed and "Push this commit to main" in why


def test_a_release_waits_for_a_run_still_going_then_takes_its_verdict():
    gh = Gh([_run(status="in_progress", conclusion="")], [_run(status="queued", conclusion="")],
            [_run()])
    waits = []
    allowed, _ = full_run.release_gate(SHA, run=gh, sleep=waits.append, say=lambda _: None)
    assert allowed and len(waits) == 2


def test_a_run_that_never_finishes_is_a_refusal_not_a_hang():
    clock = iter(range(0, 10_000, 600))
    allowed, why = full_run.release_gate(
        SHA, run=Gh([_run(status="in_progress", conclusion="")]), timeout_minutes=30,
        sleep=lambda _: None, clock=lambda: next(clock), say=lambda _: None)
    assert not allowed and "still in_progress" in why


def test_no_wait_asked_means_no_wait():
    allowed, why = full_run.release_gate(
        SHA, wait=False, run=Gh([_run(status="in_progress", conclusion="")]),
        sleep=lambda _: pytest.fail("slept"))
    assert not allowed and "still" in why


def test_the_status_line_is_short_and_names_only_the_jobs_that_are_not_green():
    jobs = [{"name": f"browser {n}/3", "status": "completed",
             "conclusion": "failure" if n == 2 else "success",
             "startedAt": "2026-09-21T10:00:00Z", "completedAt": "2026-09-21T10:12:05Z"}
            for n in (1, 2, 3)]
    text = full_run.describe(_run(conclusion="failure"), jobs)
    lines = text.splitlines()
    assert len(lines) == 3, text
    assert "1 of 3 jobs not green" in lines[0] and "browser 2/3: failure (12m05s)" in lines[2]
    assert "no full run" in full_run.describe(None, [], "abc")


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="3">
 <testcase classname="tests.test_ui_x" name="test_ok" time="4.5"/>
 <testcase classname="tests.test_ui_x" name="test_bad[900x1000]" time="2.0">
  <failure message="AssertionError: the card clipped&#10;second line">long trace</failure></testcase>
 <testcase classname="tests.test_responsive" name="test_no_layout_defects_at_each_viewport[850x1000]@browser_sweep_2" time="30.0"/>
</testsuite></testsuites>
"""


def test_failures_name_the_test_and_its_first_error_line_and_the_flaky_ones(tmp_path):
    (tmp_path / "browser-1").mkdir()
    (tmp_path / "browser-1" / "junit-1.xml").write_text(JUNIT, encoding="utf-8")
    (tmp_path / "browser-1" / "reruns.json").write_text(json.dumps([
        {"nodeid": "tests/test_ui_y.py::test_z", "cause": "fixture server failed to start",
         "flaky": True}]), encoding="utf-8")
    lines = full_run.failures_report(tmp_path)
    assert lines == [
        "  FAILURE tests/test_ui_x.py::test_bad[900x1000] -- AssertionError: the card clipped",
        "  FLAKY tests/test_ui_y.py::test_z -- fixture server failed to start",
        'rerun: uv run python tools/run_tests.py "tests/test_ui_x.py::test_bad[900x1000]"']


def test_junit_names_map_back_to_nodeids_and_units_for_rebalancing(tmp_path):
    (tmp_path / "junit-1.xml").write_text(JUNIT, encoding="utf-8")
    sweep = "tests/test_responsive.py::test_no_layout_defects_at_each_viewport[850x1000]"
    assert full_run.nodeid_of("tests.test_responsive",
                                "test_no_layout_defects_at_each_viewport[850x1000]") == sweep
    # xdist names the case `<name>@<worker group>` in JUnit; the unit is a
    # hash of the PLAIN nodeid, so the suffix must go before hashing.
    assert full_run.nodeid_of("tests.test_responsive",
                                "test_no_layout_defects_at_each_viewport[850x1000]@browser_sweep_2") == sweep
    units = full_run.durations_from(tmp_path)
    assert units["tests/test_ui_x.py"] == 6.5
    assert units[full_run.shard_unit(sweep)] == 30.0


def test_a_refresh_keeps_units_this_run_did_not_time(tmp_path):
    path = tmp_path / "durations.json"
    path.write_text(json.dumps({"source": "old", "units": {"tests/a.py": 10.0, "tests/b.py": 20.0}}))
    full_run.write_durations({"tests/b.py": 25.04}, _run(), path)
    written = json.loads(path.read_text())
    assert written["units"] == {"tests/a.py": 10.0, "tests/b.py": 25.0}
    assert b"\r\n" not in path.read_bytes(), "a tracked file stays LF on Windows"
    assert "7" in written["source"]


def test_the_baseline_candidates_are_green_runs_on_main_newest_first():
    listing = [{"headSha": "old", "databaseId": 1, "createdAt": "2026-09-20T10:00:00Z"},
               {"headSha": "new", "databaseId": 2, "createdAt": "2026-09-21T10:00:00Z"}]
    calls = []

    def gh(*args):
        calls.append(args)
        return json.dumps(listing)

    assert full_run.green_runs_on_main(run=gh) == [("new", 2), ("old", 1)]
    query = calls[0]
    assert query[query.index("--branch") + 1] == "main"
    assert query[query.index("--status") + 1] == "success"


def test_a_jobs_coverage_parts_merge_into_one_map(tmp_path, monkeypatch):
    """Each job publishes the map of the tests IT ran; the reader joins them
    without re-numbering collisions."""
    parts = tmp_path / "7" / "coverage" / "coverage-1"
    parts.mkdir(parents=True)
    (parts / "coverage-map-1.json").write_text(json.dumps(
        {"tests": ["tests/test_a.py::t"], "files": {"src/x.py": [0]}}))
    (parts / "coverage-map-2.json").write_text(json.dumps(
        {"tests": ["tests/test_b.py::t"], "files": {"src/x.py": [0], "src/y.py": [0]}}))
    monkeypatch.setattr(full_run, "DOWNLOADS", tmp_path)
    merged = json.loads(full_run.coverage_map(7, run=lambda *a: pytest.fail("cached")).read_text())
    tests = merged["tests"]
    assert {tests[i] for i in merged["files"]["src/x.py"]} == {"tests/test_a.py::t", "tests/test_b.py::t"}
    assert [tests[i] for i in merged["files"]["src/y.py"]] == ["tests/test_b.py::t"]
