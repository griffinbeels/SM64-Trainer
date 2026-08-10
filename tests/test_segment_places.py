"""A segment is shown where it can be run, or where it walks through.

Griffin, 2026-08-10: *"we shouldn't be displaying segments when the segments
are fundamentally impossible to be practiced in a location (and such that the
segment doesn't go through that location)."*

The rule it replaced compared COURSES, and `origin_course` answers None for the
castle interior, both hubs AND the three Bowser arenas alike -- so a room every
castle movement walks through and a room no movement can reach were one bucket.
Standing in the Bowser 1 arena, where the only practicable thing is the fight,
every castle movement still had a card.

BOTH HALVES ARE HERE ON PURPOSE. `segments.reachable_places` derives the set,
`ui/stagecontext.js::practicedHere` compares it, and the whole point of the
change is that they are one rule -- splitting them across two files would let
either drift while its own file stayed green.

Measured before it shipped:
  * `tools/measure_unrunnable_holds.py`, his live journal: arena holds where a
    fresh pick would be refused, 31 -> 3 (the 3 are HOOKED heads, deliberately
    still exempt -- see the projector's own comment);
  * `tools/measure_target_queue.py --before HEAD`: 49 target readings move,
    37 to none and 12 to something runnable here, and **0 attempt rows lost,
    gained or changed**.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sm64_events.memory.addresses import (BOWSER_1_ARENA, BOWSER_3_ARENA,
                                          LEVEL_CASTLE_INSIDE)
from sm64_events.tracking import topology
from sm64_events.tracking.segments import (SegmentDef, reachable_places,
                                           stage_origin, start_origin)

REPO = Path(__file__).resolve().parent.parent
STAGECONTEXT_JS = (REPO / "src" / "sm64_events" / "ui"
                   / "stagecontext.js").as_uri()

WF_LEVEL, SSL_LEVEL, BITDW_LEVEL, BITS_LEVEL = 24, 8, 17, 21
LOBBY, BASEMENT = f"{LEVEL_CASTLE_INSIDE}:1", f"{LEVEL_CASTLE_INSIDE}:3"


def movement(id=1, name="WF -> SSL", start_from=WF_LEVEL, waypoints=(),
             end_to=SSL_LEVEL):
    """A castle movement's real shape: armed by a level EXIT, ended by
    touching the destination's entrance."""
    return SegmentDef(id=id, name=name, enabled=True,
                      start_triggers=[{"type": "level_exit",
                                       "from": start_from}],
                      waypoints=[list(step) for step in waypoints],
                      end_triggers=[{"type": "entrance_touched",
                                     "to": end_to}],
                      guards=[])


def places_of(definition):
    return reachable_places(definition, start_origin(definition.start_triggers))


# ---- the derivation -------------------------------------------------------

def test_a_movement_keeps_the_castle_it_walks_through():
    """The corpus never names the LOBBY in `WF -> SSL` -- it names the
    basement and the destination -- and walking out of Whomp's Fortress puts
    you there first. The shortest-walk fill is what supplies it, which is why
    transit survives without a single hand-written exemption."""
    places = places_of(movement())
    assert str(WF_LEVEL) in places, "it starts here"
    assert LOBBY in places, "every course exit lands in the lobby"
    assert BASEMENT in places, "the way to SSL"
    assert str(SSL_LEVEL) in places, "where it ends"


def test_an_arena_is_on_no_movement_s_way():
    """THE report. A Bowser arena is entered from its course and exits back
    out; nothing walks through it, so no castle movement may draw a card
    there."""
    assert str(BOWSER_1_ARENA) not in places_of(movement())
    assert str(BOWSER_3_ARENA) not in places_of(movement())


def test_the_arena_s_own_fight_belongs_in_its_arena():
    """The rule is about what cannot be run HERE, never about arenas as such.
    Getting this backwards would hide the one card the room is for."""
    fight = SegmentDef(id=9, name="Bowser 3", enabled=True,
                       start_triggers=[{"type": "area_enter",
                                        "level": BOWSER_3_ARENA, "area": 1}],
                       end_triggers=[{"type": "key_grabbed"}], guards=[])
    assert places_of(fight) == frozenset({str(BOWSER_3_ARENA)})


