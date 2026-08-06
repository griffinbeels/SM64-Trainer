# src/sm64_events/core/snapshot.py
"""One coherent read of all game state the detectors need."""
import struct
from dataclasses import dataclass
from datetime import datetime, timezone

from sm64_events.memory import addresses as A
from sm64_events.memory.base import N64Memory
from sm64_events.memory.objects import pool_slot, slot_address

# The window of one object slot that spans BOTH identity fields, so naming what
# Mario touched costs one read rather than two.
_LANDMARK_BLOCK = A.OBJECT_BEHAVIOR + 4 - A.OBJECT_HOME_POS
_BEHAVIOUR_IN_BLOCK = A.OBJECT_BEHAVIOR - A.OBJECT_HOME_POS
_POINTERS_AT = min(A.MARIO_OBJECT_POINTERS)
_POINTERS_SIZE = max(A.MARIO_OBJECT_POINTERS) + 4 - _POINTERS_AT


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
    # Both linger after the interaction ends, which is harmless because a
    # moment reads them on its ACTION EDGE.
    landmark_behaviour: int = 0   # 0 = nothing engaged this frame
    landmark_home: tuple[float, float, float] = (0.0, 0.0, 0.0)


class SnapshotReader:
    def __init__(self, mem: N64Memory):
        self._mem = mem

    def _engaged_object(self) -> tuple[int, tuple[float, float, float]]:
        """(behaviour, spawn point) of the object Mario is engaged with.

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
                slot_address(located[0], A.OBJECT_HOME_POS), _LANDMARK_BLOCK)
            behaviour = int.from_bytes(
                found[_BEHAVIOUR_IN_BLOCK:_BEHAVIOUR_IN_BLOCK + 4], "big")
            return behaviour, struct.unpack(">fff", found[:12])
        return 0, (0.0, 0.0, 0.0)

    def read(self) -> GameSnapshot:
        m = self._mem
        landmark_behaviour, landmark_home = self._engaged_object()
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
        )
