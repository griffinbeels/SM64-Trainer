# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""Set focus's point (M4 lab): where one picture's display list drew one pixel, found on the CPU.

    walked = walk(ram, task_data_ptr)          # every triangle that writes depth, in camera space
    point = nearest(walked, u, v)              # the pixel at (u, v) of the picture, 0..1 from top left

The renderer cannot answer this. GLideN64 built for Project64's plugin API runs every call on
its own RSP thread (PluginAPI.h defines RSPTHREAD for the Zilmar API), and that thread owns the
GL context, so viewer_host.c's plugin thread has no context to read depth from: measured
2026-10-25, a depth read there found no current context on every pick while the same host drew
the pictures. So the pick walks the same Fast3D list the renderer drew: the segment table, the
matrix stacks, the vertex buffer, and the geometry and render modes that decide whether a
triangle writes depth and which faces it culls. It casts the pixel's ray against every triangle
drawn under the perspective projection with the z-buffer on and depth updates on, and the
nearest hit is where the depth buffer holds that pixel, up to what a triangle walk cannot see:
texels an alpha test discards (a coin's corners, a tree's edges) still count as their quad.

Points come back in the camera's own space, the game's lookat axes (x right, y up, z toward the
viewer): the page owns the camera's world axes and turns a point into world coordinates, so the
camera's math has one owner. `ram` is RDRAM in the CPU's big-endian order (8 MiB), as the
evaluator's views hold it; ram_from_runs builds one from a recorded frame's closure.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

RDRAM_BYTES = 8 << 20
MASK = 0x7FFFFF
# Fast3D (F3D), SM64 US/JP's microcode
G_MTX, G_MOVEMEM, G_VTX, G_DL = 0x01, 0x03, 0x04, 0x06
G_TRI1, G_POPMTX, G_MOVEWORD = 0xBF, 0xBD, 0xBC
G_SETOTHERMODE_L, G_ENDDL, G_SETGEOMETRYMODE, G_CLEARGEOMETRYMODE = 0xB9, 0xB8, 0xB7, 0xB6
G_RDPSETOTHERMODE = 0xEF
G_MV_VIEWPORT, G_MW_SEGMENT = 0x80, 0x06
G_MTX_PROJECTION, G_MTX_LOAD, G_MTX_PUSH = 0x01, 0x02, 0x04
G_ZBUFFER, G_CULL_FRONT, G_CULL_BACK = 0x1, 0x1000, 0x2000
Z_UPD = 0x20                   # othermode L: the render mode's depth update
MAX_COMMANDS = 1 << 20
MAX_DEPTH = 32                 # display list call nesting (the RSP's own limit is 10)
MAX_MATRICES = 32              # modelview stack (F3D's is 10)


@dataclass
class Walked:
    """A list's depth-writing perspective triangles in camera space (n, 3, 3), what each culls
    (0, G_CULL_FRONT, G_CULL_BACK or both), the world projection (row vectors: clip = view @
    projection, roll included) and the viewport (vscale[4], vtrans[4])."""
    triangles: np.ndarray
    culls: np.ndarray
    projection: np.ndarray | None
    viewport: tuple | None


