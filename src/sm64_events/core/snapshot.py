# src/sm64_events/core/snapshot.py
"""One coherent read of all game state the detectors need."""
import struct
from dataclasses import dataclass
from datetime import datetime, timezone

from sm64_events.memory import addresses as A
from sm64_events.memory.base import N64Memory
from sm64_events.memory.objects import pool_slot, slot_address

# The window of one object slot that spans ALL THREE identity fields —
# current position, spawn point, behaviour — so naming what Mario touched
# costs one read rather than three. Position joined 2026-08-07 (round 9
# item 7): a scriptless static object's live position IS its placement, and
# it is what lets a specific pole be named at all.
_LANDMARK_BLOCK = A.OBJECT_BEHAVIOR + 4 - A.OBJECT_POS
_BEHAVIOUR_IN_BLOCK = A.OBJECT_BEHAVIOR - A.OBJECT_POS
_HOME_IN_BLOCK = A.OBJECT_HOME_POS - A.OBJECT_POS
_POINTERS_AT = min(A.MARIO_OBJECT_POINTERS)
_POINTERS_SIZE = max(A.MARIO_OBJECT_POINTERS) + 4 - _POINTERS_AT


@dataclass(frozen=True)
class CausedState:
    """One watched object's own state this tick — what a CAUSED moment diffs.

    Only behaviours in `addresses.CAUSED_BEHAVIOURS` are carried (a handful of
    slots per level), because the pool's full change stream is mostly scenery:
    his 2026-08-07 capture logged 1,386 changes in one session, 9 of them
    player gestures. `health` rides along un-read by any shipped rule — it is
    one of the three fields the pool probe watches and the next candidate rows
    (bosses count hits on it) will need it; carrying it now spares those rows
    a snapshot-contract change.
    """

    slot: int
    behaviour: int
    action: int          # s32 oAction — THE legible field (addresses.py)
    health: int          # s32 oHealth — hitbox arming/proximity, NOT defeats
    home: tuple[float, float, float]
    pos: tuple[float, float, float]


@dataclass(frozen=True)
class GameSnapshot:
    wall_time_utc: datetime
    global_timer: int
    mario_action: int
    mario_action_timer: int
    num_stars: int
    last_completed_course: int  # 1-based; 0 = castle secret star OR never set
    last_completed_star: int    # 1-based
    # Defaulted fields (added after goal one; defaults keep old call sites valid).
    igt_overall: int = 0   # Usamune running overall star time (USAMUNE_OVERALL)
    igt_result: int = 0    # Usamune final star time, written at the grab
                           # (USAMUNE_STAR_RESULT); 0 before the first grab
    curr_level: int = 0    # gCurrLevelNum: LEVEL ids (WF=24, SSL=8...), NOT course ids — see addresses.py trap note
    particle_flags: int = 0  # Mario particleFlags, re-zeroed each frame; PARTICLE_DUST corroborates dive-slide frames
    curr_area: int = 0     # gCurrAreaIndex: per-level area (castle lobby/upstairs/basement) — see addresses.py
    pending_warp_op: int = 0  # sDelayedWarpOp; WARP_OP_WARP_FLOOR = void-out death pending (death.py)
    # sWarpDest — where the pending warp leads. A painting/portal fills this AT
    # the touch frame, which is what lets warp.py publish with no wait; a pipe
    # fills it 20 frames later. All four bytes, because freshness is tested by
    # the struct CHANGING and one byte alone would miss same-level rewrites.
    warp_dest_type: int = 0   # WARP_TYPE_*; 0 = NOT_WARPING
    warp_dest_level: int = 0
    warp_dest_area: int = 0
    warp_dest_node: int = 0
    # WHICH thing Mario is engaged with — see core/landmark.py. Two extra reads a
    # tick, not five: one block over gMarioState's three object pointers, and
    # one over the chosen object that spans its spawn point and its behaviour.
    # Both LINGER after the interaction ends — which is why a moment's
    # landmark is settled from the poll AFTER its action edge (round 9 item 4:
    # the edge poll can still read the PREVIOUS engagement, and his first WF
    # tree grab named Mario's own spawn marker that way).
    landmark_behaviour: int = 0   # 0 = nothing engaged this frame
    landmark_home: tuple[float, float, float] = (0.0, 0.0, 0.0)
    landmark_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # Watched-object states for the caused-moment detector — a switch is
    # pressed by its OWN behaviour code watching Mario's position and never
    # reaches the engagement pointers above (measured, round 10), so what the
    # player caused has to be read off the object.
    caused: tuple = ()


