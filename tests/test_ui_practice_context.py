"""ONE answer to "is there anything to practice where the player is standing?"

ui/stagecontext.js, driven through node, plus the two guards that stop the two
surfaces asking it separately again. They did until 2026-07-27: a new session
with the game on its main screen drew the banner's "No course target available"
AND, directly below it, the PREVIOUS session's Lethal Lava Land star under an
ACTIVE TARGET eyebrow.

The predicate is about the PLACE, never about the target. A target legitimately
survives a hub — the castle is transit, so an exit-and-re-enter keeps it
(projection.py caveat 12) — it simply is not ACTIVE while the player is
somewhere it cannot be run. The server half of the same rule, which refuses a
pick from such a place, is tests/test_practicable.py.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"
STAGECONTEXT_JS = (UI / "stagecontext.js").as_uri()

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def context(stage, armed=(), segment_targets=(), view=True):
    """The store slots the predicate reads, as a node expression."""
    return (f"{{ view: {json.dumps({'segment_targets': list(segment_targets)}) if view else 'null'},"
            f"  stage: {json.dumps(stage)},"
            f"  armedSegs: new Set({json.dumps(list(armed))}) }}")


def run_node(call: str, *expressions: str) -> list:
    script = (f"import {{ hasPracticeContext, practicedHere }} "
              f"from {STAGECONTEXT_JS!r};\n"
              "console.log(JSON.stringify(["
              + ",".join(f"{call}({e})" for e in expressions)
              + "]));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def asked(*expressions: str) -> list:
    return run_node("hasPracticeContext", *expressions)


def belongs(section: dict, stage_payload: dict | None) -> bool:
    """practicedHere(section, t) -- the second, looser rule."""
    return run_node("practicedHere",
                    f"{json.dumps(section)}, {{stage: {json.dumps(stage_payload)}}}")[0]


def stage(mode, level=None, area=1):
    return {"course_id": None, "level": level, "area": area, "mode": mode}


W = "2026-07-27T12:00:00Z"
# What detectors/stage.py puts in the payload's `course_id` -- the SAME
# course_for_level it resolves, which is what makes the card's comparison the
# projector's comparison (see the equivalence test below).
from sm64_events.memory.addresses import COURSE_BY_LEVEL as COURSE_OF  # noqa: E402


# ---- the predicate --------------------------------------------------------

def test_every_mode_the_banner_draws_a_row_for_is_a_context():
    modes = ["stars", "bowser_course", "arena", "castle"]
    assert asked(*(context(stage(mode)) for mode in modes)) == [True] * len(modes)


def test_the_main_screen_and_the_hubs_are_not():
    """THE live report: level 1 is the file select, level 16 the castle
    grounds — both resolve to mode None, and the card named a star from the
    session before while the banner correctly said there was nothing here."""
    assert asked(context(stage(None, level=1)),
                 context(stage(None, level=16)),
                 context(None)) == [False, False, False]


def test_a_running_segment_is_its_own_context():
    """A RUNNING segment is never invisible (user rule 2026-07-24): it is
    being practiced wherever it has got to by now, so the card must keep
    showing it even standing somewhere with no row of its own. This is also
    what makes practice.js's armed pins safe to gate on the same predicate."""
    armed = context(stage(None, level=16), armed=[12],
                    segment_targets=[{"segment_id": 12, "name": "→ SSL"}])
    idle = context(stage(None, level=16),
                   segment_targets=[{"segment_id": 12, "name": "→ SSL"}])
    assert asked(armed, idle) == [True, False]


def test_no_view_is_no_context():
    """Both callers render their empty state before the first fetch lands."""
    assert asked(context(stage("stars"), view=False)) == [False]


# ---- does a pinned card still belong here? --------------------------------

LOBBY = {"course_id": None, "level": 6, "area": 1, "mode": "castle"}
WF = {"course_id": 2, "level": 24, "area": 1, "mode": "stars"}
HMC = {"course_id": 6, "level": 7, "area": 1, "mode": "stars"}
LBLJ = {"kind": "segment", "segment_id": 3, "course_id": None}
WF_STAR = {"kind": "star", "course_id": 2, "star_id": 0}


