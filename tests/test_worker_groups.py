"""The worker group every test carries for pytest-xdist's `loadgroup` scheduler.

`tests/conftest.py::pytest_collection_modifyitems` is the one place that
decides which tests may share a worker. These read the mark off the live item
so the rule is proved on the test that asks, not restated: drop the hook and
both go red.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


PRINTER = """
def pytest_collection_finish(session):
    for item in session.items:
        mark = item.get_closest_marker("xdist_group")
        print("GROUP", item.nodeid.split("::")[0], mark.args[0] if mark else None)
"""


def test_a_long_file_is_spread_across_local_workers_and_a_paged_one_is_not(tmp_path):
    """The real conftest on a local (unsharded) collection: a file over
    SPLIT_FILE_SECONDS that shares no fixture and is not already `spread`
    gets a group per test, so the merge check's workers share it; a long
    file with a module-scoped page stays one group. The files are whichever
    the recorded durations make long today."""
    import os
    import subprocess
    import sys
    from sm64_events.core.childproc import quiet_spawn_kwargs
    from tools import test_lanes as lanes
    totals = lanes.file_totals(lanes.load_durations())
    long_files = [f for f, seconds in sorted(totals.items()) if seconds > lanes.SPLIT_FILE_SECONDS
                  and (REPO / f).is_file() and f not in lanes.BROWSER_SWEEPS
                  and "spread" not in (REPO / f).read_text(encoding="utf-8")]
    split = next(f for f in long_files if not lanes.shares_a_fixture(REPO / f))
    paged = next(f for f in long_files if lanes.shares_a_fixture(REPO / f))
    (tmp_path / "group_printer.py").write_text(PRINTER, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), os.environ.get("PYTHONPATH", "")])}
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "group_printer",
                             "-p", "no:cacheprovider", "--no-testmon", split, paged,
                             lanes.BROWSER_SWEEPS[0]],
                            cwd=REPO, env=env, capture_output=True, text=True, timeout=180,
                            **quiet_spawn_kwargs())
    groups: dict[str, set] = {}
    for line in result.stdout.splitlines():
        if line.startswith("GROUP "):
            _, path, group = line.split(" ", 2)
            groups.setdefault(path, set()).add(group)
    assert split in groups and paged in groups, result.stdout[-2000:] + result.stderr[-2000:]
    assert len(groups[split]) > 1, f"{split} ran as one worker group: {groups[split]}"
    assert groups[paged] == {paged}, f"{paged} left its page's worker: {groups[paged]}"
    pool = {f"browser_sweep_{k}" for k in range(lanes.BROWSER_SWEEP_GROUPS)}
    assert groups[lanes.BROWSER_SWEEPS[0]] <= pool, "locally the sweeps keep their bounded pool"


def test_a_plain_test_stays_with_its_file(request):
    mark = request.node.get_closest_marker("xdist_group")
    assert mark is not None, "no worker group -- the conftest hook is gone"
    assert mark.args[0] == "tests/test_worker_groups.py"


@pytest.mark.spread
@pytest.mark.parametrize("case", ["one", "two"])
def test_a_spread_test_is_its_own_group(request, case):
    mark = request.node.get_closest_marker("xdist_group")
    assert mark is not None, "no worker group -- the conftest hook is gone"
    assert mark.args[0] == (
        f"tests/test_worker_groups.py__test_a_spread_test_is_its_own_group_{case}_")
    assert "@" not in mark.args[0] and "]" not in mark.args[0], (
        "xdist splits `nodeid@group` on those characters")
    assert "::" not in mark.args[0], (
        "JUnit splits `nodeid@group` on `::`: the full run's report would name another test")


def test_a_browser_sweep_case_joins_a_BOUNDED_pool(request):
    """A viewport case boots its own server AND its own Chromium, so `spread`
    (one group per case) let ~20 browsers stand up at once and they starved
    each other -- three full runs at 8 workers went 21, 10 and 19 failed,
    always the sweep, while 4 workers passed 11016 twice (2026-09-20). These
    files share one bounded pool so the ceiling holds however many workers the
    run gets; the rest of the suite keeps all of them.

    Two properties, and the SECOND is the one that would rot quietly: the pool
    is bounded, and membership is a pure function of the nodeid. testmon
    selects subsets and reruns reorder, so an assignment that depended on
    collection order would move a case between groups from run to run -- a new
    flake source wearing the shape of a fix.
    """
    from source_scan import strip_comments

    from conftest import BROWSER_SWEEP_GROUPS, BROWSER_SWEEPS, browser_sweep_group

    # Driven by a SYNTHETIC set, never by whatever this run happened to
    # collect: the first version of this guard read the live items and passed
    # against an unbounded mutation, because a two-case probe cannot exceed a
    # bound of four. 112 cases are collected in a full gate and two in a
    # focused one; the rule is the same and so is the proof.
    sweep = BROWSER_SWEEPS[0]
    synthetic = [f"{sweep}::test_no_layout_defects_at_each_viewport[{w}x1000]"
                 for w in range(850, 900)]
    groups = {browser_sweep_group(nodeid) for nodeid in synthetic}
    assert len(groups) <= BROWSER_SWEEP_GROUPS, (
        f"{len(synthetic)} sweep cases landed in {len(groups)} groups; the pool "
        f"caps concurrent browsers at {BROWSER_SWEEP_GROUPS}")
    assert all("@" not in name and "]" not in name for name in groups), (
        "xdist splits `nodeid@group` on those characters")

    # Order-independence: reversed, and a strided SUBSET (testmon's shape),
    # must agree with the full map on every id they share.
    full = {nodeid: browser_sweep_group(nodeid) for nodeid in synthetic}
    for other in (list(reversed(synthetic)), synthetic[::3]):
        assert all(full[nodeid] == browser_sweep_group(nodeid) for nodeid in other), (
            "a sweep case moved group when the collection changed -- an "
            "assignment that depends on order, not on the nodeid")

    # And the hook APPLIES it, rather than deriving a group some other way.
    # The source check is what keeps this honest in a focused run, where too
    # few sweep cases are collected for the live marks to discriminate.
    import inspect

    from tools import test_lanes
    hook = strip_comments((REPO / "tests" / "conftest.py").read_text(encoding="utf-8"))
    rule = strip_comments(inspect.getsource(test_lanes.worker_group))
    assert "worker_group(" in hook and "browser_sweep_group(nodeid)" in rule, (
        "the hook must assign from the pure nodeid function, so testmon's "
        "subsets and reruns cannot move a case between groups")

    for item in request.session.items:
        # On a worker xdist has already rewritten the nodeid to
        # `<nodeid>@<group>`, so recomputing from it asks about a string the
        # hook never saw. Group names carry no `@` (the rule above), so the
        # split is exact, and on the controller there is nothing to strip.
        nodeid = item.nodeid.split("@")[0]
        if nodeid.split("::")[0] in BROWSER_SWEEPS:
            mark = item.get_closest_marker("xdist_group")
            assert mark is not None and mark.args[0] == browser_sweep_group(nodeid), (
                f"{nodeid} carries {mark and mark.args} -- the hook is not "
                "applying the bounded pool")


def test_a_spread_file_that_boots_no_browser_is_not_bounded(request):
    """The bound is paid for in wall time, so it goes only where browsers do.
    `tests/test_api.py` is `spread` because it is hundreds of fast in-process
    cases; folding it into the pool would serialise them behind each other for
    nothing. A regression here costs minutes per gate and breaks no test, so
    nothing else would report it."""
    from conftest import BROWSER_SWEEPS

    assert "tests/test_api.py" not in BROWSER_SWEEPS
    assert all(path.startswith("tests/test_responsive") for path in BROWSER_SWEEPS), (
        f"a non-sweep file joined the browser pool: {BROWSER_SWEEPS}")
    for path in BROWSER_SWEEPS:
        assert (REPO / path).exists(), f"{path} no longer exists; drop its row"
        source = (REPO / path).read_text(encoding="utf-8")
        assert "uilab_sweep_at" in source or "sweep" in source, (
            f"{path} is in the browser pool but reads like no sweep")


def test_under_loadgroup_the_group_reached_the_scheduler(request):
    """xdist rewrites the nodeid to `<nodeid>@<group>` on the worker, and only
    if the mark was there when ITS hook ran. Proves the hook order, which a
    look at the mark alone cannot: the mark is present either way."""
    if not getattr(request.config.option, "loadgroup", False):
        pytest.skip("only meaningful under `-n <k> --dist loadgroup`")
    assert request.node.nodeid.endswith("@tests/test_worker_groups.py"), request.node.nodeid


def test_the_session_runs_every_file_in_its_own_order(request):
    """The merge gate refreshes testmon's map with `--testmon-noselect`, and
    that mode REORDERS the collection ("prioritize the tests most likely to
    fail first") once a map exists -- which scrambled a module's tests
    across its two viewport params, rebuilt the fixture-reach file's
    module-scoped page 38 times instead of twice, and ran practice-tab
    assertions on a page a story test had left on Segments (2026-09-02,
    measured on one worker with no other load: 3 failed + 7 reruns; file
    order: 81 green). conftest's hookwrapper puts the order back after every
    plugin has had its say. This compares the LIVE session's order with the
    order that recipe produces -- raw collection order, then pytest's own
    parametrised-fixture grouping -- so a plugin that reorders, or a hook
    change that stops restoring, goes red here on the next gate. With no map
    the two are trivially equal; the gate always has one after its first run."""
    from _pytest.fixtures import reorder_items
    from conftest import RAW_INDEX

    live = [item.nodeid for item in request.session.items]
    expected = [item.nodeid for item in reorder_items(
        sorted(request.session.items, key=lambda item: item.stash[RAW_INDEX]))]
    # strict=False on purpose: a length difference IS a divergence, and the
    # message should name where it starts rather than raise on its way there.
    first_divergence = next(
        (index for index, (seen, wanted) in enumerate(zip(live, expected, strict=False))
         if seen != wanted),
        min(len(live), len(expected)))
    assert live == expected, (
        "the session's items are not in file order (plus pytest's own "
        "param grouping); a plugin reordered them and conftest did not put "
        f"them back. First divergence at index {first_divergence}")


def test_files_that_share_one_real_file_share_one_group():
    """Two files that write and read the SAME path on disk may never run in
    parallel, whatever worker is free. `test_ui_sync_page.py` writes the real
    `data/version_sync/jp.json` (by design -- the dashboard is driven against
    the real store, backed up and restored), and `test_layout_matches_report.py`
    reads that path to catch layout drift, skipping when it is absent. Under
    24 workers they overlapped once and the reader went red on the writer's
    throwaway report, then could not be reproduced alone (2026-09-05)."""
    from conftest import SHARED_GROUPS
    pair = {"tests/test_ui_sync_page.py", "tests/test_layout_matches_report.py"}
    assert pair <= set(SHARED_GROUPS), SHARED_GROUPS
    assert len({SHARED_GROUPS[name] for name in pair}) == 1, SHARED_GROUPS
    for name, group in SHARED_GROUPS.items():
        assert (REPO / name).exists(), f"{name} no longer exists; drop its row"
        assert not set(group) & {"@", "]"}, "xdist splits a group on those"


def test_the_browser_wait_bound_scales_with_the_machine_the_run_gets():
    """One bound, scaled -- not 35 hand-tuned numbers.

    Every app-shell wait used to carry its own `timeout_ms`, bumped 10s ->
    20s one file at a time as runs got slower. Under the OBS cap (a quarter
    of the CPUs, so his capture never stutters) even 20s was short, and a
    page that paints in under a second failed a merge (2026-09-19). conftest
    now sets UILAB_WAIT_MS from the budget the run actually got."""
    import inspect
    from conftest import pytest_configure
    source = inspect.getsource(pytest_configure)
    assert "UILAB_WAIT_MS" in source and "obs_is_open" in source, (
        "the bound must come from the admitted budget, not a constant")

    shell_waits = []
    for path in sorted((REPO / "tests").glob("*.py")):
        for selector in ('".log-list-card"', '".log-card"', '".sync-card"'):
            shell_waits += re.findall(
                rf"wait_for\({re.escape(selector)}, timeout_ms=\d+\)",
                path.read_text(encoding="utf-8"))
    assert not shell_waits, (
        "app-shell waits must inherit the scaled bound, not re-pin their own: "
        f"{shell_waits[:3]}")


def test_the_story_setup_waits_inherit_that_same_bound():
    """The JS half of the same rule, and the half the first sweep missed.

    `tools/uilab_project.py`'s story setups wait INSIDE `page.evaluate`, so
    they never saw `UILAB_WAIT_MS` and kept their own hand-bumped numbers:
    eight waits pinned at 15s, already raised once by hand (2026-09-17). This
    guard is about CONSISTENCY -- one bound, from the one variable -- which is
    the drift b15f16d0 retired for the Python half. A story that means a real
    short interval still passes its own explicit maxMs.

    It is NOT the cure for a starving sweep, and the neighbouring failures
    must not be read that way: raising these eight to 60s moved a full gate
    from 21 failures to 10, then 19 (2026-09-20). Worker count is the axis --
    see `shell_wait_ms`'s docstring for the measurement.
    """
    source = (REPO / "tools" / "uilab_project.py").read_text(encoding="utf-8")
    assert "SHELL_WAIT = %d" in source, (
        "the composed script must declare the bound it was given")
    pinned = re.findall(r"waitFor\([^;]*?,\s*(\d{5,})\)", source, re.S)
    assert not pinned, (
        "a story shell wait re-pinned its own five-digit bound instead of "
        f"SHELL_WAIT: {pinned[:3]}")
