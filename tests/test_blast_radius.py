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
from test_lanes import BROWSER_SWEEPS  # noqa: E402

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


def test_a_python_change_picks_the_tests_its_coverage_recorded():
    coverage = ({"src/sm64_events/core/timefmt.py": {f"{PLAIN_TEST}::test_recorded"}}, "test map")
    picked = chosen({"src/sm64_events/core/timefmt.py": "M"}, coverage=coverage)
    assert picked == {PLAIN_TEST: [f"{PLAIN_TEST}::test_recorded"]}


def test_a_python_change_no_map_has_seen_picks_the_tests_importing_it():
    picked = chosen({"src/sm64_events/core/timefmt.py": "M"})
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


def test_a_global_rule_picks_every_browser_test_and_nothing_else():
    """A shared element: the body rule reaches every page."""
    picked = chosen({INDEX: "M"}, before=before_editing("\n  body { font-family: Consolas, monospace;"))
    assert {RANK_TEST, RANK_PAGE_TEST, LIBRARY_TEST} <= set(picked)
    assert PLAIN_TEST not in picked


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