class SnapshotReader:
    def __init__(self, mem: N64Memory):
        self._mem = mem

    def _engaged_object(self):
        """(behaviour, spawn point, current position) of the object Mario is
        engaged with.

        Zeroes when he is engaged with nothing, or when the pointer does not
        land on a pool SLOT BOUNDARY — the same test that discovered which
        pointers these are, kept here so a torn read cannot name a landmark out
        of the middle of some other object.
        """
        block = self._mem.read_block(_POINTERS_AT, _POINTERS_SIZE)
        for address in A.MARIO_OBJECT_POINTERS:
            at = address - _POINTERS_AT
            pointer = int.from_bytes(block[at:at + 4], "big")
            located = pool_slot(pointer)
            if located is None or located[1] != 0:
                continue
            found = self._mem.read_block(
                slot_address(located[0], A.OBJECT_POS), _LANDMARK_BLOCK)
            behaviour = int.from_bytes(
                found[_BEHAVIOUR_IN_BLOCK:_BEHAVIOUR_IN_BLOCK + 4], "big")
            return (behaviour,
                    struct.unpack_from(">fff", found, _HOME_IN_BLOCK),
                    struct.unpack_from(">fff", found, 0))
        return 0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)

    def _caused_states(self) -> tuple:
        """Every watched behaviour's state, in ONE pool read.

        One `ReadProcessMemory` of the whole pool then a local behaviour scan,
        the same shape `tools/probe_objects.py --pool` runs live at 120 Hz
        beside his sessions — 240 separate reads would cost more than the
        poll interval, one block read costs ~a tenth of a millisecond.
        """
        pool = self._mem.read_block(A.OBJECT_POOL,
                                    A.OBJECT_COUNT * A.OBJECT_SIZE)
        found = []
        for slot in range(A.OBJECT_COUNT):
            base = slot * A.OBJECT_SIZE
            behaviour = int.from_bytes(
                pool[base + A.OBJECT_BEHAVIOR:base + A.OBJECT_BEHAVIOR + 4],
                "big")
            if behaviour not in A.CAUSED_POINTERS:
                continue
            found.append(CausedState(
                slot=slot, behaviour=behaviour,
                action=int.from_bytes(
                    pool[base + A.OBJECT_ACTION:base + A.OBJECT_ACTION + 4],
                    "big", signed=True),
                health=int.from_bytes(
                    pool[base + A.OBJECT_HEALTH:base + A.OBJECT_HEALTH + 4],
                    "big", signed=True),
                home=struct.unpack_from(">fff", pool,
                                        base + A.OBJECT_HOME_POS),
                pos=struct.unpack_from(">fff", pool, base + A.OBJECT_POS)))
        return tuple(found)

    def read(self) -> GameSnapshot:
        m = self._mem
        landmark_behaviour, landmark_home, landmark_pos = self._engaged_object()
        return GameSnapshot(
            wall_time_utc=datetime.now(timezone.utc),
            global_timer=m.read_u32(A.GLOBAL_TIMER),
            mario_action=m.read_u32(A.MARIO_ACTION),
            mario_action_timer=m.read_u16(A.MARIO_ACTION_TIMER),
            num_stars=m.read_s16(A.MARIO_NUM_STARS),
            last_completed_course=m.read_s8(A.LAST_COMPLETED_COURSE),
            last_completed_star=m.read_s8(A.LAST_COMPLETED_STAR),
            igt_overall=m.read_u16(A.USAMUNE_OVERALL),
            igt_result=m.read_u16(A.USAMUNE_STAR_RESULT),
            curr_level=m.read_s16(A.CURR_LEVEL),
            particle_flags=m.read_u32(A.MARIO_PARTICLE_FLAGS),
            curr_area=m.read_s16(A.CURR_AREA),
            pending_warp_op=m.read_u16(A.PENDING_WARP_OP),
            warp_dest_type=m.read_u8(A.WARP_DEST_TYPE),
            warp_dest_level=m.read_u8(A.WARP_DEST_LEVEL),
            warp_dest_area=m.read_u8(A.WARP_DEST_AREA),
            warp_dest_node=m.read_u8(A.WARP_DEST_NODE),
            landmark_behaviour=landmark_behaviour,
            landmark_home=landmark_home,
            landmark_pos=landmark_pos,
            caused=self._caused_states(),
        )
