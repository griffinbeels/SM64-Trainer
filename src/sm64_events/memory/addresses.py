# src/sm64_events/memory/addresses.py
"""Single authoritative registry of SM64 memory locations and ID->name tables.

ROM: SM64 US / Usamune v1.93u (Usamune is built on the US ROM).
All addresses are N64 KSEG0 virtual addresses (0x80000000-based).

Every entry below is live-verified against Usamune v1.93u in PJ64 1.6
(2026-06-10) via tools/verify_addresses.py. Mark new entries VERIFY until
they pass that harness. Cross-check sources on mismatch:
  - https://ukikipedia.net/wiki/RAM (US column)
  - SM64 decomp US symbol map (sm64.us.map build artifact)
  - STROOP mapping tables (github.com/SM64-TAS-ABC/STROOP)
"""

KSEG0_BASE = 0x80000000
RDRAM_MIN_SIZE = 0x400000   # 4 MB; vanilla SM64 runs without the expansion pak
RDRAM_FULL_SIZE = 0x800000  # 8 MB; Usamune uses expansion-pak RAM for its globals

# libultra osBootConfig — identical for every N64 game; used to find RDRAM.
OS_TV_TYPE = 0x80000300   # u32: 0 PAL, 1 NTSC, 2 MPAL
OS_ROM_BASE = 0x80000308  # u32: 0xB0000000 for cartridge boot
OS_MEM_SIZE = 0x80000318  # u32: 0x400000 or 0x800000

# Mario state (gMarioStates[0]) — source: decomp struct MarioState + STROOP US.
MARIO_STRUCT = 0x8033B170
MARIO_PARTICLE_FLAGS = MARIO_STRUCT + 0x08  # u32 particleFlags, re-zeroed every
                                            # frame; VERIFY (live gate pending)
MARIO_ACTION = MARIO_STRUCT + 0x0C        # u32; live-verified 2026-06-10
MARIO_ACTION_TIMER = MARIO_STRUCT + 0x1A  # u16, resets to 0 on action change
MARIO_NUM_STARS = MARIO_STRUCT + 0xAA     # s16, total star count; live-verified 2026-06-10

# Bit in particleFlags (the visible dust puffs) — corroborates the dust-
# trick detector's action-edge signal. Decomp (fetched 2026-06-11): slide
# actions set it whenever ground contact holds (common_slide_action,
# GROUND_STEP_NONE); jump landings set it only when forwardVel > 16
# (common_landing_action) — so a slow late jump shows NO dust even though
# the input was late; "dustless" is defined by input timing, not the puff.
# Source: decomp include/sm64.h PARTICLE_DUST. VERIFY (live gate pending).
PARTICLE_DUST = 1 << 0

GLOBAL_TIMER = 0x8032D5D4            # u32, +1 per game frame (30 Hz); live-verified 2026-06-10
# gLastCompleted* are adjacent s8 globals but sit 4 bytes apart (IDO aligns
# each initialized .data global to 4 bytes). Source: STROOP MiscData.xml
# (offsetUS) + decomp symbol maps; both agree.
LAST_COMPLETED_COURSE = 0x8032DD80   # s8, 1-based, 0 = castle/none; live-verified 2026-06-10
LAST_COMPLETED_STAR = 0x8032DD84     # s8, 1-based; live-verified 2026-06-10
# Trap, do not reuse: 0x8032DDF8 is gCurrLevelNum (s16, LEVEL ids like
# WF=24, SSL=8) — NOT a course number. We misread it as last-completed
# once; the harness caught it (course stuck at 0, star tracking level ids).
CURR_LEVEL = 0x8032DDF8              # s16 gCurrLevelNum

# Usamune practice-timer globals — STATIC addresses in expansion RAM
# (slot-independent, unlike the object-pool counters below). Located
# empirically via tools/hunt_value.py + a watch session on 2026-06-10.
USAMUNE_OVERALL = 0x80417C72      # u16, running OVERALL star time: keeps
                                  # counting across area warps (SSL pyramid
                                  # etc.); resets with Usamune level resets.
USAMUNE_STAR_RESULT = 0x80417C74  # u16, written at the star grab with the
                                  # EXACT final time Usamune displays;
                                  # persists after the grab. 0 until then.
# Observed neighbors: 0x80417C70 constant 256; 0x80417C76 written at grab.

# Usamune SECTION (per-area) counter — object-pool behavior field (slot 0
# +0x154 when observed; mirrors elsewhere). Slot-dependent AND resets on
# area warps inside a level, so it must NOT be the event IGT source (it
# under-reported multi-area stars like "Inside the Ancient Pyramid").
# Kept for diagnostics only.
USAMUNE_TIMER = 0x8033D5DC           # u32, 30 fps frames; section/area time

# Trap, do not reuse for IGT: the vanilla HUD race timer (gHudDisplay.timer,
# 0x8033B26C u16) and sTimerRunning (0x8033B25E s8) stay 0 under Usamune's
# practice timers — verified live. Vanilla races (KtQ etc.) still use them.
HUD_TIMER = 0x8033B26C               # u16, frames (vanilla races only)
HUD_TIMER_RUNNING = 0x8033B25E       # s8 sTimerRunning (vanilla races only)

