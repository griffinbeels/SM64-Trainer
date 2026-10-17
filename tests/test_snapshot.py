# tests/test_snapshot.py
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot, SnapshotReader
from sm64_events.memory import addresses as A
from sm64_events.memory.layout import US
from sm64_events.memory.buffer import BufferMemory


def test_reader_populates_all_fields():
    mem = BufferMemory()
    mem.write_u32(US.global_timer, 81234)
    mem.write_u32(US.mario_struct + A.MARIO_ACTION_OFF, A.ACT_STAR_DANCE_EXIT)
    mem.write_u16(US.mario_struct + A.MARIO_ACTION_TIMER_OFF, 2)
    mem.write_u16(US.mario_struct + A.MARIO_NUM_STARS_OFF, 57)
    mem.write_u16(US.mario_struct + A.MARIO_NUM_COINS_OFF, 104)
    mem.write_u8(US.last_completed_course, 1)
    mem.write_u8(US.last_completed_star, 3)
    mem.write_u16(US.usamune_overall, 600)
    mem.write_u16(US.usamune_star_result, 595)
    mem.write_u16(US.curr_level, 24)
    mem.write_u16(US.curr_area, 3)

    snap = SnapshotReader(mem).read()

    assert snap.global_timer == 81234
    assert snap.mario_action == A.ACT_STAR_DANCE_EXIT
    assert snap.mario_action_timer == 2
    assert snap.num_stars == 57
    assert snap.coins == 104
    assert snap.last_completed_course == 1
    assert snap.last_completed_star == 3
    assert snap.igt_overall == 600
    assert snap.igt_result == 595
    assert snap.curr_level == 24
    assert snap.curr_area == 3
    assert snap.wall_time_utc.tzinfo == timezone.utc
    assert (datetime.now(timezone.utc) - snap.wall_time_utc).total_seconds() < 5


def test_curr_area_defaults_to_zero_for_old_call_sites():
    s = GameSnapshot(wall_time_utc=datetime(2026, 6, 11, tzinfo=timezone.utc),
                     global_timer=1, mario_action=0, mario_action_timer=0,
                     num_stars=0, last_completed_course=0,
                     last_completed_star=0)
    assert s.curr_area == 0


def test_reader_carries_the_pad_and_usamunes_menu_for_the_ghost_toggle():
    from dataclasses import replace
    mem = BufferMemory()
    mem.write_u16(US.player1_controller + A.CONTROLLER_BUTTON_DOWN_OFF, 0x0820)
    mem.write_u8(US.usamune_menu, 1)
    snap = SnapshotReader(mem).read()
    assert snap.buttons_held == 0x0820 and snap.usamune_menu_open is True
    mem.write_u8(US.usamune_menu, 0)
    assert SnapshotReader(mem).read().usamune_menu_open is False
    # A layout that has not verified either address reads them as unknown.
    bare = SnapshotReader(mem, replace(US, player1_controller=None, usamune_menu=None)).read()
    assert bare.buttons_held is None and bare.usamune_menu_open is None
