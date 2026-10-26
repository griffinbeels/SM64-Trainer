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
    # Read off Usamune's own HUD code (2026-10-02); their live gates remain VERIFY.
    "usamune_menu": 0x804168D8, "usamune_input_display": 0x80417B57,
    "usamune_stop": 0x80417B4D,
    # Source-backed candidates only; their new live gates remain VERIFY.
    "current_area_pointer": 0x8032DDCC, "area_update_counter": 0x8032DF08,
    "sound_code": 0x8031EB00, "sound_producer": 0x80332F44,
    "sound_consumer": 0x80332F40, "sound_ring": 0x80361498,
    # Found by a menu-open RAM watch (2026-10-17); its live gate remains VERIFY.
    "usamune_savestate_button": 0x80417B66,
    # The scene renderer's state spans (M4 E2-live, 2026-10-24): linker-map
    # section bounds and the main pool's bounds; their live gates remain VERIFY.
    "scene_data_start": 0x8032D560, "scene_data_end": 0x803312F0,
    "scene_bss_start": 0x8033AF90, "scene_bss_end": 0x80361270,
    "scene_engine_bss_start": 0x8038BCA0, "scene_engine_bss_end": 0x8038EEE0,
    "main_pool_start": 0x8005C010, "main_pool_end": 0x801C0FF0,
    # The act selector's spans (2026-10-25): gSaveBuffer off the linker map and
    # Usamune's settings and counters; their live gates remain VERIFY.
    "save_buffer_start": 0x80207700, "save_buffer_end": 0x80207900,
    "usamune_state_start": 0x80417B4C, "usamune_state_end": 0x80417C80,
    # The stems capture's audio spans (2026-10-26): linker-map symbols checked
    # on three savestates; their live gates remain VERIFY.
    "audio_heap_start": 0x801CE000, "audio_heap_end": 0x801FF150,
    "audio_globals_start": 0x80220DA0, "audio_globals_end": 0x80226CC0,
    "sp_task_queue_start": 0x8033AE20, "sp_task_queue_end": 0x8033AE38,
    "sp_task_messages_start": 0x8033AF08, "sp_task_messages_end": 0x8033AF48,
    "sp_task_pointers_start": 0x8032D568, "sp_task_pointers_end": 0x8032D57C,
}


def test_the_us_layout_is_pinned():
    for field, expected in US_2026_08_15.items():
        assert US.value(field) == expected, field
    assert set(US_2026_08_15) == {row.field for row in LAYOUT_ROWS}
