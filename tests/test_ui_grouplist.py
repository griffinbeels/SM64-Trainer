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


def test_workshop_panes_are_viewport_bounded_so_the_page_never_shifts():
    # Live audit 2026-07-25: opening a library group made the page taller than
    # the viewport, a page scrollbar appeared, and because a scrollbar steals
    # width the ENTIRE layout shifted sideways. The library was already capped;
    # the right-hand pane was not, and it was the one overflowing.
    # Measured after the fix at 1600x{760,880,1000}: page does not scroll, both
    # panes sit 39px clear of the fold, the editor scrolls internally.
    # The cap is MEASURED (ui/viewport.js sets --pane-cap from the pane's real
    # distance to the fold), with the old constant left only as the fallback.
    # A hardcoded offset was wrong three times: the card ran off-screen
    # (2026-07-24), then the page still scrolled, twice (2026-07-25) — the hero
    # wraps, the context bar reflows, and browser chrome differs from the
    # desktop shell, so no constant can be right for all of them.
    for pane in (".segment-editor", ".route-workspace"):
        block = INDEX.split(pane + " {", 1)[1].split("}", 1)[0]
        assert "max-height: var(--pane-cap" in block, pane
    viewport = (UI / "viewport.js").read_text(encoding="utf-8")
    assert "--pane-cap" in viewport
    # the second pass that learns what sits BELOW the pane; without it the page
    # still overflowed by 12px (measured)
    assert "scrollHeight - window.innerHeight" in viewport
    # Belt to that braces: reserve the gutter so any future overflow anywhere
    # cannot shove the layout sideways.
    assert "scrollbar-gutter: stable" in INDEX


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


def test_route_item_picker_uses_the_shared_picker():
    assert "GroupedPicker" in ROUTES
    assert "starOptionsFromCatalog" in ROUTES
    assert "segmentOptions" in ROUTES


def test_route_segment_picker_groups_like_the_library():
    # The segment list beside it groups by origin region; this one did not.
    assert "segmentOptions(segs" in ROUTES
