"""The merge check starts no browser: which lane a module lands in, and the
two guards that keep a browser test from leaking into the local loop.

`tools/test_lanes.py` reads source with `ast` to decide. These tests check
that decision against an INDEPENDENT reading (the names a module's code uses,
by tokenizer rather than syntax tree), and then prove the run-time tripwire
fails a test the classifier could not see, in a real pytest process.
"""
import io
import json
import os
import subprocess
import sys
import textwrap
import tokenize
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from tools import test_lanes as lanes

ROOT = Path(__file__).resolve().parents[1]

# A module whose CODE names one of these starts a browser or the UI fixture
# server, or needs uilab to import. Strings, comments and docstrings do not
# count: "Chromium" in prose is not a launch.
BROWSER_NAMES = {"serve_ui", "serve_ui_live", "uilab", "playwright", "sync_playwright",
                 "get_driver", "uilab_project", "contact_sheet", "profile_browser"}


def _code_names(source: str) -> set[str]:
    return {token.string for token in tokenize.generate_tokens(io.StringIO(source).readline)
            if token.type == tokenize.NAME}


def _lane(source: str) -> str:
    return "browser" if lanes.browser_reason_in_source(textwrap.dedent(source)) else "merge"


@pytest.mark.parametrize("source,lane", [
    ("from uilab.driver import get_driver\n", "browser"),
    ("import playwright.sync_api\n", "browser"),
    ("def test_x():\n    from uilab import driver\n", "browser"),        # inside the test
    ("from ui_fixture import serve_ui\n", "browser"),
    ("import ui_fixture\ndef test_x():\n    ui_fixture.serve_ui_live()\n", "browser"),
    ("from ui_fixture import seed_practice, FIXTURE_COURSE\n", "merge"),  # seed helpers only
    ("from uilab_project import PROJECT\n", "browser"),                   # helper imports uilab
    ("from export_overlay import main\n", "merge"),                       # helper's uilab is lazy
    ("from find_uilab import find_uilab\n", "merge"),
    ("import json\nNOTE = 'uilab playwright serve_ui'\n", "merge"),
])
def test_a_module_lands_in_the_lane_its_imports_say(source, lane):
    assert _lane(source) == lane


def test_no_module_that_names_a_browser_entry_point_leaks_into_the_merge_check():
    """THE guard. Every test module whose code names a browser or fixture-server
    entry point must be in the browser lane. The two readings are independent
    -- tokens here, the syntax tree in the classifier -- so a classifier that
    stops seeing an import pattern goes red here instead of quietly putting
    Chromium back into the local loop."""
    leaks = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        named = _code_names(source) & BROWSER_NAMES
        if named and path.name != Path(__file__).name and lanes.lane_of(path) != "browser":
            leaks.append(f"{path.name}: names {sorted(named)}")
    assert not leaks, "browser entry points in merge-check modules:\n" + "\n".join(leaks)


def test_the_browser_lane_is_not_everything():
    """The other failure: a classifier that says `browser` for everything would
    pass the guard above and empty the merge check."""
    modules = sorted((ROOT / "tests").glob("test_*.py"))
    browser = [path for path in modules if lanes.lane_of(path) == "browser"]
    assert 50 < len(browser) < len(modules) // 3, len(browser)
    assert lanes.lane_of(Path(__file__)) == "merge"
    assert lanes.lane_of(ROOT / "tests" / "test_ui_leaderboard.py") == "browser"


def test_this_session_holds_only_its_own_lane(request):
    """On the merge check itself, nothing collected may come from a browser
    module -- the collection hook, not just the classifier, is what enforces it."""
    lane = request.config.getoption("lane")
    if lane is None:
        pytest.skip("only meaningful under --lane")
    files = {Path(str(item.path)) for item in request.session.items}
    wrong = sorted(path.name for path in files
                   if lanes.is_test_module(path) and lanes.lane_of(path) != lane)
    assert not wrong, f"--lane {lane} collected: {wrong[:5]}"


LEAKS = '''
import importlib
import sys
sys.path.insert(0, {tools!r})


def test_boots_the_fixture_server_where_the_classifier_cannot_see():
    fixture = importlib.import_module("ui_" + "fixture")
    with getattr(fixture, "serve" + "_ui")():
        pass


def test_launches_chromium_where_the_classifier_cannot_see():
    api = importlib.import_module("play" + "wright.sync_api")
    with api.sync_playwright() as driver:
        driver.chromium.launch()
'''


