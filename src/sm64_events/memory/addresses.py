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
from functools import lru_cache

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
                                            # frame; live-verified 2026-06-12
                                            # ([DUST] annotations consistent
                                            # across the gate sessions)
MARIO_ACTION = MARIO_STRUCT + 0x0C        # u32; live-verified 2026-06-10
MARIO_ACTION_TIMER = MARIO_STRUCT + 0x1A  # u16, resets to 0 on action change
MARIO_NUM_STARS = MARIO_STRUCT + 0xAA     # s16, total star count; live-verified 2026-06-10
# The pointers that say WHICH object Mario is engaged with. Live-verified
# 2026-08-05 and DISCOVERED rather than asserted: tools/probe_objects.py
# scanned every word of the struct's first 0xC0 bytes for a value landing on an
# object-pool SLOT BOUNDARY while he played, and exactly these three plus
# marioObj (+0x88, Mario's own object, deliberately absent here) ever did.
# Names are decomp's. riddenObj (+0x84) is NOT listed: nothing in that session
# rode anything, so it stays unverified.
MARIO_INTERACT_OBJ = MARIO_STRUCT + 0x78  # what he touched (stomp, painting)
MARIO_HELD_OBJ = MARIO_STRUCT + 0x7C      # what he picked up (bob-omb, shell)
MARIO_USED_OBJ = MARIO_STRUCT + 0x80      # what he operated (door, pole, tree)
# Priority when several are set at once: what he HOLDS beats what he USES beats
# what he merely touched, because the more deliberate act is the one he means.
MARIO_OBJECT_POINTERS = (MARIO_HELD_OBJ, MARIO_USED_OBJ, MARIO_INTERACT_OBJ)

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
# WHICH door / pole / enemy this is: the object's SPAWN point, live-verified
# 2026-08-05 over one ordinary session (tools/probe_objects.py). Nothing else
# in the 0x260-byte slot survived both tests. The POOL SLOT does not: his three
# castle-basement doors held slots 3/2/0, then 38/42/44 after a death exit, then
# 3/2/0 again -- same three doors, and the count is a property of the pool, not
# of the door. Nor does the CURRENT position (+0xA0): across 21 grabs of one
# SSL bob-omb it took 14 values while this one took exactly 1. Two proofs, one
# offset: 13 of 13 door captures keyed by (level, area, behaviour, home) matched
# his own labels for HMC / moat / DDD across the reload.
OBJECT_HOME_POS = 0x164      # Vec3f oHome (spawn x/y/z) within a slot
# Current position (+0xA0, Vec3f oPos). NOT an identity in general -- the
# bob-omb measurement above is exactly about that -- but for a STATIC object
# the game creates scriptless (a pole, a tree: oHome never written, reads
# 0,0,0) the live position IS the authored placement and never moves. Measured
# 2026-08-07 from the stored 2026-08-05 probe captures: the WF tree, 6 grabs
# across TWO area reloads, position (2560, 256, 4608) byte-identical every
# time -- clean power-of-2 designer coordinates. core/landmark.py owns which
# kinds may key on it.
OBJECT_POS = 0xA0            # Vec3f oPos (current x/y/z) within a slot

# WHAT MARIO JUST DID TO THIS OBJECT — the game's own record of the
# association, and Griffin's framing is what makes these the right read
# (2026-08-07): *"the switch press is the switch's, but that switch press
# never occurs without mario. by association, it's therefore mario's action…
# Defeating an enemy is a result of MARIO defeating the enemy."* An object
# does not have to reach Mario's engagement pointers to know he acted on it;
# `oInteractStatus` is where the engine writes it down.
#
# Offsets are the decomp's own (`include/object_fields.h`, fetched
# 2026-08-07) and the header states each one in a comment. VALIDATED against
# this project's two independently-measured offsets in the same table:
# `oPosX` reads 0x0A0 and `oHomeX` reads 0x164, which is exactly what
# OBJECT_POS and OBJECT_HOME_POS above were measured to be. Two hits means
# the field table lines up with the layout we actually read.
# ANSWERED 2026-08-07 (his `--pool` run, 1,386 changes): `oAction` is THE
# legible field — a switch presses 0 -> 1, an enemy dies into a death action.
# `oInteractStatus` is DEAD to a poll (8 non-zero reads in 1,386; the engine
# clears it within the frame — do not build on it). And `oHealth` is NOT a
# defeat signal: the hitbox struct inits `health: 0` (decomp bobomb.inc.c),
# written when the object's logic first runs with Mario in range, so a health
# transition means PROXIMITY — six far-apart bob-ombs "died" inside 50 frames
# of his capture while Mario flew past them, and the ledger's "goomba
# 2048 -> 0" defeats were pool initialisation at a level load.
OBJECT_INTERACT_TYPE = 0x130   # u32 oInteractType: what it offers Mario
OBJECT_INTERACT_STATUS = 0x134 # s32 oInteractStatus: cleared within the frame
OBJECT_ACTION = 0x14C          # s32 oAction: its own state machine — THE read
OBJECT_HEALTH = 0x184          # s32 oHealth: hitbox arming, NOT defeats

# WHAT THE PLAYER CAUSED — the behaviours a caused moment may fire on, and
# the rule that reads each one (detectors/caused.py owns the rules; the kind
# must also be a MOMENTS row so every label/vocab surface can say it).
# Griffin's framing is the design (2026-08-07): *"the switch press is the
# switch's, but that switch press never occurs without mario. by association,
# it's therefore mario's action."*
#
# ADDING A BEHAVIOUR IS ONE ROW HERE — with a capture first. Every shipped
# row is measured from `data/object_pool_probe.jsonl` (his 2026-08-07 run),
# never reasoned from the decomp alone: the switch was reasoned about twice
# and measured once, and only the measurement was right (round 10). Next
# candidates (cap/purple switches, bosses) each want one `--pool` capture.
#
# The POINTERS restate `tools/corpus_behaviors.py`'s derivation (segment base
# + STROOP offset). src cannot import tools/ — the frozen exe does not carry
# it — so the copy is COMPARED instead of shared:
# tests/test_caused.py::test_the_pointer_table_matches_the_shipped_catalogue.
#   symbol -> (US RAM behaviour pointer, moment kind, legibility rule)
CAUSED_BEHAVIOURS = {
    # BLUE_COIN_SWITCH_ACT: IDLE 0 -> RECEDING 1 on the press (-> TICKING 2
    # as it stays down). 3 presses in his capture, all 0 -> 1 at one stable
    # coordinate — his ruling: "We need to support blue coin switch presses
    # for sure".
    "bhvBlueCoinSwitch": (0x800ED6E8, "switch_press", "press"),
    # Dies into the engine's SHARED attacked actions (100/101/102); its own
    # walk/aggro/jump cycle is 0/1/2. 4 squishes (-> 102) in his capture.
    "bhvGoomba": (0x800EF8AC, "enemy_defeated", "attacked"),
    # Dies into ITS OWN explode state (BOBOMB_ACT_EXPLODE): chase -> 3 and
    # thrown -> 3 both measured. The fuse detonation counts too — a bob-omb
    # only fuses while chasing, which the player caused by aggroing it.
    "bhvBobomb": (0x800EE2F4, "enemy_defeated", "explode"),
}
CAUSED_POINTERS = frozenset(row[0] for row in CAUSED_BEHAVIOURS.values())

