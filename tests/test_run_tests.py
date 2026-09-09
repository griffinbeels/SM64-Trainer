"""The one door to the suite: which mode it picks, and what it hands pytest.

`tools/run_tests.py` is the merge gate AND the inner loop. The rule that
matters most is the refusal: `--changed` may only select when nothing
testmon is blind to has changed, because a JS edit that selects zero tests
reads as green. These pin that rule on the pure functions; the git listing
and the pytest call are the two seams left to the real thing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import run_tests  # noqa: E402


def test_a_checkout_with_no_record_runs_everything():
    mode, why = run_tests.decide(None, {"a.js": "1"})
    assert mode == "full"
    assert "no record" in why


def test_only_python_changes_let_testmon_select():
    saved = {"ui/x.js": "aaa", "docs/y.md": "bbb"}
    mode, why = run_tests.decide(saved, dict(saved))
    assert mode == "select"
    assert "testmon" in why


def test_a_non_python_change_forces_the_full_run_and_names_the_file():
    saved = {"ui/x.js": "aaa", "docs/y.md": "bbb"}
    mode, why = run_tests.decide(saved, {"ui/x.js": "CHANGED", "docs/y.md": "bbb"})
    assert mode == "full"
    assert "ui/x.js" in why and "docs/y.md" not in why


def test_an_added_or_removed_non_python_file_counts_as_a_change():
    saved = {"ui/x.js": "aaa"}
    assert run_tests.decide(saved, {"ui/x.js": "aaa", "ui/new.css": "c"})[0] == "full"
    assert run_tests.decide(saved, {})[0] == "full"


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
    the worker group as a suffix -- a full run and a selecting run on
    different schedulers would never agree on a single test's name."""
    full = run_tests.pytest_args("full", 16, [])
    select = run_tests.pytest_args("select", 16, ["-x"])
    for args in (full, select):
        assert args[:4] == ["-n", "16", "--dist", "loadgroup"]
        assert args[args.index("--reruns") + 1] == "0", "failures are not hidden by retries"
    assert "--testmon-noselect" in full and "--testmon" not in full
    assert "--testmon" in select and select[-1] == "-x"


def test_only_a_completed_unfiltered_full_run_is_recorded():
    """A run narrowed by `-k` or a path never covered the rest, and an
    interrupted one (pytest exit 2) left the map half-written."""
    assert run_tests.records_full_run("full", [], 0)
    assert run_tests.records_full_run("full", [], 1)
    assert not run_tests.records_full_run("full", ["-k", "rank"], 0)
    assert not run_tests.records_full_run("full", ["tests/test_api.py"], 0)
    assert not run_tests.records_full_run("full", [], 2)
    assert not run_tests.records_full_run("select", [], 0)
    assert not run_tests.records_full_run("focused", [], 0)


def test_an_explicit_scope_skips_fingerprinting_and_coverage(monkeypatch):
    def unwanted():
        raise AssertionError("focused checks must not scan the whole checkout")
    monkeypatch.setattr(run_tests, "git_known_files", unwanted)
    assert run_tests.selection(False, ["tests/test_run_tests.py"])[0] == "focused"
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
