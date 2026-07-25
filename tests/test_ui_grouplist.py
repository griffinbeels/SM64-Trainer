"""The shared collapsible group renderer. These are SOURCE contracts — the
behavioural check is the headless render in Tasks 6 and 8 (a UI feature has
shipped invisible here before on unit tests alone)."""
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"
GROUPLIST = (UI / "components" / "grouplist.js").read_text(encoding="utf-8")
INDEX = (UI / "index.html").read_text(encoding="utf-8")


def test_open_state_is_stored_as_the_open_set_not_the_closed_set():
    # Inversion from the route work: nothing stored MUST mean all collapsed.
    assert "JSON.parse(localStorage.getItem(openKey))" in GROUPLIST
    assert "new Set()" in GROUPLIST


def test_renderer_recurses_so_depth_is_not_capped_at_two():
    assert "GroupedList" in GROUPLIST
    assert "depth=${depth + 1}" in GROUPLIST


def test_css_indents_by_depth_variable_and_never_scrolls_sideways():
    assert "--depth" in INDEX
    assert ".lib-cat" in INDEX
    # the row-stretch rule that stopped the horizontal scrollbar
    assert "width: auto" in INDEX


def test_no_route_specific_group_classes_remain():
    for legacy in (".route-cat", ".route-subcat"):
        assert legacy not in INDEX, f"{legacy} should be .lib-cat*"