def test_a_castle_segment_does_not_belong_in_a_course():
    """THE live report, twice over: LBLJ is practiced in the lobby, and after
    a Usamune warp it still read ACTIVE SEGMENT inside Whomp's Fortress and
    then again in Hazy Maze Cave. The server had retired the TARGET both
    times — what held the card up was `lastPinnedSeg`, a sticky client memory
    set on arm that no place change ever cleared."""
    assert belongs(LBLJ, LOBBY)
    assert not belongs(LBLJ, WF)
    assert not belongs(LBLJ, HMC)


def test_the_castle_and_the_hubs_keep_only_what_lives_there():
    """TIGHTENED 2026-08-03. This used to assert that transit dropped NOTHING,
    which was right about what it was written for and wrong about everything
    else: standing anywhere with no course of its own returned True for every
    section, so the card could name a thing belonging to a course two rooms
    away. Live report, standing in Castle Upstairs with only `BitS Entry` in
    the selector and the card reading `Bowser in the Fire Sea · No Reds`:

        the "active" card section should never display anything other than any
        option that's currently displayed in the segment / star selector
        above... if we leave a bowser stage, we're no longer in the bowser
        stage, so it shouldn't be displayed as active.

    What transit was FOR still holds — a castle movement (course_id null)
    survives the castle, which is what keeps a card up while you walk to its
    start. A course's star does not."""
    assert not belongs(WF_STAR, LOBBY)
    assert not belongs(WF_STAR, {"course_id": None, "level": 16, "area": 1,
                                 "mode": None})
    assert belongs(LBLJ, LOBBY)            # a castle movement lives here
    assert belongs(WF_STAR, None)          # emulator detached: nothing to say


def test_what_belongs_to_the_course_you_are_in_stays():
    assert belongs(WF_STAR, WF)
    assert belongs({"kind": "segment", "segment_id": 9, "course_id": 2}, WF)
    assert not belongs(WF_STAR, HMC)


# ---- ...and it must agree with the projector, which owns the same rule ----

def test_one_course_is_one_level_which_is_the_only_reason_these_agree():
    """The projector compares NODES (`origin != stage_origin(to_level)`); the
    card compares COURSES (`sec.course_id === stage.course_id`). Those are the
    same question only while no course spans two levels. If one ever does, a
    segment starting in level A keeps its card while you stand in level B of
    the same course, and the projector will already have retired the target —
    exactly the disagreement this whole change exists to remove."""
    from collections import Counter

    from sm64_events.memory.addresses import COURSE_BY_LEVEL
    shared = [course for course, count in Counter(COURSE_BY_LEVEL.values()).items()
              if count > 1]
    assert shared == []


