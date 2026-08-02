# tests/test_stagebanner_bowser_row.py
"""BitDW/BitFS/BitS: two cells since 2026-07-30 (spec 2026-07-28-multi-step-
segments, "the Bowser Reds star/pipe toggle"), superseding the THREE-cell row
912466d landed (live report 2026-07-29, same surface/root-cause class as the
100-coin fix in test_stagebanner_hundred_coin.py) and this file's own earlier
version, which pinned that three-cell shape.

Background, in order: the corpus reshape that arms the pipe-entry segments on
stage ENTRY gave every Bowser level TWO segment_targets entries sharing the
same start_levels — the legacy EXCLUSIVE pipe-only segment and the new STRICT
reds->pipe segment (waypointed on the reds grab). `BowserCourseRow` used to
render exactly ONE pipe cell hardcoded "No reds" and ENFORCE mutual exclusion
by writing the OTHER segment's `enabled` flag whenever one was picked — with
two real segments sharing a level that flag-toggle (a) hid one of them under
a duplicate "No reds" label and (b) fought the matcher, which already keeps
both armed in parallel by design ("if 1 is armed, 2 should always be armed",
user). 912466d deleted the toggle and rendered both pipe segments through the
shared StandardSegmentCell, giving THREE cells total (the reds star + both
pipe segments). This task folds the reds->pipe cell into a star/pipe TOGGLE
inside the Reds cell itself ("the third cell goes away... what replaces it is
a toggle inside the Reds cell", user) — TWO cells: "No Reds" (still the
legacy exclusive pipe-only segment, unchanged) and "Reds" (the star, now with
an internal toggle picking between its own grab-only timing and the
reds->pipe segment's timing).

stagebanner.js is not import-free, so — same approach as
test_stagebanner_hundred_coin.py and test_star_icons.py — these are
SOURCE-SCAN assertions; the rendered behaviour is verified by rendering (see
this task's own report, including a mid-transition frame of the rank icon's
squash/pop between the two families).
"""
import re
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"
BANNER = UI / "components" / "stagebanner.js"


def _function_body(name: str, source: str) -> str:
    match = re.search(rf"^(?:export )?function {name}\(.*?^}}", source, re.S | re.M)
    assert match, f"{name} not found in stagebanner.js"
    return match.group(0)


def _bowser_row_body() -> str:
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    return _function_body("BowserCourseRow", source)


def _reds_cell_body() -> str:
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    return _function_body("RedsCell", source)


def test_bowser_row_never_writes_the_enabled_flag_to_the_OTHER_segment():
    """The retired mutual-exclusion mechanism disabled the sibling segment
    whenever one was picked -- that write must stay gone. `RedsCell`'s own
    `onPickPipe` (wired from `pickPipe` below) DOES legitimately write
    `enabled: true` to enable the segment it is ABOUT to target, the exact
    same pattern `StandardSegmentCell.pick()` already uses elsewhere in this
    file -- an enable-before-targeting write, never a disable-the-sibling
    one. What must never come back is `enabled: false`."""
    body = _bowser_row_body()
    assert "enabled: false" not in body, (
        "BowserCourseRow writes enabled:false -- the retired mutual-exclusion "
        f"toggle may have come back: {body!r}"[:400])


def test_bowser_row_never_restores_a_pick_from_the_enabled_flag():
    """The retired mutual exclusion, and ONLY that.

    This banned `useEffect` outright until 2026-07-30, reasoning that the
    toggle's selection was derived from the target every render so there was
    nothing to restore. The user then asked for the opposite -- "We need to
    remember the option that the user selected ... the last time they visited a
    bowser stage. Currently we do not remember this correctly" -- so a
    restore-on-entry effect is now REQUIRED, and a blanket ban would forbid the
    feature it is meant to protect.

    What the ban was actually guarding is still forbidden and still checked. The
    old mechanism derived "which one was picked" from `segment_defs.enabled` and
    WROTE that flag to enforce the choice; that is what stranded two of the
    user's definitions at `enabled=0` and cost him two recorded runs (migration
    v16). The memory is now localStorage keyed by level and touches no server
    state -- so `enabledPipe` and any write of `enabled: false` stay banned
    while the effect itself is allowed.
    """
    body = _bowser_row_body()
    assert "enabledPipe" not in body, (
        "the enabled-flag-derived memory is back -- that flag is server state "
        "and using it as a preference stranded real definitions (v16)")
    assert "enabled: false" not in body and "enabled:false" not in body, (
        "BowserCourseRow must never DISABLE a segment; all options track "
        "together since 912466d")
    assert "bowserModeFor" in body, (
        "the row no longer restores the remembered star/pipe mode on entry "
        "(user, 2026-07-30)")


