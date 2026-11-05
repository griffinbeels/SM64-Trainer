"""WHERE each version of the ROM keeps the globals the tracker reads.

`addresses.py` holds what is TRUE OF THE GAME whatever ROM is running: action
ids, level and course ids, names, the world graph, struct offsets. This module
holds what is true of ONE BUILD of it: the RAM address of each global. US is
Usamune v1.93u, every value live-verified (short evidence beside each row; the
long form moved into git history and `.claude/rules/memory-detectors.md` when
the literals left addresses.py on 2026-08-15). JP is Usamune v1.93j, filled
through `tools/sync_version.py`: a value lands here only after its gate is
verified and the sync report (`data/version_sync/jp.json`) says so —
`tests/test_layout_matches_report.py` keeps the two in step, and
`tests/test_layout_us.py` pins every US value so JP work cannot move one.

Every ROW carries its DERIVATION: the decomp symbol STROOP's linker map names
(so a candidate for a new version is read off `data/symbols_<v>.tsv`
with no gameplay), or the hunt that finds it (Usamune's own globals are in no
map). Fetched 2026-08-15 from STROOP MappingUS.map / MappingJP.map: the JP
shift is NOT one offset — −0xF40 for the 0x8032 block, −0x1370 for the 0x8033
block — so nothing here may ever be derived by subtraction from US.

Struct OFFSETS stay in addresses.py (`MARIO_ACTION_OFF` and friends); readers
add them to a layout field. `layout_for(version)` is the only door.
"""
from dataclasses import dataclass


class LayoutIncomplete(RuntimeError):
    """A reader asked for an address this version has not verified yet."""


@dataclass(frozen=True)
class LayoutRow:
    field: str
    symbol: str | None     # decomp symbol in STROOP's map, or None
    hunt: str | None       # how to find it when no map names it
    note: str              # what it is / the trap around it
    # A read the tracker runs WITHOUT: an absent address degrades one feature
    # and never holds the build (`Layout.missing`).
    optional: bool = False


