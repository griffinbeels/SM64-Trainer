"""Source contracts for the modal entity picker.

The real verification is the render check in this task's Step 5 — a custom
control's keyboard path cannot be proven by reading. These pin the pieces that
a refactor could silently drop.
"""
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"
MODAL = (UI / "components" / "entitymodal.js").read_text(encoding="utf-8")
INDEX = (UI / "index.html").read_text(encoding="utf-8")


def test_reuses_the_shared_filter_rather_than_reimplementing_it():
    # The keep-the-current-value-listed invariant has its own tests against
    # entities.js; the picker must not grow a second copy of that logic.
    assert "visibleGroups" in MODAL
    assert "from \"../entities.js\"" in MODAL


def test_keyboard_contract_is_implemented():
    # What native <select> gave for free and a custom control must earn back.
    for key in ("ArrowDown", "ArrowUp", "Enter", "Escape"):
        assert key in MODAL, key


def test_rows_are_listbox_options_for_screen_readers():
    assert 'role="listbox"' in MODAL
    assert 'role="option"' in MODAL
    assert "aria-activedescendant" in MODAL


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
