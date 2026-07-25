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


def test_css_indents_by_nesting_and_never_scrolls_sideways():
    # There is no --depth custom property (review I4: it was dead — nothing
    # read it). Indent comes from the DOM nesting itself: a depth-0 group has
    # no guide line, a NESTED one does, via a more specific selector.
    assert "--depth" not in INDEX
    assert ".lib-cat > .lib-group { margin-left: 0; padding-left: 0; border-left: none; }" in INDEX
    assert ".lib-cat .lib-cat > .lib-group" in INDEX
    assert "border-left: 1px solid var(--border-soft);" in INDEX
    # the row-stretch rule that stopped the horizontal scrollbar
    assert "width: auto" in INDEX


def test_no_route_specific_group_classes_remain():
    for legacy in (".route-cat", ".route-subcat"):
        assert legacy not in INDEX, f"{legacy} should be .lib-cat*"


ROUTES = (UI / "components" / "routes.js").read_text(encoding="utf-8")


def test_routes_library_uses_the_shared_primitives():
    assert "buildTree" in ROUTES and "GroupedList" in ROUTES
    # the local implementations are gone, not merely unused
    assert "function groupByCategory" not in ROUTES
    assert "function loadOpenGroups" not in ROUTES


def test_routes_keeps_its_existing_open_state_key():
    # A new key here would silently reset every user's open groups.
    assert '"sm64.routeCatsOpen"' in ROUTES