# Generic object death actions — decomp include/object_constants.h, fetched
# 2026-08-07. NOTE the deliberate collision in the decomp itself:
# OBJ_ACT_LAVA_DEATH is ALSO 100 and OBJ_ACT_DEATH_PLANE_DEATH is ALSO 101,
# so an enemy walking into lava unaided reads as "attacked". No shipped
# course pairs a watched enemy with lava close enough to matter; if a phantom
# defeat is ever reported in LLL or BBH, this collision is the first suspect.
OBJECT_ATTACKED_ACTIONS = frozenset({100, 101, 102})
BOBOMB_ACT_EXPLODE = 3         # decomp object_constants.h, same fetch

# Mario actions entered the moment a star (or key) is grabbed — decomp sm64.h.
ACT_STAR_DANCE_EXIT = 0x00001302               # live-verified 2026-06-10
ACT_STAR_DANCE_WATER = 0x00001303
ACT_STAR_DANCE_NO_EXIT = 0x00001307
ACT_FALL_AFTER_STAR_GRAB = 0x00001904  # midair grabs; live-verified 2026-06-10
# B3 grand star grab — live-verified 2026-06-12 (frame 1950504: entered
# directly from a jump action, numStars unchanged at 17, no star-dance
# action ever appeared, gLastCompleted* untouched). The grand star is NOT
# a collectable star: it never triggers a star-dance action, so
# star_collected cannot fire. The key detector claims it via this action id.
# Composed path re-verified same day: key_grabbed which=grand fired live
# at frame 12160 (third replication of gLastCompleted* staying unrelated).
ACT_JUMBO_STAR_CUTSCENE = 0x00001909

# THE X-CAM MOMENT. Usamune's manual defines Xcam as "Mario touches the ground
# after star-grab", and a star dance is what he enters when he gets there —
# measured 2026-08-01 (tools/derive_xcam.py, four midair grabs scored against
# Usamune's own settled result: the dance-entry counter + IgtClock.DISPLAY_TICK
# came back within one frame every time, against -4/-11/-23/-39 for the grab
# frame). A GROUND grab enters a dance on the grab frame itself, which is why
# grab and x-cam coincide there ("I just simply ran into the star… grabbing and
# xcam is identical", 2026-08-01); only a MIDAIR grab separates the two.
STAR_DANCE_ACTIONS = frozenset({
    ACT_STAR_DANCE_EXIT,
    ACT_STAR_DANCE_WATER,
    ACT_STAR_DANCE_NO_EXIT,
})

# The grab moment: a dance (ground) or the fall that precedes one (midair).
# ACT_JUMBO_STAR_CUTSCENE is intentionally NOT in this set — adding it
# would make star_grab.py suppress a hypothetical real star-dance in B3.
# The key detector uses it directly via FIGHT_END_LEVELS instead.
STAR_GRAB_ACTIONS = STAR_DANCE_ACTIONS | frozenset({ACT_FALL_AFTER_STAR_GRAB})

# Post-star "SAVE & CONTINUE?" course-complete screen. Exiting a course WITH a
# star lands Mario in ACT_EXIT_LAND_SAVE_DIALOG; the save-options menu
# (gMenuMode==2) renders over it and Mario HOLDS this action for the whole menu.
# Confirming any option reloads the area and resets Usamune's IGT a few frames
# later — an INVOLUNTARY reset, not a player retry. The anchor detector flags
# resets seen during this action (save_pending) so the segment engine treats
# them as echoes (segments.py shape 4) and a segment runs THROUGH the save.
# Live-verified 2026-06-12 (LLL exit watch on gMenuMode 0x803314F8 + this
# action): mario_action held 0x1327 for the entire menu — one tick before
# gMenuMode flipped to 2 — then reverted to ACT_IDLE on "Save and Continue".
# Read off the already-sampled mario_action: no new memory address needed.
# Source: decomp include/sm64.h ACT_EXIT_LAND_SAVE_DIALOG.
ACT_EXIT_LAND_SAVE_DIALOG = 0x00001327
SAVE_DIALOG_ACTIONS = frozenset({ACT_EXIT_LAND_SAVE_DIALOG})

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
# Jump-chain ids live-verified 2026-06-12 (gate sessions: double/triple
# jumps and rollouts detected consistently across castle/BitS/arenas).
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

# Pending warp op (level_update.c sDelayedWarpOp) — the ONLY in-level signal
# for void-out deaths (death barriers: HMC pits, Bowser arenas...). Those
# deaths never enter a DEATH_ACTION: decomp check_death_barrier (fetched
# 2026-06-12) pends WARP_OP_WARP_FLOOR for ~20 game frames (sDelayedWarpTimer
# = 20), THEN the level unloads, and the life is lost after the warp in the
# destination's death-exit action. Reading the pulse pre-warp keeps the death
# event BEFORE level_changed in the journal — the projection then needs no
# special casing (death closes the open attempt; the exit closes nothing).
#
# US address derived 2026-06-12, VERIFY (live gate pending):
#   level_update.c FORCE_BSS block, declaration order. Internal layout pinned
#   by BOTH legacy JP names in that block (D_80339ECA, D_80339EE0 — only
#   consistent with sWarpDest 8-aligned); absolute US position pinned by the
#   block's LAST member sTimerRunning = 0x8033B25E (HUD_TIMER_RUNNING above,
#   live-verified). Corroboration: derived sWarpDest = 0x8033B248 matches
#   STROOP's US warp destination. Walk: sCurrPlayMode 0x8033B238,
#   sWarpDest 0x8033B248, sDelayedWarpOp 0x8033B252, timer 0x8033B254.
#   Live check: tools/verify_addresses.py phase 2 prints warp-op changes —
#   fall into an HMC pit and expect 0 -> 0x13 (~20 frames) plus a
#   ">> death: fall" detector line BEFORE the level unloads. Also confirm a
#   normal (quicksand) death does NOT double-fire (Usamune's in-level death
#   respawn must not pulse 0x13).
PENDING_WARP_OP = 0x8033B252  # s16 sDelayedWarpOp; 0 = no warp pending
# Op values (decomp level_update.h): 0x12 WARP_OP_DEATH is the NORMAL death
# warp, pended ~48 frames AFTER the death action already fired our event —
# deliberately not consumed (would double-count every death). Levels that
# define a warp-floor node (TotWC's cloud exit) turn 0x13 into a non-death
# exit; falling out of those would mislabel as death — characterize at the
# live gate before excluding by level id.
WARP_OP_WARP_FLOOR = 0x13  # void-out: resolves to the death node (or game
                           # over at 0 lives) unless the level has node 0xF3

# The WARP DESTINATION (level_update.c `struct WarpDest sWarpDest`), same
# FORCE_BSS block as PENDING_WARP_OP and pinned by the same walk:
#   struct WarpDest { u8 type; u8 levelNum; u8 areaIdx; u8 nodeId; s32 arg; }
#
# LIVE-VERIFIED 2026-08-05 over 15 consecutive castle entries
# (tools/probe_warp_block.py), and it OVERTURNS the belief the held emit was
# built on. A painting or portal writes this struct AT OR BEFORE the frame
# Mario's action becomes ACT_DISAPPEARED: on all 13 painting/portal touches the
# level id here already named where he ended up, 76-78 frames before the level
# byte moved, and it was demonstrably not stale — 12 of the 13 differed from the
# destination of the warp immediately before. Only a PIPE (BitDW, BitFS) is a
# genuinely delayed warp: there PENDING_WARP_OP pulses 0x04 one to two frames
# after the touch, counts a 20-frame timer down, and only then is this struct
# written — 3 frames before the level byte moves.
#
# So `type != 0` alone is NOT a freshness test: it survives a completed painting
# warp (read live while standing idle in DDD after entering it). Freshness is
# WHETHER THE STRUCT WAS JUST WRITTEN, which detectors/warp.py tests by watching
# all four bytes change; the two pipe touches are exactly the negative cases
# that prove it, both reading a stale castle destination at the touch frame.
WARP_DEST_TYPE = 0x8033B248   # u8 WARP_TYPE_*; 0 = NOT_WARPING
WARP_DEST_LEVEL = 0x8033B249  # u8 destination LEVEL id (not a course id)
WARP_DEST_AREA = 0x8033B24A   # u8 destination area
WARP_DEST_NODE = 0x8033B24B   # u8 destination warp node (0x0A = main entry)

