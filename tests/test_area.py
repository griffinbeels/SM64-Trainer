from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.area import AreaChangeDetector


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
        global_timer=1000, mario_action=0x0C400201, mario_action_timer=0,
        num_stars=5, last_completed_course=1, last_completed_star=3,
        curr_level=6, curr_area=1)
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def test_area_change_emits_event_with_level_from_to():
    d = AreaChangeDetector()
    d.process(snap(curr_area=1), snap(curr_area=1))   # establish (1 event)
    events = d.process(snap(curr_area=1),
                       snap(curr_area=2, global_timer=1500))
    assert len(events) == 1
    assert events[0].type == "area_changed"
    assert events[0].payload == {"level": 6, "from": 1, "to": 2,
                                 "from_transient": False}


def test_first_pair_emits_establishing_event_from_may_equal_to():
    events = AreaChangeDetector().process(snap(curr_area=1), snap(curr_area=1))
    assert len(events) == 1
    # no prior same-level emission -> the from side is unvouched-for
    assert events[0].payload == {"level": 6, "from": 1, "to": 1,
                                 "from_transient": True}


def test_no_event_while_area_stable_after_establishing():
    d = AreaChangeDetector()
    d.process(snap(), snap())
    assert d.process(snap(), snap()) == []


def test_level_change_re_establishes_area_for_new_level():
    d = AreaChangeDetector()
    d.process(snap(), snap())                          # castle area 1
    events = d.process(snap(), snap(curr_level=17, curr_area=1))
    assert len(events) == 1                            # same area NUMBER, new level
    assert events[0].payload["level"] == 17


def test_reattach_gap_within_same_level_is_caught():
    d = AreaChangeDetector()
    d.process(snap(curr_area=1), snap(curr_area=1))   # established at (6, 1)
    # Server stayed up; emulator reattached at area 2 (prev re-seeded from real read).
    # from must be last EMITTED area (1), not prev.curr_area (2).
    events = d.process(snap(curr_area=2, global_timer=9000),
                       snap(curr_area=2, global_timer=9000))
    assert len(events) == 1
    assert events[0].payload == {"level": 6, "from": 1, "to": 2,
                                 "from_transient": False}


def test_area_change_frame_matches_curr_global_timer():
    d = AreaChangeDetector()
    d.process(snap(curr_area=1), snap(curr_area=1))
    events = d.process(snap(curr_area=1), snap(curr_area=2, global_timer=4321))
    assert events[0].frame == 4321


# ---------------------------------------------------------------------------
# from_transient: "did Mario actually dwell in `from` within this level?"
# Every castle entry loads the lobby (area 1) transiently, then warps to the
# real area a poll later on the SAME game frame (detectors/level.py) — so a
# course exit into the basement emits from=1 exactly like a genuine lobby
# walk. The flag is the discriminator the area_enter "coming from" trigger
# needs (tracking/segments.py).
# ---------------------------------------------------------------------------

def test_castle_load_settle_is_from_transient():
    d = AreaChangeDetector()
    # settled in HMC (level 7)
    d.process(snap(curr_level=7, curr_area=1), snap(curr_level=7, curr_area=1))
    # level edge poll: castle loaded the transient lobby
    d.process(snap(curr_level=7, curr_area=1),
              snap(curr_level=6, curr_area=1, global_timer=5000))
    # next poll, SAME game frame: warp settles into the basement
    events = d.process(snap(curr_level=6, curr_area=1, global_timer=5000),
                       snap(curr_level=6, curr_area=3, global_timer=5000))
    assert events[0].payload == {"level": 6, "from": 1, "to": 3,
                                 "from_transient": True}


def test_skipped_transient_lobby_is_still_from_transient():
    # A fast load can hide the transient-lobby poll entirely: the first
    # castle read is already the settled basement. `from` is then the OTHER
    # level's area index — never a settled castle subarea.
    d = AreaChangeDetector()
    d.process(snap(curr_level=7, curr_area=1), snap(curr_level=7, curr_area=1))
    events = d.process(snap(curr_level=7, curr_area=1),
                       snap(curr_level=6, curr_area=3, global_timer=5000))
    assert events[0].payload == {"level": 6, "from": 1, "to": 3,
                                 "from_transient": True}


def test_walked_crossing_is_not_from_transient():
    # Genuine lobby walk into the basement: the lobby was established on an
    # EARLIER frame (walking to the loading zone takes frames).
    d = AreaChangeDetector()
    d.process(snap(curr_area=1), snap(curr_area=1))            # frame 1000
    events = d.process(snap(curr_area=1),
                       snap(curr_area=3, global_timer=2000))
    assert events[0].payload == {"level": 6, "from": 1, "to": 3,
                                 "from_transient": False}
