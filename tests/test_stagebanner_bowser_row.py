# tests/test_stagebanner_bowser_row.py
"""BitDW/BitFS/BitS: two plain cells since round 31 (task 3, 2026-08-10),
superseding TWO earlier designs -- both kept here as prose because each was a
real live-reported bug this row must never regrow.

Design 1 (912466d, live report 2026-07-29): THREE cells -- the reds star, the
STRICT "seg:reds->pipe:<abbrev>" segment (a waypoint on the reds grab then the
pipe entry), and the legacy EXCLUSIVE pipe-only segment ("No Reds"). Before
that, an even earlier row rendered exactly ONE hardcoded "No reds" pipe cell
and enforced mutual exclusion by writing the OTHER segment's `enabled` flag
whenever one was picked -- which stranded two of the user's real definitions
at `enabled=0` (migration v16) and fought the matcher, which already keeps
both armed in parallel by design.

Design 2 (2026-07-30, spec 2026-07-28-multi-step-segments, "the Bowser Reds
star/pipe toggle"): TWO cells. The reds->pipe segment's own cell was folded
into a star/pipe TOGGLE living inside a hand-written `RedsCell` -- "the third
cell goes away... what replaces it is a toggle inside the Reds cell" -- so
clicking the STAR icon graded the grab alone (" (Star)") and clicking the
PIPE icon graded the whole run (" (Pipe)"), both feeding the same
requestTarget. `RedsCell` could not be a `PracticeCell` (it nested two toggle
buttons and a `<button>` may not contain one), so it was hand-written as a
`<div role="button">` -- the standing rule-11 risk this file's own comments
named at every retarget. Round 2 (2026-07-30, five live reports, then four
more) added: the remembered per-level star/pipe sub-mode (`bowserModeFor`) so
a Star/Pipe pick survived a stage revisit; a detection-driven memory
(`justCompletedStar`/`justCompletedSegment` via `freshIds`) that updated the
remembered choice on a fresh completion, not only a click; a remembered
Reds-vs-No-Reds family (`bowserFamilyFor`) with its own return-to-stage
re-target; and cell UNIFICATION, deleting `PracticeCell`'s `armed` prop
outright so a segment cell renders byte-identically to a star cell. A route
lock that forced Pipe mode whenever the active route named this stage's reds
was added and then deleted again (2026-08-02, live report: "I cannot click on
the star icon for Reds ... in the dark world") once measurement showed its
justifying premise was false in all eight seeded Bowser Reds routes.

Design 3 (round 31, task 3, THIS design): the toggle is deleted, not
redesigned again, because the CHOICE it made ("grab alone or whole run")
stopped existing. The reds STAR is a [[subsection]] of the reds->pipe
MOVEMENT now (task 1, 0dfc983) -- Griffin, approving: "Fundamentally, Bowser
Reds STAR is just... a subsection of Bowser Reds Pipe entry", and "You pick
Reds; the star grab records underneath." So the row is two ORDINARY
StandardSegmentCells, exactly like every other segment row in this file:
"Reds" now names the reds->pipe MOVEMENT itself (not the star), and "No
Reds" is unchanged. `RedsCell`, `bowserModeFor`/`writeBowserMode`, and the
`justCompletedStar` half of the detection memory are deleted outright, along
with `components/celltoggles.js` -- the LAST consumer of that module, so its
deletion here finishes what task 2 (round 31's badge retirement) could not:
`tests/test_ui_no_cell_toggles.py::test_no_surface_renders_a_cell_toggle` was
`xfail(strict=True)` naming this exact task and is a real assertion again.
`bowserFamilyFor`/`writeBowserFamily` (Reds-vs-No-Reds) and the "no route
override" rule both survive unchanged -- they never depended on the toggle.

stagebanner.js is not import-free, so -- same approach as
test_stagebanner_hundred_coin.py and test_star_icons.py -- these are
SOURCE-SCAN assertions; the rendered behaviour is verified by rendering (see
this task's own report: a contact sheet of the row plus a driven render
confirming both cells are real `<button>`s with nothing nested inside them).
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


def test_bowser_row_never_writes_the_enabled_flag_to_the_OTHER_segment():
    """The retired mutual-exclusion mechanism disabled the sibling segment
    whenever one was picked -- that write must stay gone. Both cells'
    `pick()` (StandardSegmentCell) and `pickReds`/`pickNoReds` (the
    auto-retarget effect's own writes) legitimately write `enabled: true` to
    enable the segment they are ABOUT to target -- an enable-before-
    targeting write, never a disable-the-sibling one. What must never come
    back is `enabled: false`."""
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
    while the effect itself is allowed. Round 31 deleted the star/pipe
    SUB-mode memory (`bowserModeFor`) this test used to also require; the
    Reds-vs-No-Reds family memory (`bowserFamilyFor`) it restores from is the
    only memory left to restore.
    """
    body = _bowser_row_body()
    assert "enabledPipe" not in body, (
        "the enabled-flag-derived memory is back -- that flag is server state "
        "and using it as a preference stranded real definitions (v16)")
    assert "enabled: false" not in body and "enabled:false" not in body, (
        "BowserCourseRow must never DISABLE a segment; all options track "
        "together since 912466d")
    assert "bowserFamilyFor" in body, (
        "the row no longer restores the remembered Reds/No-Reds family on "
        "entry")


def test_bowser_row_renders_two_plain_cells_and_nothing_else():
    """Round 31 (task 3): `RedsCell` -- the one hand-written cell in this
    row, a `<div role="button">` nesting two toggle buttons and the standing
    rule-11 risk since round 2 -- is gone outright. Both cells are the
    ordinary StandardSegmentCell every other row in this file already uses,
    which is ALREADY a real `<button>` (practicecell.js) that cannot itself
    nest another button -- so "both cells are real buttons with nothing
    nested inside them" follows from this row rendering through the shared
    cell and nothing else; render-verified in this task's own report.

    "Reds" now names the seg:reds->pipe:<abbrev> MOVEMENT -- StandardSegmentCell's
    `s` prop is `pipeSeg`, not the star -- so a pick targets the movement,
    and the star grab records underneath it as a [[subsection]] with no pick
    of its own."""
    body = _bowser_row_body()
    assert body.count("<${StandardSegmentCell}") == 2, (
        "BowserCourseRow must render EXACTLY two cells, both through the "
        "shared StandardSegmentCell")
    assert "RedsCell" not in body, "the hand-written Reds cell is back"
    assert "CellToggles" not in body, "the retired star/pipe toggle is back"
    assert 'nameOverride="Reds"' in body, (
        "the movement's cell no longer shows the row-local 'Reds' label")
    assert 'nameOverride="No Reds"' in body, (
        "the pipe-entry cell no longer shows 'No Reds'")
    assert "s=${pipeSeg}" in body, (
        "the Reds cell no longer passes the reds->pipe segment as its own "
        "entity -- a pick must target the MOVEMENT, not the star")
    assert "star_id: 0" not in body, (
        "BowserCourseRow writes a star target directly again -- picking Reds "
        "must only ever target the reds->pipe segment; the star records "
        "underneath it as a subsection with no pick of its own")


def test_bowser_row_reds_pick_only_ever_enables_never_disables():
    """`pickReds` (the auto-retarget effect's own Reds write -- the click
    path goes through StandardSegmentCell's shared `pick()` instead) may
    enable the reds->pipe segment before targeting it (a segment can start
    disabled), but must never write `enabled: false` to ANY segment -- that
    write, on the SIBLING segment, is exactly the retired mutual-exclusion
    mechanism this file exists to keep out."""
    body = _bowser_row_body()
    pick_reds = re.search(
        r"async function pickReds\(options\) \{.*?\n  \}\n", body, re.S)
    assert pick_reds, "pickReds not found (or changed shape) in BowserCourseRow"
    assert "pickSegmentTarget(" in pick_reds.group(0)
    assert "enabled: false" not in pick_reds.group(0)


def test_bowser_row_ignores_the_active_route():
    """The route lock is gone (2026-08-02, live report: "I cannot click on
    the star icon for Reds here in bowser in the dark world"). It disabled
    the star half whenever the active route named that stage's reds, on the
    stated premise that "every seeded Bowser Reds route step already names
    seg:reds->pipe:<abbrev>, never the bare star". That premise was false in
    every instance: all eight reds routes in tools/corpus_routes_main.py
    pair `star(16, 0, "BitDW - 8 Red Coins")` (and 17/18 for BitFS/BitS) WITH
    `*BOWSER_n_REDS`, so the bare grab is a graded route step of its own and
    the lock hid a half the route itself measures. With the star pick gone
    entirely (round 31) there is even less for a route to lock -- but the
    general rule survives: nothing in this row may consult the active route
    to decide which segment a pick targets."""
    body = _bowser_row_body()
    for symbol in ("routeStarFilter", "routeSegmentFilter", "forcedPipe"):
        assert symbol not in body, \
            f"BowserCourseRow consults the active route again ({symbol})"


def test_bowser_row_subtitle_no_longer_implies_a_choice():
    """The old subtitle framed the two PIPE cells as an either/or ("... or the
    pipe-entry skip (no reds)"); that phrasing is wrong here regardless of
    cell count. The round-31 subtitle names both real cells plainly."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    assert "or the pipe-entry skip (no reds)" not in source, \
        "the subtitle still frames this as a two-way choice"
    assert "Star or Pipe" not in source, \
        "the subtitle still names the retired star/pipe toggle"


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
        '  await requestTarget(t, { kind: "segment", segment_id: s.segment_id });\n'
        '}\n')
    stripped_comment = strip_comments(comment_only)
    stripped_code = strip_comments(real_code)
    assert "send(" not in stripped_comment and "useEffect" not in stripped_comment
    assert "send(" in stripped_code


# --- 2026-07-30 live feedback: memory, card click, labels, floor rank -------
#
# The star/pipe sub-mode tests this section used to hold
# (`test_the_selected_mode_is_explicit_state_not_derived_from_the_target`,
# `test_the_whole_reds_card_is_a_click_target`,
# `test_the_reds_cell_names_which_family_is_on_the_clock`,
# `test_an_unranked_but_rankable_reds_cell_draws_the_ladder_floor`) are
# DELETED with `RedsCell` -- there is no mode to derive-vs-explicit, no
# hand-written card to click-test, no cell-local family label (StandardSegmentCell
# shows the segment's own name, "Reds"/"No Reds"), and no cell-local floor-rank
# logic (PracticeCell's own floor-rank handling, shared by every segment cell
# in the app, already covers it -- see practicecell.js's own tests).


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
    grabbing the star, then we chose to do No Reds." Round 31 deletes the
    STAR half of this detection (`justCompletedStar`, gated on `starActive`)
    with the toggle it served -- there is no stand-alone star pick left on
    this row for a fresh star success to disambiguate. Only the MOVEMENT's
    own completion (`redsJustDone`, via `justCompletedSegment` on the
    reds->pipe segment) and No Reds's remain."""
    body = _bowser_row_body()
    assert "justCompletedStar" not in body, (
        "the star half of the detection memory is back -- there is no "
        "stand-alone star pick left on this row for it to disambiguate")
    assert re.search(
        r"const redsJustDone = !!pipeSeg\s*"
        r"&& justCompletedSegment\(v, freshIds, pipeSeg\.segment_id\);", body), (
        "the Reds completion check is missing or no longer reads the "
        "reds->pipe segment")
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
    made, including this effect's own write, must not be clobbered).
    Round 31 drops the star/pipe sub-mode branch this effect used to read
    (`bowserModeFor(stage.level) === "pipe" ? pickPipe : pickStar"`) -- there
    is only ONE way to pick Reds now."""
    body = _bowser_row_body()
    retarget = re.search(
        r"useEffect\(\(\) => \{\s*"
        r"const family = bowserFamilyFor\(stage\.level\);.*?\n  \}, \[stage\.level\]\);",
        body, re.S)
    assert retarget, "the auto-retarget-on-return effect is missing"
    effect = retarget.group(0)
    assert "if (!family) return;" in effect
    assert "if (redsActive) return;" in effect
    # The retarget is a FILL, not a click, and says so: `auto` puts the
    # write under the target queue's detection rules (round 19), so the
    # projector holds it like a detection and it can never steal a
    # promoted one. The cells' own click paths pass no options.
    assert "pickReds({ auto: true });" in effect
    assert "pickNoReds({ auto: true });" in effect
    assert "bowserModeFor" not in effect, (
        "the retired star/pipe sub-mode is still read here -- there is "
        "only one way to pick Reds now")


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
        "pickReds({ auto: true });")


def test_bowser_family_memory_has_no_default_unlike_the_star_pipe_submode():
    """Unlike the retired star/pipe sub-mode (which needed SOME visual
    default even before a pick), `bowserFamilyFor` must return null with
    nothing chosen: a default here would invent a "the user chose Reds/No
    Reds" fact for a level the player has never touched, and retarget on
    that fiction."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    body = _function_body("bowserFamilyFor", source)
    assert "return BOWSER_FAMILIES.includes(stored) ? stored : null;" in body


def test_the_family_memory_is_the_only_bowser_localstorage_key_left():
    """`sm64.bowserMode` (star vs pipe WITHIN Reds) is deleted with the
    toggle it served -- `sm64.bowserFamily` (Reds vs No Reds) is the only
    remembered choice left on this row."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    assert 'const BOWSER_FAMILY_KEY = "sm64.bowserFamily";' in source
    assert source.count('const BOWSER_FAMILY_KEY') == 1
    assert "BOWSER_MODE_KEY" not in source, (
        "the retired star/pipe sub-mode's own storage key is back")
    assert "bowserModeFor" not in source and "writeBowserMode" not in source, (
        "the retired star/pipe sub-mode functions are back")


# -- the arena keeps a pick made FOR HERE, and only that ---------------------
#
# Griffin, 2026-08-05, standing in the Bowser 3 arena with its only fight
# unselected: "it seems to have worked for DDD, so that's weird... In any
# case, if there's only one option, that's how it should look. Even if this is
# our first, second, third time visiting this place."
#
# `ArenaRow` declined on `tgt.kind === "segment"` -- ANY segment target, from
# anywhere -- so still holding "No Reds" (which starts in BitS) from earlier
# play blocked the arena's own auto-select indefinitely. The rule that guard
# exists for (2026-08-01) is narrower and is about a pick made for THIS place:
# choosing "Bowser 1 -> WF" and then walking into the arena to run it must not
# lose the pick. `heldStartsHere` states exactly that.

def _arena_row_source() -> str:
    """ArenaRow's own body. Scoped deliberately: `BowserCourseRow` carries a
    byte-identical `tgt.kind === "segment"` line for its OWN remembered-family
    re-target, which is a different rule nobody has reported, and a
    whole-file scan would fail on that instead."""
    source = strip_comments(BANNER.read_text(encoding="utf-8"))
    start = source.index("function ArenaRow(")
    rest = source[start + 1:]
    return source[start:start + 1 + rest.index("function ")]


def test_the_arena_declines_only_for_a_target_that_starts_here():
    source = _arena_row_source()
    assert "heldStartsHere" in source, (
        "the arena's auto-select no longer asks whether the held target "
        "belongs to this stage")
    assert "if (heldStartsHere) return;" in source, (
        "the auto-select must decline on that question, not on a broader one")
    assert 'if (tgt.kind === "segment") return;' not in source, (
        "declining on ANY segment target is the bug: a pick from another "
        "place blocked this arena's only fight forever")