# Level-EXIT cutscene actions — Mario is FLUNG out of a level and has no
# control (decomp include/sm64.h, the contiguous 0x1926-0x192D block). These
# are involuntary exactly as DEATH_ACTIONS are, and AnchorDetector excludes
# them from its activity flag for the same reason.
#
# LIVE-EVIDENCED 2026-08-03, and the evidence is why this set exists: the byte
# STAYS at the exit action long after the exit. He died in WF, was flung to the
# castle, sat in the pause menu for 92 seconds, and menu-warped back into WF
# with mario_action still reading ACT_DEATH_EXIT — so the arrival's own anchors
# reported mario_acted=true, the unacted-reset discard could not fire, and the
# 44 frames between the arrival's two anchors banked a phantom 1.5 s reset row
# ("there's sometimes a Reset entry RIGHT when we start the map... The first
# time we enter a map should never be considered a reset"). Measured over both
# journals: 62 anchors land on one of these and 62 of 62 carry
# mario_acted=true. Six of the seven appear in his real play — every one but
# ACT_UNUSED_DEATH_EXIT, which vanilla never triggers.
#
# NOT added to PASSIVE_ACTIONS, deliberately: that set also drives
# replay/activity.py's idle check, and the recorder should keep rolling through
# an exit cutscene so a clip is not cut short at the moment the star pays off.
ACT_EXIT_AIRBORNE = 0x00001926
ACT_DEATH_EXIT = 0x00001928
ACT_UNUSED_DEATH_EXIT = 0x00001929
ACT_FALLING_DEATH_EXIT = 0x0000192A
ACT_SPECIAL_EXIT_AIRBORNE = 0x0000192B
ACT_SPECIAL_DEATH_EXIT = 0x0000192C
ACT_FALLING_EXIT_AIRBORNE = 0x0000192D
LEVEL_EXIT_ACTIONS = frozenset({
    ACT_EXIT_AIRBORNE, ACT_DEATH_EXIT, ACT_UNUSED_DEATH_EXIT,
    ACT_FALLING_DEATH_EXIT, ACT_SPECIAL_EXIT_AIRBORNE,
    ACT_SPECIAL_DEATH_EXIT, ACT_FALLING_EXIT_AIRBORNE})

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

# gCurrLevelNum values for the three castle hub levels — decomp
# levels/level_defines.h. 6 (inside) is live-evidenced by our own journal
# (every stage exit logs level_changed to=6); 16 (grounds) live-verified
# 2026-06-12 (fresh-file spawn lands in level 16). VERIFY: 26 (courtyard)
# is still decomp-only — check a courtyard entry flags correctly.
CASTLE_LEVELS = frozenset({6, 16, 26})  # inside, grounds, courtyard

# --- Segment-event primitives (2026-06-11; FSM contract lives in --------
# --- tracking/segments.py's module docstring, which is the authority) ---

# gCurrLevelNum LEVEL ids — decomp levels/level_defines.h DEFINE_LEVEL order
# (1-based). Cross-validated against three live-verified anchors we already
# had: WF=24, SSL=8, castle 6/16/26 — all consistent with this table.
# Live-walked 2026-06-12 gate (two sessions): 6, 7, 16, 17, 19, 21, 22,
# 23, 30, 33, 34 all confirmed via level_changed payloads — every id a
# segment depends on. Only 26 (courtyard, no segment uses it) remains
# decomp-only.
# Boot transient: level id 1 (decomp UNKNOWN stub) appears with garbage
# reads during console resets — inert, matches no trigger.
LEVEL_NAMES = {
    4: "Big Boo's Haunt", 5: "Cool, Cool Mountain", 6: "Castle Inside",
    7: "Hazy Maze Cave", 8: "Shifting Sand Land", 9: "Bob-omb Battlefield",
    10: "Snowman's Land", 11: "Wet-Dry World", 12: "Jolly Roger Bay",
    13: "Tiny-Huge Island", 14: "Tick Tock Clock", 15: "Rainbow Ride",
    16: "Castle Grounds", 17: "Bowser in the Dark World",
    18: "Vanish Cap Under the Moat", 19: "Bowser in the Fire Sea",
    20: "The Secret Aquarium", 21: "Bowser in the Sky",
    22: "Lethal Lava Land", 23: "Dire, Dire Docks", 24: "Whomp's Fortress",
    26: "Castle Courtyard", 27: "The Princess's Secret Slide",
    28: "Cavern of the Metal Cap", 29: "Tower of the Wing Cap",
    30: "Bowser 1 Arena", 31: "Wing Mario Over the Rainbow",
    33: "Bowser 2 Arena", 34: "Bowser 3 Arena", 36: "Tall, Tall Mountain",
}
# Gaps: 25 (ending cutscene), 32 and 35 (decomp UNKNOWN stubs) — not
# reachable in normal play.

LEVEL_BITDW, LEVEL_BITFS, LEVEL_BITS = 17, 19, 21
LEVEL_HMC, LEVEL_DDD = 7, 23
LEVEL_CASTLE_INSIDE, LEVEL_CASTLE_GROUNDS, LEVEL_CASTLE_COURTYARD = 6, 16, 26
BOWSER_1_ARENA, BOWSER_2_ARENA, BOWSER_3_ARENA = 30, 33, 34

# Key grabs enter the same star-dance actions as stars (see STAR_GRAB_ACTIONS
# comment above). In these two arenas the grab is a KEY, not a star — the
# key detector claims it and star_grab must ignore it.
# Live-verified 2026-06-12, BOTH arenas: key grabs do NOT update
# gLastCompleted* (B1: stale course=16 star=1 from the prior star;
# B2: unrelated course=17 star=1 on a fresh 0-star file).
# The star_grab guard on KEY_GRAB_LEVELS is what prevents misattribution —
# confirmed that this guard is sufficient; no extra action-id guard needed.
KEY_GRAB_LEVELS = frozenset({BOWSER_1_ARENA, BOWSER_2_ARENA})
# Fight-ending grabs: Bowser 1/2 via star-dance actions (in KEY_GRAB_LEVELS),
# Bowser 3 via ACT_JUMBO_STAR_CUTSCENE. The value is the which-label the
# key_grabbed payload carries. Level 34 is intentionally NOT in KEY_GRAB_LEVELS
# (that set guards star_grab.py); it is only in FIGHT_END_LEVELS (read by the
# key detector). Adding 34 to KEY_GRAB_LEVELS would wrongly suppress a
# hypothetical real star-dance in the B3 arena — keep the sets separate.
FIGHT_END_LEVELS = {
    BOWSER_1_ARENA: "bitdw",
    BOWSER_2_ARENA: "bitfs",
    BOWSER_3_ARENA: "grand",
}

