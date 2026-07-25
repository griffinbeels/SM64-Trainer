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


def test_the_grid_hides_the_rank_slot_it_never_fills():
    # Nothing grades a cell in the picker, so the slot rendered a column of "–"
    # costing a line per ROW — most of the overflow that made it scroll.
    grid_rules = INDEX[INDEX.index(".entity-grid"):]
    assert ".entity-grid .starrank { display: none; }" in grid_rules
