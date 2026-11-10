"""The scene viewer's picture encoder (M4 lab): JPEGs made in a child process, from shared memory.

    slots = PictureSlots()                       # the host's reader puts each picture in one
    encoder = PictureEncoder()                   # the child process; started now, ready in ~0.1 s
    jpeg = encoder.jpeg(slots, slot, width, height)   # that slot's BGR rows (bottom-up) as JPEG,
                                                      #   or None while the child is not ready yet
    slots.release(slot)
    encoder.close(); slots.close()

Why a process: the scene evaluator runs the game's code on the server's worker thread while the
delivery thread encodes the picture before it, and the two share Python's lock (the GIL).
Measured 2026-10-26 on this desktop (smo-gil-probe.py): the evaluator's view took 4.0 ms alone,
7.0 to 8.8 ms beside a thread of the same process encoding JPEGs, and 4.9 ms beside another
process doing the same. The renderer host's reader writes each picture straight into a shared
memory slot (viewer.Host), so the server never copies the pixels either; the child encodes from
the slot with the same Pillow and settings the session used in process, so its JPEG is the same
bytes. Where the child cannot help (it has not started, it died, every slot is busy), the
session encodes in process as before.

Run as a child (python_child: the venv's site-packages, no launcher): it writes READY once
Pillow is imported, then reads requests from stdin, REQUEST (shared memory name, offset, width,
height, quality), and answers each with a signed i32 length and the JPEG (a negative length:
that many bytes of an error message). EOF exits 0.
"""
from __future__ import annotations

import io
import os
import struct
import subprocess
import sys
import threading
from multiprocessing import shared_memory
from pathlib import Path

REQUEST = struct.Struct("<64sQIII")
ANSWER = struct.Struct("<i")
READY = b"R"
SLOTS = 4                       # pictures the host's reader may hold at once (two in flight, and room)
HERE = Path(__file__).resolve().parent


class PictureSlots:
    """Shared memory for the pictures the host sends back: SLOTS slots of one picture's size,
    made when the first picture's size is known. claim() and release() may run on different
    threads."""

    def __init__(self, count: int = SLOTS):
        self.count, self.size = count, 0
        self.memory: shared_memory.SharedMemory | None = None
        self.free: list[int] = []
        self.views: dict[int, memoryview] = {}
        self.lock = threading.Lock()
        self.closed = False

    def claim(self, size: int) -> tuple[int, memoryview] | None:
        """A free slot of exactly `size` bytes and a view of it, or None (all busy, another size)."""
        with self.lock:
            if self.closed or not size:
                return None
            if self.memory is None:
                self.memory = shared_memory.SharedMemory(create=True, size=self.count * size)
                self.size, self.free = size, list(range(self.count))
            if size != self.size or not self.free:
                return None
            slot = self.free.pop()
            view = self.views[slot] = self.memory.buf[slot * size:(slot + 1) * size]
            return slot, view

    def release(self, slot: int) -> None:
        with self.lock:
            view = self.views.pop(slot, None)
            if view is not None:
                view.release()
                self.free.append(slot)

    @property
    def name(self) -> str | None:
        return self.memory.name if self.memory is not None else None

    def close(self) -> None:
        """Releases every slot and the memory; a view still being read into (a host that would not
        exit) keeps the memory until the process lets it go."""
        with self.lock:
            self.closed = True
            try:
                for view in self.views.values():
                    view.release()
                if self.memory is not None:
                    self.memory.close()
                    self.memory.unlink()
            except BufferError:
                pass
            self.views.clear()
            self.memory = None


class PictureEncoder:
    """The child process. jpeg() is called from one thread at a time (the delivery thread)."""

    def __init__(self, quality: int = 90):
        from sm64_events.core.childproc import log_child_stderr, python_child, quiet_spawn_kwargs
        argv, env = python_child("picture_encoder", "--scene-lab-encoder", site_packages=True)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(HERE), env.get("PYTHONPATH"))))
        self.quality = quality
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, env=env, **quiet_spawn_kwargs())
        log_child_stderr(self.process, "encoder")   # its errors reach the app's log
        self.lock = threading.Lock()
        self.ready = threading.Event()
        threading.Thread(target=self._await_ready, name="picture-encoder-ready", daemon=True).start()

    def _await_ready(self) -> None:
        if self.process.stdout.read(len(READY)) == READY:
            self.ready.set()

    @property
    def pid(self) -> int:
        return self.process.pid

    def jpeg(self, slots: PictureSlots, slot: int, width: int, height: int) -> bytes | None:
        """The picture in `slot` as JPEG; None while the child is not ready yet; RuntimeError when
        it cannot answer."""
        if not self.ready.is_set():
            return None
        request = REQUEST.pack(slots.name.encode("ascii"), slot * slots.size, width, height, self.quality)
        with self.lock:
            try:
                self.process.stdin.write(request)
                self.process.stdin.flush()
                head = self.process.stdout.read(ANSWER.size)
                if len(head) != ANSWER.size:
                    raise RuntimeError(f"the picture encoder exited ({self.process.poll()})")
                length = ANSWER.unpack(head)[0]
                body = self.process.stdout.read(abs(length))
            except OSError as error:
                raise RuntimeError(f"the picture encoder's pipe failed: {error}") from None
        if len(body) != abs(length):
            raise RuntimeError("the picture encoder stopped mid-answer")
        if length < 0:
            raise RuntimeError(f"the picture encoder refused: {body.decode('utf-8', 'replace')}")
        return body

    def close(self) -> int | None:
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            return self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait(timeout=5)


def encode(pixels, width: int, height: int, quality: int) -> bytes:
    """ReadScreen's BGR rows, bottom-up, as JPEG: the one encoding the session and the child share."""
    from PIL import Image
    buffer = io.BytesIO()
    Image.frombuffer("RGB", (width, height), pixels, "raw", "BGR", 0, -1).save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def serve() -> int:
    from PIL import Image  # noqa: F401 - imported before READY, so the first picture pays nothing
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    sink.write(READY)
    sink.flush()
    attached: dict[str, shared_memory.SharedMemory] = {}
    while True:
        head = source.read(REQUEST.size)
        if len(head) != REQUEST.size:
            return 0
        raw_name, offset, width, height, quality = REQUEST.unpack(head)
        try:
            name = raw_name.rstrip(b"\0").decode("ascii")
            memory = attached.get(name)
            if memory is None:
                # track= exists from Python 3.13; 3.12 (the project's floor) has no resource
                # tracker for shared memory on Windows, so attaching there tracks nothing anyway.
                untracked = {"track": False} if sys.version_info >= (3, 13) else {}
                memory = attached[name] = shared_memory.SharedMemory(name=name, **untracked)
            size = width * height * 3
            if not (0 < size and offset + size <= memory.size and 1 <= quality <= 100):
                raise ValueError("the request lies outside the shared picture memory")
            view = memory.buf[offset:offset + size]
            try:
                answer = encode(view, width, height, quality)
            finally:
                view.release()
        except Exception as error:  # noqa: BLE001 - the server falls back and shows nothing of this
            message = str(error).encode("utf-8", "replace")[:1000]
            sink.write(ANSWER.pack(-len(message)) + message)
        else:
            sink.write(ANSWER.pack(len(answer)) + answer)
        sink.flush()


if __name__ == "__main__":
    raise SystemExit(serve())