# Castle door actions — values observed in our own 2026-06-12 gate logs at
# every door crossing (decomp names: ACT_PULLING_DOOR / ACT_PUSHING_DOOR /
# ACT_WARP_DOOR_SPAWN). A door warp resets Usamune's IGT like any load,
# so the anchor detector reports a synthetic reset mid-door — inputs are
# locked during door animations, so such an anchor is never a player reset.
ACT_PULLING_DOOR = 0x00001320
ACT_PUSHING_DOOR = 0x00001321
ACT_WARP_DOOR_SPAWN = 0x00001322
# Star/key doors run their OWN cutscene actions, not PUSH/PULL — decomp
# include/sm64.h, quoted verbatim from n64decomp/sm64 master, fetched
# 2026-06-12 (0x1330 is unassigned in the decomp; flags are all
# STATIONARY|INTANGIBLE like the three above). Found via live journal event
# 3594 (2026-06-12): opening a 30/70-star door on the way to BitS fired the
# Usamune section reset with frames_since_door STALE (1976) because none of
# these were in DOOR_ACTIONS — every echo shape failed and the segment
# engine closed + rebased the armed BitS Entry segment at the door. VERIFY
# (live gate): mario_action crosses 0x1331 walking through an open star
# door, 0x132F on the first-time star-count tally, 0x132E at a key door.
ACT_UNLOCKING_KEY_DOOR = 0x0000132E
ACT_UNLOCKING_STAR_DOOR = 0x0000132F
ACT_ENTERING_STAR_DOOR = 0x00001331
DOOR_ACTIONS = frozenset({
    ACT_PULLING_DOOR, ACT_PUSHING_DOOR, ACT_WARP_DOOR_SPAWN,
    ACT_UNLOCKING_KEY_DOOR, ACT_UNLOCKING_STAR_DOOR, ACT_ENTERING_STAR_DOOR})

# Warp-entry actions — decomp include/sm64.h, quoted verbatim from
# n64decomp/sm64 master, fetched 2026-06-11. Live-verified 2026-06-12:
# ACT_DISAPPEARED fired on the BitDW pipe, the BitS end funnel, AND the
# castle upstairs -> BitS entry warp (all 0x1300).
ACT_DISAPPEARED = 0x00001300       # generic "Mario left the world" (pipes, some warps); live-verified 2026-06-12
# LIVE-VERIFIED 2026-08-03 (task 0082), and the "harmless" note below was
# wrong: an in-level teleporter — the CCM broken bridge, a WDW corner — zeroes
# Usamune's overall counter, so it fired a practice_reset the player never
# made. Journal ids 23199/23200, 23218/23219, 23231/23232 all show the pair
# 0x1336 -> ACT_TELEPORT_FADE_IN (0x1337) on the frame the counter drops, 42
# frames after touching the pad (decomp `act_teleport_fade_out` triggers the
# warp at actionTimer 20 and the delayed warp takes 20 more). The action byte
# then KEEPS reading 0x1337 for well over a hundred frames, which is why
# detectors/anchors.py keys on fade-out RECENCY rather than on the action.
ACT_TELEPORT_FADE_OUT = 0x00001336  # teleporter/cap-warp fade; also fires for in-level teleporters elsewhere
# THE BIG BOO'S HAUNT CAGE — the one course entrance that fires neither of
# the two actions above (his probe run, 2026-08-07: five entrances produced
# clean touches, BBH produced nothing). The cage plays its own animation, and
# WHICH action opens it depends on how Mario arrives — decomp
# `interaction.c::interact_bbh_entrance`, fetched verbatim 2026-08-07:
# `m->action & ACT_FLAG_AIR` → ENTER_SPIN directly, else ENTER_JUMP (which
# then transitions into the spin). So the commit moment is the FIRST of the
# pair to occur, which is what the edge INTO this set expresses with both
# members present: a ground entry fires at the jump (jump → spin is in-set,
# no second touch), an airborne entry fires at the spin. Level byte follows
# the jump by +74/75 (both probe entries) — a painting's ~77 almost exactly.
# Both values verified against decomp include/sm64.h, same fetch returning
# ACT_IN_CANNON, ACT_DISAPPEARED and ACT_TELEPORT_FADE_OUT byte-identical.
#
# THE SPIN'S FIRST DAY OUT OF THE SET COST A LIVE ROUND (round 12). The
# probe's two entries were both GROUND entries, so the jump looked like "the
# commit moment" rather than "the ground half of a fork" — and his first
# real entry was a ROLLOUT, airborne, so no jump edge ever occurred and the
# entry recorded nothing: journal id 3708's arrival anchor latched action
# 0x1535 with no warp row anywhere before the 26→4 edge. One live entry
# falsified what two probe entries had agreed on. (The old exclusion
# reasoning — a leaving-window 0x1535 sighting explained as the entry's own
# probe tail — was correct as far as it went; it answered "is the spin an
# exit signal", which was never the right question.)
ACT_BBH_ENTER_JUMP = 0x00001934
ACT_BBH_ENTER_SPIN = 0x00001535
WARP_ENTRY_ACTIONS = frozenset({ACT_DISAPPEARED, ACT_TELEPORT_FADE_OUT,
                                ACT_BBH_ENTER_JUMP, ACT_BBH_ENTER_SPIN})

# Spawn actions — same decomp fetch. Live-verified 2026-06-12:
# - FRESH file start: ACT_INTRO_CUTSCENE plays through Lakitu's dialogue;
#   the edge OUT of it (control gained) fires spawned kind="intro" — the
#   canonical Lakitu-skip timing start.
# - EXISTING-file load: Mario spawns with NO SPAWN_ACTIONS edge at all (no
#   spawned event) — so the Lakitu Skip seed arms only on fresh starts, by
#   design (the trick is a run-start trick).
# - Pipe/arena arrivals: ACT_SPAWN_SPIN_AIRBORNE (0x1924) and
#   ACT_SPAWN_NO_SPIN_AIRBORNE (0x1932) observed -> spawned kind="spawn";
#   harmless re-arms (triggers filter by level).
ACT_INTRO_CUTSCENE = 0x04001301
ACT_SPAWN_SPIN_AIRBORNE = 0x00001924
ACT_SPAWN_SPIN_LANDING = 0x00001325
ACT_SPAWN_NO_SPIN_AIRBORNE = 0x00001932
ACT_SPAWN_NO_SPIN_LANDING = 0x00001333
# NB: these four ids also appear in PASSIVE_ACTIONS (AnchorDetector's
# inactive-action set); keep both in sync when modifying spawn ids.
SPAWN_ACTIONS = frozenset({ACT_SPAWN_SPIN_AIRBORNE, ACT_SPAWN_SPIN_LANDING,
                           ACT_SPAWN_NO_SPIN_AIRBORNE,
                           ACT_SPAWN_NO_SPIN_LANDING})