def test_the_card_rule_matches_the_projector_on_every_seeded_definition():
    """The real cross-check: two implementations of one rule, in two
    languages. Every seeded definition against every place a level_changed
    can land, the projector's retire/keep answer compared with the card's."""
    import json as _json

    from sm64_events.core.paths import bundled_defaults_seed
    from sm64_events.storage.db import EventRow
    from sm64_events.tracking.projection import Projector
    from sm64_events.tracking.segments import (SegmentDef, origin_course,
                                               segment_origin)

    seed = _json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    # every main course, the three Bowser courses, the castle, both hubs, an
    # arena -- i.e. one of every kind of place course_for_level distinguishes
    levels = [9, 24, 5, 4, 7, 22, 8, 23, 10, 11, 36, 13, 12, 60, 27,
              17, 19, 21, 6, 16, 26, 30]
    pairs, expected = [], []
    for index, row in enumerate(seed["segments"], start=1):
        definition = SegmentDef(
            id=index, name=row["name"], enabled=True,
            start_triggers=row["start_triggers"],
            waypoints=row.get("waypoints") or [],
            end_triggers=row["end_triggers"], guards=row.get("guards") or [])
        course = origin_course(segment_origin(index, definition.start_triggers, {}))
        for level in levels:
            projector = Projector(segments=[definition])
            projector.feed(EventRow(id=1, session_id=1, seq=1, type="target_set",
                                    frame=1, wall_time_utc=W,
                                    payload={"kind": "segment",
                                             "segment_id": index}))
            assert projector.target == ("segment", index)
            projector.feed(EventRow(id=2, session_id=1, seq=2,
                                    type="level_changed", frame=2,
                                    wall_time_utc=W,
                                    payload={"from": 6, "to": level}))
            expected.append(projector.target is not None)
            pairs.append(({"kind": "segment", "segment_id": index,
                           "course_id": course},
                          {"course_id": COURSE_OF.get(level), "level": level}))

    answers = run_node("practicedHere", *(
        f"{json.dumps(section)}, {{stage: {json.dumps(stage)}}}"
        for section, stage in pairs))
    # ONE-DIRECTIONAL since 2026-08-03, and the direction is the whole point.
    # These two answer different questions and always did: the projector owns
    # whether the TARGET survives (it does across a hub — walking back restores
    # the same card), the card owns whether to SHOW it where you are standing
    # now. This file's own rule-file entry has said so in words the whole time:
    # "a target legitimately survives a hub... it just is not ACTIVE while you
    # stand somewhere it cannot be run".
    #
    # So the card may be STRICTER than the projector and must never be LOOSER —
    # showing a section the server has already retired is the stale-card bug
    # this module exists to prevent, and that is what is pinned. 320 of 1848
    # pairs are now deliberately stricter (a course's own things, seen from the
    # castle and the hubs); zero may go the other way.
    looser = [(pair, mine, theirs)
              for pair, mine, theirs in zip(pairs, answers, expected)
              if mine and not theirs]
    assert not looser, (
        f"the card would show {len(looser)} sections the projector retired, "
        f"e.g. {looser[:3]}")
    stricter = sum(1 for mine, theirs in zip(answers, expected)
                   if theirs and not mine)
    assert stricter > 0, (
        "the card is no longer stricter than the projector anywhere — either "
        "practicedHere lost its course-less clause, or this corpus stopped "
        "containing a course-owned section reachable from the castle")


# ---- the two surfaces cannot drift ----------------------------------------

def test_the_banner_has_a_row_for_exactly_the_modes_on_the_list():
    """stagebanner.js asks hasPracticeContext FIRST and only then indexes its
    table, so a mode on the list with no row falls through to the armed-only
    row, and a row whose mode is off the list is unreachable. Neither is
    visible in review — both render *something*."""
    listed = re.search(r"export const PRACTICE_MODES = \[([^\]]*)\]",
                       (UI / "stagecontext.js").read_text(encoding="utf-8"))
    banner = re.search(r"const STAGE_ROWS = \{(.*?)\};",
                       (UI / "components" / "stagebanner.js")
                       .read_text(encoding="utf-8"), re.S)
    assert listed and banner, "one of the two declarations has been renamed"
    assert set(re.findall(r'"(\w+)"', listed.group(1))) \
        == set(re.findall(r"(\w+):", banner.group(1)))