def test_bowser_row_renders_the_no_reds_cell_through_standard_segment_cell():
    """The remaining "No Reds" cell shows its OWN name/rank/strat (the
    shared cell already does this) instead of a hand-rolled cell hardcoding
    a label -- and the Reds cell's own toggle is a NEW cell, not another
    StandardSegmentCell (it is a star's art + an in-cell control, not a
    segment's cell)."""
    body = _bowser_row_body()
    assert "<${StandardSegmentCell}" in body, \
        "BowserCourseRow no longer reuses StandardSegmentCell for 'No Reds'"
    assert "<${RedsCell}" in body, \
        "BowserCourseRow no longer renders the Reds star/pipe toggle cell"
    assert '"No reds"' not in body, \
        "a hardcoded \"No reds\" label belongs to the segment's own name now"


def test_bowser_row_star_pick_is_a_plain_target_write():
    """pickStar (the toggle's STAR half) must not touch any segment's
    enabled flag -- picking the star grades the grab alone and never arms
    or disarms anything."""
    body = _bowser_row_body()
    pick_star = re.search(r"async function pickStar\(\) \{.*?\n  \}\n", body, re.S)
    assert pick_star, "pickStar not found (or changed shape) in BowserCourseRow"
    assert "requestTarget(" in pick_star.group(0)
    assert "send(" not in pick_star.group(0)
    assert "enabled" not in pick_star.group(0)


def test_bowser_row_pipe_pick_only_ever_enables_never_disables():
    """pickPipe (the toggle's PIPE half) may enable the reds->pipe segment
    before targeting it (a segment can start disabled), but must never write
    `enabled: false` to ANY segment -- that write, on the SIBLING segment, is
    exactly the retired mutual-exclusion mechanism this file exists to keep
    out."""
    body = _bowser_row_body()
    pick_pipe = re.search(r"async function pickPipe\(\) \{.*?\n  \}\n", body, re.S)
    assert pick_pipe, "pickPipe not found (or changed shape) in BowserCourseRow"
    assert "requestTarget(" in pick_pipe.group(0)
    assert "enabled: false" not in pick_pipe.group(0)


def test_reds_cell_toggle_buttons_never_disable_the_star():
    """The star half of the toggle is disabled (an HTML `disabled` attribute)
    inside a route, never hidden and never made to silently no-op -- and
    RedsCell itself must not carry a second segment-enable write of its own
    (that belongs to BowserCourseRow's onPickPipe, passed in as a prop, so
    there is exactly one place that ever does it)."""
    body = _reds_cell_body()
    assert "disabled=" in body, \
        "RedsCell no longer disables the star toggle inside a route"
    assert "send(" not in body, \
        "RedsCell should not itself write segment state -- that belongs to " \
        "BowserCourseRow's onPickPipe, passed down as a prop"


