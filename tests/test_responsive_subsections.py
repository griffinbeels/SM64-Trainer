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


# --- the fixture must actually REACH the subsection badges ------------------
# The sweep proves the row does not overflow or clip. It says nothing about
# WHICH row it measured, and `at=".stagebanner"` matches an ordinary
# seven-star row perfectly well. This is that other half.
#
# REWRITTEN 2026-08-08 (round 22). The state these two reached was the
# EXPANDED row -- parent plus its pieces as peer cells -- and that state no
# longer exists: a piece is a badge inside its parent's art, so the row always
# draws the course's stars and never a subsection cell.


def test_the_parent_star_wears_its_subsections_as_badges():
    """The course's own stars, with the fixture star carrying two toggles.

    Mutation-proved both ways before it was trusted: drop `arm_level`'s
    `moment_reached` branch and the badge count reads 0 (the subsections are
    placed nowhere, so `segment_targets` never offers them here); drop the
    STAR row's `subsectionToggles` wiring and it reads 0 as well.
    """
    with SUBSECTION_PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(SUBSECTION_PROJECT.ready_selector)
        assert page.count(".stagebanner .starcell") == 7, (
            "the row must draw the whole course -- a subsection is never a "
            "cell since round 22")
        assert page.count(".stagebanner .starcell.has-toggles") == 1, (
            "exactly the fixture star hosts pieces; a cell with toggles is a "
            "div rather than a button, so this also pins the element swap")
        assert page.count(".stagebanner .cell-toggle-btn") == 2, (
            "the two seeded subsections draw one badge each, INSIDE the star")


def test_a_badge_click_toggles_that_piece_rather_than_selecting_it():
    """His whole point about the redesign: "the buttons are not toggles
    BETWEEN options, but rather enable/disable options." So a click dims the
    badge and must NOT move the practice target -- the star keeps the hand
    (round 21) and the piece merely stops being tracked."""
    with SUBSECTION_PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(SUBSECTION_PROJECT.ready_selector)
        before = page.evaluate(
            "document.querySelectorAll("
            "'.stagebanner .cell-toggle-btn.is-selected').length")
        assert before == 2, "a piece is TRACKED by default"
        page.evaluate(
            "document.querySelector('.stagebanner .cell-toggle-btn').click()")
        page.wait_ms(400)
        assert page.evaluate(
            "document.querySelectorAll("
            "'.stagebanner .cell-toggle-btn.is-selected').length") == 1, (
            "the clicked badge must dim -- and stay dimmed, which means the "
            "PUT landed and the refreshed view carried enabled=false back")
        assert page.count(".stagebanner .starcell.active-star") <= 1, (
            "a badge click is not a target pick")


# --- and the PRACTICE LOG half ----------------------------------------------
# A probe answers "is something broken"; it cannot answer "is this piece drawn
# inside its parent's card". Only a render can, and this state is unreachable
# without `seed_subsections` -- nothing in the shipped corpus carries a
# `parent`, and until round 22 nothing gave a seeded piece any ATTEMPTS either,
# so the nesting would have rendered zero times.


def test_a_piece_draws_inside_its_parent_s_card_and_not_beside_it():
    """His whole reason for the round: "This is so that it's very very very
    clear that this subsection was associated with the star it was a
    subsection for."

    Both halves are asserted, because only the pair is the claim: the pieces
    are nested, AND they are not also loose at the top level.
    """
    with SUBSECTION_PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(SUBSECTION_PROJECT.ready_selector)
        page.wait_ms(400)
        top = page.evaluate(
            "Array.from(document.querySelectorAll('.log-list > .log-card'))"
            ".map(c => (c.querySelector('.log-card-name')||{}).innerText)")
        nested = page.evaluate(
            "Array.from(document.querySelectorAll("
            "'.log-card-children > .log-card'))"
            ".map(c => (c.querySelector('.log-card-name')||{}).innerText)")
        assert sorted(name.splitlines()[-1] for name in nested) == [
            "Owl Drop", "Tower Climb"], (
            "the seeded pieces must draw inside a parent card. An empty list "
            "means either the fixture recorded no piece attempts or "
            "`nestSubsections` dropped them")
        assert not [row for row in top
                    if "Owl Drop" in row or "Tower Climb" in row], (
            "a piece must not ALSO be a top-level card -- two cards for one "
            "entity means two strategy pickers writing one piece of state")


def test_a_piece_closes_with_its_parent():
    """"These should follow the visibility of the parent (e.g., if the parent
    star closes the card, these close with it)."

    Structural rather than styled: the pieces live inside the parent's own
    `Disclose` body, so folding the parent takes them with it. Mutation-proved
    by rendering `.log-card-children` as a sibling of that body instead.

    Measured as PAINTED HEIGHT rather than as element count, because `Disclose`
    unmounts what it closes -- so "the node is gone" and "the node is there at
    zero height" are both correct answers to his ask, and an assertion that
    picked one of them would go red the day the other became true.
    """
    visible_height = ("(() => { const el = "
                      "document.querySelector('.log-card-children');"
                      " return el ? el.getBoundingClientRect().height : 0; })()")
    with SUBSECTION_PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(SUBSECTION_PROJECT.ready_selector)
        page.wait_ms(400)
        assert page.evaluate(visible_height) > 0, "the pieces start visible"
        page.evaluate(
            "document.querySelector('.log-list > .log-card .log-card-fold')"
            ".click()")
        page.wait_ms(700)
        assert page.evaluate(visible_height) == 0, (
            "closing the parent must take its pieces with it")