def test_the_active_target_card_asks_the_shared_question():
    """practice.js decides whether to pin a card at all from the same call.
    Gating there rather than inside the cards is load-bearing: `activeStar`
    going undefined is what puts the target's own section back into the
    practice index below, instead of hiding it in both places at once."""
    body = (UI / "components" / "practice.js").read_text(encoding="utf-8")
    assert re.search(r'import \{[^}]*hasPracticeContext[^}]*practicedHere[^}]*\} '
                     r'from "\.\./stagecontext\.js";', body)
    # The star card asks `starPracticableHere`, NOT `hasPracticeContext`, and
    # the difference is a live bug (2026-07-30): the latter's final clause is
    # "some segment is armed", so an armed castle movement in the Castle Lobby
    # kept a Whomp's Fortress star rendering as ACTIVE TARGET on the same
    # screen as the banner's own "No course target available". Still a call
    # into the shared module — which is what this test is really guarding —
    # just the narrower of the two questions it offers.
    assert re.search(r"const starActive = starPracticableHere\(held\)", body)
    assert re.search(r"const pinnedSegs = !inContext \|\|", body)
    # ...and BOTH rules reach every pin the user can see. Armed pins are the
    # one deliberate exemption (a live timer is visible wherever it got to).
    assert re.search(r"isActiveStar\(sec\) && here\(sec\)", body)
    # Segments get a SECOND exemption stars never do (Task 6, spec
    # 2026-07-28-multi-step-segments): a section whose own armed_detail is
    # still non-null overrides the course-match gate, completing "a running
    # segment is never invisible" (user rule 2026-07-24) on the gate it never
    # reached -- a loose segment can legitimately be running several courses
    # away from where it started. An ordinary star has no armed_detail and
    # keeps the plain predicate checked above; the 100-coin star DOES carry
    # one (spec 2026-07-28-multi-step-segments, tested in test_views.py), but
    # that is read on the STAR side of the same "OR" via `activeStar`/`here`
    # above -- deliberately NOT edited into practicedHere itself, which star
    # sections also call. A THIRD exemption, isAmbientlyArmed, gates all
    # three pin candidates -- see the dedicated test below.
    assert re.search(
        r"stickyPin && !isAmbientlyArmed\(stickyPin\)\s*"
        r"&& \(stickyPin\.armed_detail \|\| here\(stickyPin\)\)", body)
    assert re.search(
        r"activeSeg && !isAmbientlyArmed\(activeSeg\)\s*"
        r"&& \(activeSeg\.armed_detail \|\| here\(activeSeg\)\)", body)


def test_ambiently_armed_segments_get_a_third_exemption_generalized():
    """A segment that arms merely by the player being present (course/stage
    entry) rather than by a deliberate action must not read as "the user
    chose this" -- `armed_detail` alone is a false positive for exactly that
    shape, unlike a real multi-step movement genuinely mid-run.

    GENERALIZED (spec 2026-07-28-multi-step-segments) from a category-keyed
    version (`sec.category === "100 Coin Exit"`, live report 2026-07-30):
    that family dissolved outright (it no longer has a segment section to
    gate at all -- views.py excludes it from `segments` entirely), but the
    IDENTICAL ambient-arm shape is not unique to it -- Bowser's seg:reds->
    pipe:<abbrev> and the legacy pipe-entry trio share it and still have
    sections, and neither carries the "100 Coin Exit" category (reds->pipe's
    own category is "Castle Movement", indistinguishable from an ordinary
    movement by name alone). `sec.arms_ambiently` (views.py, segments.
    arms_ambiently) is the server-derived, structural answer -- a flag on
    the SECTION, not a JS enumeration of categories -- so this gate covers
    whatever arms ambiently next with no second door to keep in step."""
    body = (UI / "components" / "practice.js").read_text(encoding="utf-8")
    assert re.search(r'import \{[^}]*justCompletedSegment[^}]*\} '
                     r'from "\.\./stagecontext\.js";', body)
    assert "sec.category" not in body, \
        "the retired category-string check reappeared -- use sec.arms_ambiently"
    assert re.search(
        r"isAmbientlyArmed = \(sec\) => sec != null && sec\.arms_ambiently\s*"
        r"&& !\(tgt\.kind === \"segment\" && tgt\.segment_id === sec\.segment_id\)\s*"
        r"&& !justCompletedSegment\(v, freshIds, sec\.segment_id\)", body)
    # armedPins is filtered too -- the unconditional priority branch
    # (`armedPins.length ? armedPins : ...`) is what actually surfaced the
    # bug: an ambiently-arming segment pushes onto frozen.armedOrder and
    # store.js's segment_armed handler ALSO sticks it as lastPinnedSeg, on
    # every course/stage entry, with no gate at all before this fix.
    assert re.search(
        r"\.filter\(\(sec\) => !isAmbientlyArmed\(sec\)\)", body)