def test_the_merge_check_tripwire_fails_a_launch_the_classifier_missed(tmp_path):
    """A real pytest process on the merge lane, two tests that reach the fixture
    server and Chromium through importlib. Both must FAIL with the rule, and no
    browser may start: the refusal happens before the launch."""
    planted = tmp_path / "test_planted_leak.py"
    planted.write_text(LEAKS.format(tools=str(ROOT / "tools")), encoding="utf-8")
    assert lanes.lane_of(planted) == "merge", "the plant must evade the classifier"
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "tests"), str(ROOT),
                                                        str(ROOT / "src")])}
    env.pop(lanes.LANE_ENV, None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "conftest", "--lane", "merge", "-q",
         "-p", "no:cacheprovider", "--no-testmon", str(planted),
         "--rootdir", str(tmp_path)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
        **quiet_spawn_kwargs())
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "2 failed" in output, output
    assert "refused to boot the UI fixture server" in output, output
    assert "refused to launch a browser" in output, output


def _durations():
    return {"tests/a.py": 300.0, "tests/b.py": 200.0, "tests/c.py": 100.0,
            "browser_sweep_0": 250.0, "tests/d.py": 50.0}


def test_every_unit_lands_in_exactly_one_job_and_the_jobs_are_balanced():
    units = [*_durations(), "tests/new_file.py"]
    plan = lanes.plan_shards(units, 3, _durations())
    assert set(plan) == set(units)
    assert set(plan.values()) == {1, 2, 3}
    loads = {}
    fallback = lanes.default_duration(_durations())
    for unit, shard in plan.items():
        loads[shard] = loads.get(shard, 0) + _durations().get(unit, fallback)
    # Longest-first onto the least-loaded job: within one unit of perfect.
    assert max(loads.values()) - min(loads.values()) <= max(_durations().values())


def test_the_plan_is_the_same_whatever_order_the_units_arrive_in():
    """Every job and every xdist worker computes the plan independently."""
    units = list(_durations())
    assert lanes.plan_shards(units, 3, _durations()) == \
        lanes.plan_shards(list(reversed(units)), 3, _durations())


def test_a_new_unit_costs_the_median_until_it_has_been_timed():
    assert lanes.default_duration(_durations()) == 200.0
    assert lanes.default_duration({}) > 0


def test_a_sweep_case_travels_with_its_bounded_group_and_a_file_stays_whole():
    sweep = f"{lanes.BROWSER_SWEEPS[0]}::test_no_layout_defects_at_each_viewport[900x1000]"
    assert lanes.shard_unit(sweep) == lanes.browser_sweep_group(sweep)
    assert lanes.shard_unit("tests/test_ui_scorecard.py::test_x[a]") == "tests/test_ui_scorecard.py"


def test_the_recorded_durations_cover_the_browser_lane():
    """Balancing by a stale file is fine; balancing by an EMPTY one is not."""
    recorded = json.loads(lanes.DURATIONS_PATH.read_text(encoding="utf-8"))
    assert recorded["source"] and len(recorded["units"]) > 50


@pytest.mark.parametrize("text", ["0/4", "5/4", "a/b", "3"])
def test_a_malformed_shard_is_refused(text):
    with pytest.raises(ValueError):
        lanes.parse_shard(text)


def test_the_retry_covers_setup_errors_and_never_an_assertion():
    """pytest-rerunfailures matches "<Type>: <message>" against these."""
    import re
    retried = ["RuntimeError: fixture server failed to start after 30.00s",
               "OSError: [WinError 10055] An operation on a socket could not be performed",
               "playwright._impl._errors.Error: net::ERR_NO_BUFFER_SPACE at http://127.0.0.1",
               "Error: Target page, context or browser has been closed"]
    kept = ["AssertionError: fixture server failed to start",
            "TimeoutError: Timeout 30000ms exceeded waiting for .log-card"]
    matches = lambda text: any(re.search(p, text) for p in lanes.SETUP_ERRORS)  # noqa: E731
    excluded = lambda text: any(re.search(p, text) for p in lanes.NEVER_RERUN)  # noqa: E731
    assert all(matches(text) and not excluded(text) for text in retried)
    assert not any(matches(text) and not excluded(text) for text in kept)
    args = lanes.rerun_args()
    assert args[:2] == ["--reruns", "1"] and "--rerun-except" in args
