"""THE non-regression pin: the US layout is exactly what the tracker read on
2026-08-15, field by field. Changing a US address is a deliberate edit of two
literals (here and layout.py), never a side effect of JP work.

Every shipped field must appear below. A count pin missed `player1_controller`
for a month (found 2026-09-17), so the set is derived from LAYOUT_ROWS rather
than restated as a number: a new row is unpinned until someone writes its
live-read value here."""
from sm64_events.memory.layout import LAYOUT_ROWS, US

US_2026_08_15 = {
    "global_timer": 0x8032D5D4, "mario_struct": 0x8033B170,
    "curr_level": 0x8032DDF8, "curr_area": 0x8033BACA,
    "last_completed_course": 0x8032DD80, "last_completed_star": 0x8032DD84,
    "pending_warp_op": 0x8033B252, "delayed_warp_timer": 0x8033B254,
    "warp_dest": 0x8033B248, "object_pool": 0x8033D488,
    "hud_display": 0x8033B260, "hud_timer_running": 0x8033B25E,
    "mario_object": 0x80361158, "usamune_overall": 0x80417C72,
    "usamune_star_result": 0x80417C74, "usamune_timer": 0x8033D5DC,
    "behaviour_base": 0x800EB180, "player1_controller": 0x8033AF90,
}


def test_the_us_layout_is_pinned():
    for field, expected in US_2026_08_15.items():
        assert US.value(field) == expected, field
    assert set(US_2026_08_15) == {row.field for row in LAYOUT_ROWS}
