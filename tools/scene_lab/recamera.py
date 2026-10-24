"""E2 probe: re-view one recorded graphics task from another camera.

    recamera.py <frame_dir> <out_dir> [--yaw DEG] [--pitch DEG] [--pivot UNITS]

SM64 loads every world display list with a G_MTX MODELVIEW|LOAD whose matrix
is world-to-view (geo_process_master_list_sub). Post-multiplying each one,
inside a perspective projection, by a view-space orbit around a point
--pivot units ahead of the source camera moves the camera without touching
the game's own transforms. This shows what recorded graphics alone can and
cannot show from a new angle: geometry the source drew re-projects; actors
the source culled are absent; billboards and the skybox keep facing the
source camera. A diagnostic only, never a viewer.
"""
from __future__ import annotations

import argparse
import math
import shutil
import struct
from pathlib import Path

import numpy as np

MASK = 0x7FFFFF


def word(rdram: bytearray, address: int) -> int:
    return struct.unpack_from("<I", rdram, address)[0]


def read_matrix(rdram: bytearray, address: int) -> np.ndarray:
    """N64 Mtx: 16 s16 integer parts then 16 u16 fractions, row-major."""
    halves = []
    for k in range(16):
        value = word(rdram, address + 4 * k)
        halves += [value >> 16, value & 0xFFFF]
    ints = [h - 0x10000 if h & 0x8000 else h for h in halves[:16]]
    return np.array([(ints[i] * 65536 + halves[16 + i]) / 65536.0 for i in range(16)]).reshape(4, 4)


def write_matrix(rdram: bytearray, address: int, matrix: np.ndarray) -> None:
    fixed = [int(round(v * 65536)) & 0xFFFFFFFF for v in matrix.reshape(16)]
    ints = [f >> 16 for f in fixed]
    fracs = [f & 0xFFFF for f in fixed]
    halves = ints + fracs
    for k in range(16):
        struct.pack_into("<I", rdram, address + 4 * k, (halves[2 * k] << 16) | halves[2 * k + 1])


def modelview_loads(rdram: bytearray, dl: int) -> list[int]:
    """Matrix addresses of MODELVIEW|LOAD commands under a perspective projection."""
    segment = [0] * 16
    stack = [dl & MASK]
    perspective = False
    found = []
    for _ in range(1 << 20):
        at = stack[-1]
        w0, w1 = word(rdram, at), word(rdram, at + 4)
        stack[-1] += 8
        op = w0 >> 24
        if op == 0xBC and (w0 & 0xFF) == 0x06:
            segment[(w0 >> 10) & 0xF] = w1 & 0xFFFFFF
        elif op == 0x01 and (w0 & 0xFFFF) == 64:
            address = (segment[(w1 >> 24) & 0xF] + (w1 & MASK)) & MASK
            param = (w0 >> 16) & 0xFF
            if param & 0x01 and param & 0x02:
                perspective = abs(read_matrix(rdram, address)[2, 3] + 1.0) < 1e-3
            elif not param & 0x01 and param & 0x02 and perspective:
                found.append(address)
        elif op == 0x06:
            target = (segment[(w1 >> 24) & 0xF] + (w1 & MASK)) & MASK
            if (w0 >> 16) & 0xFF == 0:
                stack.append(target)
            else:
                stack[-1] = target
        elif op == 0xB8:
            stack.pop()
            if not stack:
                return found
    raise RuntimeError("display list did not end")


def orbit(yaw: float, pitch: float, pivot: float) -> np.ndarray:
    """Row-vector view-space transform: orbit about (0, 0, -pivot)."""
    to_pivot = np.eye(4)
    to_pivot[3, :3] = (0, 0, pivot)
    back = np.eye(4)
    back[3, :3] = (0, 0, -pivot)
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    rot_y = np.array([[cy, 0, -sy, 0], [0, 1, 0, 0], [sy, 0, cy, 0], [0, 0, 0, 1]])
    rot_x = np.array([[1, 0, 0, 0], [0, cp, sp, 0], [0, -sp, cp, 0], [0, 0, 0, 1]])
    return to_pivot @ rot_y @ rot_x @ back


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("frame", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--yaw", type=float, default=30.0)
    parser.add_argument("--pitch", type=float, default=0.0)
    parser.add_argument("--pivot", type=float, default=1500.0)
    args = parser.parse_args()
    rdram = bytearray((args.frame / "rdram.bin").read_bytes())
    dmem = (args.frame / "dmem.bin").read_bytes()
    dl = struct.unpack_from("<I", dmem, 0xFF0)[0]
    addresses = sorted(set(modelview_loads(rdram, dl)))
    view = orbit(math.radians(args.yaw), math.radians(args.pitch), args.pivot)
    for address in addresses:
        write_matrix(rdram, address, read_matrix(rdram, address) @ view)
    args.out.mkdir(parents=True, exist_ok=True)
    for name in ("dmem.bin", "frame.json"):
        shutil.copy2(args.frame / name, args.out / name)
    (args.out / "rdram.bin").write_bytes(rdram)
    print(f"re-viewed {len(addresses)} world matrices -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