# Textbox / dialogue actions — decomp include/sm64.h, quoted verbatim from
# n64decomp/sm64 master, fetched 2026-06-14. Mario holds one of these for the
# whole textbox; a textbox engages a TIME-STOP that re-initialises Usamune's
# overall IGT, so an anchor (practice_reset/state_loaded) coinciding with one is
# an involuntary echo, never a player retry — see AnchorDetector._last_dialog_frame
# and segments.py echo shape (5). The intro cutscene (ACT_INTRO_CUTSCENE, the
# Lakitu-skip run-start dialogue) is tracked alongside these: it ends, control is
# regained, and Usamune zeroes the overall IGT one frame later (live journal
# 2026-06-14). We do NOT split timing on textboxes in any level/circumstance
# (user rule 2026-06-14).
# VERIFY (live gate pending): confirm mario_action reads these while a textbox is
# open — 0x20001305 on a sign/automatic dialog, 0x20001306 talking to an NPC,
# 0x0000130A waiting for a dialog to begin. ACT_INTRO_CUTSCENE is already
# live-verified (the fresh-file spawn source above).
ACT_READING_AUTOMATIC_DIALOG = 0x20001305  # signs / automatic dialogs
ACT_READING_NPC_DIALOG = 0x20001306        # talking to an NPC
ACT_WAITING_FOR_DIALOG = 0x0000130A        # dialog about to begin
DIALOG_ACTIONS = frozenset({ACT_READING_AUTOMATIC_DIALOG,
                            ACT_READING_NPC_DIALOG, ACT_WAITING_FOR_DIALOG})

# Pole / tree grabs — decomp include/sm64.h. A TREE IS A POLE to the engine:
# both use the same climbing action group, which is why one moment kind covers
# the BoB tree, the WF pole and every LLL cage pole. His report, 2026-08-06:
# *"We don't detect poles / trees when I would expect this to be there"*,
# with a screenshot of Mario hugging the BoB tree and an empty recorder.
# Only the two GRAB actions are the moment — the climb, the top transition and
# the top itself all follow from one successful grab, and entering any of them
# is not a separate practice boundary.
# VERIFY (live gate): mario_action reads 0x00100341 grabbing a pole/tree slowly
# and 0x00100342 grabbing one at speed.
ACT_GRAB_POLE_SLOW = 0x00100341
ACT_GRAB_POLE_FAST = 0x00100342
POLE_GRAB_ACTIONS = frozenset({ACT_GRAB_POLE_SLOW, ACT_GRAB_POLE_FAST})

# Picking something up — decomp include/sm64.h, quoted verbatim from
# n64decomp/sm64 master, fetched 2026-08-06. His report, same day: *"When I
# grab a bob-omb in a level, I want to be able to detect WHEN i grabbed them.
# The frame I managed to successfully grab them."* ACT_PICKING_UP is exactly
# that frame: the game sets it when the grab SUCCEEDS, so the entry edge is
# the moment and no holding action needs to be watched.
#
# THE DIVE PICKUP IS ITS OWN ID, and it is the one a runner mostly uses —
# diving onto a bob-omb grabs it in one motion, and the game enters 0x385
# rather than 0x383 for it. This set shipped for a few hours calling 0x385
# "Bowser's tail", from memory; the decomp says otherwise, and Bowser's tail
# is 0x390 — also here, deliberately, because the first tail grab is exactly
# the practiced boundary of every ranked Bowser fight. One kind covers all
# three: which thing was grabbed is the LANDMARK's job, not the action's.
# VERIFY (live gate): mario_action reads 0x00000383 walking into a bob-omb,
# 0x00000385 dive-grabbing one, 0x00000390 grabbing Bowser's tail.
ACT_PICKING_UP = 0x00000383
ACT_DIVE_PICKING_UP = 0x00000385
ACT_PICKING_UP_BOWSER = 0x00000390
PICKUP_ACTIONS = frozenset({ACT_PICKING_UP, ACT_DIVE_PICKING_UP,
                            ACT_PICKING_UP_BOWSER})

# Climbing INTO a cannon — decomp include/sm64.h, fetched 2026-08-07 (the two
# pole constants above came back byte-identical from the same file, which is
# how this version was checked). His ask, round 9 item 6: *"We also need to
# detect when the user enters a cannon"*, then *"(cannon entry xcam)"* naming
# the timing reference.
#
# ENTRY ONLY, and the entry edge is the whole point: ACT_IN_CANNON is the
# frame the game commits Mario to the in-cannon view, which is the camera cut
# he calls the cannon-entry x-cam. Firing is a different moment
# (ACT_SHOT_FROM_CANNON 0x00880898) and is deliberately NOT here — one kind,
# one boundary, and the launch already has `spawned`-shaped consequences a
# route can key on.
# VERIFY (live gate): mario_action reads 0x00001371 while aiming in a cannon.
ACT_IN_CANNON = 0x00001371
CANNON_ACTIONS = frozenset({ACT_IN_CANNON})

# gCurrAreaIndex (s16) — castle lobby/upstairs/basement are AREAS of level 6,
# not levels. Live-verified 2026-06-12 via tools/hunt_exact.py snapshot diff:
# reads 1 in the lobby, 2 upstairs, 3 in the basement, stable across repeated
# visits (the repeat-label pass proves it is state, not a counter). Sits in
# the area.c globals cluster two halfwords above gCurrCourseNum (0x8033BAC6).
CURR_AREA = 0x8033BACA               # s16 gCurrAreaIndex
CASTLE_AREA_NAMES = {1: "Lobby", 2: "Upstairs", 3: "Basement"}  # live-verified 2026-06-12 (same hunt)

# The castle-region levels the segment builder's "enter area" condition offers
# (tracking/segments.py): the interior (6, whose CASTLE_AREA_NAMES subareas
# the builder exposes) plus the two subarea-less hub levels. Ordered
# interior-first to match the region dropdown. These are LEVELS, not areas —
# only level 6 has named subareas (gCurrAreaIndex 1/2/3); 16 and 26 are single-
# area levels, so the builder shows no subarea selector for them.
CASTLE_REGION_LEVELS = (LEVEL_CASTLE_INSIDE, LEVEL_CASTLE_GROUNDS,
                        LEVEL_CASTLE_COURTYARD)  # = (6, 16, 26)

AREA_LOBBY, AREA_UPSTAIRS, AREA_BASEMENT = 1, 2, 3  # CASTLE_AREA_NAMES ids

# --- World topology (segment-builder dropdown constraints, 2026-07-23) -----
# Directed reachability between world NODES — (level, subarea) inside the
# castle, a bare level id everywhere else — under NORMAL movement: doors,
# paintings, pipes, course exits and deaths. The Usamune warp menu can
# fabricate ANY level edge, so this table drives UI option FILTERING only
# (tracking/segments.py vocab -> ui/components/segments.js); definition
# validation and the matcher stay permissive, and a stored def whose edge
# isn't listed here still renders, matches, and saves.
# Layout source: the castle's world design (user spec 2026-07-23 + common
# SM64 knowledge). The basement <-> grounds link is the drained-moat door;
# fight-exit landing nodes follow each Bowser course's entry region.
# Wrong or missing edge? Fix ONE row here — vocab and the builder rederive.
_LOBBY = (LEVEL_CASTLE_INSIDE, AREA_LOBBY)
_UPSTAIRS = (LEVEL_CASTLE_INSIDE, AREA_UPSTAIRS)
_BASEMENT = (LEVEL_CASTLE_INSIDE, AREA_BASEMENT)