def test_bowser_row_subtitle_no_longer_implies_a_choice():
    """The old subtitle framed the two PIPE cells as an either/or ("... or the
    pipe-entry skip (no reds)"); that phrasing is wrong here regardless of
    cell count."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    assert "or the pipe-entry skip (no reds)" not in source, \
        "the subtitle still frames this as a two-way choice"


def test_the_guards_can_still_fail():
    """Probe both directions (ui-core.md's own norm) so a comment mentioning
    the retired mechanism can't trip these guards, and real code restoring it
    can't hide from them."""
    comment_only = (
        '// we used to call send("PUT", ..., { enabled: false }) here,'
        " and had a useEffect to restore the pick, but not anymore\n")
    real_code = (
        'async function pickReds() {\n'
        '  await send("PUT", `/api/segments/${s.segment_id}`, { enabled: false });\n'
        '  await requestTarget(t, { course_id: stage.course_id, star_id: 0 });\n'
        '}\n')
    stripped_comment = strip_comments(comment_only)
    stripped_code = strip_comments(real_code)
    assert "send(" not in stripped_comment and "useEffect" not in stripped_comment
    assert "send(" in stripped_code


# --- 2026-07-30 live feedback: memory, card click, labels, floor rank -------

def test_the_selected_mode_is_explicit_state_not_derived_from_the_target():
    """The bug behind "it incorrectly SWAPS OVER TO STAR MODE".

    While the mode was `!starActive`, grabbing the reds star mid-run made that
    star the most recent thing the app had seen, the derived value went true, and
    the Star button lit ITSELF while the user was timing to the pipe. Explicit
    state cannot be moved by anything that happens in the level -- only a click
    moves it. Pipe therefore never auto-swaps to Star, which is the asymmetry he
    asked for ("But pipe mode should not swap to star mode").
    """
    body = _bowser_row_body()
    assert "const pipeMode = !starActive" not in body, (
        "pipeMode is derived from the target again -- a star grab will relight "
        "the Star button mid-run (user, 2026-07-30)")
    assert "useState(() => bowserModeFor(stage.level))" in body, (
        "the mode is no longer explicit per-level state")


def test_the_whole_reds_card_is_a_click_target():
    """User drew a box round the entire cell: "if we just click on the card
    itself normally, it should activate it (with whatever pipe/star mode we have
    selected already)". The two toggle buttons remain their own targets."""
    body = _reds_cell_body()
    assert 'role="button"' in body, "the Reds card is not clickable as a whole"
    assert "onPickCard" in body, "the card click does not select in the current mode"


def test_the_reds_cell_names_which_family_is_on_the_clock():
    """'When Pipe is selected, it should be displayed as "8 Red Coins (Pipe)",
    and when Star is selected ... "8 Red Coins (Star)"'. The two are graded
    against different ladders, so a medal that changes with no label to explain
    it reads as a rendering fault. Round 2 part 2: composed through the
    shared familyLabel (../redsfamily.js), not a second inline template --
    see tests/test_single_source.py for the guard banning a third one."""
    body = _reds_cell_body()
    assert "familyLabel(course.stars[0] || \"8 Red Coins\", pipeMode)" in body, (
        "the Reds cell no longer spells out which family is being timed "
        "via the shared familyLabel composer")


def test_the_no_reds_cell_is_labelled_no_reds():
    """"For all bowser stages, it's Reds or No Reds." The corpus name stays
    "BitDW Pipe Entry" -- right in the library and in a route, where "No Reds"
    would name nothing -- so this is a row-local short label, exactly as the
    star is shown as "Reds" rather than its own name."""
    body = _bowser_row_body()
    assert 'nameOverride="No Reds"' in body, (
        "the pipe-entry cell shows its corpus name again instead of 'No Reds'")


def test_an_unranked_but_rankable_reds_cell_draws_the_ladder_floor():
    """"instead of displaying a '-' we should display the Capless 5 icon in its
    place (or, generically, the default lowest rank icon)". Generic: RANK_FLOOR
    is derived from RANK_NAMES/DIVISION_NUMERALS, so this must not name a tier."""
    body = _reds_cell_body()
    assert "RANK_FLOOR" in body or "floorRank" in body, (
        "the Reds cell shows a bare dash for an unranked-but-rankable entity")
    assert '"Iron"' not in body and '"Capless"' not in body, (
        "the floor is hardcoded here -- it must come from caps.js's RANK_FLOOR, "
        "which derives it from the ladder registries")


# --- 2026-07-30 round 2 live feedback: 5 more reports ------------------------
#
# The unifying idea across all five: an EXPLICIT CHOICE outranks background
# truth. Three of them share ONE missing signal -- attempt recency inside
# StageBanner (freshIds, practice.js's useFreshAttemptIds) -- which this round
# finally threads down (see test_stagebanner_hundred_coin.py's own updated
# test for the StageBanner-level half of that wiring).

# --- 2026-07-30 round 2, part 2: four more live reports ---------------------
#
# Item 1's `suppressRunning` fix (immediately above/below in history) is
# SUPERSEDED by item 3's unification, not layered under it: the running
# chip/glow it suppressed no longer exists at all, so there is nothing left
# to suppress. These tests replace the retired `test_no_reds_suppresses_
# running_when_reds_is_the_explicit_target` /
# `test_standard_segment_cell_suppresses_both_the_chip_and_the_glow`.

def test_standard_segment_cell_has_no_armed_or_running_concept_at_all():
    """Item 3 (user, 2026-07-30): "for segments, we should remove the
    'armed' or 'running' display... it should use the same visual display
    as the stars, i.e., replace the 'running' with the strategy name... The
    color of the highlight should also be yellow for the segments, not
    green. Basically, we unify this design." `StandardSegmentCell` must
    read neither `t.armedSegs` nor pass an `armed` prop, and its `sub` must
    be the plain strategy sub-line unconditionally -- the identical PROPS a
    star cell passes, so this is one call shape, not two that happen to
    look alike."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    body = _function_body("StandardSegmentCell", source)
    assert "armedSegs" not in body, \
        "StandardSegmentCell still reads live-armed state for its own display"
    assert "suppressRunning" not in body, \
        "the retired running-suppression mechanism reappeared"
    assert "armed=" not in body, \
        "StandardSegmentCell still passes an armed prop to PracticeCell"
    assert "sub=${stratSub(s.strat)}" in body, \
        "the cell no longer shows the strategy name unconditionally"


def test_every_segment_cell_renders_through_standard_segment_cell():
    """Item 3 reaches EVERY segment cell, not just the Bowser row (user,
    2026-07-31, on hitting it again in the Bowser 1 arena: "when we're in
    bowser 1, it's displaying as 'running' -- again, I want to replace this
    in EVERY circumstance... No 'Running' because we're already
    highlighting the card"). ArenaRow, SegmentRow (castle) and ArmedOnlyRow
    all render their segment cells through the SAME StandardSegmentCell --
    none may grow a second, hand-rolled cell that reads t.armedSegs itself,
    or the unification above would be true for Bowser alone."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    for row_name in ("ArenaRow", "SegmentRow", "ArmedOnlyRow"):
        body = _function_body(row_name, source)
        assert "<${StandardSegmentCell}" in body, \
            f"{row_name} no longer renders its segment cells through " \
            "StandardSegmentCell"
        # ArmedOnlyRow's OWN row heading legitimately says "Running" (a
        # segment timer is live is the row's whole reason for existing) --
        # that is prose on the SECTION, not a per-cell state, so this only
        # guards against a hand-rolled cell reading armed state itself.
        assert "t.armedSegs.has(" not in body, \
            f"{row_name} reads armed state for its own cell rendering"


def test_practice_cell_has_no_armed_prop_left_to_diverge_into():
    """The unification is only real if the SHARED component itself carries
    no such prop -- pinning this here (not just at the call site) is what
    stops a future segment-only tweak from reintroducing a second look."""
    source = strip_comments(
        (UI / "components" / "practicecell.js").read_text(encoding="utf-8"))
    assert "armed" not in source, \
        "PracticeCell's armed prop (or its .armed class) came back"


def test_star_row_and_standard_segment_cell_make_the_identical_practicecell_call():
    """Not "two call sites that happen to look alike" (user's own standing
    rule, 2026-07-26) -- both must pass the SAME prop set, in the SAME
    order, modulo the fields that are genuinely per-kind (iconSrc/rank/name/
    onPick/onEdit identify WHICH entity; dimIdle/fallbackSlot are look
    constants). If a future PracticeCell prop is added to one cell and not
    the other, this fails."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    star_body = _function_body("StarRow", source)
    seg_body = _function_body("StandardSegmentCell", source)
    # `hasStandards` is NOT in this set -- StarRow's own cells don't pass it
    # today (a pre-existing asymmetry this task didn't touch: only the Reds
    # cell's own floor-rank logic exists for stars, addendum 2026-07-30).
    # Listed here only so a future "share it" fix has one line to update.
    shared_props = {"dimIdle", "active", "iconSrc", "rank",
                    "name", "sub", "onPick", "onEdit"}
    for prop in shared_props:
        assert f"{prop}=" in star_body, f"StarRow no longer passes {prop}"
        assert f"{prop}=" in seg_body, f"StandardSegmentCell no longer passes {prop}"


def test_bowser_row_detects_completions_via_freshids_not_only_clicks():
    """Item 2: "if I successfully complete a Star Reds / Pipe Reds run, then
    we should highlight the Reds card... if I enter the pipe without
    grabbing the star, then we chose to do No Reds." The star half must be
    gated on `starActive` -- projection.py's _close_by_grab records a real
    star attempt on EVERY reds grab, pipe run or not, so a fresh success
    alone can't tell a stand-alone Star pick apart from a mid-Pipe-run
    waypoint grab; only the CURRENT target (protected from that grab by the
    round-2 projection.py fix) can."""
    body = _bowser_row_body()
    assert re.search(
        r"const starJustDone = starActive\s*"
        r"&& justCompletedStar\(v, freshIds, stage\.course_id, 0\);", body), (
        "the star completion check is missing or no longer gated on "
        "starActive -- a mid-Pipe-run grab would relight Star mode")
    assert re.search(
        r"const pipeJustDone = !!pipeSeg\s*"
        r"&& justCompletedSegment\(v, freshIds, pipeSeg\.segment_id\);", body)
    assert re.search(
        r"const noRedsJustDone = !!noRedsSeg\s*"
        r"&& justCompletedSegment\(v, freshIds, noRedsSeg\.segment_id\);", body)
    assert "writeBowserFamily(stage.level, \"reds\")" in body
    assert "writeBowserFamily(stage.level, \"no_reds\")" in body


def test_bowser_row_reads_freshids_from_its_own_prop():
    """The detection above is dead code without the prop actually arriving
    -- pins the row's OWN signature, complementing
    test_stagebanner_hundred_coin.py's StageBanner-level assertion."""
    assert "function BowserCourseRow({ t, v, stage, freshIds })" \
        in strip_comments(BANNER.read_text(encoding="utf-8"))


def test_returning_to_a_bowser_stage_retargets_the_remembered_family():
    """Item 5: "If I have selected reds (or no reds) and leave a bowser
    stage, and come back, I would expect that same selection to persist to
    my next session" -- read as RE-TARGETING (his own words), not merely
    pre-selecting a toggle nobody has clicked. Must never fire while either
    of this row's own things is already the target (an explicit pick just
    made, including this effect's own write, must not be clobbered)."""
    body = _bowser_row_body()
    retarget = re.search(
        r"useEffect\(\(\) => \{\s*"
        r"const family = bowserFamilyFor\(stage\.level\);.*?\n  \}, \[stage\.level\]\);",
        body, re.S)
    assert retarget, "the auto-retarget-on-return effect is missing"
    effect = retarget.group(0)
    assert "if (!family) return;" in effect
    assert "if (redsActive) return;" in effect
    assert "pickPipe();" in effect and "pickStar();" in effect
    assert "pickNoReds();" in effect


def test_returning_to_a_bowser_stage_never_steals_a_segment_already_picked():
    """The third thief, after `_close_by_grab`'s star grab and `ArenaRow`'s
    arena entry, found the same way and ruled on the same way (live report
    2026-08-02): he picked `Bowser 1 -> WF` in the lobby and walked into
    BitDW to run it, and 17 ms after the level change this effect re-targeted
    the remembered reds family -- journal ids 240 -> 246, `target_set
    segment_id=32` then `target_set segment_id=67`. *"If I selected a segment
    that spans multiple courses / areas, it should stay selected."*

    The guard this replaces (`noRedsSeg && tgt.segment_id ===
    noRedsSeg.segment_id`) only declined for THIS row's own two cells, i.e.
    exactly the targets that were never the problem. The rule is the one
    ArenaRow already carries: a convenience default may fill an empty hand;
    it may not take something out of one.

    Source-scan, like every test in this file (stagebanner.js is not
    import-free); the rendered behaviour is verified live."""
    body = _bowser_row_body()
    retarget = re.search(
        r"useEffect\(\(\) => \{\s*"
        r"const family = bowserFamilyFor\(stage\.level\);.*?\n  \}, \[stage\.level\]\);",
        body, re.S)
    assert retarget, "the auto-retarget-on-return effect is missing"
    effect = retarget.group(0)
    assert 'if (tgt.kind === "segment") return;' in effect, (
        "the auto-retarget must decline whenever ANY segment is the target, "
        "not only this row's own cells")
    # And it must come BEFORE either pick, or the guard is decoration.
    assert effect.index('if (tgt.kind === "segment") return;') < effect.index(
        "pickPipe();")


def test_bowser_family_memory_has_no_default_unlike_the_star_pipe_submode():
    """Unlike bowserModeFor (which needs SOME visual default even before a
    pick -- DEFAULT_BOWSER_MODE), bowserFamilyFor must return null with
    nothing chosen: a default here would invent a "the user chose Reds/No
    Reds" fact for a level the player has never touched, and retarget on
    that fiction."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    body = _function_body("bowserFamilyFor", source)
    assert "return BOWSER_FAMILIES.includes(stored) ? stored : null;" in body


def test_the_family_and_submode_memories_stay_two_separate_keys():
    """bowserFamilyFor (Reds vs No Reds) must not collapse into
    bowserModeFor (star vs pipe WITHIN Reds) -- they answer different
    questions and a level can have one without the other (e.g. "no_reds"
    chosen, star/pipe sub-mode irrelevant)."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    assert 'const BOWSER_MODE_KEY = "sm64.bowserMode";' in source
    assert 'const BOWSER_FAMILY_KEY = "sm64.bowserFamily";' in source
    assert source.count('const BOWSER_MODE_KEY') == 1
    assert source.count('const BOWSER_FAMILY_KEY') == 1
