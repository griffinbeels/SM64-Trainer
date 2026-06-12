# tests/test_snapshot.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot, SnapshotReader
from sm64_events.memory import addresses as A
from sm64_events.memory.buffer import BufferMemory


def test_reader_populates_all_fields():
    mem = BufferMemory()
    mem.write_u32(A.GLOBAL_TIMER, 81234)
    mem.write_u32(A.MARIO_ACTION, A.ACT_STAR_DANCE_EXIT)
    mem.write_u16(A.MARIO_ACTION_TIMER, 2)
    mem.write_u16(A.MARIO_NUM_STARS, 57)
    mem.write_u8(A.LAST_COMPLETED_COURSE, 1)
    mem.write_u8(A.LAST_COMPLETED_STAR, 3)
    mem.write_u16(A.USAMUNE_OVERALL, 600)
    mem.write_u16(A.USAMUNE_STAR_RESULT, 595)
    mem.write_u16(A.CURR_LEVEL, 24)

    snap = SnapshotReader(mem).read()

    assert snap.global_timer == 81234
    assert snap.mario_action == A.ACT_STAR_DANCE_EXIT
    assert snap.mario_action_timer == 2
    assert snap.num_stars == 57
    assert snap.last_completed_course == 1
    assert snap.last_completed_star == 3
    assert snap.igt_overall == 600
    assert snap.igt_result == 595
    assert snap.curr_level == 24
    assert snap.wall_time_utc.tzinfo == timezone.utc
    assert (datetime.now(timezone.utc) - snap.wall_time_utc).total_seconds() < 5
    # TODO Task 17 live gate: write A.CURR_AREA + assert snap.curr_area == <value>
    #      once CURR_AREA is replaced with the live-pinned address in addresses.py


def test_curr_area_defaults_to_zero_for_old_call_sites():
    s = GameSnapshot(wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
                     global_timer=1, mario_action=0, mario_action_timer=0,
                     num_stars=0, last_completed_course=0,
                     last_completed_star=0)
    assert s.curr_area == 0