# Two-way edges: walking/painting entries whose exit returns where you came
# from (course exits, deaths, and save-quit all land at the entry region).
WORLD_EDGES_TWO_WAY = (
    # lobby (1F): its paintings + secret levels, the front door, the
    # courtyard doors, the BitDW star door, and the interior stairs
    (_LOBBY, 9), (_LOBBY, 24), (_LOBBY, 12), (_LOBBY, 5),   # BoB WF JRB CCM
    (_LOBBY, 27), (_LOBBY, 20), (_LOBBY, 29),               # PSS SA TotWC
    (_LOBBY, 17),                                           # BitDW
    (_LOBBY, 16), (_LOBBY, 26),                             # grounds, courtyard
    (_LOBBY, _UPSTAIRS), (_LOBBY, _BASEMENT),               # interior stairs/doors
    # basement: its paintings, BitFS (user spec 2026-07-23: the basement
    # region owns BitFS), and the drained-moat door to the grounds
    (_BASEMENT, 7), (_BASEMENT, 22), (_BASEMENT, 8),        # HMC LLL SSL
    (_BASEMENT, 23),                                        # DDD
    (_BASEMENT, 19),                                        # BitFS
    (_BASEMENT, 16),                                        # drained-moat door
    # upstairs (2F+3F = area 2): its paintings, WMOTR, and BitS
    (_UPSTAIRS, 10), (_UPSTAIRS, 11), (_UPSTAIRS, 36),      # SL WDW TTM
    (_UPSTAIRS, 13), (_UPSTAIRS, 14), (_UPSTAIRS, 15),      # THI TTC RR
    (_UPSTAIRS, 31),                                        # WMOTR
    (_UPSTAIRS, 21),                                        # BitS
    # single-area hubs and in-course entrances
    (26, 4),                                                # courtyard <-> BBH
    (16, 18),                                               # grounds <-> VCUtM (moat)
    (7, 28),                                                # HMC pool <-> CotMC
    # Bowser course <-> its arena. The pipe goes in; LOSING the fight puts you
    # back at the top of the course you came from, which is the same shape as
    # any other "the exit returns where you came from" row above. These were
    # one-way (course -> arena only) until 2026-08-02, when the human read the
    # map this table now draws and corrected all three -- exactly the class of
    # error tools/measure_topology_cancels.py is structurally blind to, since a
    # MISSING edge only makes the rules stricter in a place he never walked.
    (17, BOWSER_1_ARENA), (19, BOWSER_2_ARENA), (21, BOWSER_3_ARENA),
)

# One-way edges: moves with a DIFFERENT return path. Arena pipes only run
# course -> arena; fight exits (key grab / death) dump Mario at the course's
# castle region, never back into the course.
#
# `(23, 19)` -- "DDD sub bay -> BitFS", described here as the only natural way
# INTO BitFS -- was REMOVED 2026-07-27. BitFS is entered from the BASEMENT,
# which the two-way `(_BASEMENT, 19)` row above already says. Live capture of
# the real walk, twice in one session: `level_changed 23 -> 6 (from_area 2)`,
# `area_changed 6: 2 -> 3`, `warp_entered {level: 6, area: 3}`, then
# `level_changed 6 -> 19 (from_area 3)`. There is no 23 -> 19 transition in
# the journal at all. The bad edge made the corpus's own independent walker
# route DDD -> BitFS in one hop, which is what forced `seg:ddd->bitfs` onto a
# `star_grabbed` start (see tools/corpus_movements.py) -- and that start is
# what fired the movement while the player was still standing in DDD holding
# the star they were practising (live report 2026-07-27).
#
# The three `course -> arena` rows that used to live here MOVED to the two-way
# table on 2026-08-02: losing the fight returns you to the course, so the pair
# is symmetric and only the WIN is one-way.
#
# `(34, _UPSTAIRS)` was REMOVED the same day, on the human's correction reading
# tools/topology_map.py: "Bowser 3 cannot go back to upstairs, because if you
# win in bowser 3 you beat the game; if you lose in bowser 3, you go back into
# bowser in the sky." So the Bowser 3 arena has no exit into the castle at all
# -- its only edge is the two-way pair with BitS. Bowser 1 and 2 keep theirs:
# their key cutscene really does put Mario back in the castle.
#
# `(19, _LOBBY)` -- "BitFS exit -> LOBBY" -- was ADDED 2026-08-02, on his live
# report plus a measurement of both journals. BitFS is ENTERED from the
# basement (the two-way row above), but EXITING it does not put you back there:
# it puts you in the lobby, and that is not a quirk, it is a movement TRICK he
# routes on. His words: *"The fastest path to getting to upstairs is actually
# to go Bowser 2 -> Basement -> Re-enter bowser in the fire sea -> Exit to
# lobby -> Upstairs."*
#
# Measured over every `level_changed <bowser level> -> 6` in both journals,
# taking the SETTLED area (the same per-frame collapse the matcher uses):
#   BitFS -> Lobby x11, -> Basement x1      <- this row
#   BitDW -> Lobby x27                      <- already covered by (_LOBBY, 17)
#   BitS  -> Upstairs x2, -> Lobby x1       <- UNRESOLVED, deliberately not
#                                              added: three observations cannot
#                                              tell a trick from a mis-sample,
#                                              and a wrong edge is invisible to
#                                              every test (that is why
#                                              tools/topology_map.py exists)
# What the missing row COST, which is the whole argument for measuring rather
# than reasoning about a table: with BitFS reachable only from the basement,
# BitFS sat 3 hops from Upstairs where the basement sat 2 -- so Rule 2 read the
# fastest real route as walking away from the destination and silently killed
# `Bowser 2 -> Upstairs` the instant he entered the pipe. With this row both
# are 2, equal is sideways, and the rule waves it through.
#
# NOT made two-way: a lobby -> BitFS move is the warp menu, and the basement is
# the real door in.
# THE PAUSE EXIT, added 2026-08-02 — and `(19, _LOBBY)` above turns out to have
# been one instance of it rather than a fact about BitFS.
#
# LIVE-VERIFIED by the human in every course: *"every single course has an 'Exit
# Course' button that results in going back to the Lobby. The only exceptions
# are: Castle Basement, Castle Upstairs, Castle Courtyard, Castle Grounds — for
# these, if you press pause, there's no option to exit (because we're already in
# the castle). That's fine. We navigate the castle as normal."* And separately
# for the arenas: *"for Bowser 1/2/3 there's an Exit Course option, which brings
# you back to the lobby."*
#
# So the rule has NO exceptions beyond the castle's own areas, and
# `tests/test_topology.py::test_every_course_can_pause_exit_to_the_lobby`
# states it that way rather than as a list — a hand-written list is what got it
# wrong before (an earlier draft named 12 and named them wrongly, by reading
# level ids through COURSE_NAMES, which is keyed by COURSE number).
#
# ONE-WAY: you cannot enter a course from the lobby that you have no door for.
#
# This is what makes each re-entry movement a TRICK: re-entering a course and
# pause-exiting is how you reach the Lobby without walking to it — `SL ->
# Basement` skips Upstairs -> Lobby, `HMC -> RR` skips Basement -> Lobby.
# Nothing else explains why those movements exist.
#
# It is a LIVE defect fix, not only authoring groundwork: loose definitions
# judge by hop count through this table, so a real pause exit out of any of
# these was either an impossible move (Rule 1) or read as walking away (Rule 2).
#
# The Bowser 3 arena keeps having no WIN or LOSE edge into the castle (see
# `(34, _UPSTAIRS)`'s removal above) — a pause exit is a third, different
# mechanism, and the human tested it directly.
# Their own tuple because they are their own MECHANISM, and consumers need to
# tell them apart: a course's ordinary exit is the door it came in by, and a
# walker that took the pause exit instead would route BBH -> Lobby and never
# see the Courtyard. `(19, _LOBBY)` lives here now — it was added the day
# before as a fact about BitFS and is one instance of this rule.
# Levels already carrying a two-way lobby edge (BoB WF JRB CCM PSS SA TotWC
# BitDW) reach it anyway and need no row.
WORLD_PAUSE_EXITS = (
    (4, _LOBBY), (7, _LOBBY), (8, _LOBBY),        # BBH HMC SSL
    (10, _LOBBY), (11, _LOBBY), (13, _LOBBY),     # SL WDW THI
    (14, _LOBBY), (15, _LOBBY), (18, _LOBBY),     # TTC RR VCUtM
    (19, _LOBBY), (21, _LOBBY), (22, _LOBBY),     # BitFS BitS LLL
    (23, _LOBBY), (28, _LOBBY), (31, _LOBBY),     # DDD CotMC WMOTR
    (36, _LOBBY),                                 # TTM
    (BOWSER_2_ARENA, _LOBBY), (BOWSER_3_ARENA, _LOBBY),
)