def test_a_definition_that_names_no_START_place_is_shown_anywhere():
    """A `reset_game` start is satisfiable wherever he is standing, so nothing
    about such a definition may be hidden. Its END triggers say where the
    route GOES, never where it may begin -- reading them as a constraint
    retired a placeless target the moment he walked into any course, which
    `tests/test_practicable.py` had already forbidden years of rounds ago."""
    anywhere = SegmentDef(id=7, name="from the top", enabled=True,
                          start_triggers=[{"type": "reset_game"}],
                          end_triggers=[{"type": "level_enter", "to": 9}],
                          guards=[])
    assert places_of(anywhere) == frozenset()


def test_a_declared_detour_is_kept_even_when_it_is_not_the_short_way():
    """A re-entry movement deliberately goes somewhere the shortest walk
    would skip. Chaining the walk pair by pair -- not just origin-to-each-step
    -- is what keeps those hops, and a route the definition NAMES is never a
    place we may hide it in (the same premise `declared_nodes` rests on)."""
    detour = movement(waypoints=[[{"type": "level_enter",
                                   "to": BITDW_LEVEL}]])
    assert str(BITDW_LEVEL) in places_of(detour)


def test_the_start_origin_s_own_key_is_normalised_into_the_graph_s():
    """`start_origin` builds keys straight out of clause params, so an
    `area_enter(level=21, area=1)` resolves to "21:1" -- a subarea key for a
    level the graph models as ONE place. Left unnormalised it answers None
    from `hops`, every fill comes back empty, and a real node reads as no
    constraint. This cost a whole round: the first arena rule compared a
    def's "33:1" against a stage's "33" and retired the arena's own fight."""
    pipe = SegmentDef(id=3, name="BitS Pipe Entry", enabled=True,
                      start_triggers=[{"type": "area_enter",
                                       "level": BITS_LEVEL, "area": 1}],
                      end_triggers=[{"type": "warp_entered",
                                     "level": BITS_LEVEL}], guards=[])
    assert start_origin(pipe.start_triggers) == f"{BITS_LEVEL}:1"
    assert places_of(pipe) == frozenset({str(BITS_LEVEL)})
    assert stage_origin(BITS_LEVEL, 1) in places_of(pipe), (
        "the player's own key and the definition's must land in one vocabulary")


def test_between_is_inclusive_and_declines_on_an_impossible_walk():
    assert topology.between("30", "30") == frozenset({"30"})
    assert topology.between(None, "24") == frozenset()
    assert topology.between("24", "not-a-node") == frozenset()


def test_every_seeded_definition_lands_somewhere():
    """An empty set means "show it anywhere", which is the right answer for a
    placeless definition and the WRONG one for all 84 shipped rows -- it would
    silently switch the whole rule off while every other test stayed green."""
    from sm64_events.core.paths import bundled_defaults_seed
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    homeless = []
    for index, row in enumerate(seed["segments"], start=1):
        definition = SegmentDef(
            id=index, name=row["name"], enabled=True,
            start_triggers=row["start_triggers"],
            waypoints=row.get("waypoints") or [],
            end_triggers=row["end_triggers"], guards=row.get("guards") or [])
        if not places_of(definition):
            homeless.append(row["name"])
    assert homeless == []


