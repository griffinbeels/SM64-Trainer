"""The selector's EXPANDED state: its own layout gate (task 0087).

Nothing in the shipped corpus carries a `parent`, so before
`ui_fixture.serve_ui(seed_subsections=True)` existed no instrument could reach
the expanded row at all -- and an unreachable state is one no gate is looking
at. What that cost, all four found in the first render and none of them
visible to the 17 node-driven rule tests in `tests/test_ui_subsections.py`:

  * the STAR row had no disclosure wiring whatsoever, so the case Griffin
    named first ("practice only a small portion of a star") had no path;
  * a selected SUBSECTION collapsed the row to that single cell, and picking
    an ordinary star with no subsections emptied its course's row the same
    way -- both dead ends with no gesture back;
  * `segments.arm_level` had no `moment_reached` branch, so a subsection
    started by a moment was placed in NO row anywhere;
  * `.selector-expanded` was on the section and styled nowhere, so the
    expanded row was pixel-identical to an ordinary one.

Same seam as `tests/test_responsive_bowser.py`: uilab's `uilab_sweep` fixture
reads `uilab_project` off the TEST MODULE, so a second module gets its own
sweep with its own `Project`. Deliberately does NOT repeat
`test_component_layout_gates_on_the_container` -- that law is a project-wide
stylesheet scan and already runs once in `tests/test_responsive.py`.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from uilab.pytest_plugin import (  # noqa: E402,F401
    assert_no_new_defects, assert_no_stale_exemptions, uilab_sweep)
from uilab_project import SUBSECTION_PROJECT  # noqa: E402

# The plugin's `uilab_sweep` fixture reads this off the module.
uilab_project = SUBSECTION_PROJECT


def test_the_sweep_is_not_silently_disabled():
    if os.environ.get("UILAB_SKIP") == "1":
        pytest.skip("UILAB_SKIP=1 — layout sweep deliberately disabled")


def test_no_layout_defects_across_the_matrix(uilab_sweep):
    assert_no_new_defects(SUBSECTION_PROJECT, uilab_sweep)


def test_the_known_defect_list_does_not_outlive_its_defects(uilab_sweep):
    assert_no_stale_exemptions(SUBSECTION_PROJECT, uilab_sweep)


# --- the fixture must actually REACH the expanded row -----------------------
# The sweep proves the row does not overflow or clip. It says nothing about
# WHICH row it measured, and `at=".stagebanner"` matches an ordinary
# seven-star row perfectly well. This is that other half.

def test_the_row_opens_expanded_into_the_targeted_star_s_subsections():
    """Three cells: the fixture star, then its two subsections -- never the
    course's seven stars.

    Mutation-proved both ways before it was trusted: drop `arm_level`'s
    `moment_reached` branch and this reads 7 (the subsections are placed
    nowhere, so the row draws the plain course); drop the STAR row's
    `visibleEntities` wiring and it reads 7 as well.
    """
    with SUBSECTION_PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(SUBSECTION_PROJECT.ready_selector)
        assert page.count(".stagebanner.selector-expanded") == 1, (
            "the row did not expand -- the target's subsections reached "
            "neither `segment_targets` nor `visibleEntities`")
        assert page.count(".stagebanner .starcell") == 3, (
            "expected the parent star plus its two subsections. 7 means the "
            "row fell back to the plain course list")
        assert page.count(".stagebanner .starcell.subsection-cell") == 2, (
            "the subsections are drawn but wear no child treatment, so the "
            "expanded row reads as three peer options")


def test_tapping_the_targeted_star_folds_the_row_back_to_the_course():
    """The way OUT, which the header has promised since the day the feature
    shipped ("tap the star again to go back") and nothing implemented. Without
    it, picking a star with subsections hides the course's other six for good:
    `/api/target` cannot clear a target, so there is no other gesture."""
    with SUBSECTION_PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(SUBSECTION_PROJECT.ready_selector)
        page.evaluate(
            "document.querySelector('.stagebanner .starcell.active-star')"
            ".click()")
        page.wait_ms(250)
        assert page.count(".stagebanner.selector-expanded") == 0
        assert page.count(".stagebanner .starcell") == 7, (
            "folding must restore the whole course, not some subset")