# SM64 object pool (used by diagnostic tools and timer location).
# 240 slots of 0x260 bytes; Usamune's practice timers live in object
# rawData fields, so their addresses depend on slot assignment per level.
OBJECT_POOL = 0x8033D488     # first slot (STROOP US ObjectStartAddress)
OBJECT_SIZE = 0x260
OBJECT_COUNT = 240
OBJECT_BEHAVIOR = 0x20C      # u32 behavior-script pointer within a slot

# Mario actions entered the moment a star (or key) is grabbed — decomp sm64.h.
ACT_STAR_DANCE_EXIT = 0x00001302               # live-verified 2026-06-10
ACT_STAR_DANCE_WATER = 0x00001303
ACT_STAR_DANCE_NO_EXIT = 0x00001307
ACT_FALL_AFTER_STAR_GRAB = 0x00001904  # midair grabs; live-verified 2026-06-10

STAR_GRAB_ACTIONS = frozenset({
    ACT_STAR_DANCE_EXIT,
    ACT_STAR_DANCE_WATER,
    ACT_STAR_DANCE_NO_EXIT,
    ACT_FALL_AFTER_STAR_GRAB,
})

# Dust-trick action chains (decomp include/sm64.h, all values quoted
# verbatim from n64decomp/sm64 master, fetched 2026-06-11).
#
# Landing-transition model (decomp-verified 2026-06-11, confirmed live by a
# 50-trial session): when an air action lands, common_air_action_step /
# act_dive run `set_mario_action(...); break;` — the landing action is in
# memory at the END of the landing frame but its function (with its A/B
# cancel check) first RUNS the next frame. Cancels out of a landing action
# (act_dive_slide -> rollout, act_jump_land -> double jump) DO re-execute
# same-frame (`return set_mario_action(...)`). Consequence: every chained
# trick shows >= 1 visible landing/slide frame; exactly 1 visible frame IS
# the frame-perfect (dustless) input, and a direct air->launch edge (0
# visible frames) is impossible. See detectors/dust.py.
# VERIFY (live gate pending for the jump-chain ids).
ACT_DIVE = 0x0188088A
ACT_DIVE_SLIDE = 0x00880456
ACT_FORWARD_ROLLOUT = 0x010008A6
ACT_BACKWARD_ROLLOUT = 0x010008AD
ACT_JUMP = 0x03000880
ACT_DOUBLE_JUMP = 0x03000881
ACT_TRIPLE_JUMP = 0x01000882
ACT_JUMP_LAND = 0x04000470
ACT_DOUBLE_JUMP_LAND = 0x04000472

ROLLOUT_ACTIONS = frozenset({ACT_FORWARD_ROLLOUT, ACT_BACKWARD_ROLLOUT})

# Mario death actions -> cause label (decomp include/sm64.h, fetched 2026-06-10).
# VERIFY (live gate pending). Cause strings are the API vocabulary for
# attempt outcome_detail; keep them stable.
DEATH_ACTIONS = {
    0x00021311: "standing",    # ACT_STANDING_DEATH
    0x00021312: "quicksand",   # ACT_QUICKSAND_DEATH
    0x00021313: "electrocution",  # ACT_ELECTROCUTION
    0x00021314: "suffocation", # ACT_SUFFOCATION
    0x00021315: "on_stomach",  # ACT_DEATH_ON_STOMACH
    0x00021316: "on_back",     # ACT_DEATH_ON_BACK
    0x00021317: "eaten_by_bubba",  # ACT_EATEN_BY_BUBBA
    0x300032C4: "drowning",    # ACT_DROWNING
    0x300032C7: "water",       # ACT_WATER_DEATH
}

# Actions Mario passes through or rests in WITHOUT user input (spawn-in,
# idle, sleep). Used by AnchorDetector's activity flag: any OTHER action
# observed since the last anchor means the player actually did something.
# Camera-only input never changes mario_action -> counts as inactive (the
# user's requested rule for ignoring no-op resets). VERIFY (live gate pending).
PASSIVE_ACTIONS = frozenset({
    0x00000000,  # ACT_UNINITIALIZED
    0x0C400201,  # ACT_IDLE
    0x0C400202,  # ACT_START_SLEEPING
    0x0C000203,  # ACT_SLEEPING
    0x0C000204,  # ACT_WAKING_UP
    0x0C400205,  # ACT_PANTING
    0x00001924,  # ACT_SPAWN_SPIN_AIRBORNE
    0x00001325,  # ACT_SPAWN_SPIN_LANDING
    0x00001932,  # ACT_SPAWN_NO_SPIN_AIRBORNE
    0x00001333,  # ACT_SPAWN_NO_SPIN_LANDING
})

# ---------------------------------------------------------------------------
# Name tables (display-only; IDs are the authoritative identity).
# ---------------------------------------------------------------------------

