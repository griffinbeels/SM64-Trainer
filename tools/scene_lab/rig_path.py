"""The stage Mario's path for the cinematic cameras (M4 round 6, item 1), read off the server's threads.

    reader = PathReader()                                     # a below-normal child, hidden
    future = reader.read(package_bytes, rom, indices, mario)  # Future of an (n, 4) array: x, y, z, facing
    reader.close()

A cinematic camera following the stage's Mario needs his place in every picture of the scene
(camera_rig.Runner). Each comes from that picture's scene state, 16 bytes of it (his position and
the word holding his facing), but reaching them rebuilds the scene state frame by frame: about
1.2 ms a frame, 1.0 s for an 800-frame take (measured 2026-10-27), most of it hashing and inflating.
On the session's worker that held the first cinematic picture, and Play with it, for that long; in
the server process it would share the Python lock with the pictures. So a child process reads
them (serve), below normal priority and hidden: the package's bytes and the frames go in a shared
memory block, the answer comes back in the same block, and a thread of the reader's own waits on
the pipe, so nothing of the server's waits for it. One request at a time, in order; the ROM goes
once. A growing scene asks again for only the frames it gained.

InProcessReader reads in the caller's thread: tests, and the fallback when the child cannot start.
"""
from __future__ import annotations

import json
import math
import os
import queue
import struct
import subprocess
import sys
import threading
from concurrent.futures import Future
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
ROW = 4                                   # float64s a frame: x, y, z, the game's s16 facing (NaN: no place)


def read_poses(package, indices, mario: int) -> np.ndarray:
    """(len(indices), ROW) of the stage's Mario in each package frame of `indices` (read in that order:
    ascending is one forward pass of the state cursor), NaN where his position is not a place (a
    load's reset memory). `mario`: gMarioStates' RDRAM offset."""
    from sm64_events.memory.addresses import MARIO_POS_LIMIT, MARIO_POS_OFF, MARIO_YAW_OFF
    yaw_word = (mario + MARIO_YAW_OFF) & ~3          # the yaw is the low half of its big-endian word
    out = np.full((len(indices), ROW), np.nan)
    for row, index in enumerate(indices):
        where, facing = package.scene_bytes(int(index), [(mario + MARIO_POS_OFF, 12), (yaw_word, 4)])
        position = struct.unpack("<3f", where)
        if all(math.isfinite(value) and abs(value) < MARIO_POS_LIMIT for value in position):
            out[row, :3] = position
            out[row, 3] = struct.unpack("<h", struct.pack("<H", int.from_bytes(facing, "little") & 0xFFFF))[0]
    return out


class InProcessReader:
    """read() in the caller's thread, the answer ready when it returns."""

    def read(self, data: bytes, rom: bytes, indices, mario: int) -> Future:
        import package as scene_package
        future = Future()
        try:
            future.set_result(read_poses(scene_package.Package(data, rom), indices, mario))
        except Exception as error:  # noqa: BLE001 - the session says why the cameras cannot follow him
            future.set_exception(error)
        return future

    def close(self) -> None:
        pass


class PathReader:
    """The child that reads Mario's path (serve), and the thread of this process that talks to it."""

    def __init__(self):
        from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
        argv, env = python_child("rig_path", "--scene-lab-rig-path", ["--serve"], site_packages=True)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(HERE), env.get("PYTHONPATH"))))
        spawn = quiet_spawn_kwargs()
        spawn["creationflags"] = spawn.get("creationflags", 0) | _BELOW_NORMAL
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env=env, **spawn)
        self.requests: queue.Queue = queue.Queue()
        self.rom = None                                # the ROM the child holds
        self.thread = threading.Thread(target=self._run, name="scene-lab-rig-path", daemon=True)
        self.thread.start()

    @property
    def pid(self) -> int:
        return self.process.pid

    def read(self, data: bytes, rom: bytes, indices, mario: int) -> Future:
        """The future of read_poses over these package bytes; RuntimeError with the child's reason."""
        future = Future()
        self.requests.put((future, data, rom, [int(index) for index in indices], int(mario)))
        return future

    def _run(self) -> None:
        while True:
            item = self.requests.get()
            if item is None:
                return
            future, *request = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(self._ask(*request))
            except Exception as error:  # noqa: BLE001 - handed to whoever waits on the future
                future.set_exception(error)

    def _ask(self, data: bytes, rom: bytes, indices: list, mario: int) -> np.ndarray:
        from multiprocessing import shared_memory
        send_rom = b"" if self.rom is rom else rom
        frames = np.asarray(indices, "<u4").tobytes()
        parts, at = {}, 0
        for name, size in (("package", len(data)), ("rom", len(send_rom)), ("frames", len(frames)),
                           ("out", len(indices) * ROW * 8)):
            parts[name], at = [at, size], at + size
        memory = shared_memory.SharedMemory(create=True, size=max(1, at))
        try:
            for name, content in (("package", data), ("rom", send_rom), ("frames", frames)):
                begin, size = parts[name]
                memory.buf[begin:begin + size] = content
            try:
                self.process.stdin.write((json.dumps({"memory": memory.name, "mario": mario, **parts}) + "\n")
                                         .encode("utf-8"))
                self.process.stdin.flush()
                line = self.process.stdout.readline()
            except (OSError, ValueError) as error:
                raise RuntimeError(f"the path reader stopped: {error}") from None
            if not line:
                raise RuntimeError(f"the path reader stopped (exit {self.process.poll()})")
            answer = json.loads(line)
            if "error" in answer:
                raise RuntimeError(answer["error"])
            self.rom = rom
            begin, size = parts["out"]
            return np.frombuffer(memory.buf[begin:begin + size], "<f8").reshape(-1, ROW).copy()
        finally:
            memory.close()
            memory.unlink()

    def close(self) -> int | None:
        """Ends the child (it may be mid-read: nothing it holds needs keeping) and the thread."""
        self.requests.put(None)
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            code = self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            code = self.process.wait(timeout=5)
        self.thread.join(timeout=5)
        return code


def _part(memory, span) -> bytes:
    begin, size = span
    return bytes(memory.buf[begin:begin + size])


def serve(argv=None) -> int:
    """The path reader child (PathReader): one JSON request a line, one answer a line, EOF exits."""
    from multiprocessing import shared_memory
    import package as scene_package
    untracked = {"track": False} if sys.version_info >= (3, 13) else {}   # as picture_encoder.serve
    rom, sink = None, sys.stdout.buffer
    for line in sys.stdin.buffer:
        try:
            request = json.loads(line)
            memory = shared_memory.SharedMemory(name=request["memory"], **untracked)
            try:
                if request["rom"][1]:
                    rom = _part(memory, request["rom"])
                indices = np.frombuffer(_part(memory, request["frames"]), "<u4").tolist()
                poses = read_poses(scene_package.Package(_part(memory, request["package"]), rom), indices,
                                   request["mario"])
                begin, size = request["out"]
                if poses.nbytes != size:
                    raise ValueError("the path is not the frames' length")
                memory.buf[begin:begin + size] = np.ascontiguousarray(poses, "<f8").tobytes()
            finally:
                memory.close()
            answer = {"frames": len(indices)}
        except Exception as error:  # noqa: BLE001 - the session says why the cameras cannot follow him
            answer = {"error": f"{type(error).__name__}: {error}"[:500]}
        sink.write((json.dumps(answer) + "\n").encode("utf-8"))
        sink.flush()
    return 0


if __name__ == "__main__":
    if "--serve" in sys.argv:
        raise SystemExit(serve())
