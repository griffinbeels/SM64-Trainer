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


def test_css_indents_every_level_and_never_scrolls_sideways():
    # There is no --depth custom property (review I4: it was dead — nothing
    # read it). Indent comes from the DOM nesting itself, and EVERY level
    # indents so a child always sits one step right of its parent (user rule
    # 2026-07-25). One .lib-group rule carries margin + padding + the guide
    # line; nesting compounds it.
    assert "--depth" not in INDEX
    group_rule = INDEX.split(".lib-group {", 1)[1].split("}", 1)[0]
    for indent_property in ("margin-left:", "padding-left:",
                            "border-left: 1px solid var(--border-soft)"):
        assert indent_property in group_rule, indent_property
    # No depth-specific exception may cancel the indent — that is exactly what
    # left sub-group HEADERS flush with their parent (live audit 2026-07-25).
    assert "border-left: none" not in INDEX
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