def test_a_piece_lines_up_with_the_rank_standards_card_above_it():
    """"all of the segment cards should be in-line with the rank standards
    card above it (aka they should be expanded to the left a bit to be in line
    with the rest of the elements above)."

    Measured in PIXELS rather than asserted from the CSS, because the value
    that matters is the sum of four paddings and a negative margin, and every
    one of them is legal on its own. Mutation-proved by restoring the extra
    `margin-left: 1rem`.
    """
    with SUBSECTION_PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(SUBSECTION_PROJECT.ready_selector)
        page.wait_ms(400)
        edges = page.evaluate(
            "(() => { const card = document.querySelector("
            "'.log-card-children > .log-card');"
            " const panel = card.closest('.log-card-body')"
            ".querySelector(':scope > .stdpanel');"
            " return [card.getBoundingClientRect().left,"
            " panel.getBoundingClientRect().left]; })()")
        assert abs(edges[0] - edges[1]) <= 1, (
            f"a piece's left edge is {edges[0]} and the Rank standards panel's "
            f"is {edges[1]} -- they must line up")


def test_a_piece_s_attempt_row_fits_on_one_line_at_the_narrowest_width():
    """"need to make sure that the scaling is correct, because otherwise some
    elements may bleed into two rows (like the checkbox next to the 12\"20."

    The cause was the indent being paid TWICE (the body's own
    `--log-body-indent` stacking inside the parent's), so a child's result cell
    had ~70px less than its parent's identical cell. Asserted at the supported
    FLOOR, which is where it showed. Mutation-proved by restoring the nested
    body's indent.
    """
    floor = SUBSECTION_PROJECT.min_viewport_width
    with SUBSECTION_PROJECT.open() as url, get_driver().launch(
            viewport=(floor, 1200)) as page:
        page.goto(url)
        page.wait_for(SUBSECTION_PROJECT.ready_selector)
        page.wait_ms(400)
        # A piece is CLOSED by default (it has not earned the one auto-open
        # slot), so its table is not in the DOM until asked for.
        page.evaluate(
            "document.querySelector("
            "'.log-card-children > .log-card .log-card-fold').click()")
        page.wait_ms(600)
        cells = page.evaluate(
            "(() => { const h = (sel) => Array.from("
            "document.querySelectorAll(sel)).map(c =>"
            " c.getBoundingClientRect().height);"
            " return [h('.log-card-children .attempt-table .attempt-result'),"
            " h('.log-list > .log-card > .log-card-disclose"
            " .attempt-table .attempt-result')]; })()")
        piece_rows, parent_rows = cells
        assert piece_rows, "no piece rows rendered -- the fixture missed the state"
        assert parent_rows, "no parent rows to calibrate against"
        # SELF-CALIBRATING: the parent's identical cell at the identical width
        # is the control, so this cannot go red for a font or padding change.
        # Mutation-proved by restoring the extra `margin-left: 1rem` above --
        # which is what the wrap actually was. Restoring the nested card's own
        # `--log-body-indent` does NOT reproduce it (44px vs 44px, measured),
        # so that second rule was written and then deleted rather than kept on
        # a hunch.
        assert max(piece_rows) <= max(parent_rows) + 2, (
            f"a piece's result cell is {max(piece_rows):.0f}px where its "
            f"parent's is {max(parent_rows):.0f}px at {floor}px -- it wrapped")


def test_a_piece_with_no_strategies_does_not_shout_for_one():
    """"If a segment doesn't have a ranked ladder (aka there are no strategies
    to select), it should not be blinking red (there's nothing to select...)."

    The control stays enabled -- "+ new strat..." is a real action -- so this
    asserts the GLOW is gone, not the picker. Mutation-proved by dropping the
    `nothingToPick` clause in stratpicker.js.
    """
    with SUBSECTION_PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(SUBSECTION_PROJECT.ready_selector)
        page.wait_ms(400)
        pickers = page.evaluate(
            "Array.from(document.querySelectorAll("
            "'.log-card-children .log-card-strat-picker select')).map(s => ["
            "s.className.includes('needs-strat'), s.options.length,"
            "s.disabled])")
        assert pickers, "no piece pickers rendered -- the fixture missed the state"
        for shouts, options, disabled in pickers:
            assert options <= 2, (
                "this guard only means anything on a picker with nothing to "
                f"choose; this one has {options} options")
            assert not shouts, "a piece with no strategies must not glow red"
            assert not disabled, (
                "it must stay usable -- '+ new strat...' is a real action")
