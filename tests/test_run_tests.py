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
        # `--reruns` retries only failed tests, so a load-contention browser
        # flake does not redden the merge gate while a real failure still does.
        assert "--reruns" in args
    assert "--testmon-noselect" in full and "--testmon" not in full
    assert "--testmon" in select and select[-1] == "-x"


def _rec(name, created, parent=None, cmd=(), cwd=None):
    return {"name": name, "pid": 1, "cmdline": list(cmd), "create_time": created,
            "cwd": cwd, "parent": parent}


def test_only_orphans_are_strays_and_a_live_siblings_run_is_never_touched(tmp_path):
    """A live parent means somebody owns it -- another session's run, his own
    browser. A dead parent, or a parent pid reused by a younger process, means
    nobody does."""
    live_parent = {"create_time": 100.0}
    reused_pid = {"create_time": 900.0}
    procs = [
        _rec("chrome.exe", 500.0, live_parent, ["chrome", "--headless"]),          # sibling's
        _rec("chrome.exe", 500.0, None, ["chrome", "--headless"]),                 # orphan
        _rec("chrome.exe", 500.0, None, ["chrome", "--type=renderer"]),            # his browser, not headless
        _rec("chrome.exe", 500.0, reused_pid, ["chrome", "--headless"]),           # orphan via pid reuse
        _rec("ffmpeg.exe", 500.0, None),                                           # orphan
        _rec("ffmpeg.exe", 500.0, live_parent),                                    # OBS's
        _rec("python.exe", 500.0, None, cwd=str(tmp_path / "tests")),              # our dead worker
        _rec("python.exe", 500.0, None, cwd=str(tmp_path.parent / "elsewhere")),   # another repo's
        _rec("python.exe", 500.0, live_parent, cwd=str(tmp_path)),                 # sibling's live worker
    ]
    strays = run_tests.stray_processes(procs, tmp_path)
    assert [(s["name"], s["create_time"], s["parent"]) for s in strays] == [
        ("chrome.exe", 500.0, None), ("chrome.exe", 500.0, reused_pid),
        ("ffmpeg.exe", 500.0, None), ("python.exe", 500.0, None)]


def test_a_profile_dir_named_by_a_live_browser_is_kept(tmp_path):
    kept = tmp_path / "playwright_chromiumdev_profile-AAA"
    orphan = tmp_path / "playwright_chromiumdev_profile-BBB"
    other = tmp_path / "somebody_elses_dir"
    live = [f"chrome.exe --headless --user-data-dir={kept}"]
    assert run_tests.stray_profile_dirs([kept, orphan, other], live) == [orphan]


def test_only_a_completed_unfiltered_full_run_is_recorded():
    """A run narrowed by `-k` or a path never covered the rest, and an
    interrupted one (pytest exit 2) left the map half-written."""
    assert run_tests.records_full_run("full", [], 0)
    assert run_tests.records_full_run("full", [], 1)
    assert not run_tests.records_full_run("full", ["-k", "rank"], 0)
    assert not run_tests.records_full_run("full", ["tests/test_api.py"], 0)
    assert not run_tests.records_full_run("full", [], 2)
    assert not run_tests.records_full_run("select", [], 0)
