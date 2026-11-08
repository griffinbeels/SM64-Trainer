"""Where a free camera goes in the game's RAM before geo_process_root draws it (M4 lab).

    place(memory, camera, lakitu_address, fov_state_address)

memory is RDRAM in the CPU's big-endian order (a sandbox's, about to run). The camera is (focus,
position) or (focus, position, roll, fov), with None for the frame's own:

- focus and position replace gLakituState's focus and pos, the camera update_graph_node_camera
  hands the scene graph;
- roll (the game's s16 angle) replaces gLakituState.roll, which it copies to the camera node's
  screen roll (the PROJECTION|MUL rotation after the perspective);
- fov (degrees) is drawn as given: sFOVState's FOV function is switched off (fovFunc 0 matches
  no case in geo_camera_fov), its fov set, and its shake amplitude zeroed, so
  perspective->fov is exactly this number on every frame.

A free camera owns all four, so stepping through a FOV ramp or a camera shake never zooms or
tilts the inspected view. Kept free of the emulator so tests can check the bytes it writes.

A fifth item, True, leaves the stage's Mario out of the picture (the Scene viewer's Mario switch,
2026-11-08, from Xander's SM64Scripts): his object's graph node loses GRAPH_RENDER_ACTIVE, so the
game's own geo_process_node_and_siblings skips him and everything his node draws (his shadow, what
he holds). Only this sandbox's copy of RAM changes, never the game.
"""
from __future__ import annotations

import struct

LAKITU_ROLL, LAKITU_FOCUS, LAKITU_POS = 0x7A, 0x80, 0x8C      # struct LakituState
# struct FieldOfViewState: u8 fovFunc; f32 fov; f32 fovOffset (the shake just drawn); f32 shakeAmplitude
FOV_FUNC, FOV_VALUE, FOV_OFFSET, FOV_SHAKE = 0x00, 0x04, 0x08, 0x10
MARIO_OBJ = 0x88                  # struct MarioState: marioObj, his own object (memory/addresses.py notes it)
NODE_FLAGS = 0x02                 # struct GraphNode (an object's first member): s16 flags
GRAPH_RENDER_ACTIVE = 0x0001


def phys(address: int) -> int:
    return address & 0x1FFFFFFF


def hide_mario(memory, mario_states: int) -> bool:
    """Clears GRAPH_RENDER_ACTIVE on the stage Mario's object (gMarioStates.marioObj); False when that
    pointer is not one into RDRAM (no Mario in this frame)."""
    obj = struct.unpack_from(">I", memory, phys(mario_states + MARIO_OBJ))[0]
    if not 0x80000000 <= obj < 0x80800000 - NODE_FLAGS - 2:
        return False
    at = phys(obj + NODE_FLAGS)
    struct.pack_into(">H", memory, at, struct.unpack_from(">H", memory, at)[0] & ~GRAPH_RENDER_ACTIVE)
    return True


def place(memory, camera, lakitu: int, fov_state: int, mario_states: int | None = None) -> None:
    focus, position = camera[0], camera[1]
    roll, fov = (list(camera[2:4]) + [None, None])[:2]
    if len(camera) > 4 and camera[4] and mario_states is not None:
        hide_mario(memory, mario_states)
    struct.pack_into(">3f", memory, phys(lakitu + LAKITU_FOCUS), *focus)
    struct.pack_into(">3f", memory, phys(lakitu + LAKITU_POS), *position)
    if roll is not None:
        struct.pack_into(">h", memory, phys(lakitu + LAKITU_ROLL), roll)
    if fov is not None:
        struct.pack_into(">B", memory, phys(fov_state + FOV_FUNC), 0)
        struct.pack_into(">f", memory, phys(fov_state + FOV_VALUE), fov)
        struct.pack_into(">f", memory, phys(fov_state + FOV_SHAKE), 0.0)