# ---- ...and the browser compares it ---------------------------------------

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def drew(section: dict, stage: dict | None) -> bool:
    script = (f"import {{ practicedHere }} from {STAGECONTEXT_JS!r};\n"
              f"console.log(JSON.stringify(practicedHere("
              f"{json.dumps(section)}, {{stage: {json.dumps(stage)}}})));")
    done = subprocess.run(["node", "--input-type=module", "-"], input=script,
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


ARENA_STAGE = {"course_id": None, "level": BOWSER_3_ARENA, "area": 1,
               "mode": "arena", "node": str(BOWSER_3_ARENA)}
LOBBY_STAGE = {"course_id": None, "level": LEVEL_CASTLE_INSIDE, "area": 1,
               "mode": "castle", "node": LOBBY}
PIPE_SECTION = {"kind": "segment", "segment_id": 3, "course_id": None,
                "places": [str(BITS_LEVEL)]}
MOVEMENT_SECTION = {"kind": "segment", "segment_id": 1, "course_id": None,
                    "places": [LOBBY, BASEMENT, str(SSL_LEVEL)]}


def test_the_card_leaves_an_arena_it_has_no_business_in():
    assert not drew(PIPE_SECTION, ARENA_STAGE)
    assert not drew(MOVEMENT_SECTION, ARENA_STAGE)


def test_the_card_stays_in_the_castle_it_walks_through():
    assert drew(MOVEMENT_SECTION, LOBBY_STAGE)


def test_a_section_with_no_places_falls_back_to_the_course_rule():
    """A STAR carries no `places` and never will -- one atomic grab has no
    route -- and neither does a deleted definition. Both must keep answering
    exactly as they did before this field existed."""
    star = {"kind": "star", "course_id": None, "star_id": 0}
    assert drew(star, LOBBY_STAGE)
    assert drew({**PIPE_SECTION, "places": []}, ARENA_STAGE)


def test_an_unknown_player_place_never_hides_anything():
    """The emulator detached, or a stage published before the node was
    stamped: unknown means yes, everywhere in this codebase."""
    assert drew(PIPE_SECTION, {**ARENA_STAGE, "node": None})


def test_the_real_payload_carries_both_halves(tmp_path):
    """The chain, not the parts. Every piece above can be right while the
    field never reaches the browser -- the class of failure that shipped an
    instrument reading "exact" over a join that was never unique
    (`.claude/rules/ui-core.md`). So drive the REAL service and the REAL view
    builder and read the two keys the rule compares.

    The stage half is the one that would go wrong quietly: the browser MERGES
    a `stage_changed` payload into its held stage rather than refetching, so a
    `node` present only on the session view would go stale on the next move
    and keep answering for a room he has left."""
    import asyncio

    from sm64_events.core.events import Event
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.storage.db import Database
    from sm64_events.tracking.service import TrackerService
    from sm64_events.tracking.views import build_session_view

    published: list = []
    broadcaster = Broadcaster()
    service = TrackerService(Database(tmp_path / "reach.db"), broadcaster)
    asyncio.run(service.start())
    original = broadcaster.publish

    async def watching(event):
        published.append(event)
        return await original(event)

    broadcaster.publish = watching
    asyncio.run(service.publish(Event(
        type="stage_changed", frame=10,
        timestamp_utc=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc),
        payload={"course_id": None, "level": LEVEL_CASTLE_INSIDE, "area": 3,
                 "mode": "castle"})))

    broadcast = next(e for e in published if e.type == "stage_changed")
    assert broadcast.payload["node"] == BASEMENT, (
        "the BROADCAST must carry it -- the browser merges this payload")
    view = build_session_view(service.db, service, clock="igt")
    assert view["stage"]["node"] == BASEMENT, "and so must the initial load"
    # The selector's own payload deliberately carries NO reach -- its rows
    # filter on where a definition STARTS, which is strictly narrower. Pinned
    # so nobody adds the field back without a caller for it.
    assert all("places" not in row for row in view["segment_targets"])


def test_the_place_rule_only_ever_narrows_the_course_rule():
    """It is an AND, deliberately. The path rule alone would also SHOW a
    movement all along its own route -- `WF -> SSL` in the lobby it walks
    through -- which the course rule has always hidden and nobody has asked
    for (111 such pairs across the shipped corpus). Widening is a separate
    decision; keeping it an AND is also what keeps the card STRICTER than the
    projector, the one direction test_ui_practice_context.py pins."""
    in_a_course = {"course_id": 2, "level": WF_LEVEL, "area": 1,
                   "mode": "stars", "node": str(WF_LEVEL)}
    walks_through_wf = {"kind": "segment", "segment_id": 5, "course_id": None,
                        "places": [str(WF_LEVEL), LOBBY]}
    assert not drew(walks_through_wf, in_a_course), (
        "a course-less section is still hidden inside a course, as before")
