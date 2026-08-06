# tests/test_place_events_carry_the_time.py
"""Every row the recorder draws says WHEN, not just some of them.

His report, 2026-08-06, with a screenshot of the recorder's own list: *"It
looks like some events have the timer next to them, most don't? I would expect
the timer for all of them."* Two door opens carried `15"50` and `08"56`; the
level exits, the arrivals and the moves between areas above them carried
nothing at all.

The cause was one line in `server/api.py` -- the row surfaces
`row.payload.get("igt_frames")` and never computes one, deliberately, so a
blank cell means the DETECTOR never stamped a number rather than that the
number was unknown. `star_collected`, `key_grabbed`, `warp_entered` and
`moment_reached` stamped one; `level_changed`, `area_changed` and `spawned`
did not.

This file is one test per detector plus the join, because the three are
separate classes that each had to be changed and a per-detector failure names
which one regressed. The number itself is `IgtClock`'s to be right about
(`tests/test_igt_clock.py`); what is asserted here is that each detector ASKS
it, which is the part that was missing.
"""
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.area import AreaChangeDetector
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.detectors.level import LevelChangeDetector
from sm64_events.detectors.spawn import SpawnDetector
from sm64_events.main import build_detectors
from sm64_events.memory.addresses import (ACT_INTRO_CUTSCENE,
                                          ACT_SPAWN_SPIN_AIRBORNE)

W = "2026-08-06T10:48:01Z"

# The four types that already stamped a time, and the three this change adds.
TIMED_TYPES = frozenset({"star_collected", "key_grabbed", "warp_entered",
                         "moment_reached", "level_changed", "area_changed",
                         "spawned"})


def snap(**overrides) -> GameSnapshot:
    base = dict(global_timer=5000, curr_level=8, curr_area=1,
                mario_action=0x0440, mario_action_timer=0, num_stars=0,
                last_completed_course=0, last_completed_star=0,
                igt_overall=200, wall_time_utc=W)
    base.update(overrides)
    return GameSnapshot(**base)


def only(events):
    assert len(events) == 1, events
    return events[0]


def carries_a_time(event) -> None:
    payload = event.payload
    assert payload.get("igt_frames") is not None, (
        f"{event.type} draws a blank cell in the recorder: {payload}")
    assert payload["igt"], "the pre-formatted string rides along like every other"
    assert payload["igt_source"], "which path answered is part of the record"


def test_a_level_edge_says_when_it_happened():
    detector = LevelChangeDetector()
    detector.process(snap(curr_level=24), snap(curr_level=24))   # establish
    carries_a_time(only(detector.process(snap(curr_level=24), snap(igt_overall=613))))


def test_an_area_edge_says_when_it_happened():
    detector = AreaChangeDetector()
    detector.process(snap(), snap())                             # establish
    carries_a_time(only(detector.process(snap(), snap(curr_area=2,
                                                      igt_overall=201))))


def test_an_arrival_reads_the_start_of_the_run():
    """Usamune zeroes at the SPAWN, so this row is the zero everything else is
    measured from -- and it reads ONE DISPLAY TICK rather than a literal zero,
    which is `IgtClock.DISPLAY_TICK` and not this detector's opinion.

    NOT LIVE-GATED AT A SPAWN. That tick was calibrated at a pipe and at a star
    and is live-verified there; whether Usamune's screen reads 00"00 or 00"03
    on the frame control returns has never been read off the emulator. Pinned
    to the clock's own answer rather than to a number, so this file cannot be
    the thing that quietly asserts an unmeasured one.
    """
    arrival = snap(igt_overall=0)
    event = only(SpawnDetector().process(
        snap(mario_action=ACT_INTRO_CUTSCENE, igt_overall=0), arrival))
    carries_a_time(event)
    expected, _ = IgtClock().igt_at(arrival.global_timer, arrival)
    assert event.payload["igt_frames"] == expected == IgtClock.DISPLAY_TICK


def test_a_respawn_says_when_it_happened():
    carries_a_time(only(SpawnDetector().process(
        snap(), snap(mario_action=ACT_SPAWN_SPIN_AIRBORNE, igt_overall=940))))


def test_the_place_detectors_read_the_shared_clock_and_not_a_second_one():
    """The one-door rule for a displayed time (CLAUDE.md): every number on
    screen comes from `detectors/igt_clock.py`. A detector holding its own
    arithmetic would satisfy every assertion above and still be a second
    derivation that can drift."""
    wired = {type(d).__name__: d for d in build_detectors()}
    for name in ("LevelChangeDetector", "AreaChangeDetector", "SpawnDetector"):
        assert isinstance(wired[name]._clock, IgtClock), name


def test_every_type_the_recorder_can_draw_stamps_a_time():
    """The JOIN, and the reason this file is not three separate assertions: a
    new place-change detector added later is exactly the shape that would
    reintroduce a blank cell, and it lands in this set the moment it is wired."""
    from sm64_events.server.api import _TIMELINE_STEP_TYPES
    missing = _TIMELINE_STEP_TYPES - TIMED_TYPES
    assert not missing, (
        f"{sorted(missing)} can be drawn by the recorder and stamps no time, "
        "so its rows show a blank cell")
