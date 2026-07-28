"""The practice plan is ONE selection with TWO surfaces (the header's route
rank card and the Practice tab's route focus), so it lives in the store rather
than in either of them. It was practice.js state until 2026-07-28; the header
cannot reach a component's useState, and a second copy would be a second
answer to "which route am I practising"."""
from pathlib import Path

from source_scan import code_only

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
STORE = code_only(UI / "store.js")
PRACTICE = code_only(UI / "components" / "practice.js")
HEADER = code_only(UI / "components" / "header.js")


def test_the_store_owns_the_active_route():
    assert "sm64.activeRoute" in STORE
    assert "activeRouteId" in STORE and "pickRoute" in STORE


def test_no_component_keeps_its_own_copy_of_the_selection():
    # The localStorage key and the /api/route/select write are the two halves
    # of owning this. Either one appearing in a component is a second owner.
    for name, source in (("practice.js", PRACTICE), ("header.js", HEADER)):
        assert "sm64.activeRoute" not in source, name
        assert "/api/route/select" not in source, name


def test_the_route_list_comes_from_the_scopes_endpoint():
    # One list, one endpoint, shared with the Rank tab's scope picker (user,
    # 2026-07-27: "These should be identical lists ... the exact same set of
    # options that trigger the exact same things"). Course scopes are dropped
    # -- a course is a rating you can browse, not a plan you can practise.
    assert "/api/marelo/scopes" in STORE
