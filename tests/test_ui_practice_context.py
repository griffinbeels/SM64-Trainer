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


def test_the_castle_and_the_hubs_drop_nothing():
    """Transit — every course is entered through them, so a card must survive
    walking back to a movement's start, and the star you just practiced stays
    up in the lobby (the user's ruling, asked explicitly 2026-07-27)."""
    assert belongs(WF_STAR, LOBBY)
    assert belongs(WF_STAR, {"course_id": None, "level": 16, "area": 1,
                             "mode": None})
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
    disagreed = [(pair, mine, theirs)
                 for pair, mine, theirs in zip(pairs, answers, expected)
                 if mine != theirs]
    assert not disagreed, (
        f"{len(disagreed)} of {len(pairs)} disagree, e.g. {disagreed[:3]}")


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
    assert re.search(r"const starActive = inContext &&", body)
    assert re.search(r"const pinnedSegs = !inContext \|\|", body)
    # ...and BOTH rules reach every pin the user can see. Armed pins are the
    # one deliberate exemption (a live timer is visible wherever it got to).
    assert re.search(r"isActiveStar\(sec\) && here\(sec\)", body)
    assert re.search(r"stickyPin && here\(stickyPin\)", body)
    assert re.search(r"activeSeg && here\(activeSeg\)", body)
