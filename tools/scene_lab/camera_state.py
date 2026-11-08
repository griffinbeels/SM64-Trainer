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
he holds). And every moved camera draws Mario's most detailed model whatever its distance
(full_detail), as Xander's playback forces. Only this sandbox's copy of RAM changes, never the game.
"""
from __future__ import annotations

import struct

LAKITU_ROLL, LAKITU_FOCUS, LAKITU_POS = 0x7A, 0x80, 0x8C      # struct LakituState
# struct FieldOfViewState: u8 fovFunc; f32 fov; f32 fovOffset (the shake just drawn); f32 shakeAmplitude
FOV_FUNC, FOV_VALUE, FOV_OFFSET, FOV_SHAKE = 0x00, 0x04, 0x08, 0x10
MARIO_OBJ = 0x88                  # struct MarioState: marioObj, his own object (memory/addresses.py notes it)
# struct GraphNode: s16 type, s16 flags, prev, next, parent, children (an object's first member)
NODE_FLAGS, NODE_NEXT, NODE_CHILDREN = 0x02, 0x08, 0x10
GRAPH_RENDER_ACTIVE = 0x0001
SHARED_CHILD = 0x14               # struct GraphNodeObject: sharedChild, the root of its model's graph
GRAPH_NODE_TYPE_LEVEL_OF_DETAIL = 0x00B
LOD_MIN, LOD_BYTES = 0x14, 0x18   # struct GraphNodeLevelOfDetail: s16 minDistance, maxDistance after the node
MAX_NODES = 4096                  # a model's graph is a few hundred nodes; a broken one never loops


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


def _node(memory, address: int) -> int | None:
    """`address` as an RDRAM offset when it points at a whole graph node, else None."""
    return phys(address) if 0x80000000 <= address < 0x80800000 - LOD_BYTES and not address & 3 else None


def full_detail(memory, mario_states: int) -> int:
    """Mario's model drawn at its most detailed whatever the camera's distance. The game picks one of
    his models by distance (GEO_RENDER_RANGE: measured on the BoB package 2026-11-08, 752 triangles
    nearer than 600 units, 514 to about 1600, 212 beyond), so a far free camera or a long lens drew a
    low-poly Mario. Among each set of sibling level-of-detail nodes in his object's graph, the one for
    the nearest camera now covers every distance and the others none. Returns how many nodes changed;
    0 without a Mario. Only this sandbox's RAM changes."""
    obj = struct.unpack_from(">I", memory, phys(mario_states + MARIO_OBJ))[0]
    root = _node(memory, obj) and struct.unpack_from(">I", memory, phys(obj + SHARED_CHILD))[0]
    if not root or _node(memory, root) is None:
        return 0
    changed, seen, parents = 0, set(), [root]
    while parents and len(seen) < MAX_NODES:
        first = struct.unpack_from(">I", memory, phys(parents.pop()) + NODE_CHILDREN)[0]
        ring, at = [], first
        while _node(memory, at) is not None and at not in seen and len(seen) < MAX_NODES:
            seen.add(at)
            ring.append(at)
            at = struct.unpack_from(">I", memory, phys(at) + NODE_NEXT)[0]
            if at == first:
                break
        parents.extend(ring)
        lods = [node for node in ring if struct.unpack_from(">h", memory, phys(node))[0] == GRAPH_NODE_TYPE_LEVEL_OF_DETAIL]
        if len(lods) > 1:
            nearest = min(lods, key=lambda node: struct.unpack_from(">h", memory, phys(node) + LOD_MIN)[0])
            for node in lods:
                struct.pack_into(">hh", memory, phys(node) + LOD_MIN, *((-32768, 32767) if node == nearest else (32767, 32767)))
                changed += 1
    return changed


def place(memory, camera, lakitu: int, fov_state: int, mario_states: int | None = None) -> None:
    focus, position = camera[0], camera[1]
    roll, fov = (list(camera[2:4]) + [None, None])[:2]
    if mario_states is not None:
        full_detail(memory, mario_states)
        if len(camera) > 4 and camera[4]:
            hide_mario(memory, mario_states)
    struct.pack_into(">3f", memory, phys(lakitu + LAKITU_FOCUS), *focus)
    struct.pack_into(">3f", memory, phys(lakitu + LAKITU_POS), *position)
    if roll is not None:
        struct.pack_into(">h", memory, phys(lakitu + LAKITU_ROLL), roll)
    if fov is not None:
        struct.pack_into(">B", memory, phys(fov_state + FOV_FUNC), 0)
        struct.pack_into(">f", memory, phys(fov_state + FOV_VALUE), fov)
        struct.pack_into(">f", memory, phys(fov_state + FOV_SHAKE), 0.0)