COURSE_NAMES = {
    0: "Castle Secret",
    1: "Bob-omb Battlefield",
    2: "Whomp's Fortress",
    3: "Jolly Roger Bay",
    4: "Cool, Cool Mountain",
    5: "Big Boo's Haunt",
    6: "Hazy Maze Cave",
    7: "Lethal Lava Land",
    8: "Shifting Sand Land",
    9: "Dire, Dire Docks",
    10: "Snowman's Land",
    11: "Wet-Dry World",
    12: "Tall, Tall Mountain",
    13: "Tiny-Huge Island",
    14: "Tick Tock Clock",
    15: "Rainbow Ride",
    16: "Bowser in the Dark World",
    17: "Bowser in the Fire Sea",
    18: "Bowser in the Sky",
    19: "The Princess's Secret Slide",
    20: "Cavern of the Metal Cap",
    21: "Tower of the Wing Cap",
    22: "Vanish Cap Under the Moat",
    23: "Wing Mario Over the Rainbow",
    24: "The Secret Aquarium",
}

STAR_NAMES = {
    1: ("Big Bob-omb on the Summit", "Footrace with Koopa the Quick",
        "Shoot to the Island in the Sky", "Find the 8 Red Coins",
        "Mario Wings to the Sky", "Behind Chain Chomp's Gate"),
    2: ("Chip off Whomp's Block", "To the Top of the Fortress",
        "Shoot into the Wild Blue", "Red Coins on the Floating Isle",
        "Fall onto the Caged Island", "Blast Away the Wall"),
    3: ("Plunder in the Sunken Ship", "Can the Eel Come Out to Play?",
        "Treasure of the Ocean Cave", "Red Coins on the Ship Afloat",
        "Blast to the Stone Pillar", "Through the Jet Stream"),
    4: ("Slip Slidin' Away", "Li'l Penguin Lost", "Big Penguin Race",
        "Frosty Slide for 8 Red Coins", "Snowman's Lost His Head",
        "Wall Kicks Will Work"),
    5: ("Go on a Ghost Hunt", "Ride Big Boo's Merry-Go-Round",
        "Secret of the Haunted Books", "Seek the 8 Red Coins",
        "Big Boo's Balcony", "Eye to Eye in the Secret Room"),
    6: ("Swimming Beast in the Cavern", "Elevate for 8 Red Coins",
        "Metal-Head Mario Can Move!", "Navigating the Toxic Maze",
        "A-Maze-Ing Emergency Exit", "Watch for Rolling Rocks"),
    7: ("Boil the Big Bully", "Bully the Bullies",
        "8-Coin Puzzle with 15 Pieces", "Red-Hot Log Rolling",
        "Hot-Foot-It into the Volcano", "Elevator Tour in the Volcano"),
    8: ("In the Talons of the Big Bird", "Shining Atop the Pyramid",
        "Inside the Ancient Pyramid", "Stand Tall on the Four Pillars",
        "Free Flying for 8 Red Coins", "Pyramid Puzzle"),
    9: ("Board Bowser's Sub", "Chests in the Current",
        "Pole-Jumping for Red Coins", "Through the Jet Stream",
        "The Manta Ray's Reward", "Collect the Caps..."),
    10: ("Snowman's Big Head", "Chill with the Bully", "In the Deep Freeze",
         "Whirl from the Freezing Pond", "Shell Shreddin' for Red Coins",
         "Into the Igloo"),
    11: ("Shocking Arrow Lifts!", "Top o' the Town",
         "Secrets in the Shallows & Sky", "Express Elevator--Hurry Up!",
         "Go to Town for Red Coins", "Quick Race Through Downtown!"),
    12: ("Scale the Mountain", "Mystery of the Monkey Cage",
         "Scary 'Shrooms, Red Coins", "Mysterious Mountainside",
         "Breathtaking View from Bridge", "Blast to the Lonely Mushroom"),
    13: ("Pluck the Piranha Flower", "The Tip Top of the Huge Island",
         "Rematch with Koopa the Quick", "Five Itty Bitty Secrets",
         "Wiggler's Red Coins", "Make Wiggler Squirm"),
    14: ("Roll into the Cage", "The Pit and the Pendulums", "Get a Hand",
         "Stomp on the Thwomp", "Timed Jumps on Moving Bars",
         "Stop Time for Red Coins"),
    15: ("Cruiser Crossing the Rainbow", "The Big House in the Sky",
         "Coins Amassed in a Maze", "Swingin' in the Breeze",
         "Tricky Triangles!", "Somewhere Over the Rainbow"),
    16: ("8 Red Coins",),
    17: ("8 Red Coins",),
    18: ("8 Red Coins",),
    19: ("Slide Star", "Slide Star (Under 21 Seconds)"),
    20: ("8 Red Coins",),
    21: ("8 Red Coins",),
    22: ("8 Red Coins",),
    23: ("8 Red Coins",),
    24: ("8 Red Coins",),
}


def course_name(course_id: int) -> str:
    return COURSE_NAMES.get(course_id, f"Course {course_id}")


def star_name(course_id: int, star_id: int) -> str:
    if 1 <= course_id <= 15 and star_id == 6:
        return "100 Coins"
    names = STAR_NAMES.get(course_id, ())
    if 0 <= star_id < len(names):
        return names[star_id]
    return f"Star {star_id + 1}"
