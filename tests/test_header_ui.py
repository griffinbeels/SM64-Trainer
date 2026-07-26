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


# test_target_modal_still_posts_course_and_star_as_numbers used to live here,
# pinning "course_id: Number(course)" / "star_id: Number(star)" in HEADER_JS.
# Task 7 (2026-07-25-target-picker-strategy-step) deleted the inline
# TargetEditor that write lived in -- the header now opens the picker dialog
# directly and the write moved to strategystep.js, which owns the entity ids
# once and for all. The same string->number boundary is now pinned by
# tests/test_ui_strategy_step.py::test_posts_course_and_star_as_numbers (and
# ::test_posts_segment_id_as_a_number_too for the segment shape this form
# never had).


def test_target_picker_is_the_icon_modal():
    # header.js renders PickerDialog directly rather than EntityPicker: its
    # own context card IS the trigger, so it has no use for EntityPicker's
    # own <button class="entity-trigger"> (task 7, 2026-07-25). The other
    # three EntityPicker call sites (segment builder, route step editor) are
    # untouched -- this only asserts what THIS file does.
    assert "PickerDialog" in HEADER_JS
    assert "EntityPicker" not in HEADER_JS
    assert "GroupedPicker" not in HEADER_JS
    assert "optionIcon" in HEADER_JS


def test_target_editor_is_gone_the_card_opens_the_picker_directly():
    # The inline two-field TargetEditor card is deleted; its star field and
    # strategy field are now steps 2 and 3 of the SAME picker dialog
    # (StrategyStep is the third layer, wired via PickerDialog's `nextStep`).
    assert "TargetEditor" not in HEADER_JS
    assert "StrategyStep" in HEADER_JS
    assert "nextStep=" in HEADER_JS


def test_target_picker_has_no_clear_cell():
    # placeholder=null renders no clear cell (entitymodal.js). The old
    # control's clear cell was dead by construction: /api/target requires an
    # identity, so clicking it posted {course_id: null, star_id: null}, the
    # server 409d, and the button silently did nothing (whole-branch review,
    # task 7 brief, 2026-07-25). This is a live bug removed, not a refactor.
    assert "placeholder=${null}" in HEADER_JS


def test_target_ranks_are_fetched_only_when_the_dialog_opens():
    # The header re-renders on every WebSocket event; the ranks fetch must be
    # keyed on the dialog's own open state, not run on every render.
    assert "/api/target/ranks" in HEADER_JS
    assert "}, [editing]);" in HEADER_JS


def test_closing_the_target_picker_refreshes_the_view():
    # StrategyStep's write is a plain POST, not something the client always
    # hears about over the WebSocket: re-picking a star that's ALREADY the
    # target with a new (or first) strategy leaves the projector's target
    # tuple unchanged, so service.py's auto target_changed republish never
    # fires, and set_target's truthy-strat_tag branch never calls set_strat
    # either (unlike set_target_segment, which delegates to
    # set_strat_segment and self-heals) -- verified empirically against
    # TrackerService: that specific call publishes ONLY "target_set", which
    # is not in store.js's REFRESH_ON. Every other /api/target call site
    # (stagebanner.js, practice.js, the deleted TargetEditor.apply()) closes
    # AND refreshes explicitly right after, for exactly this reason -- the
    # picker dialog must too, on every dismissal (a plain Esc/backdrop close
    # refetches data that didn't change, which is harmless).
    assert "function closeTargetPicker() { setEditing(false); t.refresh(); }" in HEADER_JS
    assert "onPick=${closeTargetPicker} onClose=${closeTargetPicker}" in HEADER_JS


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
