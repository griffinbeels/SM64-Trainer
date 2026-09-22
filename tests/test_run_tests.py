"""The one door to the suite: which mode it picks, and what it hands pytest.

`tools/run_tests.py` is the merge check (the blast radius of a change), the
inner loop (exactly the tests named) and the full run's worker on GitHub (the
whole suite, one shard at a time). These pin what each mode hands pytest; the
selection itself is tests/test_blast_radius.py's job.
"""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import run_tests  # noqa: E402


def test_every_mode_shares_the_scheduler_flags():
    """One scheduler for the merge check, the inner loop and the full run, so a
    worker group never means one thing locally and another on GitHub."""
    for mode in ("merge", "focused", "all"):
        args = run_tests.pytest_args(mode, 8, [])
        assert args[:4] == ["-n", "8", "--dist", "loadgroup"]


def test_no_run_through_the_runner_retries():
    """A local failure is never hidden by a retry, and the full run's one
    retry is its own step after the suite (tools/full_run.py retry)."""
    for mode in ("merge", "focused", "all"):
        args = run_tests.pytest_args(mode, 2, ["--shard", "1/12"])
        assert args[args.index("--reruns") + 1] == "0"
        assert "--only-rerun" not in args
        assert args[-2:] == ["--shard", "1/12"]


def test_only_a_full_run_that_asks_for_it_records_coverage():
    """pytest-testmon's tracing is the price of the map the blast radius reads;
    only the full run pays it, and only when told to."""
    assert "--testmon-noselect" in run_tests.pytest_args("all", 2, [], record_coverage=True)
    for mode in ("merge", "focused", "all"):
        assert "--no-testmon" in run_tests.pytest_args(mode, 2, [])


@pytest.mark.parametrize("argv", [["--shard", "1/4"], ["--record-coverage"], ["--lane", "browser"],
                                  ["--all", "tests/test_x.py"], ["--all", "--why"],
                                  ["--all", "--shard", "5/4"], ["--why", "tests/test_x.py"],
                                  ["-n", "4", "tests/test_x.py"]])
def test_the_door_refuses_a_contradictory_request(argv):
    with pytest.raises(SystemExit) as error:
        run_tests.main(argv)
    assert error.value.code == 2


class _Queued:
    """A lease that spends its first 3 seconds waiting for a slot."""

    entered = []

    def __init__(self, workers, reserve, admit):
        self.workers = 0
        self.admit = admit

    def __enter__(self):
        _Queued.entered.append(self.admit)
        time.sleep(3.0)
        return self

    def __exit__(self, *exc):
        pass


def _sleeper(monkeypatch, seconds):
    monkeypatch.setattr(run_tests, "TestResources", _Queued)
    monkeypatch.setattr(run_tests, "PYTEST", [sys.executable, "-c", f"import time; time.sleep({seconds})"])


def test_the_time_limit_starts_at_admission_not_in_the_queue(monkeypatch):
    """3 s queued, a 2.4 s limit and 0.3 s of testing: not a timeout."""
    _sleeper(monkeypatch, 0.3)
    assert run_tests.main(["tests/test_x.py", "--limit-minutes", "0.04"]) == 0


def test_a_run_past_its_limit_is_stopped_with_its_whole_tree(monkeypatch, tmp_path):
    """Past the limit the job closes: the child never reaches its next line."""
    marker = tmp_path / "alive"
    code = f"import time, pathlib; time.sleep(3); pathlib.Path({str(marker)!r}).write_text('x')"
    monkeypatch.setattr(run_tests, "TestResources", _Queued)
    monkeypatch.setattr(run_tests, "PYTEST", [sys.executable, "-c", code])
    assert run_tests.main(["tests/test_x.py", "--limit-minutes", "0.01"]) == run_tests.TIMED_OUT
    time.sleep(3.5)
    assert not marker.exists(), "the job must close the child when the limit stops the run"


def test_the_whole_suite_refuses_to_run_without_uilab(monkeypatch, capsys):
    """A whole run with no uilab would skip every browser module and look green."""
    monkeypatch.setattr(run_tests, "find_uilab", lambda: "uilab not found (test)")
    assert run_tests.main(["--all"]) == 2
    assert "needs uilab" in capsys.readouterr().out


def test_an_empty_blast_radius_runs_nothing_and_takes_no_slot(monkeypatch, capsys):
    """A docs-only change owes no local test run; the full run covers it."""
    monkeypatch.setattr(run_tests, "blast_radius", lambda base, why: ({}, "blast radius: 0 test files"))
    monkeypatch.setattr(run_tests, "TestResources", _Queued)
    _Queued.entered.clear()
    assert run_tests.main([]) == 0
    assert _Queued.entered == [], "nothing to run must not queue behind two merge checks"
    assert "nothing in the blast radius" in capsys.readouterr().out


def test_the_merge_check_hands_pytest_exactly_its_radius_and_takes_a_slot(monkeypatch, tmp_path):
    chosen = {"tests/test_a.py": None, "tests/test_b.py": ["tests/test_b.py::test_one"]}
    seen = tmp_path / "seen.json"
    code = ("import sys, pathlib; arg = next(a for a in sys.argv if a.startswith('--select-from=')); "
            f"pathlib.Path({str(seen)!r}).write_text(pathlib.Path(arg.split('=', 1)[1]).read_text())")
    monkeypatch.setattr(run_tests, "blast_radius", lambda base, why: (chosen, "radius"))
    monkeypatch.setattr(run_tests, "TestResources", _Queued)
    monkeypatch.setattr(run_tests, "PYTEST", [sys.executable, "-c", code])
    _Queued.entered.clear()
    assert run_tests.main([]) == 0
    assert _Queued.entered == [True], "a merge check takes one of the two slots"
    assert json.loads(seen.read_text()) == {"files": chosen}


def test_named_tests_never_queue(monkeypatch):
    _sleeper(monkeypatch, 0)
    _Queued.entered.clear()
    assert run_tests.main(["tests/test_x.py::test_y"]) == 0
    assert _Queued.entered == [False]


def test_runner_and_children_ignore_a_foreign_editable_checkout(tmp_path):
    import os
    import subprocess
    from sm64_events.core.childproc import quiet_spawn_kwargs

    foreign = tmp_path / "foreign" / "sm64_events"
    foreign.mkdir(parents=True)
    (foreign / "__init__.py").write_text("raise RuntimeError('wrong checkout')")
    root = Path(run_tests.ROOT)
    code = (
        "import sys,subprocess,json; "
        f"sys.path.insert(0,{str(root / 'tools')!r}); "
        "import run_tests; import sm64_events.replay.ring as ring; "
        "from sm64_events.core.childproc import quiet_spawn_kwargs; "
        "child=subprocess.check_output([sys.executable,'-c',"
        "'import sm64_events.replay.ring as r; print(r.__file__)'],text=True,**quiet_spawn_kwargs()); "
        "print(json.dumps([ring.__file__,child.strip()]))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                            env={**os.environ, "PYTHONPATH": str(foreign.parent)},
                            capture_output=True, text=True, timeout=20,
                            check=True, **quiet_spawn_kwargs())
    paths = json.loads(result.stdout)
    expected = (root / "src/sm64_events/replay/ring.py").resolve()
    assert [Path(path).resolve() for path in paths] == [expected, expected]