def ram_from_runs(runs, run_bytes: bytes) -> bytearray:
    """A recorded frame's closure ((begin, end) storage ranges, bytes in Project64's word order)
    as big-endian RDRAM, zero elsewhere: everything the renderer read to draw it."""
    ram, at = bytearray(RDRAM_BYTES), 0
    for begin, end in runs:
        if not 0 <= begin <= end <= RDRAM_BYTES or (begin | end) & 3:
            raise ValueError("a closure run lies outside RDRAM")
        ram[begin:end] = np.frombuffer(run_bytes, "<u4", (end - begin) // 4, at).astype(">u4").tobytes()
        at += end - begin
    return ram


def _matrix(ram, address: int) -> np.ndarray:
    """N64 Mtx: 16 s16 integer parts, then 16 u16 fractions, row-major."""
    if address & 3 or address > len(ram) - 64:
        raise ValueError("a matrix lies outside RDRAM")
    whole = np.frombuffer(ram, ">i2", 16, address).astype(np.float64)
    part = np.frombuffer(ram, ">u2", 16, address + 32).astype(np.float64)
    return ((whole * 65536 + part) / 65536).reshape(4, 4)


class _Walker:
    """The renderer's state while a list runs, one Fast3D command at a time (COMMANDS)."""

    def __init__(self, ram):
        self.ram, self.segment = ram, [0] * 16
        self.modelview, self.saved = np.eye(4), []
        self.projection, self.perspective, self.world, self.viewport = np.eye(4), False, None, None
        self.geometry = self.other_l = 0
        self.vertices = np.zeros((16, 3))
        self.triangles, self.culls = [], []

    def moveword(self, w0, w1, _target):
        if w0 & 0xFF == G_MW_SEGMENT:
            self.segment[(w0 >> 10) & 0xF] = w1 & 0xFFFFFF

    def movemem(self, w0, _w1, target):
        if (w0 >> 16) & 0xFF == G_MV_VIEWPORT and target <= len(self.ram) - 16:
            self.viewport = struct.unpack_from(">8h", self.ram, target)

    def matrix(self, w0, _w1, target):
        param, loaded = (w0 >> 16) & 0xFF, _matrix(self.ram, target)
        if param & G_MTX_PROJECTION:             # SM64's screen roll arrives as a MUL after the LOAD
            self.projection = loaded if param & G_MTX_LOAD else loaded @ self.projection
            if param & G_MTX_LOAD:
                self.perspective = abs(loaded[2, 3] + 1.0) < 1e-3
            if self.perspective:
                self.world = self.projection
            return
        if param & G_MTX_PUSH and len(self.saved) < MAX_MATRICES:
            self.saved.append(self.modelview)
        self.modelview = loaded if param & G_MTX_LOAD else loaded @ self.modelview

    def pop(self, _w0, _w1, _target):
        if self.saved:
            self.modelview = self.saved.pop()

    def vertex(self, w0, _w1, target):
        count, first = ((w0 >> 20) & 0xF) + 1, (w0 >> 16) & 0xF
        if target > len(self.ram) - 16 * count:
            raise ValueError("vertices lie outside RDRAM")
        raw = np.frombuffer(self.ram, ">i2", 8 * count, target).reshape(count, 8)[:, :3].astype(np.float64)
        moved = np.hstack([raw, np.ones((count, 1))]) @ self.modelview
        count = min(count, 16 - first)
        self.vertices[first:first + count] = moved[:count, :3]

    def triangle(self, _w0, w1, _target):
        if self.perspective and self.geometry & G_ZBUFFER and self.other_l & Z_UPD:
            corners = [((w1 >> shift) & 0xFF) // 10 for shift in (16, 8, 0)]
            if max(corners) < 16:
                self.triangles.append(self.vertices[corners].copy())
                self.culls.append(self.geometry & (G_CULL_FRONT | G_CULL_BACK))

    def set_geometry(self, _w0, w1, _target):
        self.geometry |= w1

    def clear_geometry(self, _w0, w1, _target):
        self.geometry &= ~w1

    def other_mode_l(self, w0, w1, _target):
        mask = ((1 << (w0 & 0xFF)) - 1) << ((w0 >> 8) & 0xFF)
        self.other_l = (self.other_l & ~mask) | (w1 & mask)

    def other_modes(self, _w0, w1, _target):
        self.other_l = w1

    COMMANDS = {G_MOVEWORD: moveword, G_MOVEMEM: movemem, G_MTX: matrix, G_POPMTX: pop, G_VTX: vertex,
                G_TRI1: triangle, G_SETGEOMETRYMODE: set_geometry, G_CLEARGEOMETRYMODE: clear_geometry,
                G_SETOTHERMODE_L: other_mode_l, G_RDPSETOTHERMODE: other_modes}

    def run(self, display_list: int) -> Walked:
        word, stack = struct.Struct(">II"), [display_list & MASK]
        for _ in range(MAX_COMMANDS):
            at = stack[-1]
            if at > len(self.ram) - 8:
                raise ValueError("the display list leaves RDRAM")
            w0, w1 = word.unpack_from(self.ram, at)
            stack[-1] += 8
            op = w0 >> 24
            target = (self.segment[(w1 >> 24) & 0xF] + (w1 & 0xFFFFFF)) & MASK
            if op == G_DL:                       # a call, or a branch that never returns
                if (w0 >> 16) & 0xFF:
                    stack[-1] = target
                elif len(stack) == MAX_DEPTH:
                    raise ValueError("display lists nest too deep")
                else:
                    stack.append(target)
            elif op == G_ENDDL:
                stack.pop()
                if not stack:
                    return Walked(np.array(self.triangles).reshape(-1, 3, 3), np.array(self.culls, dtype=np.int64),
                                  self.world, self.viewport)
            elif op in self.COMMANDS:
                self.COMMANDS[op](self, w0, w1, target)
        raise ValueError("the display list never ends")


def walk(ram, display_list: int) -> Walked:
    """Every triangle the list at `display_list` draws under a perspective projection with the
    z-buffer and depth updates on, in camera space; ValueError for a list that leaves RDRAM or
    never ends."""
    return _Walker(ram).run(display_list)


def fov_of(projection) -> float:
    """The vertical field of view, in degrees, that a perspective projection draws (libultra's
    guPerspective: column 1 holds cot(fov / 2), turned by any screen roll multiplied in)."""
    return float(np.degrees(2 * np.arctan(1 / np.hypot(projection[0, 1], projection[1, 1]))))


def ray(walked: Walked, u: float, v: float) -> tuple[np.ndarray, np.ndarray] | None:
    """The pixel at (u, v) (0..1 across and down the picture) as camera-space points on the near
    and far planes. The viewport covers the picture, as SM64's does: (vscale + vtrans) / 4 is
    the frame's size, and the picture is that frame scaled."""
    if walked.projection is None or walked.viewport is None:
        return None
    vscale, vtrans = walked.viewport[:4], walked.viewport[4:]
    if not (vscale[0] and vscale[1]):
        return None
    across, down = u * (vscale[0] + vtrans[0]) / 4, v * (vscale[1] + vtrans[1]) / 4
    x, y = (across - vtrans[0] / 4) / (vscale[0] / 4), (vtrans[1] / 4 - down) / (vscale[1] / 4)
    try:
        inverse = np.linalg.inv(walked.projection)
    except np.linalg.LinAlgError:
        return None
    ends = np.array([[x, y, -1.0, 1.0], [x, y, 1.0, 1.0]]) @ inverse
    return ends[0, :3] / ends[0, 3], ends[1, :3] / ends[1, 3]


def nearest(walked: Walked, u: float, v: float) -> list[float] | None:
    """The nearest point the list drew depth at pixel (u, v), in camera space, or None where it
    drew none (sky, or past the far plane). A face pointing away from the camera (clockwise on
    screen, the order N64 culls as a back face) is skipped when its culling mode says so."""
    ends = ray(walked, u, v)
    if ends is None or not len(walked.triangles):
        return None
    origin, direction = ends[0], ends[1] - ends[0]
    a, b, c = walked.triangles[:, 0], walked.triangles[:, 1], walked.triangles[:, 2]
    edge1, edge2 = b - a, c - a
    p = np.cross(direction, edge2)
    det = np.einsum("ij,ij->i", edge1, p)
    usable = np.abs(det) > 1e-12
    inverse = np.where(usable, 1.0 / np.where(usable, det, 1.0), 0.0)
    offset = origin - a
    s = np.einsum("ij,ij->i", offset, p) * inverse
    q = np.cross(offset, edge1)
    t = np.einsum("j,ij->i", direction, q) * inverse
    along = np.einsum("ij,ij->i", edge2, q) * inverse
    front = det > 0                     # counter-clockwise from the camera
    culled = np.where(front, walked.culls & G_CULL_FRONT, walked.culls & G_CULL_BACK) != 0
    hit = usable & ~culled & (s >= 0) & (t >= 0) & (s + t <= 1) & (along >= 0) & (along <= 1)
    if not hit.any():
        return None
    nearest_hit = along[hit].min()
    return [float(value) for value in origin + nearest_hit * direction]
