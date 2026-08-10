# tests/test_spawn.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.spawn import SpawnDetector
from sm64_events.memory.addresses import (ACT_INTRO_CUTSCENE,
                                          ACT_SPAWN_SPIN_AIRBORNE,
                                          LEVEL_CASTLE_GROUNDS)

ACT_IDLE = 0x0C400201


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
        global_timer=500, mario_action=ACT_IDLE, mario_action_timer=0,
        num_stars=0, last_completed_course=0, last_completed_star=0,
        curr_level=LEVEL_CASTLE_GROUNDS, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_leaving_intro_cutscene_emits_spawned_intro():
    events = SpawnDetector().process(
        snap(mario_action=ACT_INTRO_CUTSCENE), snap())
    assert len(events) == 1
    assert events[0].type == "spawned"
    # The keys this test OWNS, not the whole dict: `spawned` grew a time on
    # 2026-08-06 and a shape assertion would go red for a field it is not
    # about (auto-memory: pin-fields-not-payload-dicts).
    assert events[0].payload["level"] == LEVEL_CASTLE_GROUNDS
    assert events[0].payload["kind"] == "intro"


def test_edge_into_spawn_action_emits_spawned_spawn():
    events = SpawnDetector().process(
        snap(), snap(mario_action=ACT_SPAWN_SPIN_AIRBORNE))
    assert events[0].payload["kind"] == "spawn"
    assert events[0].payload["level"] == LEVEL_CASTLE_GROUNDS


def test_idle_to_idle_is_silent():
    assert SpawnDetector().process(snap(), snap()) == []


# -- round 20 item 3: a spawn names its subarea and its spawn point ----------
# "When I reset the level INSIDE OF A SUBAREA, we should actually have a
# special 'Spawned into Lethal Lava Land [Subarea Name]' event... ideally we
# would be able to identify *which* spawn we came through in each subarea."

SSL, PYRAMID = 8, 2


def test_a_spawn_carries_its_settled_area():
    events = SpawnDetector().process(
        snap(curr_level=SSL, curr_area=PYRAMID),
        snap(curr_level=SSL, curr_area=PYRAMID,
             mario_action=ACT_SPAWN_SPIN_AIRBORNE))
    assert events[0].payload["area"] == PYRAMID


def test_the_warp_struct_names_the_spawn_point_when_it_matches():
    """The game performs every spawn through sWarpDest, and the struct
    SURVIVES the completed warp (probe 2026-08-05) — so at the spawn edge
    its nodeId says which entry placed Mario. Pyramid top vs bottom are
    different nodes; this is the discriminator his SSL logs asked for."""
    events = SpawnDetector().process(
        snap(curr_level=SSL, curr_area=PYRAMID),
        snap(curr_level=SSL, curr_area=PYRAMID,
             mario_action=ACT_SPAWN_SPIN_AIRBORNE,
             warp_dest_type=2, warp_dest_level=SSL,
             warp_dest_area=PYRAMID, warp_dest_node=0x0A))
    assert events[0].payload["spawn_node"] == 0x0A


def test_a_stale_warp_struct_degrades_to_no_node_never_a_wrong_one():
    """A savestate load, or a struct still describing some EARLIER warp
    (level or area mismatch), must answer None — a foreign node id pinned
    into a recorded definition would silently never fire."""
    mismatched_area = SpawnDetector().process(
        snap(curr_level=SSL, curr_area=PYRAMID),
        snap(curr_level=SSL, curr_area=PYRAMID,
             mario_action=ACT_SPAWN_SPIN_AIRBORNE,
             warp_dest_type=2, warp_dest_level=SSL,
             warp_dest_area=1, warp_dest_node=0x0A))
    assert mismatched_area[0].payload["spawn_node"] is None
    never_warped = SpawnDetector().process(
        snap(curr_level=SSL, curr_area=PYRAMID),
        snap(curr_level=SSL, curr_area=PYRAMID,
             mario_action=ACT_SPAWN_SPIN_AIRBORNE,
             warp_dest_type=0, warp_dest_level=SSL,
             warp_dest_area=PYRAMID, warp_dest_node=0x0A))
    assert never_warped[0].payload["spawn_node"] is None