LAYOUT_ROWS: tuple[LayoutRow, ...] = (
    LayoutRow("global_timer", "gGlobalTimer", None,
              "u32, +1 per game frame (30 Hz)"),
    LayoutRow("mario_struct", "gMarioStates", None,
              "gMarioStates[0]; fields at addresses.MARIO_*_OFF"),
    LayoutRow("curr_level", "gCurrLevelNum", None,
              "s16 LEVEL id (WF=24), NOT a course number"),
    LayoutRow("curr_area", "gCurrAreaIndex", None,
              "s16; castle lobby 1 / upstairs 2 / basement 3"),
    LayoutRow("last_completed_course", "gLastCompletedCourseNum", None,
              "s8, 1-based, 0 = castle/none"),
    LayoutRow("last_completed_star", "gLastCompletedStarNum", None,
              "s8, 1-based; 4 bytes after the course (IDO aligns .data)"),
    LayoutRow("pending_warp_op", "sDelayedWarpOp", None,
              "s16; 0 = no warp pending; 0x13 = void-out"),
    LayoutRow("delayed_warp_timer", "sDelayedWarpTimer", None,
              "s16 countdown beside the op; tells a cancel from a ride"),
    LayoutRow("warp_dest", "sWarpDest", None,
              "struct WarpDest {u8 type, levelNum, areaIdx, nodeId; s32 arg}"),
    LayoutRow("object_pool", "gObjectPool", None,
              "240 slots x 0x260 bytes (addresses.OBJECT_*)"),
    LayoutRow("player1_controller", None, "controller_shape",
              "gControllers[0]: struct Controller (addresses.CONTROLLER_*) — "
              "raw stick, processed stick, and every button in two u16 masks"),
    LayoutRow("hud_display", "gHudDisplay", None,
              "the vanilla HUD struct; +HUD_TIMER_OFF is the u16 race timer, "
              "which stays 0 under Usamune"),
    LayoutRow("hud_timer_running", "sTimerRunning", None,
              "s8; vanilla races only"),
    LayoutRow("mario_object", "gMarioObject", None,
              "pointer to Mario's own object; its behaviour is bhvMario"),
    LayoutRow("current_area_pointer", "gCurrentArea", None,
              "VERIFY: pointer to the active Area and its persistent camera graph; "
              "address.current_area_pointer checks independent area/root identity"),
    LayoutRow("area_update_counter", "gAreaUpdateCounter", None,
              "VERIFY: u16 render animation/blink phase; address.area_update_counter "
              "compares Mario gfx animTimer during live motion"),
    LayoutRow("usamune_overall", None, "usamune_time",
              "u16 running leg counter; counter_epoch banks subarea resets"),
    LayoutRow("usamune_star_result", None, "usamune_time",
              "u16 written at the grab with the EXACT displayed time"),
    LayoutRow("usamune_timer", None, "usamune_time",
              "u32 SECTION counter (diagnostics only; resets per area)"),
    LayoutRow("sound_code", "play_sound", None,
              "the 48-byte play_sound leaf; the native reader matches its "
              "instructions against sound_producer/sound_ring before reading sounds"),
    LayoutRow("sound_producer", "sSoundRequestCount", None,
              "u8 producer of the 256-entry sound request ring, published "
              "after each entry's bits (+0) and source pointer (+4)"),
    LayoutRow("sound_consumer", "sNumProcessedSoundRequests", None,
              "u8 count the audio thread has consumed from the ring"),
    LayoutRow("sound_ring", "sSoundRequests", None,
              "256 x {u32 sound bits, u32 source}; Mario's source is his "
              "object's gfx.cameraToObject (object + 0x54)"),
    LayoutRow("usamune_menu", None, "usamune_hud",
              "VERIFY: u8, nonzero while Usamune's practice menu is open: its HUD hook "
              "(RAM 0x80410f14) then draws the menu instead of its HUD and input display"),
    LayoutRow("usamune_input_display", None, "usamune_hud",
              "VERIFY: u8 Usamune input display setting, 0 = off (2 = stick, else values); "
              "its HUD path calls the display (RAM 0x80408848) only while nonzero"),
    # Missing hotkey adaptation must not disable tracking on a verified ROM.
    LayoutRow("usamune_savestate_button", None, "usamune_setting_watch",
              "VERIFY: u8 Usamune's save state button (RESET page), settings array "
              "+0x1A: 0 = Cx4, 1 = DUP (ghost/dpad_toggle.py SAVESTATE_ON_DPAD_UP)", optional=True),

    LayoutRow("usamune_stop", None, "usamune_hud",
              "u8 Usamune TIMER -> STOP, a bitmask in menu order: bit 0 copies "
              "the counter into the star result at the grab, bit 1 at the x-cam "
              "(0 None, 1 Grab, 2 Xcam, 3 GrabX); settings base + 1, read off its menu "
              "table and its grab/dance hooks. Trap: base + 2 is DISPLAY",
              optional=True),
    LayoutRow("behaviour_base", None, "mario_object_behaviour",
              "RAM address of segment 0x13; = mario_object->behaviour "
              "minus bhvMario's segmented offset for this version"),
    # The game state SM64's own scene renderer (geo_process_root) reads, as
    # spans the scene tap copies with each graphics task (M4 E2-live, lab
    # only: tools/scene_lab/tap.py arm). Bounds are [start, end) and come from
    # the linker map's section layout, not a symbol; optional, so a version
    # without them only loses the free-camera capture.
    LayoutRow("scene_data_start", None, "linker_map_sections",
              "VERIFY: first byte of the game .data the renderer reads (main.o) "
              "through moving_texture.o: area, camera, render-mode, skybox globals",
              optional=True),
    LayoutRow("scene_data_end", None, "linker_map_sections",
              "VERIFY: end (exclusive) of that .data span", optional=True),
    LayoutRow("scene_bss_start", None, "linker_map_sections",
              "VERIFY: game .bss from game_init.o (display-list head, gfx pool) "
              "through object_list_processor.o: MarioState, Lakitu, FOV, the object pool",
              optional=True),
    LayoutRow("scene_bss_end", None, "linker_map_sections",
              "VERIFY: end (exclusive) of that .bss span", optional=True),
    LayoutRow("scene_engine_bss_start", None, "linker_map_sections",
              "VERIFY: engine .bss from geo_layout.o through surface_load.o: the "
              "surface partitions and pool pointers shadows read", optional=True),
    LayoutRow("scene_engine_bss_end", None, "linker_map_sections",
              "VERIFY: end (exclusive) of that engine .bss span", optional=True),
    LayoutRow("main_pool_start", None, "main_pool_bounds",
              "VERIFY: the value sPoolStart holds: the main pool, where a level's "
              "segments, scene graph, surfaces and Mario's animation buffer live",
              optional=True),
    LayoutRow("main_pool_end", None, "main_pool_bounds",
              "VERIFY: the value sPoolEnd holds (exclusive)", optional=True),
    # Two more spans the scene tap copies with each task, for the act selector
    # (star select): it prints the course's coin score out of the save buffer,
    # and Usamune's hook prints its own counter there under two settings.
    LayoutRow("save_buffer_start", None, "linker_map_symbols",
              "VERIFY: gSaveBuffer: four save files in two copies, then two copies of the "
              "menu data; the act selector reads the course's coin score", optional=True),
    LayoutRow("save_buffer_end", None, "linker_map_symbols",
              "VERIFY: end (exclusive) of gSaveBuffer: the next symbol, gGfxSPTaskStack",
              optional=True),
    LayoutRow("usamune_state_start", None, "usamune_code_refs",
              "VERIFY: Usamune's settings array (usamune_stop is base + 1), where its "
              "boot-copied code ends; its act-select hook reads settings +7 and +8",
              optional=True),
    LayoutRow("usamune_state_end", None, "usamune_code_refs",
              "VERIFY: end (exclusive) past the counters after the settings: the u16 at +0x122 "
              "the act-select hook prints, usamune_overall, usamune_star_result", optional=True),
    # Ghost Handoff's Slot Library (slotlibrary/usamune.py). Usamune's nine save-state slots and the
    # routines that identify the build whose slot format they hold.
    LayoutRow("usamune_slot_save", None, "usamune_code_refs",
              "VERIFY: Usamune's save routine, read from its code; only its first 0x400 bytes "
              "are read, as half of the build's fingerprint", optional=True),
    LayoutRow("usamune_slot_load", None, "usamune_code_refs",
              "VERIFY: Usamune's load routine, the fingerprint's other half", optional=True),
    LayoutRow("usamune_slots_start", None, "usamune_code_refs",
              "VERIFY: slot 0 of nine, 0x26460 bytes apart; he loaded a slot written here "
              "(2026-11-05). The only address range the Trainer writes", optional=True),
    # What a slot copies that the scene spans stop short of, and the seed a frame's random calls start from: with
    # these the scene tap's copy of frame K-1 is all a slot of frame K needs (tools/scene_lab/moment_slot.py).
    LayoutRow("slot_tail_start", None, "linker_map_sections",
              "VERIFY: the first byte of Usamune's slot body past scene_bss_end", optional=True),
    LayoutRow("slot_tail_end", None, "usamune_code_refs",
              "VERIFY: end (exclusive) of the slot body: mario_struct + 0x26320", optional=True),
    LayoutRow("power_meter_hud_start", None, "usamune_code_refs",
              "VERIFY: the four words Usamune's save copies to trailer +0x10", optional=True),
    LayoutRow("power_meter_hud_end", None, "usamune_code_refs",
              "VERIFY: end (exclusive) of those four words", optional=True),
    LayoutRow("random_seed_start", None, "linker_map_symbols",
              "VERIFY: the word holding gRandomSeed16; a load does not restore it", optional=True),
    LayoutRow("random_seed_end", None, "linker_map_symbols",
              "VERIFY: end (exclusive) of that word", optional=True),
    LayoutRow("segment_table", None, "linker_map_symbols",
              "VERIFY: where the level's segments loaded; a slot's pointers mean something only "
              "under the table it was saved with", optional=True),
    LayoutRow("second_camera_focus", "gSecondCameraFocus", None,
              "VERIFY: an object pointer outside the slot; a relocated slot moves its twin here "
              "(slotlibrary/relocate.py)", optional=True),
    # Audio spans the scene tap copies at every VI (M4 stems, lab only:
    # tools/scene_lab/tap.py arm): at UpdateScreen the audio task the game just
    # dispatched is still unrun, so these hold its whole input, and at the next
    # VI its output. Bounds off the linker map, [start, end).
    LayoutRow("audio_heap_start", None, "linker_map_symbols",
              "VERIFY: gAudioHeap: command lists, notes and their RSP state, sample DMA buffers, "
              "loaded banks, the reverb ring and the AI buffers", optional=True),
    LayoutRow("audio_heap_end", None, "linker_map_symbols",
              "VERIFY: end (exclusive): gAudioHeap plus the value gAudioHeapSize holds", optional=True),
    LayoutRow("audio_globals_start", None, "linker_map_symbols",
              "VERIFY: gAudioGlobalsStartMarker: the audio .bss (reverb, pools, the gNotes pointer, "
              "sequence players, channels and layers, the sample DMA table, the audio tasks)", optional=True),
    LayoutRow("audio_globals_end", None, "linker_map_symbols",
              "VERIFY: end (exclusive): gAudioGlobalsEndMarker", optional=True),
    LayoutRow("sp_task_queue_start", None, "linker_map_symbols",
              "VERIFY: gSPTaskMesgQueue, the OSMesgQueue each RSP task is posted to", optional=True),
    LayoutRow("sp_task_queue_end", None, "linker_map_symbols",
              "VERIFY: end (exclusive): the 0x18-byte queue", optional=True),
    LayoutRow("sp_task_messages_start", None, "linker_map_symbols",
              "VERIFY: that queue's 16-message buffer (STROOP names it gUnknownMesgBuf)", optional=True),
    LayoutRow("sp_task_messages_end", None, "linker_map_symbols",
              "VERIFY: end (exclusive): the next symbol, gDmaIoMesg", optional=True),
    LayoutRow("sp_task_pointers_start", None, "linker_map_symbols",
              "VERIFY: gActiveSPTask, then sCurrentAudioSPTask, sCurrentDisplaySPTask, "
              "sNextAudioSPTask and sNextDisplaySPTask", optional=True),
    LayoutRow("sp_task_pointers_end", None, "linker_map_symbols",
              "VERIFY: end (exclusive): the next symbol, sAudioEnabled", optional=True),
)


