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
from uilab_project import SUBSECTION_PROJECT, BOWSER_COURSE, BOWSER_LEVEL  # noqa: E402
from ui_fixture import serve_ui  # noqa: E402

# The plugin's `uilab_sweep` fixture reads this off the module.
uilab_project = SUBSECTION_PROJECT


def test_the_sweep_is_not_silently_disabled():
    if os.environ.get("UILAB_SKIP") == "1":
        pytest.skip("UILAB_SKIP=1 — layout sweep deliberately disabled")


def test_no_layout_defects_across_the_matrix(uilab_sweep):
    assert_no_new_defects(SUBSECTION_PROJECT, uilab_sweep)


def test_the_known_defect_list_does_not_outlive_its_defects(uilab_sweep):
    assert_no_stale_exemptions(SUBSECTION_PROJECT, uilab_sweep)


# --- the fixture must actually REACH the star row with pieces attached ------
# The sweep proves the row does not overflow or clip. It says nothing about
# WHICH row it measured, and `at=".stagebanner"` matches an ordinary
# seven-star row perfectly well. This is that other half.
#
# REWRITTEN 2026-08-08 (round 22), then again 2026-08-10 (round 31). The state
# these reached was first the EXPANDED row (parent plus its pieces as peer
# cells), then the badged row (parent plus a toggle overlay) -- neither exists
# any more. A piece is never a cell and wears no switch on the selector; the
# row always draws the course's plain stars, unchanged by whether any of them
# has pieces.


def test_a_star_with_pieces_draws_no_different_from_one_without():
    """Round 31 (2026-08-10) retired the badge outright: "we don't need a
    button to enable / disable them now." A piece still exists and still
    nests inside its parent's practice-log card (see the tests below); the
    SELECTOR row simply has nothing left to show about it.

    Mutation-proved: restoring `subsectionToggles`' wiring on the star row
    makes `.cell-toggle-btn`/`.has-toggles` reappear here.
    """
    with SUBSECTION_PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(SUBSECTION_PROJECT.ready_selector)
        assert page.count(".stagebanner .starcell") == 7, (
            "the row must draw the whole course -- a subsection is never a "
            "cell since round 22")
        assert page.count(".stagebanner .starcell.has-toggles") == 0, (
            "no ordinary star cell wears a toggle overlay any more (round 31)")
        assert page.count(".stagebanner .cell-toggle-btn") == 0, (
            "the badge is gone -- a piece is tracked unconditionally, with "
            "nothing on the selector left to switch")


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
    # BOTH ENDS, and the WIDE one is the one that matters. Below the narrow
    # `@container` threshold the row is already a grid with its own `nowrap`,
    # so a floor-only check is green for a reason that has nothing to do with
    # the bug -- which is exactly how round 23's version passed while he was
    # looking at a wrapped row on a ~1300px window.
    for floor in (SUBSECTION_PROJECT.min_viewport_width, 1500):
        _one_line_at(floor)


def _one_line_at(floor):
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
        rows = page.evaluate(
            "(() => { const h = (sel) => Array.from("
            "document.querySelectorAll(sel)).map(c =>"
            " c.closest('tr').getBoundingClientRect().height);"
            " return [h('.log-card-children .attempt-table .attempt-result'),"
            " h('.log-list > .log-card > .log-card-disclose"
            " .attempt-table .attempt-result')]; })()")
        piece_rows, parent_rows = rows
        assert piece_rows, "no piece rows rendered -- the fixture missed the state"
        assert parent_rows, "no parent rows to calibrate against"
        # HIS RULE, and it is stronger than "does not wrap": "each entry line
        # should be exactly ONE row's worth of height. We need to make sure
        # elements look the same for a row regardless of whether it's in a
        # subentry or a top level entry." The parent's own row at the same
        # width is the control, so this cannot go red for a font change, and a
        # wrap anywhere shows up as a taller child row.
        # Round 23 measured the RESULT CELL with a 2px tolerance and passed
        # while he was looking at a wrapped one -- the cell is not the row, and
        # a tolerance is where a one-line difference hides.
        # Mutation-proved by dropping `white-space: nowrap` from
        # `.attempt-result`.
        assert abs(max(piece_rows) - max(parent_rows)) <= 1, (
            f"a piece's row is {max(piece_rows):.0f}px where its parent's is "
            f"{max(parent_rows):.0f}px at {floor}px -- it wrapped")
        # THE MECHANISM, asserted directly, because the comparison above has
        # NO TEETH IN THIS FIXTURE and saying so is the point: its seeded rows
        # are short enough that they do not wrap even with the rule removed,
        # at any width from 850 to 1500. Round 23 shipped a version of that
        # comparison and it passed while he was looking at a wrapped row. The
        # PAINTED value can still be pinned, which is what `.claude/rules/
        # ui-core.md` prescribes whenever a style is the whole claim -- and
        # this one goes red the moment the rule is dropped.
        assert page.evaluate(
            "getComputedStyle(document.querySelector("
            "'.log-card-children .attempt-table .attempt-result'))"
            ".whiteSpace") == "nowrap", (
            "a piece's result cell must never be allowed to wrap -- its check "
            "and its time belong on one line at every width and every depth")

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


