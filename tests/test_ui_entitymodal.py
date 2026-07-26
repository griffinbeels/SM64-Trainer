"""Source contracts for the modal entity picker.

The real verification is the render check in this task's Step 5 — a custom
control's keyboard path cannot be proven by reading. These pin the pieces that
a refactor could silently drop.

Every assertion here runs against MODAL_CODE, never the raw file: the
component's header comment names Escape, role="grid" and role="listbox" while
explaining them, so a raw-text guard reports the prose and not the code (see
tests/source_scan.py).
"""
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"
MODAL = (UI / "components" / "entitymodal.js").read_text(encoding="utf-8")
MODAL_CODE = strip_comments(MODAL)
INDEX = (UI / "index.html").read_text(encoding="utf-8")


def test_reuses_the_shared_filter_rather_than_reimplementing_it():
    # The keep-the-current-value-listed invariant has its own tests against
    # entities.js; the picker must not grow a second copy of that logic.
    assert "visibleGroups" in MODAL_CODE
    assert "from \"../entities.js\"" in MODAL_CODE


def test_keyboard_contract_is_implemented():
    # The grid's cells are real <button>s, so Tab/Enter/Space are native and
    # nothing here re-implements them. What IS ours: Escape backs out of a
    # drilled-in group before closing the dialog (2026-07-25 redesign).
    assert "Escape" in MODAL_CODE
    assert "setOpenGroupKey(null)" in MODAL_CODE


def test_it_does_not_claim_an_aria_pattern_it_has_not_implemented():
    # role="grid" promises gridcell/row structure and roving tabindex. The
    # cells are plain buttons in a container, so claiming it would tell a
    # screen reader a lie — the honest markup is no role at all.
    assert 'role="grid"' not in MODAL_CODE
    assert 'role="listbox"' not in MODAL_CODE   # the list version is gone
    assert "aria-haspopup" in MODAL_CODE        # the trigger still announces itself


# The picker's no-domain-vocabulary guard lives in tests/test_ui_picker_parity.py
# (`test_the_picker_owns_no_domain_vocabulary`), which owns the shared-picker
# contract and probes the guard against real code on every run. This file had a
# second copy with a different word list — parallel branches, same rule.


def test_row_art_has_a_fixed_box_so_a_missing_image_cannot_reflow_the_list():
    assert ".entity-row-icon" in INDEX


def test_the_grid_opens_the_WIDE_modal_shell():
    # 25 course cells in the default 600px shell came out 5 columns x 5 rows
    # and scrolled — the exact thing a grid replaced a scrolling list to avoid
    # (live audit 2026-07-25). At ~1100px they lay out 9 across in 3 rows.
    assert 'size="grid"' in MODAL_CODE
    assert ".modal-grid" in INDEX


CELL = (UI / "components" / "practicecell.js").read_text(encoding="utf-8")
CELL_CODE = strip_comments(CELL)


def _grid_rank_guard_holds(index_source: str, cell_source: str) -> bool:
    """Task 4 (2026-07-25): the picker grades a cell via an out-of-flow
    .starrank-badge over the art, never the banner's in-flow .starrank row —
    an in-flow row cost a line per grid ROW even when unranked, most of the
    94px that made the picker scroll on a 900px-tall window (live audit
    2026-07-25), and grading the cells does not pay that back (a course with
    two of seven stars practiced still renders five "–" placeholders). Both
    halves of that contract must survive together: the CSS still hides the
    in-flow row, AND the cell component still has a rankBadge branch to draw
    the replacement — either alone regressing to the scrolling row is the
    bug this guards."""
    grid_css = strip_comments(index_source)
    grid_rules = grid_css[grid_css.index(".entity-grid"):] if ".entity-grid" in grid_css else ""
    guard_present = ".entity-grid .starrank { display: none; }" in grid_rules
    badge_wired = "rankBadge" in strip_comments(cell_source)
    return guard_present and badge_wired


def test_the_grid_hides_the_in_flow_rank_row_and_grades_via_a_corner_badge():
    assert _grid_rank_guard_holds(INDEX, CELL)


def test_the_grid_rank_guard_can_still_fail():
    # Comment-only mentions of both pieces must stay green — a raw substring
    # check would trip on prose explaining the rule (see tests/source_scan.py
    # and test_the_guards_can_still_fail in test_ui_picker_parity.py, the
    # pattern this copies).
    assert not _grid_rank_guard_holds(
        "// .entity-grid .starrank { display: none; } used to live here\n",
        "// rankBadge used to gate the corner badge, removed in a refactor\n")
    # Real code, both halves present: caught as intact.
    assert _grid_rank_guard_holds(
        ".entity-grid { color: red; }\n.entity-grid .starrank { display: none; }",
        "function PracticeCell({ rankBadge = false }) {}")
    # Real code, only one half present: caught as broken either way.
    assert not _grid_rank_guard_holds(
        ".entity-grid { color: red; }\n.entity-grid .starrank { display: none; }",
        "function PracticeCell({}) {}")
    assert not _grid_rank_guard_holds(
        ".entity-grid { color: red; }",
        "function PracticeCell({ rankBadge = false }) {}")