@dataclass(frozen=True)
class Layout:
    version: str
    global_timer: int | None = None
    mario_struct: int | None = None
    curr_level: int | None = None
    curr_area: int | None = None
    last_completed_course: int | None = None
    last_completed_star: int | None = None
    pending_warp_op: int | None = None
    delayed_warp_timer: int | None = None
    warp_dest: int | None = None
    object_pool: int | None = None
    player1_controller: int | None = None
    hud_display: int | None = None
    hud_timer_running: int | None = None
    mario_object: int | None = None
    current_area_pointer: int | None = None
    area_update_counter: int | None = None
    sound_code: int | None = None
    sound_producer: int | None = None
    sound_consumer: int | None = None
    sound_ring: int | None = None
    usamune_overall: int | None = None
    usamune_star_result: int | None = None
    usamune_timer: int | None = None
    usamune_menu: int | None = None
    usamune_input_display: int | None = None
    usamune_savestate_button: int | None = None

    usamune_stop: int | None = None
    behaviour_base: int | None = None
    scene_data_start: int | None = None
    scene_data_end: int | None = None
    scene_bss_start: int | None = None
    scene_bss_end: int | None = None
    scene_engine_bss_start: int | None = None
    scene_engine_bss_end: int | None = None
    main_pool_start: int | None = None
    main_pool_end: int | None = None
    save_buffer_start: int | None = None
    save_buffer_end: int | None = None
    usamune_state_start: int | None = None
    usamune_state_end: int | None = None
    usamune_slot_save: int | None = None
    usamune_slot_load: int | None = None
    usamune_slots_start: int | None = None
    slot_tail_start: int | None = None
    slot_tail_end: int | None = None
    power_meter_hud_start: int | None = None
    power_meter_hud_end: int | None = None
    random_seed_start: int | None = None
    random_seed_end: int | None = None
    segment_table: int | None = None
    second_camera_focus: int | None = None
    audio_heap_start: int | None = None
    audio_heap_end: int | None = None
    audio_globals_start: int | None = None
    audio_globals_end: int | None = None
    sp_task_queue_start: int | None = None
    sp_task_queue_end: int | None = None
    sp_task_messages_start: int | None = None
    sp_task_messages_end: int | None = None
    sp_task_pointers_start: int | None = None
    sp_task_pointers_end: int | None = None

    def value(self, field: str) -> int | None:
        return getattr(self, field)

    def missing(self) -> tuple[str, ...]:
        return tuple(row.field for row in LAYOUT_ROWS
                     if not row.optional and getattr(self, row.field) is None)

    def require(self, *names: str) -> None:
        absent = [name for name in names if getattr(self, name) is None]
        if absent:
            raise LayoutIncomplete(
                f"{self.version} layout has no verified address for: "
                + ", ".join(absent))