def test_just_completed_star_is_fresh_success_only():
    """justCompletedStar (spec 2026-07-28-multi-step-segments round 2, item
    2's detection signal for the Bowser Reds row): true only for a star
    section whose own MOST RECENT attempt (by id, never assumed sorted)
    landed as a FRESH success -- mirrors justCompletedSegment one level up
    (a star instead of a segment), driven directly through node rather than
    only via a source-scan of a caller."""
    v = {"stars": [{"course_id": 16, "star_id": 0,
                    "attempts": [{"id": 1, "outcome": "reset"},
                                 {"id": 2, "outcome": "success"}]}]}
    script = (
        f"import {{ justCompletedStar }} from {STAGECONTEXT_JS!r};\n"
        f"const v = {json.dumps(v)};\n"
        "console.log(JSON.stringify([\n"
        "  justCompletedStar(v, new Set([2]), 16, 0),\n"   # latest id IS fresh -> true
        "  justCompletedStar(v, new Set([1]), 16, 0),\n"   # fresh id isn't the latest -> false
        "  justCompletedStar(v, new Set(), 16, 0),\n"      # nothing fresh -> false
        "  justCompletedStar(v, new Set([2]), 16, 5),\n"   # no section for this star -> false
        "]));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [True, False, False, False]


def test_the_segment_he_clicked_leads_the_objective_card():
    """Live report 2026-08-02, four screenshots: he tapped `Bowser 2 →
    Upstairs`, the CELL lit up, and the card below kept reading `Bowser 2 →
    BitS`. Same for WDW and DDD. *"once we select something, it should always
    appear in that display below, and continue to be displayed while it's
    still valid and selected."*

    It became reachable the moment "no active route" stopped meaning "no
    filter" (`segments.py::_route_allows`): all FOUR `Bowser 2 → X` movements
    arm on the same event now, `frozen.armedOrder` cannot order things that
    arrived together, and `armedPins[0]` was whichever landed first. Two
    surfaces answering one question two ways — the class the single-source
    rule in CLAUDE.md exists for, arriving as an ORDERING rather than a
    derivation.

    Both halves are pinned, because sorting armedPins alone only covers a pick
    that happens to be armed as well:
      1. within armedPins, the targeted section sorts to the front;
      2. a targeted section that is NOT armed still leads.
    Source-scan (practice.js is not import-free); the behaviour is verified by
    rendering."""
    body = (UI / "components" / "practice.js").read_text(encoding="utf-8")
    assert re.search(
        r"isTargetedSeg = \(sec\) => sec != null && tgt\.kind === \"segment\"\s*"
        r"&& tgt\.segment_id === sec\.segment_id", body), \
        "no single reader for 'is this section the target'"
    assert re.search(
        r"\.sort\(\(a, b\) => \(isTargetedSeg\(b\) \? 1 : 0\)\s*"
        r"- \(isTargetedSeg\(a\) \? 1 : 0\)\)", body), \
        "armedPins no longer promotes the targeted segment"
    assert re.search(r"pickedSeg = segs\.find\(isTargetedSeg\)", body) and \
        re.search(r"\[pickedSeg, \.\.\.armedPins\]", body), \
        "a targeted-but-unarmed segment no longer leads"


def test_nothing_picked_and_several_running_claims_no_active_segment():
    """Live report 2026-08-02: standing Upstairs with `BitS Entry`, `Bowser 2 →
    WDW` and `Bowser 2 → BitS` all armed and NO cell lit, the card still read
    "ACTIVE SEGMENT · BitS Entry · Running". *"When I don't have selected in
    the UI, it should never display an active segment like this, especially
    since there are multiple options."*

    It bends his own earlier rule — "a RUNNING segment is never invisible"
    (2026-07-24) — and the reason that rule stopped fitting is worth keeping:
    it was written when at most ONE thing armed at a time, so "the armed one"
    and "the one he means" were the same section. Six movements arm off one
    exit now. So ONE armed pin is still shown (nothing to be ambiguous about);
    two or more with an empty hand shows none, because picking between them
    asserts a selection he never made.

    The threshold is `> 1`, not `>= 1`: dropping the single case would make a
    live timer invisible, which is the bug the 2026-07-24 rule exists for."""
    body = (UI / "components" / "practice.js").read_text(encoding="utf-8")
    assert re.search(
        r"ambiguousPins = !pickedSeg && armedPins\.length > 1", body), \
        "the empty-hand ambiguity gate is gone or changed shape"
    assert re.search(
        r"pinnedSegs = !inContext \|\| starActive \|\| ambiguousPins \? \[\]",
        body), "the gate is not wired into pinnedSegs"
