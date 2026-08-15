# src/sm64_events/core/snapshot.py
"""One coherent read of all game state the detectors need."""
import struct
from dataclasses import dataclass
from datetime import datetime, timezone

from sm64_events.memory import addresses as A
from sm64_events.memory.base import N64Memory
from sm64_events.memory.behaviours import pointer_of, symbol_of
from sm64_events.memory.layout import Layout, layout_for
from sm64_events.memory.objects import ObjectPool

# The window of one object slot that spans ALL THREE identity fields —
# current position, spawn point, behaviour — so naming what Mario touched
# costs one read rather than three. Position joined 2026-08-07 (round 9
# item 7): a scriptless static object's live position IS its placement, and
# it is what lets a specific pole be named at all.
_LANDMARK_BLOCK = A.OBJECT_BEHAVIOR + 4 - A.OBJECT_POS
_BEHAVIOUR_IN_BLOCK = A.OBJECT_BEHAVIOR - A.OBJECT_POS
_HOME_IN_BLOCK = A.OBJECT_HOME_POS - A.OBJECT_POS
_POINTERS_AT_OFF = min(A.MARIO_OBJECT_POINTER_OFFS)
_POINTERS_SIZE = max(A.MARIO_OBJECT_POINTER_OFFS) + 4 - _POINTERS_AT_OFF
# Every field the reader dereferences. behaviour_base is deliberately absent:
# without it a symbol degrades to `ptr_xxxxxxxx` (memory/behaviours.py) and
# nothing else is lost, so a JP layout can be read before its base is known.
_REQUIRED_FIELDS = ("global_timer", "mario_struct", "curr_level", "curr_area",
                    "last_completed_course", "last_completed_star",
                    "pending_warp_op", "delayed_warp_timer", "warp_dest",
                    "object_pool", "usamune_overall", "usamune_star_result")


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
    behaviour: int       # the RAM pointer this ROM gives the behaviour (evidence)
    symbol: str          # the decomp symbol — THE identity (memory/behaviours.py)
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
    # A dialog action's own sub-state (MARIO_ACTION_STATE) -- 0 outside a
    # dialog action (the field the game itself zeroes on every action
    # change). `moment.py`'s textbox gate reads this to find the frame the
    # box actually OPENS, not the frame Mario started turning toward the
    # NPC -- addresses.BOX_OPENS_AT_STATE.
    mario_action_state: int = 0
    igt_result: int = 0    # Usamune final star time, written at the grab
                           # (USAMUNE_STAR_RESULT); 0 before the first grab
    curr_level: int = 0    # gCurrLevelNum: LEVEL ids (WF=24, SSL=8...), NOT course ids — see addresses.py trap note
    particle_flags: int = 0  # Mario particleFlags, re-zeroed each frame; PARTICLE_DUST corroborates dive-slide frames
    curr_area: int = 0     # gCurrAreaIndex: per-level area (castle lobby/upstairs/basement) — see addresses.py
    pending_warp_op: int = 0  # sDelayedWarpOp; WARP_OP_WARP_FLOOR = void-out death pending (death.py)
    # sDelayedWarpTimer — the countdown beside that op, and the only thing
    # that tells a warp INITIATING apart from one being CANCELLED, since both
    # zero the op. A ride that runs its course reaches 0 with the op still
    # armed; a ride killed by a reset has both zeroed together with frames
    # still on the clock (warp.py's cancel branch, probe 2026-08-11).
    delayed_warp_timer: int = 0
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
    landmark_behaviour: int = 0   # 0 = nothing engaged this frame (the pointer, evidence)
    landmark_symbol: str = ""     # "" = nothing engaged; else "bhvDoor" / "ptr_xxxxxxxx"
    landmark_home: tuple[float, float, float] = (0.0, 0.0, 0.0)
    landmark_pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # Watched-object states for the caused-moment detector — a switch is
    # pressed by its OWN behaviour code watching Mario's position and never
    # reaches the engagement pointers above (measured, round 10), so what the
    # player caused has to be read off the object.
    caused: tuple = ()


