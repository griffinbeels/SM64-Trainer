"""Protocol doubles for the scene viewer: a viewer_host.c stand-in and an evaluator stand-in.

    python tools/scene_lab/fake_viewer_host.py [--width W] [--height H] [--delay-ms N] [--exit-after N] [--ram]

Run as a child, it speaks viewer_host.c's pipe protocol (viewer.py REQUEST/RESPONSE) and
answers each render with a synthetic picture: the sha256 of everything the request carried
(task words, VI registers, runs, bytes, and the camera when the request sets one) repeated
to fill W x H BGR pixels, so a picture names exactly what was sent. With --ram it keeps an
8 MiB RDRAM as viewer_host.c does (each request writes its runs into it) and the picture names
the task words, VI registers and that whole RDRAM instead: two ways of sending that leave the
host the same memory give the same picture. --delay-ms is a slow renderer; --exit-after ends
the process after that many renders, as a crashed host would. EOF or a quit request exits 0.
It opens no window and touches no GPU: tests and e3_measure.py --fake use it where the real
host would need the renderer.

FakeEvaluator stands in for evaluator.Evaluator the same way: its "display list" is a few
bytes naming the frame and camera (or, with ram_bytes, an image that size whose first words
name them), and prepare() records what it was asked to resolve ahead, and on which thread.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

REQUEST = struct.Struct("<5I16I14I16d")       # viewer.py's, repeated: this file runs without its imports
RESPONSE = struct.Struct("<9I")
REQUEST_MAGIC, RESPONSE_MAGIC = 0x51525756, 0x53525756
RDRAM_BYTES = 8 << 20


def command(delay_ms: float = 0, width: int = 64, height: int = 48, exit_after: int = 0,
            ram: bool = False) -> list[str]:
    """The argv prefix viewer.Host(command=...) runs: the base interpreter directly, so the
    process the host owns is the one that answers (no venv launcher in between)."""
    interpreter = getattr(sys, "_base_executable", None) or sys.executable
    return [interpreter, "-I", str(Path(__file__).resolve()), "--width", str(width), "--height", str(height),
            "--delay-ms", str(delay_ms), "--exit-after", str(exit_after), *(["--ram"] if ram else [])]


def _read(stream, size: int) -> bytes | None:
    data = stream.read(size)
    return data if len(data) == size else None


def serve(args) -> int:
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    rendered = 0
    rdram = bytearray(RDRAM_BYTES) if args.ram else None
    while True:
        head = _read(source, REQUEST.size)
        if head is None:
            return 0
        fields = REQUEST.unpack(head)
        magic, kind, flags, run_count, byte_count = fields[:5]
        if magic != REQUEST_MAGIC:
            print("fake_viewer_host: bad request magic", file=sys.stderr, flush=True)
            return 3
        if kind == 2:
            return 0
        runs = _read(source, run_count * 8) if run_count else b""
        data = _read(source, byte_count) if byte_count else b""
        if runs is None or data is None:
            print("fake_viewer_host: stdin closed inside a request", file=sys.stderr, flush=True)
            return 3
        started = time.perf_counter()
        if args.delay_ms:
            time.sleep(args.delay_ms / 1000)
        if rdram is not None:
            at = 0
            for begin, end in struct.iter_unpack("<II", runs):
                rdram[begin:end] = data[at:at + end - begin]
                at += end - begin
            digest = hashlib.sha256(head[20:20 + 30 * 4] + rdram).digest()
        else:
            digest = hashlib.sha256(head[20:20 + 30 * 4] + runs + data
                                    + (head[20 + 30 * 4:] if flags & 1 else b"")).digest()
        size = 0 if flags & 2 else args.width * args.height * 3
        pixels = (digest * (size // len(digest) + 1))[:size]
        render_us = int((time.perf_counter() - started) * 1e6)
        sink.write(RESPONSE.pack(RESPONSE_MAGIC, 0, 0 if flags & 2 else args.width, 0 if flags & 2 else args.height,
                                 render_us, 0, 0, 0, size) + pixels)
        sink.flush()
        rendered += 1
        if args.exit_after and rendered >= args.exit_after:
            return 9


class FakeEvaluator:
    """An evaluator.Evaluator stand-in: display_list's bytes name (frame, camera), pose is fixed,
    frames in `no_scene` have none (None, as a level load does), and each call waits `delay_s`."""

    POSE = {"focus": [0.0, 160.0, 0.0], "position": [0.0, 560.0, 1500.0], "mario": [12.0, 40.0, -30.0]}

    def __init__(self, delay_s: float = 0.0, no_scene: tuple = (), ram_bytes: int = 0):
        self.delay_s, self.no_scene, self.ram_bytes = delay_s, set(no_scene), ram_bytes
        self.calls: list[tuple] = []
        self.prepared: list[tuple[int, str]] = []

    def pose(self, index: int) -> dict:
        return {key: list(value) for key, value in self.POSE.items()}

    def prepare(self, index: int) -> None:
        self.prepared.append((index, threading.current_thread().name))

    def display_list(self, index: int, camera: tuple):
        self.calls.append((index, camera))
        if self.delay_s:
            time.sleep(self.delay_s)
        if index in self.no_scene:
            return None
        body = hashlib.sha256(repr((index, camera)).encode("ascii")).digest() * 2
        if self.ram_bytes:            # a whole image: the frame's own words high up, the view's at the start
            ram = bytearray(self.ram_bytes)
            ram[self.ram_bytes // 2:self.ram_bytes // 2 + 32] = hashlib.sha256(repr(index).encode("ascii")).digest()
            ram[:len(body)] = body
            return SimpleNamespace(task=(1,) + (0,) * 15, ram=ram, data_ptr=0x80000000, data_size=len(body))
        return SimpleNamespace(task=(1,) + (0,) * 15, ram=bytearray(body), data_ptr=0x80000000,
                               data_size=len(body))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--height", type=int, default=48)
    parser.add_argument("--delay-ms", type=float, default=0)
    parser.add_argument("--exit-after", type=int, default=0)
    parser.add_argument("--ram", action="store_true", help="keep an RDRAM and name it in the picture")
    args, _host_arguments = parser.parse_known_args()   # --renderer, --plugin-dir, --header: unused
    return serve(args)


if __name__ == "__main__":
    raise SystemExit(main())