# US / Usamune v1.93u. Live-verified 2026-06-10 (tools/verify_addresses.py)
# unless the row says otherwise.
US = Layout(
    version="us",
    global_timer=0x8032D5D4,
    mario_struct=0x8033B170,
    curr_level=0x8032DDF8,          # trap: NOT last-completed; the harness caught it once
    curr_area=0x8033BACA,           # live-verified 2026-06-12, tools/hunt_exact.py
    last_completed_course=0x8032DD80,
    last_completed_star=0x8032DD84,
    pending_warp_op=0x8033B252,     # corroborated 2026-08-11, probe_warp_block
    delayed_warp_timer=0x8033B254,  # live-verified 2026-08-11, same probe
    warp_dest=0x8033B248,           # live-verified 2026-08-05, 15 castle entries
    object_pool=0x8033D488,         # STROOP US ObjectStartAddress
    player1_controller=0x8033AF90,  # HUNTED, not read off a map (2026-08-20,
                                    # tools/probe_inputs.py --at scan): no
                                    # symbol row names it, and the decomp's
                                    # gPlayer1Controller is a POINTER variable
                                    # rather than this struct. Its statusData
                                    # points at gControllerStatuses, whose [0]
                                    # reads CONT_TYPE_NORMAL for a pad in port
                                    # 1 and whose [1..3] read the no-controller
                                    # errno; its controllerData points at
                                    # gControllerPads with matching errnos; its
                                    # port is 0. VERIFY until the live gate.
    hud_display=0x8033B260,         # +HUD_TIMER_OFF is the race timer; 0 under Usamune (trap)
    hud_timer_running=0x8033B25E,
    mario_object=0x80361158,        # STROOP MappingUS.map gMarioObject
    current_area_pointer=0x8032DDCC, # VERIFY: STROOP MappingUS.map gCurrentArea;
                                    # new ghost read, not yet live gate verified.
    area_update_counter=0x8032DF08, # VERIFY: STROOP MappingUS.map gAreaUpdateCounter;
                                    # u16, distinct from gGlobalTimer.
    # STROOP MappingUS.map symbols; the retired audio feasibility probe
    # (docs/history/ghost-audio-feasibility-20260922.md)
    # found identical play_sound code in stock and Usamune (SHA256 1c8030b6...)
    # naming exactly this producer and ring, 2026-09-22. Live gates
    # address.sound_* all verified on Usamune US while he jumped, 2026-09-23
    # (data/version_sync/us.json: 4 Mario requests seen, consumer caught up).
    sound_code=0x8031EB00,
    sound_producer=0x80332F44,
    sound_consumer=0x80332F40,
    sound_ring=0x80361498,
    usamune_overall=0x80417C72,     # tools/hunt_value.py + watch session 2026-06-10
    usamune_star_result=0x80417C74,
    usamune_timer=0x8033D5DC,       # section counter; diagnostics only
    # VERIFY: read off Usamune's own code, 2026-10-02 (disassembly of its HUD
    # hook at RAM 0x80410f14: `lbu 0x68d8(0x8041)` picks the menu path; the HUD
    # path's `lbu 0xb(0x80417b4c)` gates `jal 0x80408848`, the input display).
    usamune_menu=0x804168D8,
    usamune_input_display=0x80417B57,
    # VERIFY until his live check: Usamune's settings array 0x80417B4C + 0x1A.
    # A read-only watch of 0x80416000-0x80418000 at 20 Hz while Usamune's menu
    # was open, 2026-10-17: he flipped DUP -> Cx4 -> DUP -> Cx4 -> DUP and this
    # byte, and no other in that range, went 1 -> 0 -> 1 -> 0 -> 1 in step.
    usamune_savestate_button=0x80417B66,

    usamune_stop=0x80417B4D,        # ROM-derived; live-confirmed 2026-10-17: Grab then
                                    # GrabX (journal 20368/20377; final raw byte 3)
    behaviour_base=0x800EB180,      # anchored on his bob-omb pointer, 8/8 confirmed 2026-08-07
    # VERIFY until the live E2 proof: STROOP MappingUS.map section layout
    # (.data main.o 0x...D560 .. moving_texture.o end; .bss game_init.o ..
    # object_list_processor.o end; engine .bss geo_layout.o .. surface_load.o
    # end). On savestates u_a (WF) and u_b (castle grounds), 2026-10-24, these
    # spans plus the main pool and the ROM held every byte geo_process_root
    # read under the source camera and five orbits: regenerated display lists
    # matched byte for byte with everything else poisoned. Pool bounds: what
    # sPoolStart/sPoolEnd held in all three savestates, Usamune and vanilla.
    scene_data_start=0x8032D560,
    scene_data_end=0x803312F0,
    scene_bss_start=0x8033AF90,
    scene_bss_end=0x80361270,
    scene_engine_bss_start=0x8038BCA0,
    scene_engine_bss_end=0x8038EEE0,
    main_pool_start=0x8005C010,
    main_pool_end=0x801C0FF0,
    # VERIFY until the next live E2 proof. On session tap-20261025-184022 the 9
    # act-select tasks regenerate identically once these bytes come from the
    # session's own full RDRAM copy (2026-10-25); the hook read gSaveBuffer +0x25
    # (file A's BoB coin score) and Usamune settings +7, +8 and the u16 at +0x122.
    # gSaveBuffer: STROOP MappingUS.map, up to the next symbol (gGfxSPTaskStack);
    # 0x200 = sizeof(struct SaveBuffer), and every file and menu copy in that
    # copy carried its save block signature and checksum. Usamune: base =
    # usamune_stop - 1, which its code addresses 251 times and where its
    # boot-copied ROM image ends (97,100 bytes from 0x80400000); the end is the
    # first 16-byte address boundary past usamune_star_result (ends 0x80417C76),
    # below 0x80417C86, the next global its code addresses on its own.
    save_buffer_start=0x80207700,
    save_buffer_end=0x80207900,
    usamune_state_start=0x80417B4C,
    usamune_state_end=0x80417C80,
    usamune_slot_save=0x8040392C,
    usamune_slot_load=0x80411CF0,
    usamune_slots_start=0x80500000,
    # VERIFY until a slot from a new recording loads live. Read off the save routine's own loads (0x804039C8,
    # 0x804039D8) and MappingUS.map; moment_slot's comparison with the closed replay: 73 of 73 slots equal.
    slot_tail_start=0x80361270,
    slot_tail_end=0x80361490,
    power_meter_hud_start=0x803325F0,
    power_meter_hud_end=0x80332600,
    random_seed_start=0x8038EEE0,
    random_seed_end=0x8038EEE4,
    segment_table=0x8033B400,
    second_camera_focus=0x8032DF30,  # STROOP MappingUS.map gSecondCameraFocus; VERIFY
    # VERIFY until the stems live session (G0/G1). STROOP MappingUS.map
    # symbols, checked on three savestates (two Usamune, one vanilla),
    # 2026-10-25: each holds exactly one message in gSPTaskMesgQueue, its
    # buffer pointer reads 0x8033AF08 with 16 slots, the message is
    # &gAudioTasks[gAudioTaskIndex] and that task's command list, notes,
    # reverb ring and AI buffers all lie in the heap; gAudioHeapSize reads
    # 0x31150. Usamune's audio code is byte-identical to vanilla US.
    audio_heap_start=0x801CE000,
    audio_heap_end=0x801FF150,
    audio_globals_start=0x80220DA0,
    audio_globals_end=0x80226CC0,
    sp_task_queue_start=0x8033AE20,
    sp_task_queue_end=0x8033AE38,
    sp_task_messages_start=0x8033AF08,
    sp_task_messages_end=0x8033AF48,
    sp_task_pointers_start=0x8032D568,
    sp_task_pointers_end=0x8032D57C,
)