# --- and the SEGMENT half of the same feature -------------------------------
# Its own `serve_ui` instance rather than SUBSECTION_PROJECT, deliberately:
# this is a CASTLE row, a different stage entirely, and folding it into the
# shared project would re-derive that project's whole `known_defects` table
# for a state its sweep has no reason to measure (the same reasoning
# `test_fixture_reaches_the_real_page.py`'s own standalone instances use).


def test_a_piece_of_a_segment_is_not_a_cell_of_its_own():
    """Round 30, 2026-08-09, against three pieces of his BLJs movement drawn
    as loose cells beside it: *"I would therefore expect NOT to see 'Key Door
    (R) - Wooden Door' next to BLJs, because it should instead be a button on
    the BLJs segment. We should reuse the exact system used for stars, and
    make sure this doesn't regress going forward."*

    Every star-side gate above stayed green through that bug, and structurally
    had to: a star row draws stars, so a piece of a SEGMENT has no parent cell
    there to ride. This is the case none of them can reach.

    Round 30's claim was that the parent wears a badge for its piece AND the
    piece is not also a cell of its own; round 31 (2026-08-10) retired the
    badge, so only the second half still holds -- the piece nests inside its
    parent's practice-log card instead (see the reds-star test below and
    `tests/test_ui_subsections.py`).
    """
    with serve_ui(castle_stage=2, seed_castle_pieces=True) as url, \
            get_driver().launch() as page:
        page.goto(url)
        page.wait_for(".stagebanner")
        page.wait_ms(400)
        names = page.evaluate(
            "Array.from(document.querySelectorAll('.stagebanner .starcell'))"
            ".map(c => (c.querySelector('.starname')||c).innerText)")
        assert any("Upstairs Run" in name for name in names), (
            f"the seeded movement is not on the row at all; drew {names!r}")
        assert not any("Key Door" in name for name in names), (
            f"the piece drew its own cell -- the round-30 bug exactly: {names!r}")
        assert page.count(".stagebanner .starcell.has-toggles") == 0, (
            "no cell wears a toggle overlay for its piece any more (round 31)")
        assert page.count(".stagebanner .cell-toggle-btn") == 0, (
            "the badge is gone; the piece's parent has nothing to draw for it "
            "here")


# --- and the STAR-as-piece half (spec 2026-08-10-reds-as-subsection) --------
# The reds star names its own movement as a parent (task 1, views.py's
# `parents` stamp); this is the render half, proving `nestSubsections`' rule
# 4 ("a parent with a nesting child earns a card") actually meets the
# ambient-arm exemption (`.claude/rules/hundred-coin.md`) that would
# otherwise leave the movement with no section to nest the star inside.


def test_the_reds_star_draws_inside_its_movement_s_card():
    """Griffin, 2026-08-10: "the Reds card should contain the star subsection
    inside of it, and if we do the star subsection, it'll show it inside and
    earns the card in this case, yes."

    The interaction this exists for: seg:reds->pipe:* `arms_ambiently`, and an
    ambient arm with nothing chosen publishes NO section (2026-08-05) -- while
    `nestSubsections`' rule 4 says a parent with a nesting child earns a card.
    Those two rules meet here for the first time. If the exemption wins, the
    star vanishes from the log instead of nesting, silently."""
    with serve_ui(reconcile_full_corpus=True,
                  bowser_stage=(BOWSER_COURSE, BOWSER_LEVEL),
                  enter_level=BOWSER_LEVEL,
                  seed_reds_run=True) as url, \
            get_driver().launch() as page:
        page.goto(url)
        page.wait_for(".log-list-card")
        page.wait_ms(400)
        top = page.evaluate(
            "Array.from(document.querySelectorAll('.log-list > .log-card'))"
            ".map(c => (c.querySelector('.log-card-name')||{}).innerText)")
        nested = page.evaluate(
            "Array.from(document.querySelectorAll("
            "'.log-card-children > .log-card'))"
            ".map(c => (c.querySelector('.log-card-name')||{}).innerText)")
        assert any("Pipe" in name for name in top), (
            f"the Reds movement earned no card of its own; top cards {top!r}")
        assert any("Star" in name for name in nested), (
            f"the reds star is not nested inside it; nested {nested!r}")
        assert not any("Star" in name for name in top), (
            "the star must not ALSO be a top-level card")
