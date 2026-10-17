"""WHERE each version of the ROM keeps the globals the tracker reads.

`addresses.py` holds what is TRUE OF THE GAME whatever ROM is running: action
ids, level and course ids, names, the world graph, struct offsets. This module
holds what is true of ONE BUILD of it: the RAM address of each global. US is
Usamune v1.93u, every value live-verified (short evidence beside each row; the
long form moved into git history and `.claude/rules/memory-detectors.md` when
the literals left addresses.py on 2026-08-15). JP starts empty and fills
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
    LayoutRow("usamune_savestate_button", None, "usamune_setting_watch",
              "VERIFY: u8 Usamune's save state button (RESET page), settings array "
              "+0x1A: 0 = Cx4, 1 = DUP (ghost/dpad_toggle.py SAVESTATE_ON_DPAD_UP)"),
    LayoutRow("behaviour_base", None, "mario_object_behaviour",
              "RAM address of segment 0x13; = mario_object->behaviour "
              "minus bhvMario's segmented offset for this version"),
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
    behaviour_base: int | None = None

    def value(self, field: str) -> int | None:
        return getattr(self, field)

    def missing(self) -> tuple[str, ...]:
        return tuple(row.field for row in LAYOUT_ROWS
                     if getattr(self, row.field) is None)

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
    behaviour_base=0x800EB180,      # anchored on his bob-omb pointer, 8/8 confirmed 2026-08-07
)

# JP — nothing verified yet. Candidates come from data/symbols_jp.tsv
# through the sync runner; a value is written here only after its gate passes.
JP = Layout(version="jp")

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
