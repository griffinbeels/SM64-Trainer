import re
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"
HEADER_JS = (UI / "components" / "header.js").read_text(encoding="utf-8")
PICKER_JS = (UI / "components" / "targetpicker.js").read_text(encoding="utf-8")
PRACTICE_JS = (UI / "components" / "practice.js").read_text(encoding="utf-8")
INDEX_HTML = (UI / "index.html").read_text(encoding="utf-8")
MARELO_JS = (UI / "components" / "marelo.js").read_text(encoding="utf-8")
CONTEXT_JS = (UI / "components" / "contextselect.js").read_text(encoding="utf-8")


def context_select_rule(css: str) -> str:
    """The declarations that stretch a context card's <select> over the card."""
    found = re.search(r"\.context-select\s*>\s*select\s*\{([^}]*)\}",
                      strip_comments(css))
    return found.group(1) if found else ""


def test_every_context_card_is_one_hit_target():
    # A click ANYWHERE on a context card opens it, and the card highlights as
    # a unit (user, 2026-07-25 -- the mismatch between a whole-card <button>
    # and three select-only cards read as a bug). The fix is half JS (the
    # stretched <select> plus the value + chevron we draw ourselves) and half
    # CSS; either half alone silently restores the small hit target.
    #
    # FOUR cards since 2026-07-28: session, route rank, clock, grading. The
    # fourth is the route rank card, and it must go through the SAME
    # mechanism rather than hand-rolling one -- which is what CardSelect is
    # for, and why the count below is of CardSelect and not of markup.
    assert strip_comments(HEADER_JS).count("<${ContextSelect}") == 3
    assert strip_comments(CONTEXT_JS).count("<${CardSelect}") == 1
    assert strip_comments(MARELO_JS).count("<${CardSelect}") == 1
    rule = context_select_rule(INDEX_HTML)
    assert "position: absolute" in rule and "inset: 0" in rule, rule
    # Hidden by OPACITY, never by transparent colours: Chromium themes a
    # select's popup off its computed background, so a transparent one gets a
    # white list (tests/test_ui_dropdown_theming.py owns that rule).
    assert "opacity: 0" in rule, rule


def test_the_rank_card_names_the_scope_it_is_rating():
    # "the M 25.6 and C 16% feels like worthless AI slop information to me. It
    # should just be clear that this is the OVERALL RANKING FOR THE ROUTE THAT
    # I'M PRACTICING" (user, 2026-07-28). Mastery and Coverage keep their real
    # meters on the Rank tab and the card's own title; the freed line is what
    # lets the scope name sit under the rank.
    #
    # The label reads "Overall"/"Route", not "Overall rank"/"Route rank": the
    # responsive sweep (tools/responsive_probe.js's NEVER_TRUNCATE, which
    # `.context-label` is opted into everywhere else it appears -- Session,
    # Clock, Grading) caught the longer pair genuinely truncating at several
    # widths, and this codebase already made the identical call for an
    # identical squeeze (ui-ranks.md: "Round 4 dropped the trailing 'Rank'
    # from both kickers") -- the big rank icon and name right below the label
    # already say "rank".
    code = strip_comments(MARELO_JS)
    assert "marelo-split" not in code
    assert '"Overall"' in code and '"Route"' in code


def test_the_rank_card_never_renders_nothing():
    # It hosts the route picker now, so the control has to exist before the
    # rating does. `.marelo-slot:empty` was the placeholder that covered the
    # old null render and goes with it.
    assert "return null" not in strip_comments(MARELO_JS)
    assert ".marelo-slot:empty" not in strip_comments(INDEX_HTML)


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
    assert body.count("<${RouteRankCard}") == 1
    assert "marelo-row" not in body and "marelo-row" not in strip_comments(INDEX_HTML)
    # The wrapper carries container-type: inline-size for the card's own
    # @container rules -- without it the clock card would slide into this
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
    assert "optionIconSrc" in PICKER_JS


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
    assert 'icon: optionIconSrc(t, "course"' in PICKER_JS