# JP / Usamune v1.93j. Every value passed its address gate on his JP game
# 2026-10-16/17 (data/version_sync/jp.json). Symbol rows came from STROOP's
# MappingJP.map (data/symbols_jp.tsv); Usamune's own globals and the hunted
# controller came from the ROM images (tools/derive_rom_candidates.py, which
# also agreed with STROOP on all 18 symbol rows it reached).
JP = Layout(
    version="jp",
    global_timer=0x8032C694,        # ticked 30/s
    mario_struct=0x80339E00,        # action edge on his jump; 120 stars
    curr_level=0x8032CE98,          # read 24 entering Whomp's Fortress
    curr_area=0x8033A75A,           # read 1 in the lobby, then 3 in the basement
    last_completed_course=0x8032CE20,  # read 2 after Chip off Whomp's Block
    last_completed_star=0x8032CE24,    # read 1 after the same grab
    pending_warp_op=0x80339EE2,     # nonzero 1 frame after his BitDW pipe touch
    delayed_warp_timer=0x80339EE4,  # counted down from 19 to 0 on the same ride
    warp_dest=0x80339ED8,           # changed to level 17 (BitDW), area 1, node 10
    object_pool=0x8033C118,         # holds Mario's object; slot confirmed as bhvMario
    player1_controller=0x80339C20,  # ROM-derived; stick off centre with A and B held
    hud_display=0x80339EF0,         # race timer 0 under Usamune, as on US
    hud_timer_running=0x80339EEE,
    mario_object=0x8035FDE8,        # points at a slot boundary in the pool
    current_area_pointer=0x8032CE6C,  # agrees with MarioState.area and the area graph
    area_update_counter=0x8032CFA8, # equals Mario's gfx animation timer while moving
    sound_code=0x8031DC78,          # play_sound names exactly this producer and ring
    sound_producer=0x80331E34,      # advanced as he jumped
    sound_consumer=0x80331E30,      # caught up with the producer
    sound_ring=0x80360128,          # each new entry's source was Mario's object
    usamune_overall=0x80417D3A,     # ROM-derived; ticked 30.1/s
    usamune_star_result=0x80417D3C, # ROM-derived; 1193 at the grab, 1196 at the
                                    # x-cam, zeroed at the course exit, as on US
    usamune_timer=0x8033C26C,       # object slot 0's oTimer (pool + 0x154), as on US;
                                    # diagnostics only, its gate never verifies
    usamune_menu=0x804169A0,        # ROM-derived; 0, nonzero while open, 0 again
    usamune_input_display=0x80417C1F,  # ROM-derived; 0 while off, nonzero while on
    usamune_stop=0x80417C15,        # ROM-derived; read 3 2 1 0 1 2 3 as he stepped STOP
                                    # GrabX -> Grab -> None and back, 2026-10-17
    behaviour_base=0x800E8320,      # from Mario's object, 2026-10-16
)

VERSIONS = ("us", "jp")
_LAYOUTS = {"us": US, "jp": JP}


def layout_for(version: str) -> Layout:
    """The only door. Raises KeyError for a version this tracker does not know."""
    return _LAYOUTS[version]


def version_from_argv(argv: list[str] | None = None, default: str = "us") -> str:
    """`--version jp` (or `--version=jp`) out of a tool's argv, else
    `SM64_VERSION` from the environment, else `default`. Every memory-reading
    tool under tools/ takes its layout through this so they all spell the
    flag the same way; a tool that also uses argparse declares the same
    option there for --help and ignores its value."""
    import os
    import sys
    args = sys.argv[1:] if argv is None else argv
    chosen = os.environ.get("SM64_VERSION", default)
    for index, arg in enumerate(args):
        if arg == "--version" and index + 1 < len(args):
            chosen = args[index + 1]
        elif arg.startswith("--version="):
            chosen = arg.split("=", 1)[1]
    if chosen not in VERSIONS:
        raise SystemExit(f"--version must be one of {', '.join(VERSIONS)}, not {chosen!r}")
    return chosen
