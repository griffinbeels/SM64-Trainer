# tests/test_ui_practice_log.py
"""The practice log is the session's history, grouped by what you practiced.

Ordering is pure and is driven under node. The card itself is verified by
RENDERING (tests/test_responsive.py + tools/contact_sheet.py) -- a unit test
cannot tell whether two rank banners crowd each other, and this project has
shipped an invisible feature on unit tests plus `node --check` before.

`orderedSections`'s own declaration is extracted from source rather than
imported. practicelog.js pulls in Preact -- through ranks.js, icons.js and
attemptlog.js -- via the browser's import map (ui/index.html's `"preact":
"/ui/vendor/preact.module.js"`), which plain `node` cannot resolve at all
(`import('preact')` from this directory fails with ERR_MODULE_NOT_FOUND,
verified directly rather than assumed). Every node-driven test in this suite
already targets an import-free module for exactly that reason --
tests/test_cross_language_parity.py's own docstring names the same
constraint for ranks.js/statmenu.js and uses the same fix: extract the ONE
declaration that is actually pure (comments stripped first, so a stale
commented-out version can never be the one picked up) and evaluate it on its
own. `orderedSections` needs nothing from Preact -- it only merges and sorts
plain arrays -- so this is the real function, never a restatement of it.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from source_scan import strip_comments

REPO = Path(__file__).resolve().parent.parent
UI = REPO / "src" / "sm64_events" / "ui"
LOG_JS = UI / "components" / "practicelog.js"
ENTITY_SECTION_JS = UI / "entitysection.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def _extract(source: str, name: str) -> str:
    match = re.search(rf"^export function {name}\(.*?^\}}", source,
                       re.S | re.M)
    assert match, f"{name} not found — renamed?"
    return match.group(0)


def _ordered_sections_source() -> str:
    source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    return _extract(source, "orderedSections")


def ordered(view) -> list:
    script = (_ordered_sections_source() + "\n"
              f"console.log(JSON.stringify(orderedSections("
              f"{json.dumps(view)}).map(s => s.id)));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _rows(count, first_id=1):
    """`count` stub attempt rows. Only their EXISTENCE matters to the
    ordering and auto-open rules under test here."""
    return [{"id": first_id + n, "journal_id": first_id + n,
             "outcome": "reset"} for n in range(count)]


def star(id_, last_activity, course_id=13, star_id=1, attempts=1):
    """`attempts` defaults to ONE, not zero.

    An entity with nothing recorded is the special case now -- it still
    leads the list when chosen, but it cannot hold the auto-open slot
    (topEntityKey's own comment). A helper defaulting to zero made every
    unrelated ordering test silently exercise that special case, which is
    how two of them came to encode the pre-2026-08-05 rule."""
    return {"id": id_, "course_id": course_id, "star_id": star_id,
            "last_activity": last_activity,
            "attempts": _rows(attempts, first_id=100)}


def segment(id_, last_activity, segment_id=12, attempts=1):
    """Same default and the same reason as `star` above."""
    return {"id": id_, "kind": "segment", "segment_id": segment_id,
            "last_activity": last_activity,
            "attempts": _rows(attempts, first_id=200)}


# ---- orderedSections(view, activeKey) -- the active-leads round ------------
#
# `orderedSections` reaches for the REAL `entityKey` (entitysection.js) the
# moment `activeKey` is non-null -- the same "real function, comments
# stripped" reason `_top_entity_key_source` below already includes it.

def _ordered_sections_with_entity_key_source() -> str:
    log_source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    return "\n".join([_entity_key_source_early(),
                       _extract(log_source, "orderedSections")])


def _entity_key_source_early() -> str:
    source = strip_comments(ENTITY_SECTION_JS.read_text(encoding="utf-8"))
    is_segment = re.search(r"^export const isSegment = .*?;$", source, re.M)
    assert is_segment, "isSegment not found in entitysection.js — renamed?"
    return is_segment.group(0) + "\n" + _extract(source, "entityKey")


def ordered_active(view, active_key) -> list:
    script = (_ordered_sections_with_entity_key_source() + "\n"
              f"console.log(JSON.stringify(orderedSections("
              f"{json.dumps(view)}, {json.dumps(active_key)}).map(s => s.id)));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_active_entity_leads_regardless_of_its_own_recency():
    """Griffin: entering Bowser 1 with it the only segment available "should
    be at the top immediately" -- before this, a freshly-selected target
    (recency -1) sat at the very BOTTOM until an attempt landed."""
    view = {"stars": [star("played", 100, course_id=2, star_id=4)],
            "segments": [segment("fresh", -1, segment_id=8)]}
    assert ordered_active(view, "segment:8") == ["fresh", "played"]


def test_with_no_active_entity_the_order_is_byte_identical_to_before():
    view = {"stars": [star("a", 100), star("c", 10)], "segments": []}
    assert ordered_active(view, None) == ordered(view)


def test_an_active_key_naming_nothing_in_this_view_changes_nothing():
    """The active entity is routinely off in another course entirely --
    `orderedSections` must not raise, and must not promote anything, just
    because the key it was handed matches no section here."""
    view = {"stars": [star("a", 100), star("c", 10)], "segments": []}
    assert ordered_active(view, "star:99:9") == ["a", "c"]


def test_the_already_leading_active_entity_is_a_no_op():
    view = {"stars": [star("a", 100, course_id=2, star_id=4)], "segments": []}
    assert ordered_active(view, "star:2:4") == ["a"]


# ---- hasEarnedACard -- "if we leave without practicing anything" -----------

def _has_earned_a_card_source() -> str:
    log_source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    return "\n".join([_entity_key_source_early(),
                       _extract(log_source, "hasEarnedACard")])


def earned(sec, active_key) -> bool:
    script = (_has_earned_a_card_source() + "\n"
              f"console.log(JSON.stringify(hasEarnedACard("
              f"{json.dumps(sec)}, {json.dumps(active_key)})));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _sec(course_id=2, star_id=4, kind=None, segment_id=None,
         attempts=None, armed_detail=None):
    base = {"attempts": attempts or [], "armed_detail": armed_detail}
    if kind == "segment":
        base.update({"kind": "segment", "segment_id": segment_id,
                     "course_id": course_id})
    else:
        base.update({"course_id": course_id, "star_id": star_id})
    return base


def test_a_target_only_star_the_player_is_standing_at_earns_its_card():
    """The Piece A companion case: picked, zero attempts, still there."""
    assert earned(_sec(course_id=2, star_id=4), "star:2:4") is True


def test_a_target_only_star_the_player_has_left_earns_nothing():
    """Griffin: "If we leave without practicing anything, its card should
    disappear from the list." activeKey no longer names this entity once he
    has left -- the same signal the card's own `.log-card-active` border
    already reads."""
    assert earned(_sec(course_id=2, star_id=4), None) is False
    assert earned(_sec(course_id=2, star_id=4), "star:9:1") is False


def test_any_real_attempt_earns_a_card_unconditionally():
    assert earned(_sec(course_id=2, star_id=4, attempts=[{"id": 1}]), None) is True


def test_a_still_armed_entity_earns_a_card_even_with_no_active_key():
    """"A RUNNING segment is never invisible" (2026-07-24), independent of
    `activeKey` on purpose: several defs can arm off one course entry with no
    single one of them unambiguously "the" pick (practice.js's own
    `ambiguousPins`), and none of them may vanish for lack of one."""
    assert earned(_sec(kind="segment", segment_id=8, armed_detail={"progress": 0}),
                  None) is True


def test_a_course_less_target_the_player_has_left_earns_nothing_even_though_activekey_still_names_it():
    """The measured gap: `stagecontext.js`'s `practicedHere` treats EVERY
    course-less place (the castle, any hub, any OTHER arena) as "still here"
    for an arena-originated entity, so `activeKey` alone kept naming a
    disarmed Bowser fight long after the player had walked back to the
    lobby (verified live against a synthetic TrackerService run: entering
    the Bowser 1 arena, auto-selecting its fight, leaving to the lobby with
    nothing grabbed -- the fight disarms correctly, but `activeKey` still
    named it). `course_id == null` is exactly how such an entity is shaped;
    a course-BEARING target has no such gap (`practicedHere` requires an
    exact course match), which is the case the test above covers."""
    course_less = _sec(kind="segment", segment_id=8, armed_detail=None)
    course_less["course_id"] = None
    assert earned(course_less, "segment:8") is False


# ---- the reds pair -- both halves show, star nested inside the movement ---
#
# `applyRedsPipeExclusivity` is DELETED (design spec 2026-08-10-reds-as-
# subsection item 5): a Bowser course's reds run is no longer two things
# practiced with one hidden by a star/pipe toggle -- the star is a
# [[subsection]] of its paired reds->pipe movement now (views.py's `parents`
# stamp, task 1), and `nestSubsections` (ui/subsections.js) draws it nested
# inside the movement's card instead of a mode picking one of the two to
# hide. This suite replaces the deleted one and proves the composition
# `PracticeLog` actually runs -- `orderedSections` (this file) feeding the
# REAL `nestSubsections` -- at the same pure level the old one lived at; the
# render-level proof that this composition actually PAINTS that way lives in
# tests/test_responsive_subsections.py::test_the_reds_star_draws_inside_its_movement_s_card.

SUBSECTIONS_JS = UI / "subsections.js"


def _reds_star(course_id=16, pipe_segment_id=5, last_activity=10):
    return {"course_id": course_id, "star_id": 0,
            "pipe_segment_id": pipe_segment_id,
            "parents": [f"segment:{pipe_segment_id}"],
            "last_activity": last_activity, "attempts": [{"id": 1}]}


def _pipe_segment(course_id=16, segment_id=5, last_activity=20):
    return {"kind": "segment", "segment_id": segment_id, "course_id": course_id,
            "pipe_segment_id": None, "last_activity": last_activity,
            "attempts": [{"id": 2}]}


def reds_pipe_groups(sections) -> list:
    """`orderedSections` (extracted, as `ordered()` above) piped straight
    into the REAL `nestSubsections` -- imported for real, since
    ui/subsections.js is import-free the same way test_ui_subsections.py's
    own `nest()` helper relies on. Returns `[(key, (child keys...))]`, the
    same shape that helper returns.

    `orderedSections` takes a VIEW (`{stars, segments}`), not a flat list --
    split by `kind` the same way `star()`/`segment()`'s own callers never
    have to, since this is the one place in this file that hands it a mixed
    reds/pipe pair rather than one bucket at a time."""
    view = {"stars": [sec for sec in sections if sec.get("kind") != "segment"],
            "segments": [sec for sec in sections if sec.get("kind") == "segment"]}
    script = (
        f"import {{ nestSubsections }} from {SUBSECTIONS_JS.as_uri()!r};\n"
        + _ordered_sections_source() + "\n"
        "const key = (s) => s.kind === 'segment' ? `segment:${s.segment_id}`"
        " : `star:${s.course_id}:${s.star_id}`;\n"
        f"const ordered = orderedSections({json.dumps(view)});\n"
        "const groups = nestSubsections(ordered);\n"
        "console.log(JSON.stringify(groups.map("
        "(g) => [key(g.sec), g.children.map(key)])));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return [(k, tuple(kids)) for k, kids in json.loads(result.stdout)]


def test_both_halves_of_a_reds_pair_are_present_star_nested_inside():
    """Neither half is dropped any more -- the movement leads at the top
    level and the star nests inside it, not beside it."""
    groups = reds_pipe_groups([_reds_star(), _pipe_segment()])
    assert groups == [("segment:5", ("star:16:0",))]


def test_each_courses_pair_nests_independently():
    """A second Bowser course's pair nests under ITS OWN movement -- the
    parents stamp is per-instance (`segment:<id>`), so there is no shared
    'mode' left for one course's pairing to leak into another's the way
    `applyRedsPipeExclusivity`'s course-keyed lookup could have."""
    sections = [_reds_star(course_id=16, pipe_segment_id=5),
                _pipe_segment(course_id=16, segment_id=5),
                _reds_star(course_id=17, pipe_segment_id=6, last_activity=30),
                _pipe_segment(course_id=17, segment_id=6, last_activity=25)]
    assert dict(reds_pipe_groups(sections)) == {
        "segment:5": ("star:16:0",), "segment:6": ("star:17:0",)}


def test_a_plain_star_with_no_pipe_pairing_is_unaffected():
    """No `parents` at all -- unrelated to any Bowser pairing -- stays a lone
    top-level card, same as it always did."""
    plain = star("s", 5, course_id=13, star_id=2)
    plain["pipe_segment_id"] = None
    assert reds_pipe_groups([plain]) == [("star:13:2", ())]


# ---- topEntityKey / isCardOpen -- the auto-open-newest feature -------------
#
# `topEntityKey` calls `entityKey` (entitysection.js), which practicelog.js
# imports for real -- so the node script needs the REAL `entityKey` too, not a
# restatement of its `star:<course>:<star>` / `segment:<id>` shape. `isSegment`
# and `entityKey` are both plain, import-free declarations in entitysection.js
# (only `displayName`/`segmentFamily` there reach for redsfamily.js), so
# extracting them is the same "real function, comments stripped" technique
# `_ordered_sections_source` above already uses, just from a second file.

def _entity_key_source() -> str:
    source = strip_comments(ENTITY_SECTION_JS.read_text(encoding="utf-8"))
    is_segment = re.search(r"^export const isSegment = .*?;$", source, re.M)
    assert is_segment, "isSegment not found in entitysection.js — renamed?"
    return is_segment.group(0) + "\n" + _extract(source, "entityKey")


def _played_keys_source() -> str:
    """`hasRecordedAttempts` + `playedEntityKeys` -- the auto-open slot's own
    eligibility rule, which `topEntityKey` is now a one-line consumer of."""
    log_source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    has = re.search(r"^export const hasRecordedAttempts = .*?;$", log_source,
                    re.M)
    assert has, "hasRecordedAttempts not found in practicelog.js — renamed?"
    return "\n".join([has.group(0),
                     _extract(log_source, "playedEntityKeys")])


def _top_entity_key_source() -> str:
    log_source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    return "\n".join([_entity_key_source(),
                     _extract(log_source, "orderedSections"),
                     _played_keys_source(),
                     _extract(log_source, "topEntityKey")])


def played_keys(view):
    log_source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    script = "\n".join([_entity_key_source(),
                       _extract(log_source, "orderedSections"),
                       _played_keys_source(),
                       f"console.log(JSON.stringify(playedEntityKeys("
                       f"{json.dumps(view)})));"])
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# ---- the slot's eligibility rule, asked about ANY entity ------------------
#
# 2026-08-05. `topEntityKey` had carried this rule since earlier the same day
# and stated it emphatically in its own comment -- and the card it is most
# about, the one Griffin had just selected, sailed straight past it, because
# practice.js resolved the slot as `live.activeKey ?? frozen.topKey`: an
# UNCONDITIONAL override that never consulted the rule at all. Three
# screenshots, three stages, one sentence: "If there are no attempts yet,
# it's closed by default. If there are attempts, then it's autoopened."
#
# So the rule is exported as its own function and BOTH callers ask it. These
# pin the shared half; test_practice_page_order.py pins the composition.

def test_an_entity_with_nothing_recorded_is_not_eligible():
    view = {"stars": [star("empty", 99, course_id=22, star_id=3, attempts=0)],
            "segments": [], "unassigned": []}
    assert played_keys(view) == []


def test_one_recorded_row_is_enough():
    """A RESET counts. He said "an attempt or reset", and a reset is the
    ordinary first thing a practice card ever holds."""
    view = {"stars": [star("one", 99, course_id=22, star_id=3, attempts=1)],
            "segments": [], "unassigned": []}
    assert played_keys(view) == ["star:22:3"]


def test_eligibility_is_reported_in_recency_order_and_drops_the_empties():
    view = {"stars": [star("mid", 50, course_id=1, star_id=1),
                       star("empty", 99, course_id=2, star_id=2, attempts=0),
                       star("old", 5, course_id=3, star_id=3)],
            "segments": [], "unassigned": []}
    assert played_keys(view) == ["star:1:1", "star:3:3"]


def top_key(view):
    script = (_top_entity_key_source() + "\n"
              f"console.log(JSON.stringify(topEntityKey("
              f"{json.dumps(view)})));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _is_card_open_source() -> str:
    source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    return _extract(source, "isCardOpen")


def is_open(overrides, top, key, child_keys=()):
    """`child_keys` defaults to empty, never `null` -- `isCardOpen`'s own
    default parameter only kicks in for a genuinely OMITTED argument, and a
    `null` fourth arg would reach `childKeys.includes` and throw."""
    script = (_is_card_open_source() + "\n"
              f"console.log(JSON.stringify(isCardOpen("
              f"{json.dumps(overrides)}, {json.dumps(top)}, "
              f"{json.dumps(key)}, {json.dumps(list(child_keys))})));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_stars_and_segments_interleave_by_recency():
    """The whole point of the feature: the segment you just finished sits
    above the star you did before it, whatever kind each one is."""
    view = {"stars": [star("a", 100), star("c", 10)],
            "segments": [segment("b", 50)]}
    assert ordered(view) == ["a", "b", "c"]


def test_a_section_with_no_activity_sorts_last_not_first():
    """A target set but never run carries last_activity -1."""
    view = {"stars": [star("fresh", -1), star("played", 5)], "segments": []}
    assert ordered(view) == ["played", "fresh"]


def test_a_missing_segments_key_is_not_a_crash():
    assert ordered({"stars": [star("a", 1)]}) == ["a"]


# ---- topEntityKey -----------------------------------------------------------

def test_the_top_key_is_the_most_recently_practiced_entity():
    view = {"stars": [star("a", 100, course_id=2, star_id=4),
                       star("c", 10, course_id=9, star_id=3)],
            "segments": [segment("b", 50, segment_id=6)]}
    assert top_key(view) == "star:2:4"


def test_the_top_key_is_null_with_nothing_practiced():
    """Nothing classified yet -- the unassigned bucket is not an entity and
    is never eligible for the auto-open slot (UnassignedLogCard's own
    comment); `view` itself may not have loaded yet either."""
    assert top_key({"stars": [], "segments": []}) is None
    assert top_key(None) is None


# ---- isCardOpen -- the ONE auto-open slot -----------------------------------

def test_the_top_card_opens_with_no_override():
    assert is_open({}, "star:2:4", "star:2:4") is True


def test_a_non_top_card_stays_closed_with_no_override():
    assert is_open({}, "star:2:4", "star:9:3") is False


def test_nothing_opens_with_no_top_key_at_all():
    assert is_open({}, None, "star:2:4") is False


def test_a_card_the_user_opened_himself_survives_a_different_top():
    """"A card the user opened himself is his. Arriving entries must never
    close it" -- his rule verbatim. The override wins even though a
    DIFFERENT entity now holds the auto-open slot."""
    assert is_open({"star:9:3": "open"}, "star:2:4", "star:9:3") is True


def test_a_card_the_user_closed_himself_stays_closed_even_at_the_top():
    """"A card the user closed himself stays closed... It reopens only when a
    genuinely new entity takes the top slot" -- the override wins even while
    this SAME entity still holds the slot (a mere view refresh must not
    reopen it)."""
    assert is_open({"star:2:4": "closed"}, "star:2:4", "star:2:4") is False


def test_a_displaced_top_card_closes_with_no_override_needed():
    """The half of the feature that needs no state at all: the PREVIOUS top
    card simply stops matching `topKey` the moment a new entity takes the
    slot, so it closes by falling through to the auto-open rule -- exactly
    as if it had never been touched."""
    assert is_open({}, "star:9:3", "star:2:4") is False


def test_a_parent_opens_when_its_own_child_holds_the_slot():
    """A [[subsection]]'s own card cannot open without its PARENT's --
    `Disclose` does not mount closed body content at all, so a nested piece
    that wins the auto-open slot must ALSO open the card wrapping it, or the
    piece is computed correctly and invisible anyway (round 2026-08-10,
    reds-as-subsection: the render gate this fixes)."""
    assert is_open({}, "star:16:0", "segment:67", child_keys=["star:16:0"]) is True


def test_a_card_with_an_unrelated_child_does_not_open():
    assert is_open({}, "star:16:0", "segment:67", child_keys=["star:9:3"]) is False


def test_a_manual_close_on_the_parent_still_wins_over_its_child():
    """His rule outranks this one too -- a card he closed himself stays
    closed even while one of its own pieces holds the slot."""
    assert is_open({"segment:67": "closed"}, "star:16:0", "segment:67",
                    child_keys=["star:16:0"]) is False


def test_no_child_keys_at_all_is_the_old_behaviour_byte_for_byte():
    """Every pre-nesting call site (three positional args, no fourth) must
    keep working exactly as it did -- this is the default the omitted
    argument falls back to."""
    assert is_open({}, "star:2:4", "star:2:4") is True
    assert is_open({}, "star:2:4", "star:9:3") is False


def test_an_unassigned_reset_can_never_take_the_top_slot():
    """The unassigned bucket is noise and stays at the bottom, closed.

    Griffin, 2026-08-04: "the unassigned runs card should ALWAYS stay closed,
    unless the user opens it. It should also ALWAYS stay at the bottom of the
    list, even if an unassigned reset is the newest entry. This is because
    that information is noise and should be at the bottom of the screen,
    tucked away."

    The ordering half holds by construction rather than by a rule anyone has
    to remember: `topEntityKey` reads `orderedSections`, which merges the
    view's stars and segments, and the bucket is neither -- it is a flat
    attempt list with no `last_activity` of its own. This pins that, because
    "by construction" stops being true the moment someone teaches
    orderedSections about a third kind.
    """
    view = {"stars": [star("s", 10)], "segments": [],
            "unassigned": [{"id": 99, "journal_id": 9999, "outcome": "reset"}]}
    assert top_key(view) == "star:13:1", (
        "an unassigned attempt, however recent, must not win the auto-open "
        "slot -- it has no card of its own in the recency ordering")


def test_the_unassigned_card_starts_closed():
    """Its own default, and the one half of his rule that needed code.

    Deliberately NOT a check that some option is set to a value -- this is the
    component's shipped behaviour, not a tuning default, so pinning it here
    does not collide with the "no test may assert a shipped default" rule.
    """
    source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    match = re.search(r"function UnassignedLogCard\([^)]*\)\s*\{\s*"
                      r"const \[open, setOpen\] = useState\((?P<initial>\w+)\)",
                      source)
    assert match, "UnassignedLogCard no longer owns an `open` useState"
    assert match.group("initial") == "false", (
        "the unassigned bucket must start CLOSED -- it is noise tucked away "
        "at the bottom, and only a click of his opens it")


# ---- the log's own row reaches the active-strategy reclassification path ---
#
# `tracking/service.py::set_attempt_strat` already does what Griffin asked for
# (2026-07-24): reclassifying an entity's NEWEST non-cleared attempt also
# moves that entity's ACTIVE strategy, on the theory that "my last run was
# actually strat X" means X is what is being practised now. That rule is
# server-side and pre-dates this round -- nothing here re-implements it. What
# this round changed is where the row doing the reclassifying LIVES: every
# attempt is now inside a `LogCard` (practicelog.js) rather than a per-kind
# section, so the thing worth proving is that the WIRING still reaches the
# server rule, not that the rule itself works.
ATTEMPTLOG_JS = UI / "components" / "attemptlog.js"


def test_the_log_cards_open_attempt_table_carries_its_own_entity():
    """`AttemptRow`'s strategy picker only reclassifies THIS attempt against
    THIS entity's own strategies/identity when it has `sec` -- drop the prop
    and every row falls back to the plain, non-reclassifiable
    `<span>${a.strat_tag || "no strategy"}</span>` branch
    (attemptlog.js::AttemptTable), which would make a log card's own
    top-row-reclassify-the-active-strategy path silently unreachable while
    every other guard in this file stays green (the exact shape a reviewer
    already caught once on this branch, StatChipsRow silently dropped for
    segments)."""
    log_source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    assert re.search(
        r"<\$\{AttemptTable\}\s+attempts=\$\{sec\.attempts\}\s+rows=\$\{shown\}"
        r"\s+t=\$\{t\}\s*\n\s*focus=\$\{selected \? focus : null\}"
        r"\s+clearFocus=\$\{clearFocus\}\s*\n\s*freshIds=\$\{freshIds\}"
        r"\s+openCompare=\$\{openCompare\}\s+sec=\$\{sec\}", log_source), (
        "LogCard's <AttemptTable> call no longer passes sec=${sec} -- the "
        "attempt rows it renders would lose their entity, and the top row's "
        "own strategy picker could no longer reclassify anything")


def test_the_reclassify_wiring_guard_can_still_fail():
    """Probed in both directions, per the norm in ui-core.md: a comment
    mentioning the shape, or the prop simply missing, must not satisfy the
    positive regex above."""
    comment_only = strip_comments(
        "// <${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t}\n"
        "// focus=${selected ? focus : null} clearFocus=${clearFocus}\n"
        "// freshIds=${freshIds} openCompare=${openCompare} sec=${sec} />\n")
    dropped_prop = (
        "<${AttemptTable} attempts=${sec.attempts} rows=${shown} t=${t}\n"
        "focus=${selected ? focus : null} clearFocus=${clearFocus}\n"
        "freshIds=${freshIds} openCompare=${openCompare} />\n")
    pattern = (r"<\$\{AttemptTable\}\s+attempts=\$\{sec\.attempts\}\s+rows=\$\{shown\}"
               r"\s+t=\$\{t\}\s*\n\s*focus=\$\{selected \? focus : null\}"
               r"\s+clearFocus=\$\{clearFocus\}\s*\n\s*freshIds=\$\{freshIds\}"
               r"\s+openCompare=\$\{openCompare\}\s+sec=\$\{sec\}")
    assert not re.search(pattern, comment_only)
    assert not re.search(pattern, dropped_prop)


def test_the_attempt_row_still_posts_a_reclassification_to_the_server_rule():
    """The other half of the same chain, in attemptlog.js -- unchanged by
    this round, and pinned here anyway because it is the other link a future
    edit could quietly cut. `POST /api/attempts/{id}/strat` is
    `tracking/service.py::set_attempt_strat`'s own route
    (`server/api.py`); this only proves the CLIENT still calls it from a row
    that has an entity, never the server-side newest-row rule itself."""
    attemptlog_source = strip_comments(ATTEMPTLOG_JS.read_text(encoding="utf-8"))
    assert re.search(
        r'send\("POST", `/api/attempts/\$\{a\.id\}/strat`,\s*'
        r"\{\s*strat_tag:\s*tag\s*\}\)", attemptlog_source), (
        "AttemptRow's strategy picker no longer posts to "
        "/api/attempts/{id}/strat -- reclassifying a row from inside a "
        "LogCard would no longer reach set_attempt_strat's newest-row rule")
    assert re.search(r"\$\{sec\s*\n?\s*\?\s*html`<\$\{StratPicker\}", attemptlog_source), (
        "AttemptRow no longer gates its strategy picker on having a `sec` -- "
        "a row rendered with none would silently fall back to plain, "
        "unreclassifiable text")


def test_an_entity_with_nothing_recorded_does_not_claim_the_auto_open_slot():
    """Selecting something shows its card; it does not OPEN it.

    Griffin, 2026-08-05: "The preselected option should be closed by default
    because otherwise we're wasting space to tell the user they dont have
    anything practiced, which they already know. When we actually have an
    attempt or reset, then it autoopens."

    Two facts this pins together, because they are easy to conflate: the
    empty entity still LEADS the list (the active-leads rule), and it is
    still not the auto-open slot. Only the second is tested here; the first
    is orderedSections' own.
    """
    empty = star("fresh", 99, attempts=0)   # newest by activity, nothing run
    played = star("played", 5)
    view = {"stars": [empty, played], "segments": [], "unassigned": []}
    assert top_key(view) == "star:13:1", (
        "an entity with no attempts must not hold the auto-open slot -- its "
        "open body is an empty state whose only message is 'nothing yet'")


def test_the_slot_moves_the_moment_a_first_attempt_lands():
    """The other half: no separate 'now open it' trigger has to exist,
    because recording an attempt also makes that entity newest by activity."""
    fresh = star("fresh", 99, course_id=22, star_id=3)
    played = star("played", 5)
    view = {"stars": [fresh, played], "segments": [], "unassigned": []}
    assert top_key(view) == "star:22:3"


# ---- autoOpenKey: which RENDERED card holds the one open slot -------------
#
# 2026-08-05, the round after the eligibility rule. Two claims, and the second
# is the one that produced a live bug: the slot is chosen from the list the
# user is LOOKING at, and it does not go dark when the newly-active entity is
# empty -- the thing he just finished keeps it. Griffin: "when we're moving
# between courses / segments of the game, I want to still see the thing I just
# accomplished (until I've now accomplished the next star/segment)... we
# shouldn't close the last thing until we've started a new one (with a valid
# practice log entry)."

def _auto_open_source() -> str:
    log_source = strip_comments(LOG_JS.read_text(encoding="utf-8"))
    return "\n".join([_entity_key_source(),
                        _played_keys_source(),
                        _extract(log_source, "autoOpenKey")])


def auto_open(sections, active_key, played):
    script = (_auto_open_source() + "\n"
              f"console.log(JSON.stringify(autoOpenKey("
              f"{json.dumps(sections)}, {json.dumps(active_key)}, "
              f"{json.dumps(played)})));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_active_entity_takes_the_slot_once_it_has_recorded_something():
    played = star("played", 5, course_id=1, star_id=1)
    active = star("active", 99, course_id=2, star_id=2)
    assert auto_open([active, played], "star:2:2",
                     ["star:2:2", "star:1:1"]) == "star:2:2"


def test_an_empty_active_entity_leaves_the_slot_where_it_was():
    """The reported behaviour: walking into Bowser 1 must not close the reds
    run he just finished. The empty active card leads the list and stays
    shut; the last thing he actually played keeps the open slot."""
    just_done = star("done", 50, course_id=1, star_id=1)
    fresh = star("fresh", 99, course_id=2, star_id=2, attempts=0)
    assert auto_open([fresh, just_done], "star:2:2", ["star:1:1"]) == "star:1:1"


def test_the_handover_happens_on_the_new_entity_s_first_row():
    """...UNTIL the user adds an entry for the new area, whether reset or
    valid entry. One row is the whole trigger -- `_rows` produces resets."""
    just_done = star("done", 50, course_id=1, star_id=1)
    fresh = star("fresh", 99, course_id=2, star_id=2, attempts=1)
    assert auto_open([fresh, just_done], "star:2:2",
                     ["star:2:2", "star:1:1"]) == "star:2:2"


def test_a_played_key_that_is_not_rendered_is_skipped_not_returned():
    """THE BUG. A Bowser reds star and its pipe segment tie on activity and
    the star sorts first, so `playedKeys` leads with the star -- while the
    log renders only the pipe. Returning the star opened nothing at all."""
    pipe = segment("pipe", 1414, segment_id=67)
    rendered = [pipe]
    assert auto_open(rendered, None, ["star:16:0", "segment:67"]) == "segment:67"


def test_nothing_played_and_nothing_active_leaves_every_card_closed():
    fresh = star("fresh", 99, course_id=2, star_id=2, attempts=0)
    assert auto_open([fresh], "star:2:2", []) is None
