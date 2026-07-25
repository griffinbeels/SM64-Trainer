import re
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"
HEADER_JS = (UI / "components" / "header.js").read_text(encoding="utf-8")
INDEX_HTML = (UI / "index.html").read_text(encoding="utf-8")


def context_select_rule(css: str) -> str:
    """The declarations that stretch a context card's <select> over the card."""
    found = re.search(r"\.context-select\s*>\s*select\s*\{([^}]*)\}",
                      strip_comments(css))
    return found.group(1) if found else ""


def test_every_context_card_is_one_hit_target():
    # A click ANYWHERE on a context card opens it, and the card highlights as
    # a unit — the practice-target card did this for free by being a <button>,
    # the three select cards only reacted on the select itself, and the
    # mismatch read as a bug (user, 2026-07-25). The fix lives half in JS (the
    # shared ContextSelect renders the value + chevron and tags the card) and
    # half in CSS (that select is absolutely stretched over the card). Either
    # half alone silently restores the small hit target, so pin both.
    # Three cards today: session, clock, rank. A fourth raises the count
    # deliberately — it must not appear by growing a hand-rolled one.
    assert strip_comments(HEADER_JS).count("<${ContextSelect}") == 3
    rule = context_select_rule(INDEX_HTML)
    assert "position: absolute" in rule and "inset: 0" in rule, rule
    # Hidden by OPACITY, never by transparent colours: Chromium themes a
    # select's popup off its computed background, so a transparent one gets a
    # white list (tests/test_ui_dropdown_theming.py owns that rule).
    assert "opacity: 0" in rule, rule


def test_the_hit_target_guard_can_still_fail():
    # Probed in both directions (tests/source_scan.py): a comment naming the
    # rule must not satisfy it, and the real rule must.
    assert context_select_rule("/* .context-select > select { inset: 0 } */") == ""
    assert "inset: 0" in context_select_rule(
        ".context-select > select { position: absolute; inset: 0; }")


def test_target_modal_still_posts_course_and_star_as_numbers():
    # The API contract is unchanged — only the control collapsed. This must
    # catch the string/number boundary the refactor introduced (a picked id
    # is a STRING; the endpoint needs integers) — `"course_id:" in HEADER_JS`
    # alone would pass even if a raw string reached the API (review M6).
    assert "course_id: Number(course)" in HEADER_JS
    assert "star_id: Number(star)" in HEADER_JS


def test_target_picker_is_the_icon_modal():
    assert "EntityPicker" in HEADER_JS
    assert "GroupedPicker" not in HEADER_JS
    assert "optionIcon" in HEADER_JS


def test_layer_one_cells_carry_a_course_portrait():
    # The heading-vs-cell decision itself lives in entitymodal.js, which this
    # file never opens — so assert only what IS this call site's job: attaching
    # an icon to each course group (review M4: the old version asserted the
    # five-character substring "icon:" and could not fail for its stated
    # reason).
    assert 'icon: optionIcon("course"' in HEADER_JS
def test_target_picker_resolves_segment_art_like_the_banner_does():
    # Without segmentLevels + iconOverrides in the icon context, every segment
    # cell falls through to a plain gold star while the banner and the route
    # editor show its real art — and a user's explicit icon override is
    # ignored (whole-branch review I1, 2026-07-25).
    assert "segmentLevelsOf(t.segments)" in HEADER_JS
    assert "icon_overrides" in HEADER_JS