class SnapshotReader:
    """One coherent read of the game, over ONE version's layout.

    `layout` defaults to `layout_for(version)` and `version` to "us", so every
    caller that never cared which ROM is running (`SnapshotReader(mem)`) still
    reads exactly what it did before 2026-08-15. A layout missing any field
    the reader dereferences is refused HERE, naming the field, so a JP server
    can never silently read address 0.
    """

    def __init__(self, mem: N64Memory, layout: Layout | None = None,
                 version: str = "us"):
        self._mem = mem
        self.version = version
        self.layout = layout if layout is not None else layout_for(version)
        self.layout.require(*_REQUIRED_FIELDS)
        self._pool = ObjectPool(self.layout)
        L = self.layout
        self._at = {
            "global_timer": L.global_timer,
            "mario_action": L.mario_struct + A.MARIO_ACTION_OFF,
            "mario_action_timer": L.mario_struct + A.MARIO_ACTION_TIMER_OFF,
            "mario_action_state": L.mario_struct + A.MARIO_ACTION_STATE_OFF,
            "num_stars": L.mario_struct + A.MARIO_NUM_STARS_OFF,
            "particle_flags": L.mario_struct + A.MARIO_PARTICLE_FLAGS_OFF,
            "pointers_block": L.mario_struct + _POINTERS_AT_OFF,
            "last_completed_course": L.last_completed_course,
            "last_completed_star": L.last_completed_star,
            "igt_overall": L.usamune_overall,
            "igt_result": L.usamune_star_result,
            "curr_level": L.curr_level,
            "curr_area": L.curr_area,
            "pending_warp_op": L.pending_warp_op,
            "delayed_warp_timer": L.delayed_warp_timer,
            "warp_dest_type": L.warp_dest + A.WARP_DEST_TYPE_OFF,
            "warp_dest_level": L.warp_dest + A.WARP_DEST_LEVEL_OFF,
            "warp_dest_area": L.warp_dest + A.WARP_DEST_AREA_OFF,
            "warp_dest_node": L.warp_dest + A.WARP_DEST_NODE_OFF,
        }
        # The watched behaviours, as THIS ROM's pointers -> their symbols. A
        # symbol this ROM lacks, or a base not yet known, simply watches
        # nothing for that row rather than failing.
        self._caused_by_pointer: dict[int, str] = {}
        for symbol in A.CAUSED_BEHAVIOURS:
            pointer = pointer_of(version, symbol, base=L.behaviour_base)
            if pointer is not None:
                self._caused_by_pointer[pointer] = symbol

    def _symbol(self, pointer: int) -> str:
        return symbol_of(self.version, pointer, base=self.layout.behaviour_base)

    def _engaged_object(self):
        """(behaviour, spawn point, current position) of the object Mario is
        engaged with.

        Zeroes when he is engaged with nothing, or when the pointer does not
        land on a pool SLOT BOUNDARY — the same test that discovered which
        pointers these are, kept here so a torn read cannot name a landmark out
        of the middle of some other object.
        """
        block = self._mem.read_block(self._at["pointers_block"], _POINTERS_SIZE)
        for offset in A.MARIO_OBJECT_POINTER_OFFS:
            at = offset - _POINTERS_AT_OFF
            pointer = int.from_bytes(block[at:at + 4], "big")
            located = self._pool.pool_slot(pointer)
            if located is None or located[1] != 0:
                continue
            found = self._mem.read_block(
                self._pool.slot_address(located[0], A.OBJECT_POS), _LANDMARK_BLOCK)
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
        pool = self._mem.read_block(self._pool.base,
                                    A.OBJECT_COUNT * A.OBJECT_SIZE)
        found = []
        for slot in range(A.OBJECT_COUNT):
            base = slot * A.OBJECT_SIZE
            behaviour = int.from_bytes(
                pool[base + A.OBJECT_BEHAVIOR:base + A.OBJECT_BEHAVIOR + 4],
                "big")
            symbol = self._caused_by_pointer.get(behaviour)
            if symbol is None:
                continue
            found.append(CausedState(
                slot=slot, behaviour=behaviour, symbol=symbol,
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
        m, at = self._mem, self._at
        landmark_behaviour, landmark_home, landmark_pos = self._engaged_object()
        return GameSnapshot(
            wall_time_utc=datetime.now(timezone.utc),
            global_timer=m.read_u32(at["global_timer"]),
            mario_action=m.read_u32(at["mario_action"]),
            mario_action_timer=m.read_u16(at["mario_action_timer"]),
            mario_action_state=m.read_u16(at["mario_action_state"]),
            num_stars=m.read_s16(at["num_stars"]),
            last_completed_course=m.read_s8(at["last_completed_course"]),
            last_completed_star=m.read_s8(at["last_completed_star"]),
            igt_overall=m.read_u16(at["igt_overall"]),
            igt_result=m.read_u16(at["igt_result"]),
            curr_level=m.read_s16(at["curr_level"]),
            particle_flags=m.read_u32(at["particle_flags"]),
            curr_area=m.read_s16(at["curr_area"]),
            pending_warp_op=m.read_u16(at["pending_warp_op"]),
            delayed_warp_timer=m.read_s16(at["delayed_warp_timer"]),
            warp_dest_type=m.read_u8(at["warp_dest_type"]),
            warp_dest_level=m.read_u8(at["warp_dest_level"]),
            warp_dest_area=m.read_u8(at["warp_dest_area"]),
            warp_dest_node=m.read_u8(at["warp_dest_node"]),
            landmark_behaviour=landmark_behaviour,
            landmark_symbol=(self._symbol(landmark_behaviour)
                             if landmark_behaviour else ""),
            landmark_home=landmark_home,
            landmark_pos=landmark_pos,
            caused=self._caused_states(),
        )
