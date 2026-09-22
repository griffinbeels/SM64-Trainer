"""The blast radius picks a change's dependents and leaves unrelated surfaces
alone. Each case is a change nobody has to make: `select` takes the changed
paths, a file's text before the change, and the coverage map as arguments, so
these read the real UI, the real tests and the real stylesheet.

Griffin, 2026-09-21: "A change on the rank page CANNOT affect a change on the
practice page (unless there are shared elements)." The Rank page is the
changed surface throughout; the Library search page is the unrelated one.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import blast_radius as radius  # noqa: E402
from test_lanes import BROWSER_SWEEPS, lane_of  # noqa: E402

ROOT = radius.ROOT
UI = radius.UI
INDEX = f"{UI}/index.html"
NO_MAP = ({}, "no coverage map (test)")

LEADERBOARD = f"{UI}/components/leaderboard.js"      # imported by the Rank page's root
RANK_TEST = "tests/test_ui_leaderboard.py"             # names the leaderboard's own classes
RANK_PAGE_TEST = "tests/test_ui_rank_page_load.py"     # names only the Rank page (the importer)
LIBRARY_TEST = "tests/test_ui_library_search.py"       # another surface entirely
PLAIN_TEST = "tests/test_timefmt.py"                   # starts no browser


def chosen(changed, before=None, coverage=NO_MAP):
    return radius.select(ROOT, "HEAD", changed=changed, before=before, coverage=coverage,
                         last_failed=set()).selection()


def before_editing(rule: str):
    """The index page as it was BEFORE someone edited one rule: today's text
    with one extra declaration in the rule that opens with `rule`."""
    today = (ROOT / INDEX).read_text(encoding="utf-8")
    assert today.count(rule) == 1, f"pick a rule opening that occurs once, not {rule!r}"
    return lambda path: today.replace(rule, rule + " outline: 0;", 1)


TIMEFMT = "src/sm64_events/core/timefmt.py"


def _blocks_today(path: str) -> tuple[int, ...]:
    from testmon.process_code import Module
    return tuple(Module(source_code=(ROOT / path).read_text(encoding="utf-8")).checksums)


def test_a_python_change_picks_only_tests_whose_executed_code_changed():
    """pytest-testmon's rule, read straight off its record: a test is in the
    radius when a block it executed is no longer there as it was. One test
    ran only blocks that still exist; the other ran one that changed."""
    today = _blocks_today(TIMEFMT)
    coverage = ({TIMEFMT: [(today[:3], {f"{PLAIN_TEST}::test_untouched"}),
                           ((*today[:3], 123456789), {f"{PLAIN_TEST}::test_touched"})]}, "test map")
    picked = chosen({TIMEFMT: "M"}, coverage=coverage)
    assert picked == {PLAIN_TEST: [f"{PLAIN_TEST}::test_touched"]}


def test_a_python_change_no_map_has_seen_picks_the_tests_importing_it():
    picked = chosen({TIMEFMT: "M"})
    assert PLAIN_TEST in picked
    assert RANK_TEST not in picked and LIBRARY_TEST not in picked


def test_a_component_change_reaches_its_importers_page_and_no_other():
    picked = chosen({LEADERBOARD: "M"})
    assert RANK_TEST in picked, "its own classes"
    assert RANK_PAGE_TEST in picked, "the page that imports it: reached only through the importer"
    assert LIBRARY_TEST not in picked, "a different surface"
    assert PLAIN_TEST not in picked


def test_a_rule_for_one_surfaces_class_picks_that_surface_and_no_other():
    """The stylesheet is one block in index.html; the RULE that changed
    decides, not the file."""
    picked = chosen({INDEX: "M"}, before=before_editing("\n  .leaderboard-row {"))
    assert RANK_TEST in picked
    assert RANK_PAGE_TEST in picked, "the class's component's page"
    assert LIBRARY_TEST not in picked
    assert PLAIN_TEST not in picked


def test_a_global_rule_reaches_every_page_and_the_cap_leaves_them_to_the_full_run():
    """A shared element: the body rule reaches every page, far past the local
    cap, and names none of them: the canary runs here, the pages on GitHub."""
    result = radius.select(ROOT, "HEAD", changed={INDEX: "M"}, last_failed=set(), coverage=NO_MAP,
                           before=before_editing("\n  body { font-family: Consolas, monospace;"))
    picked = result.selection()
    assert {RANK_TEST, RANK_PAGE_TEST, LIBRARY_TEST} <= set(result.deferred)
    assert set(picked) == set(radius.CANARIES)
    assert not set(radius.CANARIES) & set(result.deferred), "the canary is kept, not left"
    assert PLAIN_TEST not in picked


UILOG = "src/sm64_events/core/uilog.py"
UILOG_PAGE = "tests/test_ui_log_records_the_real_page.py"   # imports sm64_events.core.uilog


def _pages_not_naming(path: str, count: int) -> list[str]:
    """Real browser files that neither import nor name `path`."""
    index = radius._test_index(ROOT)
    naming = radius._importers(index, path) | {
        name for name, refs in index.items() if Path(path).name in refs.basenames}
    pages = sorted(name for name in index if lane_of(ROOT / name) == "browser"
                   and name not in {*naming, *BROWSER_SWEEPS, *radius.CANARIES})
    assert len(pages) >= count
    return pages[:count]


def _reached(path: str, test_files) -> tuple[dict, str]:
    """A map in which every one of these files executed a block of `path`
    that has since changed."""
    changed_block = (*_blocks_today(path)[:3], 123456789)
    return {path: [(changed_block, {f"{name}::test_a" for name in test_files})]}, "test map"


def test_a_change_past_the_browser_cap_keeps_its_plain_tests_the_canary_and_the_page_naming_it():
    """A core module every page's first load executes: past the cap, the
    local run is what names it, what runs in-process and the canary; the
    pages that only executed it run in the full run after the push."""
    pages = _pages_not_naming(UILOG, radius.BROWSER_CAP + 5)
    result = radius.select(ROOT, "HEAD", changed={UILOG: "M"}, last_failed=set(),
                           coverage=_reached(UILOG, [*pages, UILOG_PAGE, PLAIN_TEST, *radius.CANARIES]))
    picked = result.selection()
    assert picked[PLAIN_TEST] == [f"{PLAIN_TEST}::test_a"], "a plain test always runs here"
    assert picked[UILOG_PAGE] == [f"{UILOG_PAGE}::test_a"], "the page that imports the module"
    assert all(picked[canary] is None for canary in radius.CANARIES), "the canary runs whole"
    assert result.deferred == sorted(pages)
    shown = f"{len(pages)} of {len(pages) + 1 + len(radius.CANARIES)} browser files: over the local cap"
    assert shown in radius.why(result)


def test_a_change_within_the_browser_cap_runs_every_page_it_reaches():
    pages = _pages_not_naming(UILOG, 3)
    result = radius.select(ROOT, "HEAD", changed={UILOG: "M"}, last_failed=set(),
                           coverage=_reached(UILOG, [*pages, PLAIN_TEST]))
    assert set(result.selection()) == {*pages, PLAIN_TEST}
    assert result.deferred == []


def test_a_changed_test_file_picks_itself():
    assert chosen({PLAIN_TEST: "M"}) == {PLAIN_TEST: None}


def test_a_runner_change_picks_everything_that_starts_no_browser():
    picked = chosen({"tools/run_tests.py": "M"})
    assert PLAIN_TEST in picked and RANK_TEST not in picked


def test_a_document_picks_the_tests_that_read_it():
    picked = chosen({"docs/glossary.md": "M"})
    assert "tests/test_glossary.py" in picked
    assert RANK_TEST not in picked and PLAIN_TEST not in picked


def test_the_viewport_sweeps_are_left_to_the_full_run():
    """Every sweep case sweeps every page; any UI change would pick them all."""
    picked = chosen({INDEX: "M"}, before=before_editing("\n  body { font-family: Consolas, monospace;"))
    assert not set(BROWSER_SWEEPS) & set(picked)


@pytest.mark.parametrize("css,global_", [
    (".leaderboard-row { color: red }", False),
    ("body { margin: 0 }", True),
    (":root { --gold: #fc0 }", True),
    ("@font-face { font-family: X }", True),
    (".a, button { color: red }", True),
    ("@media (max-width: 900px) { .leaderboard-row { color: red } }", False),
])
def test_the_rule_parser_tells_a_class_rule_from_a_global_one(css, global_):
    rules = radius.css_rules(css)
    assert rules
    selectors = [selector for _, selector, _ in rules]
    has_global = any(selector.startswith("@") or any(
        not (radius._CLASS_IN_SELECTOR.search(part) or radius._ID_IN_SELECTOR.search(part))
        for part in selector.split(",")) for selector in selectors)
    assert has_global is global_


def test_script_strings_skip_comments_and_regular_expressions():
    text = "const a = x / 2; const r = /\"it's\"/g; const s = \"log-card\"; // it's \"no\"\n/* 'x' */"
    assert radius.js_strings(text) == ["log-card"]


def test_the_why_names_every_reason_and_the_sweeps():
    result = radius.select(ROOT, "HEAD", changed={LEADERBOARD: "M", "data/nothing-reads-this.bin": "A"},
                           coverage=NO_MAP, last_failed=set())
    text = radius.why(result)
    assert "leaderboard.js" in text and "no tests name: data/nothing-reads-this.bin" in text
    assert "viewport sweeps" in text


def test_a_test_that_failed_last_time_is_picked_again():
    picked = radius.select(ROOT, "HEAD", changed={}, coverage=NO_MAP,
                           last_failed={f"{PLAIN_TEST}::test_x"}).selection()
    assert picked == {PLAIN_TEST: [f"{PLAIN_TEST}::test_x"]}


def test_a_failure_recorded_before_the_green_baseline_is_not_picked(tmp_path):
    cache = tmp_path / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    (cache / "lastfailed").write_text(f'{{"{PLAIN_TEST}::test_x": true}}', encoding="utf-8")
    recorded = (cache / "lastfailed").stat().st_mtime
    assert radius._last_failed(tmp_path, since=recorded + 60) == set()
    assert radius._last_failed(tmp_path, since=recorded - 60) == {f"{PLAIN_TEST}::test_x"}
    assert radius._last_failed(tmp_path) == {f"{PLAIN_TEST}::test_x"}
