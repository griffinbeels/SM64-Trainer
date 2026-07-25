from pathlib import Path

HEADER_JS = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
             / "ui" / "components" / "header.js").read_text(encoding="utf-8")


def test_target_modal_picks_a_star_with_one_control():
    # Redesigned 2026-07-25: still ONE control, now a two-layer grid whose
    # layer 2 unions a course's stars with the segments that begin in it.
    # Course + Star collapsed into one grouped control (user decision
    # 2026-07-25): the optgroup is the course, the option is the star.
    assert "courseUnionGroups" in HEADER_JS   # layer 1 = courses
    assert "EntityPicker" in HEADER_JS
    assert "parseStarId" in HEADER_JS


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


def test_course_portraits_ride_the_group_heading():
    # The course is the group; its portrait belongs on the heading, not
    # repeated on all seven star rows (spec decision 3).
    assert "icon:" in HEADER_JS or "group.icon" in HEADER_JS
