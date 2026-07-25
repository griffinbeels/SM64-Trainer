from pathlib import Path

HEADER_JS = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
             / "ui" / "components" / "header.js").read_text(encoding="utf-8")


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
