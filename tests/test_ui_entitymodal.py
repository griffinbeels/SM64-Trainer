"""Source contracts for the modal entity picker.

The real verification is the render check in this task's Step 5 — a custom
control's keyboard path cannot be proven by reading. These pin the pieces that
a refactor could silently drop.
"""
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"
MODAL = (UI / "components" / "entitymodal.js").read_text(encoding="utf-8")
INDEX = (UI / "index.html").read_text(encoding="utf-8")

def _strip_comments(source: str) -> str:
    """JS with comments removed — assertions must inspect CODE, not prose."""
    without_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_blocks, flags=re.MULTILINE)


def test_reuses_the_shared_filter_rather_than_reimplementing_it():
    # The keep-the-current-value-listed invariant has its own tests against
    # entities.js; the picker must not grow a second copy of that logic.
    assert "visibleGroups" in MODAL
    assert "from \"../entities.js\"" in MODAL


def test_keyboard_contract_is_implemented():
    # The grid's cells are real <button>s, so Tab/Enter/Space are native and
    # nothing here re-implements them. What IS ours: Escape backs out of a
    # drilled-in group before closing the dialog (2026-07-25 redesign).
    assert "Escape" in MODAL
    assert "setOpenGroupKey(null)" in MODAL


def test_it_does_not_claim_an_aria_pattern_it_has_not_implemented():
    # role="grid" promises gridcell/row structure and roving tabindex. The
    # cells are plain buttons in a container, so claiming it would tell a
    # screen reader a lie — the honest markup is no role at all.
    #
    # Comments are stripped FIRST: the component's header explains both roles
    # by name, and a guard that a comment can trip is not a guard. That mistake
    # has now been made four separate times in this codebase.
    code = _strip_comments(MODAL)
    assert 'role="grid"' not in code
    assert 'role="listbox"' not in code   # the list version is gone
    assert "aria-haspopup" in code        # the trigger still announces itself


def test_the_component_owns_no_domain_vocabulary():
    # Icons come from the caller via iconFor(); the picker must not learn what
    # a course or a star is. Comments stripped first — a guard that a comment
    # can satisfy is not a guard (learned 2026-07-25).
    import re
    source = re.sub(r"/\*.*?\*/", "", MODAL, flags=re.S)
    source = re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)
    for domain_word in ("course", "star", "vocab", "catalog", "segment"):
        assert domain_word not in source.lower(), domain_word


def test_row_art_has_a_fixed_box_so_a_missing_image_cannot_reflow_the_list():
    assert ".entity-row-icon" in INDEX
