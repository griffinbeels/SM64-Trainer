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
"""
from __future__ import annotations

import struct

LAKITU_ROLL, LAKITU_FOCUS, LAKITU_POS = 0x7A, 0x80, 0x8C      # struct LakituState
# struct FieldOfViewState: u8 fovFunc; f32 fov; f32 fovOffset (the shake just drawn); f32 shakeAmplitude
FOV_FUNC, FOV_VALUE, FOV_OFFSET, FOV_SHAKE = 0x00, 0x04, 0x08, 0x10


def phys(address: int) -> int:
    return address & 0x1FFFFFFF


def place(memory, camera, lakitu: int, fov_state: int) -> None:
    focus, position = camera[0], camera[1]
    roll, fov = (list(camera[2:4]) + [None, None])[:2]
    struct.pack_into(">3f", memory, phys(lakitu + LAKITU_FOCUS), *focus)
    struct.pack_into(">3f", memory, phys(lakitu + LAKITU_POS), *position)
    if roll is not None:
        struct.pack_into(">h", memory, phys(lakitu + LAKITU_ROLL), roll)
    if fov is not None:
        struct.pack_into(">B", memory, phys(fov_state + FOV_FUNC), 0)
        struct.pack_into(">f", memory, phys(fov_state + FOV_VALUE), fov)
        struct.pack_into(">f", memory, phys(fov_state + FOV_SHAKE), 0.0)
