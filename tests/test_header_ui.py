import re
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"
HEADER_JS = (UI / "components" / "header.js").read_text(encoding="utf-8")
PICKER_JS = (UI / "components" / "targetpicker.js").read_text(encoding="utf-8")
PRACTICE_JS = (UI / "components" / "practice.js").read_text(encoding="utf-8")
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
    # Three cards today: session, clock, grading. A fourth raises the count
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


# The header's PRACTICE TARGET card is gone (2026-07-26, user): it named a
# target the Active-target card and the quick-select row already name, and its
# own pick was mostly a dead end — you cannot practice Shifting Sand Land
# while loaded into Lethal Lava Land. The picker it opened moved to
# components/targetpicker.js and is triggered from the Active-target card;
# the four tests that used to pin it here now read PICKER_JS.


def test_the_header_no_longer_carries_a_practice_target_card():
    body = strip_comments(HEADER_JS)
    assert "target-context" not in body
    assert "PickerDialog" not in body and "StrategyStep" not in body
    # The armed "Running" chip rode that card and was dropped with it (user's
    # explicit call): armed state is shown on the Practice tab by the stage
    # banner's own running chip and the pinned segment card.
    assert "armedOrder" not in body and "armedNames" not in body


def test_the_rank_bar_sits_in_the_context_grid_not_a_row_of_its_own():
    body = strip_comments(HEADER_JS)
    assert body.count("<${MareloBar}") == 1
    assert "marelo-row" not in body and "marelo-row" not in strip_comments(INDEX_HTML)
    # MareloBar renders null until /api/marelo lands. A null grid child is no
    # child at all, so without a wrapper the clock card would slide into this
    # column and the whole bar would shift left for a beat.
    assert 'class="marelo-slot"' in body
    assert ".marelo-slot" in strip_comments(INDEX_HTML)


def test_the_rank_mode_card_is_not_also_called_rank():
    # It sets HOW a rank is graded and now sits two cards from the MARELO bar,
    # which shows what your rank IS. Two cards reading RANK side by side, one
    # of them a mode, is the correct-but-unexplained pairing that reads as a
    # rendering fault. The wire contract (id/name/endpoint) is unchanged.
    body = strip_comments(HEADER_JS)
    assert 'label="Grading"' in body
    assert 'label="Rank"' not in body
    assert 'id="rankmode-select"' in body and "/api/ranks/mode" in body


def test_the_picker_is_triggered_from_the_active_target_card():
    body = strip_comments(PRACTICE_JS)
    assert "useTargetPicker" in body
    # ONE instance for the page: mounting the dialog's state inside every card
    # in the practice index would pay for ~30 copies of a fetch effect.
    assert body.count("useTargetPicker(t)") == 1
    # Star card, segment card and the no-target card all open the same dialog
    # from the same place (rule 11 parity + the empty state needs it most).
    assert body.count("<${ObjectiveEyebrow}") == 3
    assert ".objective-pick" in strip_comments(INDEX_HTML)


def test_target_picker_is_the_icon_modal():
    # targetpicker.js renders PickerDialog directly rather than EntityPicker:
    # its caller's own element IS the trigger, so it has no use for
    # EntityPicker's own <button class="entity-trigger">. The other
    # EntityPicker call sites (segment builder, route step editor) are
    # untouched -- this only asserts what THIS file does.
    assert "PickerDialog" in PICKER_JS
    assert "EntityPicker" not in PICKER_JS
    assert "GroupedPicker" not in PICKER_JS
    assert "optionIcon" in PICKER_JS


def test_target_editor_is_gone_the_trigger_opens_the_picker_directly():
    # The inline two-field TargetEditor card is deleted; its star field and
    # strategy field are steps 2 and 3 of the SAME picker dialog
    # (StrategyStep is the third layer, wired via PickerDialog's `nextStep`).
    assert "TargetEditor" not in PICKER_JS
    assert "StrategyStep" in PICKER_JS
    assert "nextStep=" in PICKER_JS


def test_target_picker_has_no_clear_cell():
    # placeholder=null renders no clear cell (entitymodal.js). The old
    # control's clear cell was dead by construction: /api/target requires an
    # identity, so clicking it posted {course_id: null, star_id: null}, the
    # server 409d, and the button silently did nothing (whole-branch review,
    # task 7 brief, 2026-07-25). This is a live bug removed, not a refactor.
    assert "placeholder=${null}" in PICKER_JS


def test_target_ranks_are_fetched_only_when_the_dialog_opens():
    # The picker's host re-renders on every WebSocket event; the ranks fetch
    # must be keyed on the dialog's own open state, not run on every render.
    assert "/api/target/ranks" in PICKER_JS
    assert "}, [editing]);" in PICKER_JS


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
    # (stagebanner.js, practice.js) closes AND refreshes explicitly right
    # after, for exactly this reason -- the picker dialog must too, on every
    # dismissal (a plain Esc/backdrop close refetches data that didn't
    # change, which is harmless).
    assert "function close() { setEditing(false); t.refresh(); }" in PICKER_JS
    assert "onPick=${close} onClose=${close}" in PICKER_JS


def test_layer_one_cells_carry_a_course_portrait():
    # The heading-vs-cell decision itself lives in entitymodal.js, which this
    # file never opens — so assert only what IS this call site's job: attaching
    # an icon to each course group (review M4: the old version asserted the
    # five-character substring "icon:" and could not fail for its stated
    # reason).
    assert 'icon: optionIcon("course"' in PICKER_JS


def test_target_picker_resolves_segment_art_like_the_banner_does():
    # Without segmentLevels + iconOverrides in the icon context, every segment
    # cell falls through to a plain gold star while the banner and the route
    # editor show its real art — and a user's explicit icon override is
    # ignored (whole-branch review I1, 2026-07-25).
    assert "segmentLevelsOf(t.segments)" in PICKER_JS
    assert "icon_overrides" in PICKER_JS
