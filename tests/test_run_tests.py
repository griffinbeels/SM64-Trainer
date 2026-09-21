"""The one door to the suite: which mode it picks, and what it hands pytest.

`tools/run_tests.py` is the merge check AND the inner loop. The rule that
matters most is the refusal: `--changed` may only select when nothing
testmon is blind to has changed, because a JS edit that selects zero tests
reads as green. These pin that rule on the pure functions; the git listing
and the pytest call are the two seams left to the real thing.
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import run_tests  # noqa: E402


def test_a_checkout_with_no_record_runs_everything():
    mode, why = run_tests.decide(None, {"a.js": "1"})
    assert mode == "merge"
    assert "no record" in why


def test_only_python_changes_let_testmon_select():
    saved = {"ui/x.js": "aaa", "docs/y.md": "bbb"}
    mode, why = run_tests.decide(saved, dict(saved))
    assert mode == "select"
    assert "testmon" in why


def test_a_non_python_change_forces_the_full_run_and_names_the_file():
    saved = {"ui/x.js": "aaa", "docs/y.md": "bbb"}
    mode, why = run_tests.decide(saved, {"ui/x.js": "CHANGED", "docs/y.md": "bbb"})
    assert mode == "merge"
    assert "ui/x.js" in why and "docs/y.md" not in why


def test_an_added_or_removed_non_python_file_counts_as_a_change():
    saved = {"ui/x.js": "aaa"}
    assert run_tests.decide(saved, {"ui/x.js": "aaa", "ui/new.css": "c"})[0] == "merge"
    assert run_tests.decide(saved, {})[0] == "merge"


def test_the_fingerprint_hashes_content_and_skips_python():
    files = {"ui/x.js": b"one", "src/m.py": b"code", "data/seed.json": b"{}"}
    prints = run_tests.fingerprint(sorted(files), read=files.__getitem__)
    assert set(prints) == {"ui/x.js", "data/seed.json"}
    same = run_tests.fingerprint(sorted(files), read=files.__getitem__)
    assert prints == same
    files["ui/x.js"] = b"two"
    assert run_tests.fingerprint(sorted(files), read=files.__getitem__) != prints


def test_the_doors_own_state_never_enters_the_fingerprint():
    """testmon's SQLite side files (`-wal`, `-shm`) come and go between runs;
    counting them made every `--changed` a full run, forever."""
    files = {".testmondata": b"db", ".testmondata-wal": b"w", ".testmondata-shm": b"s",
             ".run_tests.json": b"{}", "ui/x.js": b"one"}
    assert set(run_tests.fingerprint(sorted(files), read=files.__getitem__)) == {"ui/x.js"}


def test_a_file_that_vanished_between_listing_and_reading_is_skipped():
    def read(path):
        raise FileNotFoundError(path)
    assert run_tests.fingerprint(["gone.js"], read=read) == {}


def test_both_modes_share_the_scheduler_flags():
    """testmon keys its map by nodeid, and under loadgroup the nodeid carries
    the worker group as a suffix -- a merge check and a selecting run on
    different schedulers would never agree on a single test's name. Both stay
    on the merge lane, so `--changed` can never pull a browser test in."""
    merge = run_tests.pytest_args("merge", 8, [])
    select = run_tests.pytest_args("select", 8, ["-x"])
    for args in (merge, select):
        assert args[:4] == ["-n", "8", "--dist", "loadgroup"]
        assert args[args.index("--reruns") + 1] == "0", "failures are not hidden by retries"
        assert args[args.index("--lane") + 1] == "merge"
    assert "--testmon-noselect" in merge and "--testmon" not in merge
    assert "--testmon" in select and select[-1] == "-x"


def test_only_the_browser_lane_retries_and_only_setup_errors():
    browser = run_tests.pytest_args("browser", 2, ["--shard", "1/8"])
    assert browser[browser.index("--lane") + 1] == "browser"
    assert browser[browser.index("--reruns") + 1] == "1"
    assert "--only-rerun" in browser and "--rerun-except" in browser
    assert "--no-testmon" in browser and browser[-2:] == ["--shard", "1/8"]
    for mode in ("merge", "select", "focused"):
        assert "--only-rerun" not in run_tests.pytest_args(mode, 2, [])


def test_only_a_completed_unfiltered_merge_check_is_recorded():
    """A run narrowed by `-k` or a path never covered the rest, and an
    interrupted one (pytest exit 2) left the map half-written."""
    assert run_tests.records_full_run("merge", [], 0)
    assert run_tests.records_full_run("merge", [], 1)
    assert not run_tests.records_full_run("merge", ["-k", "rank"], 0)
    assert not run_tests.records_full_run("merge", ["tests/test_api.py"], 0)
    assert not run_tests.records_full_run("merge", [], 2)
    assert not run_tests.records_full_run("select", [], 0)
    assert not run_tests.records_full_run("focused", [], 0)
    assert not run_tests.records_full_run("browser", [], 0)


def test_an_explicit_scope_skips_fingerprinting_and_coverage(monkeypatch):
    def unwanted():
        raise AssertionError("focused checks must not scan the whole checkout")
    monkeypatch.setattr(run_tests, "git_known_files", unwanted)
    assert run_tests.selection(False, False, ["tests/test_run_tests.py"])[0] == "focused"
    assert run_tests.selection(False, True, [])[0] == "browser"
    flags = run_tests.pytest_args("focused", 0, ["tests/test_run_tests.py"])
    assert "--no-testmon" in flags and "--testmon-noselect" not in flags
    assert flags[flags.index("-n") + 1] == "0"


def test_runner_and_children_ignore_a_foreign_editable_checkout(tmp_path):
    import json
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


@pytest.mark.parametrize("argv", [["--shard", "1/4"], ["--browser", "tests/test_x.py"],
                                  ["--browser", "--changed"], ["--browser", "--shard", "5/4"],
                                  ["--changed", "tests/test_x.py"]])
def test_the_door_refuses_a_contradictory_request(argv):
    with pytest.raises(SystemExit) as error:
        run_tests.main([*argv, "--dry-run"])
    assert error.value.code == 2


class _Queued:
    """A lease that spends its first 3 seconds waiting for a slot."""

    def __init__(self, workers, reserve, admit):
        self.workers = 0

    def __enter__(self):
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


def test_the_browser_lane_refuses_to_run_without_uilab(monkeypatch, capsys):
    """A browser lane with no uilab would skip every module and look green."""
    monkeypatch.setattr(run_tests, "find_uilab", lambda: "uilab not found (test)")
    assert run_tests.main(["--browser"]) == 2
    assert "needs uilab" in capsys.readouterr().out