WORLD_EDGES_ONE_WAY = (
    (30, _LOBBY), (33, _BASEMENT),                # winning key cutscene -> castle
) + WORLD_PAUSE_EXITS


def _world_node(spec) -> tuple:
    """Registry shorthand: a bare level id means (level, no subarea)."""
    return spec if isinstance(spec, tuple) else (spec, None)


def node_key(level: int, area: int | None = None) -> str:
    """THE world-node key format: "6:1" for a castle subarea, "22" for a
    whole level. Used by world_connections (the builder's dropdown filter),
    the segment-origin taxonomy, and every consumer of either."""
    return f"{level}:{area}" if area is not None else str(level)


def node_label(key: str) -> str:
    """Display name for a node key. Castle subareas read as "Basement";
    everything else takes its LEVEL_NAMES entry. Display only — the key is
    the identity."""
    level_str, _, area_str = key.partition(":")
    if area_str:
        return CASTLE_AREA_NAMES.get(int(area_str), f"Area {area_str}")
    level = int(level_str)
    return LEVEL_NAMES.get(level, f"Level {level}")


def world_connections() -> dict:
    """Successor map serialized for the segment-builder vocab: node key
    ("6:1" castle subarea / "22" whole level) -> sorted [level, area|None]
    destination pairs. JSON-shaped here so vocab() ships it untouched."""
    successors: dict[str, list] = {}

    def add_edge(from_spec, to_spec):
        from_level, from_area = _world_node(from_spec)
        to_level, to_area = _world_node(to_spec)
        key = node_key(from_level, from_area)
        destination = [to_level, to_area]
        bucket = successors.setdefault(key, [])
        if destination not in bucket:
            bucket.append(destination)

    for node_a, node_b in WORLD_EDGES_TWO_WAY:
        add_edge(node_a, node_b)
        add_edge(node_b, node_a)
    for from_spec, to_spec in WORLD_EDGES_ONE_WAY:
        add_edge(from_spec, to_spec)
    return {key: sorted(dests, key=lambda d: (d[0], d[1] or 0))
            for key, dests in successors.items()}


# --- Castle regions (segment-origin taxonomy, 2026-07-24) ------------------
# The five castle nodes every other place hangs off, in GAMEFLOW order — the
# order the castle opens up (8 stars -> basement, 12 -> courtyard, 30 ->
# upstairs). The UI renders regions in this order; it is a user decision
# (spec 2026-07-24-segment-origin-categories), not an implementation detail.
CASTLE_REGION_NODES = (
    (LEVEL_CASTLE_GROUNDS, None),
    (LEVEL_CASTLE_INSIDE, AREA_LOBBY),
    (LEVEL_CASTLE_INSIDE, AREA_BASEMENT),
    (LEVEL_CASTLE_COURTYARD, None),
    (LEVEL_CASTLE_INSIDE, AREA_UPSTAIRS),
)

# The Bowser courses (17/19/21) and their arenas. Grouped as one class so the
# taxonomy can pin them above the main courses of their region; level-id order
# then puts each course above its own arena (17 < 30, 19 < 33, 21 < 34).
BOWSER_STAGE_LEVELS = frozenset({17, 19, 21, BOWSER_1_ARENA,
                                 BOWSER_2_ARENA, BOWSER_3_ARENA})

# WHAT THE PLAYER CALLS THE THING HE JUST TOUCHED. One event covers all of
# them (`warp_entered`), and calling every one a pipe made the row he needed
# unrecognisable -- live report 2026-08-05, reading his own BOB warp back:
# *"it's a warp not a pipe, but maybe they're the same thing internally -- in
# bowser levels it's a pipe, in every other level it's a warp"*. Exactly his
# rule, and exactly these three courses: the thing that ends a Bowser stage is
# a pipe, and every other in-level teleporter in the game is a warp. The
# ARENAS are not here -- a fight ends on a key, never on a warp.
PIPE_LEVELS = frozenset({LEVEL_BITDW, LEVEL_BITFS, LEVEL_BITS})


def warp_word(level: int | None) -> str:
    """"pipe" or "warp", for the level Mario is standing in."""
    return "pipe" if level in PIPE_LEVELS else "warp"

# Where a course-0 (castle secret) star is GRABBED, for segments that start on
# one. MIPS runs in the basement, both catches. The Toad stars are DELIBERATELY
# ABSENT: their per-star locations are not established anywhere in this
# codebase, and a guessed row would mis-file a segment silently, where a
# missing row files it under "Anywhere" where the user can see and fix it.
# Do not "complete" this table from memory — only from a live check.
CASTLE_SECRET_STAR_AREAS = {3: AREA_BASEMENT, 4: AREA_BASEMENT}  # MIPS 1st/2nd


@lru_cache(maxsize=1)
def world_regions() -> dict[str, str]:
    """Every world node -> the castle-region node it belongs to.

    Cached (review M10): `region_for_node` calls this once per segment, so an
    uncached `GET /api/segments` reran the full BFS ~65 times a request. Safe
    to cache because every input (WORLD_EDGES_*, CASTLE_REGION_NODES) is a
    module constant, never mutated at runtime.

    BFS out from CASTLE_REGION_NODES over the same WORLD_EDGES_* tables the
    builder's dropdown filtering uses, treating one-way edges as undirected
    (a Bowser arena belongs to the region its exit lands in). Region nodes are
    pre-seeded, so the walk never crosses THROUGH one — each place is claimed
    by the region you actually reach it from: BBH by the courtyard, VCUtM by
    the grounds, CotMC by the basement (through HMC).

    A node reachable from two regions at equal distance goes to whichever
    comes first in CASTLE_REGION_NODES — gameflow order, deterministic.
    A wrong or missing edge is fixed in ONE row of WORLD_EDGES_* and both this
    and the dropdown filter re-derive.
    """
    def undirected(edges) -> dict[str, set[str]]:
        adjacency: dict[str, set[str]] = {}
        for node_a, node_b in edges:
            key_a = node_key(*_world_node(node_a))
            key_b = node_key(*_world_node(node_b))
            adjacency.setdefault(key_a, set()).add(key_b)
            adjacency.setdefault(key_b, set()).add(key_a)
        return adjacency

    regions = {node_key(level, area): node_key(level, area)
               for level, area in CASTLE_REGION_NODES}

    def walk(adjacency) -> None:
        frontier = list(regions)
        while frontier:
            current = frontier.pop(0)
            for neighbour in sorted(adjacency.get(current, ())):
                if neighbour in regions:
                    continue
                regions[neighbour] = regions[current]
                frontier.append(neighbour)

    # TWO-WAY FIRST, one-way only for what is still unclaimed (2026-08-02).
    # A one-way row is an EXIT, and an exit says where you come OUT, never
    # where a place belongs. That distinction did not exist while the only
    # one-way rows were the Bowser key cutscenes — an arena has no other
    # castle link, so its exit IS its ownership. `(19, _LOBBY)` broke the tie:
    # BitFS has a real two-way door from the BASEMENT (his spec, 2026-07-23,
    # "the basement region owns BitFS") and now also exits to the lobby, and
    # one undirected pass moved it into the lobby region on gameflow order —
    # renaming its library group and reordering the origin taxonomy, for a
    # topology fix that had nothing to do with either.
    walk(undirected(WORLD_EDGES_TWO_WAY))
    walk(undirected(WORLD_EDGES_ONE_WAY))
    return regions