def test_target_picker_resolves_segment_art_like_the_banner_does():
    # A hand-built icon context is how this picker drew a plain gold star for
    # a segment the banner drew real art for (whole-branch review I1,
    # 2026-07-25 — the context was missing segmentLevels and iconOverrides).
    # It now takes entityicons.js's shared builder, which is the only way the
    # two surfaces can be structurally unable to disagree (2026-07-26, after
    # the Rank tab turned out to have the same bug for a different field).
    assert "optionIconSrc(t" in PICKER_JS
    assert 'from "./entityicons.js"' in PICKER_JS
    assert "iconContext" not in strip_comments(PICKER_JS), \
        "the target picker is back to holding an icon context of its own"


def test_the_practice_toolbar_is_gone():
    # Its route select was the SAME control as the header card's (practice.js
    # said so in a comment), its guidance line described a control that has
    # moved, and its Stats button now sits beside the chips it configures. A
    # card holding one leftover sentence reads as unfinished.
    code = strip_comments(PRACTICE_JS)
    assert "practice-toolbar" not in code
    assert "route-focus-control" not in code
    assert "practice-toolbar" not in strip_comments(INDEX_HTML)


def test_the_tuning_page_is_reachable_from_the_app_and_from_the_launcher():
    """A dev page behind a hand-typed path is a page that does not exist.

    The tuning inspector spent its first day that way and went missing the
    moment the port moved -- the user had restarted on 8066 while the only
    written-down URL said 8065 ("I clicked run-test-server.bat and restarted
    our server on 8066, but I don't see the demo page loading. If I click
    run-test-server.bat, I would expect any demo pages we have to also work",
    2026-07-27). Two entry points, so neither the app nor the launcher can be
    the only one that knows.

    The href must stay ORIGIN-RELATIVE. The server binds 8064 frozen, 8065
    from source and whatever run-test-server.bat was given, so any absolute
    URL here is wrong in at least two of the three -- which is the whole bug.
    """
    code = strip_comments(HEADER_JS)
    assert 'href="/ui/tune.html"' in code, \
        "the settings drawer no longer links to the tuning page"
    assert not re.search(r'href="https?://[^"]*tune\.html', code), \
        "the tuning link must be origin-relative, never a hardcoded host/port"
    # The overall rank-up's own inspector (Wave 3, 2026-07-28) needs the same
    # guarantee -- a link only the header knows about is a page that does not
    # exist to a user who has not memorized its path.
    assert 'href="/ui/tunemarelo.html"' in code, \
        "the settings drawer no longer links to the overall rank-up tuning page"
    assert not re.search(r'href="https?://[^"]*tunemarelo\.html', code), \
        "the tunemarelo.html link must be origin-relative, never a hardcoded host/port"

    launcher = (UI.parent.parent.parent / "run-test-server.bat").read_text(
        encoding="utf-8")
    assert "/ui/tune.html" in launcher, \
        "run-test-server.bat must print every dev page it hosts"
    assert "%SM64_PORT%/ui/tune.html" in launcher, \
        ("the launcher's URL must interpolate the port it actually chose -- a "
         "literal port there is the exact failure this test exists for")


def test_the_card_label_is_derived_from_the_rated_scope_not_the_client_slot():
    """The label and the rating beneath it must answer to ONE source.

    They did not. `activeRouteId` is the client's localStorage mirror and
    `marelo` is the server's answer, so any client that had never written that
    key -- a fresh browser, cleared storage, the desktop GUI's first run --
    rendered the label "Overall" directly above its own value line reading
    "16 Star - LBLJ (Standard)". Caught by RENDER on 2026-07-28 and invisible
    to every unit test in the suite, because each half was individually right.
    """
    code = strip_comments(MARELO_JS)
    found = re.search(r"const cardLabel = (.*?);", code, re.S)
    assert found, "cardLabel was renamed or removed -- re-point this guard"
    expression = found.group(1)
    assert "scope_id" in expression, expression
    assert "activeRouteId" not in expression, expression
