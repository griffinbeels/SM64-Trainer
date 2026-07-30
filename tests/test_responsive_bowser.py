"""The Bowser banner row: its own layout gate (task-bowser-sweep).

`stagebanner.js` dispatches on `stage.mode` via `STAGE_ROWS`, and no story in
the main practice sweep (`tests/test_responsive.py`) ever puts the stage in
"bowser_course" — so `BowserCourseRow` (three cells since 912466d rewrote it
from two: the "reds" 8-coin star plus BOTH BitDW pipe segments, each now a
full `StandardSegmentCell` with its own name, rank and strategy sub-line) had
never been rendered by any gate, before or after that rewrite.

`stage` is SERVER state, seeded once when a fixture starts, and all of the
main PROJECT's stories share ONE page and ONE server — no story there can
move the app into `bowser_course` mid-sweep. uilab's `uilab_sweep` fixture
reads `uilab_project` off the TEST MODULE (`uilab/pytest_plugin.py`), so a
second module gets its own sweep with its own `Project`: that is the seam,
and it needed no uilab change. See `tools/uilab_project.py::BOWSER_PROJECT`
for the fixture wiring (`ui_fixture.py::serve_ui(reconcile_full_corpus=True,
bowser_stage=(16, 17))`).

Mirrors `tests/test_responsive.py`'s structure — including the `find_uilab`
skip guard and the "not silently disabled" reasoning — but deliberately does
NOT repeat `test_component_layout_gates_on_the_container`: that law is
project-wide (a stylesheet scan, no browser involved) and already runs once
in `tests/test_responsive.py` against the identical stylesheet and the
identical `shell_selectors`. Running it again here would check nothing new.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

# The resolver lives in `tools/find_uilab.py` — see tests/test_responsive.py
# for why this is the one door (a second copy could drift into one of them
# skipping while the other runs).
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from uilab.pytest_plugin import (  # noqa: E402,F401
    assert_no_new_defects, assert_no_stale_exemptions, uilab_sweep)
from uilab_project import BOWSER_PROJECT  # noqa: E402

# `uilab_sweep` is IMPORTED, not inherited from the pytest11 entry point —
# same reasoning as tests/test_responsive.py: `uv sync` prunes the editable
# install (uilab is absent from the lockfile), so importing the fixture is
# the one thing a package manager cannot revoke.

# The plugin's `uilab_sweep` fixture reads this off the module.
uilab_project = BOWSER_PROJECT


def test_the_sweep_is_not_silently_disabled():
    """UILAB_SKIP is for a machine without a browser, and saying so out loud
    is the point — the alternative is a suite that quietly stops checking."""
    if os.environ.get("UILAB_SKIP") == "1":
        pytest.skip("UILAB_SKIP=1 — layout sweep deliberately disabled")


def test_no_layout_defects_across_the_matrix(uilab_sweep):
    assert_no_new_defects(BOWSER_PROJECT, uilab_sweep)


def test_the_known_defect_list_does_not_outlive_its_defects(uilab_sweep):
    """A stale exemption is a lie about what is broken, and the list stops
    meaning anything the moment one is allowed to sit there."""
    assert_no_stale_exemptions(BOWSER_PROJECT, uilab_sweep)


# --- the fixture must actually REACH the row --------------------------------
# tests/test_fixture_reaches_the_real_page.py's own lesson, a fourth instance
# of the failure it names three of: a sweep that never reaches its target
# state reports a clean page nobody is looking at. `uilab_sweep` above proves
# the row doesn't OVERFLOW/clip/overlap; it says nothing about whether the
# row it measured is the real, three-cell BowserCourseRow or an empty/one-cell
# stand-in the `at=".stagebanner"` selector matched anyway. This is that
# other half, expressed the way test_fixture_reaches_the_real_page.py's own
# tests are: open the real fixture, count the real cells.
def test_the_stage_is_actually_in_bowser_course_mode_with_two_cells():
    """Prove the story reaches the row, not just SOME row.

    Two cells since 2026-07-30 (spec 2026-07-28-multi-step-segments, "the
    Bowser Reds star/pipe toggle"): "No Reds" (the legacy exclusive
    seg:<abbrev>-pipe, seeded by the schema migration itself, present with
    or without `reconcile_full_corpus`) and "Reds", which folds the
    star/segment-time toggle that used to be a third cell
    (seg:reds->pipe:<abbrev>, corpus-only) INTO the Reds cell itself —
    `.starcell` counts 2, not 3, because the reds->pipe segment no longer
    renders its own StandardSegmentCell at all.

    This is why `reconcile_full_corpus=True` no longer changes the CELL
    COUNT the way it did for the old three-cell row (empirically checked,
    `/api/session`: segment 5 "BitDW Pipe Entry" exists either way; segment
    67 "BitDW — 8 Red Coins → Pipe" exists only with reconcile) — removing
    it leaves the Reds cell's pipe toggle with nothing to target
    (`pipeSeg` undefined, its onclick a no-op) but the cell is still drawn,
    so this specific test can no longer distinguish "reconcile ran" from
    "it didn't." What it still mutation-proves: with `bowser_stage` removed
    entirely, `t.stage.mode` stays "stars" (`seed_practice`'s own hardcoded
    default — see its comment in `ui_fixture.py`) and `.stagebanner` renders
    `StarRow` instead, so `.starcell` still exists but the count is 7 (six
    named stars + 100 Coins), never 2 — run by hand against this test
    before it was trusted.
    """
    with BOWSER_PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(BOWSER_PROJECT.ready_selector)
        assert page.count(".stagebanner") == 1, (
            "no stage banner reached bowser_course context at all — "
            "hasPracticeContext/practiceMode returned no row for it")
        assert page.count(".stagebanner .starcell") == 2, (
            "expected 2 cells on the Bowser row ('No Reds' + 'Reds', the "
            "latter folding the star/pipe toggle inside itself). If this is "
            "7, the stage never reached bowser_course mode and StarRow "
            "rendered instead")