def region_for_node(key: str | None) -> str | None:
    """Region a node belongs to, or None when it has no place in the castle.

    The one node the BFS cannot answer is a subarea-less castle interior
    ("6", from a `level_enter to=6` with no to_subarea): regions are keyed on
    the three subareas. It resolves to the LOBBY, because every castle entry
    lands there before settling elsewhere — the same transient-lobby behaviour
    detectors/level.py journals and area_changed's `from_transient` flags.
    """
    if key is None:
        return None
    regions = world_regions()
    if key in regions:
        return regions[key]
    level_str, _, area_str = key.partition(":")
    if not area_str and int(level_str) == LEVEL_CASTLE_INSIDE:
        return node_key(LEVEL_CASTLE_INSIDE, AREA_LOBBY)
    return None

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
    # Castle secret stars (COURSE_NONE / gCurrCourseNum 0) — the Toad and MIPS
    # stars, which belong to no course. Ids are the save-file star-flag bit
    # order from the decomp: include/save_file.h defines
    # SAVE_FLAG_COLLECTED_TOAD_STAR_1..3 as (1 << 24..26) and _MIPS_STAR_1/2 as
    # (1 << 27..28), and SAVE_FLAG_TO_STAR_FLAG(x) = (x >> 24) & 0x7F, so the
    # star indices are Toad 0/1/2 and MIPS 3/4. Cross-checked independently:
    # behaviors/mips.inc.c spawns STAR_INDEX_ACT_4 + oBhvParams2ndByte, i.e.
    # 3 + {0, 1} — the same two ids. (Both files quoted from n64decomp/sm64
    # master, fetched 2026-07-24.)
    # VERIFY (live gate): WHICH Toad carries which index. The binding below
    # follows the flag order together with the 12/25/35-star spawn thresholds
    # (the basement Toad spawns first). A live grab of any Toad star settles
    # it — the journal held zero course-0 grabs when this shipped.
    0: ("Toad Star (Basement)", "Toad Star (Upstairs)", "Toad Star (Tippy)",
        "MIPS 1st Star", "MIPS 2nd Star"),
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


def star_count(course_id: int) -> int:
    """Selectable stars per course — THE one home for the 7-star rule.

    Main courses (1-15) have six named stars plus the 100-coin star at
    star_id 6 (star_name owns the naming side of that rule); everything
    else has exactly its STAR_NAMES entries (course 0 has none)."""
    return 7 if 1 <= course_id <= 15 else len(STAR_NAMES.get(course_id, ()))


# gCurrLevelNum -> gCurrCourseNum (course id — the SAME id space as star
# course_ids / gLastCompletedCourseNum, NOT the level-id space; see the trap
# note at CURR_LEVEL). THE level->course registry: each entry pairs a
# LEVEL_NAMES key with its identically-named COURSE_NAMES id (decomp
# levels/course_table.h order). Hub levels (6/16/26 Castle Inside/Grounds/
# Courtyard) and the Bowser fight arenas (30/33/34) have NO course of their
# own and are intentionally ABSENT -> course_for_level returns None, which
# callers read as "not a course stage" (passing through never invalidates an
# active star). Used by the projector to retire a stale active-star target
# when Mario enters a DIFFERENT course (tracking/projection.py). A test keeps
# this consistent with the two name tables (test_addresses.py).
COURSE_BY_LEVEL = {
    9: 1, 24: 2, 12: 3, 5: 4, 4: 5, 7: 6, 22: 7, 8: 8, 23: 9, 10: 10,
    11: 11, 36: 12, 13: 13, 14: 14, 15: 15,            # 15 main courses
    17: 16, 19: 17, 21: 18,                            # Bowser courses
    27: 19, 28: 20, 29: 21, 18: 22, 31: 23, 20: 24,    # slide / caps / WMOTR / aquarium
}


def course_for_level(level: int | None) -> int | None:
    """Course id a level belongs to, or None for hub levels, Bowser fight
    arenas, and unknown ids (see COURSE_BY_LEVEL). None means "not a course
    stage" — callers must not treat it as a course change."""
    return COURSE_BY_LEVEL.get(level)


# The community abbreviation for each main course. Lives HERE rather than in
# links.py (which held it until 2026-08-03) because two unrelated consumers
# need the same fifteen strings: that module builds Ukikipedia/xcams URLs from
# them, and `node_short_label` below writes them on screen. links.py cannot own
# it — it already imports this module, so the dependency only runs one way.
COURSE_ABBREV = {
    1: "BoB", 2: "WF", 3: "JRB", 4: "CCM", 5: "BBH", 6: "HMC", 7: "LLL",
    8: "SSL", 9: "DDD", 10: "SL", 11: "WDW", 12: "TTM", 13: "THI",
    14: "TTC", 15: "RR",
}

# Short forms for the levels COURSE_ABBREV does not reach: the two hubs, the
# three Bowser fight arenas, and the six secret stages whose full LEVEL_NAMES
# entry is a sentence. Spelled the way the seeded corpus already spells them in
# its own segment names ("WF → BitDW", "WF → PSS", "WF → Secret Aquarium"), so
# a step chip and the segment title above it read as the same route.
_SHORT_LEVEL_NAMES = {
    16: "Grounds", 26: "Courtyard",
    17: "BitDW", 19: "BitFS", 21: "BitS",
    BOWSER_1_ARENA: "Bowser 1", BOWSER_2_ARENA: "Bowser 2",
    BOWSER_3_ARENA: "Bowser 3",
    27: "PSS", 28: "MC", 29: "WC", 18: "VC", 31: "WMotR", 20: "Aquarium",
}


def node_short_label(key: str) -> str:
    """`node_label` in its short, route-notation form.

    A castle subarea is already short ("Basement"); a main course takes its
    COURSE_ABBREV; everything else takes `_SHORT_LEVEL_NAMES`, falling back to
    the full name for a level no table names.

    This is a PRESENTATION choice, and worth stating so nobody re-derives it as
    a fitting constraint: the practice card's step track has room for the full
    names (the corpus's longest route needs 386px into 614px at the 850px
    floor — measured 2026-08-03, not estimated). It is short because the step
    track is route notation, and a route reads as "BitFS › Lobby › Upstairs ›
    BitS" beside a card titled "Bowser 2 → BitS". Mixing one sentence-length
    name into a line of codes is what would look broken, which is why
    `tests/test_addresses.py` pins every node in the world graph to a short
    form rather than pinning a width.
    """
    level_str, _, area_str = key.partition(":")
    if area_str:
        return node_label(key)
    level = int(level_str)
    course = COURSE_BY_LEVEL.get(level)
    if course in COURSE_ABBREV:
        return COURSE_ABBREV[course]
    return _SHORT_LEVEL_NAMES.get(level, node_label(key))
