# tests/test_spawn_subarea.py
"""Round 20 item 3, the halves ABOVE the detector (which test_spawn.py owns):
the label a subarea spawn wears, the trigger clause that can start a segment
on it, and the synthesis that pins one from a picked recorder row.

His ask, verbatim: "When I reset the level INSIDE OF A SUBAREA, we should
actually have a special 'Spawned into Lethal Lava Land [Subarea Name]'
event... I need to be able to start a segment when the player spawns into
the SUBAREA, and be able to annotate it as such."
"""
from sm64_events.memory.addresses import subarea_name
from sm64_events.storage.db import EventRow
from sm64_events.tracking.eventlabel import label_event, label_level_entry
from sm64_events.tracking.segments import TRIGGERS, MatchContext
from sm64_events.tracking.synthesize import clause_for

W = "2026-08-08T12:00:00Z"
LLL, VOLCANO = 22, 2
SSL, PYRAMID = 8, 2


def spawned_row(payload, id=10):
    return EventRow(id=id, session_id=1, seq=id, type="spawned", frame=1000,
                    wall_time_utc=W, payload=payload)


# -- the names ---------------------------------------------------------------

def test_named_subareas_answer_and_course_starts_do_not():
    assert subarea_name(LLL, VOLCANO) == "Volcano"
    assert subarea_name(SSL, PYRAMID) == "Pyramid"
    assert subarea_name(LLL, 1) is None          # a course starts in area 1
    assert subarea_name(LLL, None) is None
    assert subarea_name(6, 3) == "Basement"      # the castle's are all named


def test_an_unnamed_subarea_reads_area_n_not_a_guess():
    assert subarea_name(36, 3) == "Area 3"


# -- the label ---------------------------------------------------------------

def test_a_subarea_spawn_row_names_the_subarea():
    label = label_event(spawned_row(
        {"level": LLL, "kind": "spawn", "area": VOLCANO}))
    assert label == "Spawned into Lethal Lava Land: Volcano"


def test_a_course_start_spawn_row_reads_as_it_always_did():
    label = label_event(spawned_row({"level": LLL, "kind": "spawn", "area": 1}))
    assert label == "Spawned into Lethal Lava Land"


def test_a_historical_spawn_row_without_area_is_unchanged():
    label = label_event(spawned_row({"level": LLL, "kind": "spawn"}))
    assert label == "Spawned into Lethal Lava Land"


def test_a_promoted_subarea_restart_does_not_claim_the_course_started():
    """His distinction exactly: "This is different than spawning into the
    starting position of the entire course." """
    assert label_level_entry(LLL, {"level": LLL, "area": VOLCANO}) \
        == "Spawned into Lethal Lava Land: Volcano"
    assert label_level_entry(LLL, {"level": LLL, "area": 1}) \
        == "Started Lethal Lava Land"
    assert label_level_entry(LLL) == "Started Lethal Lava Land"


# -- the trigger -------------------------------------------------------------

def match(params, payload):
    trig = TRIGGERS["spawned"]
    ev = spawned_row(payload)
    return trig.match(params, ev, MatchContext(level=payload.get("level"),
                                               prev_level=None,
                                               num_stars=None, area=None))


def test_a_level_only_clause_matches_exactly_what_it_always_did():
    assert match({"level": LLL}, {"level": LLL, "kind": "spawn"})
    assert not match({"level": LLL}, {"level": SSL, "kind": "spawn"})


def test_a_pinned_subarea_matches_only_that_subarea():
    assert match({"level": LLL, "area": VOLCANO},
                 {"level": LLL, "kind": "spawn", "area": VOLCANO})
    assert not match({"level": LLL, "area": VOLCANO},
                     {"level": LLL, "kind": "spawn", "area": 1})


def test_a_pinned_spawn_node_tells_the_two_pyramid_entries_apart():
    top = {"level": SSL, "kind": "spawn", "area": PYRAMID, "spawn_node": 0x0A}
    bottom = {"level": SSL, "kind": "spawn", "area": PYRAMID,
              "spawn_node": 0x0B}
    clause = {"level": SSL, "area": PYRAMID, "spawn_node": 0x0A}
    assert match(clause, top)
    assert not match(clause, bottom)


def test_a_pinned_subarea_fails_a_historical_row():
    """An old row cannot prove it was the pyramid; conservative means a
    recorded subarea start never fires from the course's front door."""
    assert not match({"level": SSL, "area": PYRAMID},
                     {"level": SSL, "kind": "spawn"})


# -- the synthesis -----------------------------------------------------------

def test_a_picked_subarea_spawn_synthesizes_the_pinned_clause():
    clause = clause_for(spawned_row(
        {"level": SSL, "kind": "spawn", "area": PYRAMID,
         "spawn_node": 0x0A}), role="start")
    assert clause == {"type": "spawned", "level": SSL, "area": PYRAMID,
                      "spawn_node": 0x0A}


def test_a_course_start_spawn_synthesizes_without_the_extras():
    clause = clause_for(spawned_row(
        {"level": LLL, "kind": "spawn", "area": 1, "spawn_node": None}),
        role="start")
    assert clause == {"type": "spawned", "level": LLL, "area": 1}


def test_a_historical_spawn_synthesizes_the_level_alone():
    clause = clause_for(spawned_row({"level": LLL, "kind": "spawn"}),
                        role="start")
    assert clause == {"type": "spawned", "level": LLL}
