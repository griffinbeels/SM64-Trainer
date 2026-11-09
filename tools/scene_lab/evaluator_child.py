"""The free camera's evaluator process (Ghost Lab beta, sim-split): evaluator.Evaluator in a child of its own.

    client = EvaluatorClient(package, rom_path, map_path, source)    # RuntimeError: why it could not start
    view = client.display_list(index, camera)       # evaluator.View; its RDRAM comes back through shared memory
    client.pose(index), client.mario(index)         # as evaluator.Evaluator answers them
    client.prepare(index)                           # resolved in the child, on its look-ahead thread
    handle = client.start_resolvers(warm, ahead)    # the state resolvers, started by the child (handle.close())
    client.ahead(indices)                           # frames handed to them; False while none can take them
    client.package = grown                          # the scene's package grew: the child's grows the same way
    client.close()

Why a process. The free camera runs SM64's own scene-graph code under Unicorn, and Unicorn 2.1.4 as shipped is
GPL-2.0-only (the license ruling of 2026-11-09: QEMU's v2-only files are in every target). The Trainer's server
loads PyAV's FFmpeg, a GPL-3.0 build, and GPL-2.0-only code cannot share a program with GPL-3.0 code. So the
evaluator, the state resolvers it feeds (state_resolver.py, now children of this child) and every other Unicorn
user run in simulation processes (sim_boundary.py), never in the server; in the installed app those are the
separate program SM64TrainerSim.exe. scene_session.GameCodeEvaluator makes this client; the session calls it as
it called the evaluator, from the same threads: the views, poses and Mario from its worker, prepare() from its
look-ahead thread.

The child keeps the evaluator's own threading: requests are read and answered in order on its main thread
(views, poses, Mario, the package growing), prepare() runs on a look-ahead thread of its own, and the state
resolvers deliver on theirs, so a view of a cached state never waits behind a resolution, as before. What the
server gives up is the copy of each view's RDRAM out of shared memory (about 1 ms for the 8 MiB) and two pipe
messages; what it gains is that none of the game's code runs on its Python lock any more.

Run as a child (python_child; frozen, SM64TrainerSim.exe --scene-lab-evaluator):
    python -m evaluator_child --rom R --map M --source NAME --memory SHM
Messages both ways are framed: <II (header bytes, payload bytes), a JSON header, then the payload. A request is
{"id", "op", ...}: "package" (the payload is the package, whole or grown: _package), "view" {index, camera}
(the view's RDRAM is written at the start of the shared memory SHM), "pose", "mario", "prepare" {index},
"ahead" {indices} and "resolvers" {warm, ahead} (neither answered). Each answer is {"id", ...} or {"id",
"error"}, and every answer carries "usable": the state resolvers can take frames. EOF closes the resolvers and
exits 0.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import queue
import struct
import subprocess
import sys
import threading
import time
from multiprocessing import shared_memory
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

FRAME = struct.Struct("<II")       # header bytes, payload bytes
SLOT_BYTES = 8 << 20               # one view's RDRAM (the expansion pak's 8 MiB)
START_SECONDS = 120.0              # the child's first answer: Python, Unicorn, the package and the ROM's code images
CALL_SECONDS = 60.0                # any later answer (a cold seek is ~100 ms; a stuck child is ended)
log = logging.getLogger("sm64.scene_lab")


class EvaluatorStopped(RuntimeError):
    """The evaluator's process ended or stopped answering."""


def _plain(value):
    """JSON for the numbers numpy hands back (a run's counters, a pose's floats)."""
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (tuple, set, bytes)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON")


def _send(stream, header: dict, payload: bytes = b"") -> None:
    data = json.dumps(header, default=_plain).encode("utf-8")
    stream.write(FRAME.pack(len(data), len(payload)) + data)
    if payload:
        stream.write(payload)
    stream.flush()


def _read_exact(stream, size: int) -> bytes | None:
    chunks, left = [], size
    while left:
        chunk = stream.read(left)
        if not chunk:
            return None
        chunks.append(chunk)
        left -= len(chunk)
    return b"".join(chunks)


def _receive(stream) -> tuple[dict, bytes] | None:
    head = _read_exact(stream, FRAME.size)
    if head is None:
        return None
    header_bytes, payload_bytes = FRAME.unpack(head)
    header = _read_exact(stream, header_bytes)
    payload = _read_exact(stream, payload_bytes) if payload_bytes else b""
    if header is None or payload is None:
        return None
    return json.loads(header), payload


def has_unicorn() -> bool:
    """Whether the simulation processes can load Unicorn. In a source checkout they run in this venv, so
    finding it here is the answer; the installed app's simulation program carries it."""
    if getattr(sys, "frozen", False):
        return True
    import importlib.util
    return importlib.util.find_spec("unicorn") is not None


# ---------------------------------------------------------------- the server's half

class _ChildResolvers:
    """The state resolvers the child started (the session closes them with the scene)."""

    def __init__(self, client: EvaluatorClient):
        self.client = client

    def close(self) -> None:
        self.client._post({"op": "resolvers", "close": True})


class EvaluatorClient:
    """The evaluator in its child process, with evaluator.Evaluator's viewer API. One caller thread at a time
    per lane, as the session calls it: views, poses and Mario from its worker; prepare() from its look-ahead
    thread. Any other answer than a value raises RuntimeError (EvaluatorStopped once the child is gone)."""

    def __init__(self, package, rom_path: Path, map_path: Path, source: Path | None = None, *,
                 extra_path: tuple = (), start_seconds: float = START_SECONDS, wait: bool = True):
        """Starts the child and hands it the package. With wait, returns once its evaluator is open
        (RuntimeError: why not); without, returns at once and the first call waits for that, so the
        start (~0.4 s: the interpreter, Unicorn, the package and the ROM's code images) overlaps
        whatever the caller does meanwhile (scene_session opens the renderer)."""
        from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
        self.memory = shared_memory.SharedMemory(create=True, size=SLOT_BYTES)
        args = ["--rom", str(rom_path), "--map", str(map_path), "--source", str(source or "package"),
                "--memory", self.memory.name]
        argv, env = python_child("evaluator_child", "--scene-lab-evaluator", args, site_packages=True)
        env = dict(env if env is not None else os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (*map(str, extra_path), str(HERE), env.get("PYTHONPATH"))))
        self.write_lock = threading.Lock()
        self.lock = threading.Lock()
        self.waiting: dict[int, list] = {}            # id -> [event, answer]
        self.serial = 0
        self.usable = False                           # the child's state resolvers can take frames
        self.stopped: str | None = None               # why the child no longer answers
        self._package = package
        self._sent: tuple | None = None               # ("whole" | "grown", frames, body pieces)
        self._buffers, self._turn = [bytearray(), bytearray()], 0     # the views' RDRAM, in turn (display_list)
        try:
            self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, env=env, **quiet_spawn_kwargs())
        except OSError:
            self._release()
            raise
        self.reader = threading.Thread(target=self._read, name="scene-lab-evaluator", daemon=True)
        self.reader.start()
        # The package goes on a thread of its own: the child reads it only once Python, numpy and Unicorn are
        # loaded (~0.25 s), and a pipe write that size waits for its reader (it held a scene's open 0.22 s).
        self._opening = self._begin(*self._package_message(package), background=True)
        self._opened: str | None = None               # None until open; "" when open; else why it is not
        self._start_seconds = start_seconds
        if wait:
            try:
                self._ready()
            except BaseException:
                self.close()
                raise

    def opened(self) -> str | None:
        """Waits for the child's evaluator to open (once): None when it did, else why not."""
        if self._opened is None:
            try:
                self._finish(self._opening, self._start_seconds)
                self._opened = ""
            except RuntimeError as error:
                self._opened = str(error) or "The free camera's helper did not start."
        return self._opened or None

    def _ready(self) -> None:
        reason = self.opened()
        if reason:
            raise RuntimeError(reason)

    @property
    def pid(self) -> int:
        return self.process.pid

    # the package
    @property
    def package(self):
        return self._package

    @package.setter
    def package(self, package) -> None:
        """The session's package grew (scene_session._use): the child's gets the same frames. A package with
        the frames the child already has (the finished file again, or with its sound added) sends nothing."""
        self._package = package
        message = self._package_message(package)
        if message is not None:
            self._post(*message)

    def _package_message(self, package) -> tuple[dict, bytes] | None:
        """The package as the child takes it: a whole package's bytes, or a growing one's sidecar (header and
        index) and the body pieces the child lacks (package.Joined's parts: the header, the body as read, the
        index). None when the child holds these frames already."""
        data = package.data
        frames = len(package)
        if isinstance(data, (bytes, bytearray)):
            # The finished file once (its state resolvers read whole bytes); not again when only its sound changed.
            if self._sent is not None and self._sent[0] == "whole" and self._sent[1] == frames:
                return None
            self._sent = ("whole", frames, ())
            return {"op": "package", "kind": "whole", "frames": frames}, bytes(data)
        parts = getattr(data, "parts", None)
        if parts is None or len(parts) < 2:                 # any other byte source: whole, once per change
            if self._sent is not None and self._sent[1] == frames:
                return None
            self._sent = ("whole", frames, ())
            return {"op": "package", "kind": "whole", "frames": frames}, bytes(data[0:len(data)])
        if self._sent is not None and self._sent[1] == frames:
            return None
        body = list(parts[1:-1])
        known = self._sent[2] if self._sent is not None and self._sent[0] == "grown" else ()
        start = 0
        while start < min(len(known), len(body)) and known[start] is body[start]:
            start += 1
        sidecar = bytes(parts[0]) + bytes(parts[-1])
        pieces = [bytes(piece) for piece in body[start:]]
        self._sent = ("grown", frames, tuple(body))
        return ({"op": "package", "kind": "grown", "frames": frames, "body_from": start, "sidecar": len(sidecar),
                 "sizes": [len(piece) for piece in pieces]}, sidecar + b"".join(pieces))

    # the evaluator's API
    def __len__(self) -> int:
        return len(self._package)

    def display_list(self, index: int, camera: tuple):
        """evaluator.Evaluator.display_list from the child: None when the frame has no scene; a View (task,
        ram, data_ptr, data_size; its run and culling stay in the child) whose ram is None after a violation or
        without pool room. Its RDRAM is copied out of the shared slot into one of two buffers this client
        reuses in turn, so a view's ram holds until the second display_list after it: the session keeps only
        the newest (its pick's). Measured 2026-11-09 on the 8 MiB: numpy's copy into a reused buffer 0.49 ms,
        bytes() 1.0 ms, a bytearray slice 1.5 ms; the round trip itself 0.36 ms."""
        answer, _ = self._call({"op": "view", "index": index, "camera": camera})
        if answer.get("none"):
            return None
        ram = None
        size = answer.get("size")
        if size is not None:
            self._turn ^= 1
            if len(self._buffers[self._turn]) != size:
                self._buffers[self._turn] = bytearray(size)
            ram = self._buffers[self._turn]
            np.copyto(np.frombuffer(ram, np.uint8), np.frombuffer(self.memory.buf, np.uint8, size))
        return SimpleNamespace(task=tuple(answer["task"]), ram=ram, data_ptr=answer["data_ptr"],
                               data_size=answer["data_size"], child_ms=answer.get("ms"))

    def pose(self, index: int) -> dict:
        return self._call({"op": "pose", "index": index})[0]["pose"]

    def mario(self, index: int) -> list[float] | None:
        """Mario's position at that frame, or None when the child cannot say (a pick's "mario" is optional)."""
        try:
            return self._call({"op": "mario", "index": index})[0]["mario"]
        except RuntimeError:
            return None

    def prepare(self, index: int) -> None:
        if 0 <= index < len(self):
            self._call({"op": "prepare", "index": index})

    def start_resolvers(self, warm: int | None, ahead: int) -> _ChildResolvers:
        """The state resolvers, started by the child over its own copy of the whole package (each resolves
        frame `warm` first), keeping room for `ahead` states. ahead() says False until one is ready."""
        self._post({"op": "resolvers", "warm": warm, "ahead": ahead})
        return _ChildResolvers(self)

    def ahead(self, indices) -> bool:
        """Hands the frames a viewer expects next to the state resolvers, without waiting; False while none of
        them is ready (the child's last answer said so): the caller then prepares ahead itself."""
        if not self.usable or self.stopped:
            return False
        self._post({"op": "ahead", "indices": [int(index) for index in indices]})
        return True

    # the pipe
    def _post(self, header: dict, payload: bytes = b"") -> None:
        """A request nobody waits for (an answer that comes is only an error, logged)."""
        if self.stopped:
            return
        try:
            with self.write_lock:
                _send(self.process.stdin, {**header, "id": 0}, payload)
        except (OSError, ValueError):
            self._stop("The free camera's helper stopped.")

    def _call(self, header: dict, payload: bytes = b"", timeout: float = CALL_SECONDS) -> tuple[dict, bytes]:
        self._ready()
        return self._finish(self._begin(header, payload), timeout)

    def _begin(self, header: dict, payload: bytes = b"", background: bool = False) -> tuple[int, list]:
        """Sends a request whose answer someone waits for (_finish); with `background`, from a thread of its
        own (every later request queues behind it on the pipe)."""
        with self.lock:
            if self.stopped:
                raise EvaluatorStopped(self.stopped)
            self.serial += 1
            ident = self.serial
            entry = self.waiting[ident] = [threading.Event(), None]
        sending = threading.Event()

        def send() -> None:
            try:
                with self.write_lock:
                    sending.set()
                    _send(self.process.stdin, {**header, "id": ident}, payload)
            except (OSError, ValueError):
                self._stop("The free camera's helper stopped.")
            finally:
                sending.set()
        if background:
            self._sender = threading.Thread(target=send, name="scene-lab-evaluator-package", daemon=True)
            self._sender.start()
            sending.wait()                           # it holds the pipe: nothing overtakes the package
        else:
            send()
        return ident, entry

    def _finish(self, sent: tuple[int, list], timeout: float) -> tuple[dict, bytes]:
        ident, entry = sent
        if not entry[0].wait(timeout):
            self._stop("The free camera's helper stopped answering.")
            self._end()
        with self.lock:
            self.waiting.pop(ident, None)
        if entry[1] is None:
            raise EvaluatorStopped(self.stopped or "The free camera's helper stopped.")
        answer, data = entry[1]
        if "error" in answer:
            raise RuntimeError(answer["error"])
        return answer, data

    def _read(self) -> None:
        stream = self.process.stdout
        while True:
            try:
                message = _receive(stream)
            except (OSError, ValueError):
                message = None
            if message is None:
                self._stop("The free camera's helper stopped.")
                return
            answer, data = message
            self.usable = bool(answer.get("usable"))
            with self.lock:
                entry = self.waiting.get(answer.get("id"))
            if entry is not None:
                entry[1] = (answer, data)
                entry[0].set()
            elif "error" in answer:
                log.warning("scene viewer: the evaluator's process refused a request: %s", answer["error"])

    def _stop(self, reason: str) -> None:
        with self.lock:
            self.stopped = self.stopped or reason
            waiting = list(self.waiting.values())
        self.usable = False
        for entry in waiting:
            entry[0].set()

    def _end(self) -> None:
        if self.process.poll() is None:
            self.process.kill()

    def close(self) -> int | None:
        """EOF to the child (it closes its resolvers and exits), a kill after a few seconds, the slot released.
        A scene closed while its package is still on its way ends the child first: the pipe is that write's."""
        self._stop("The free camera's helper was closed.")
        sender = getattr(self, "_sender", None)
        if sender is not None and sender.is_alive():
            self._end()
            sender.join(timeout=5)
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            code = self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            code = self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        self._release()
        return code

    def _release(self) -> None:
        try:
            self.memory.close()
            self.memory.unlink()
        except (BufferError, FileNotFoundError):
            pass


# ---------------------------------------------------------------- the child

class Child:
    """The child's evaluator, its package and its state resolvers."""

    def __init__(self, args, sink):
        import e2_georender
        import package as scene_package
        self.e2, self.pk = e2_georender, scene_package
        self.rom_path, self.map_path, self.source = args.rom, args.map, Path(args.source)
        self.rom = args.rom.read_bytes()
        self.symbols = e2_georender.load_map(args.map, rom=self.rom)
        self.memory = shared_memory.SharedMemory(name=args.memory, **({"track": False} if sys.version_info >= (3, 13) else {}))
        self.sink, self.write_lock = sink, threading.Lock()
        self.evaluator = None
        self.body: list[bytes] = []                  # a growing package's body, as the server sent it
        self.resolvers = None
        self.ahead_jobs: queue.SimpleQueue = queue.SimpleQueue()
        threading.Thread(target=self._ahead_thread, name="evaluator-ahead", daemon=True).start()

    def answer(self, header: dict, extra: dict | None = None, *, error: str | None = None) -> None:
        if not header.get("id"):
            if error is None:
                return
        message = {"id": header.get("id", 0), "usable": self._usable(), **(extra or {})}
        if error is not None:
            message["error"] = error[:500]
        with self.write_lock:
            _send(self.sink, message)

    def _usable(self) -> bool:
        resolvers = None if self.evaluator is None else self.evaluator.resolvers
        return resolvers is not None and resolvers.usable

    def handle(self, header: dict, payload: bytes) -> None:
        op = header.get("op")
        if op == "prepare":
            self.ahead_jobs.put(header)
            return
        try:
            if op == "package":
                self._package(header, payload)
                self.answer(header, {"frames": len(self.evaluator)})
            elif op == "view":
                self.answer(header, self._view(int(header["index"]), header["camera"]))
            elif op == "pose":
                self.answer(header, {"pose": self.evaluator.pose(int(header["index"]))})
            elif op == "mario":
                self.answer(header, {"mario": self.evaluator.mario(int(header["index"]))})
            elif op == "ahead":
                self.evaluator.ahead([int(index) for index in header["indices"]])
            elif op == "resolvers":
                self._resolvers(header)
            else:
                raise ValueError(f"unknown request {op!r}")
        except (Exception, SystemExit) as error:  # noqa: BLE001 - the server shows it as the reason, as in process
            self.answer(header, error=str(error) or type(error).__name__)

    def _package(self, header: dict, payload: bytes) -> None:
        previous = None if self.evaluator is None else self.evaluator.package
        if header["kind"] == "whole":
            package = self.pk.Package(payload, self.rom)
            self.body = []
            if previous is not None:
                try:
                    package.carry(previous)
                except ValueError:                    # not grown from it: decoded afresh
                    pass
        else:
            sidecar, at = payload[:header["sidecar"]], header["sidecar"]
            pieces = []
            for size in header["sizes"]:
                pieces.append(payload[at:at + size])
                at += size
            self.body = self.body[:header["body_from"]] + pieces
            grown_from = previous if previous is not None and not isinstance(previous.data, bytes) else None
            package = self.pk.grown_package(sidecar, list(self.body), self.rom, grown_from)
        if self.evaluator is None:
            import evaluator
            self.evaluator = evaluator.Evaluator(package, self.rom, self.symbols, source=self.source)
        else:
            self.evaluator.package = package

    def _view(self, index: int, camera) -> dict:
        """The view's small parts (the server's session reads no more of a View: its run and culling stay
        here) and its RDRAM in the shared slot; "ms" is the evaluator's own time."""
        started = time.perf_counter()
        view = self.evaluator.display_list(index, tuple(camera))
        if view is None:
            return {"none": True}
        found = {"task": list(view.task), "data_ptr": view.data_ptr, "data_size": view.data_size, "size": None,
                 "violations": len(view.run.get("violations") or ())}
        if view.ram is not None:
            if len(view.ram) > self.memory.size:
                raise ValueError("the view does not fit its shared memory slot")
            self.memory.buf[:len(view.ram)] = view.ram
            found["size"] = len(view.ram)
        found["ms"] = round((time.perf_counter() - started) * 1000, 3)
        return found

    def _resolvers(self, header: dict) -> None:
        if header.get("close"):
            resolvers, self.resolvers = self.resolvers, None
            if resolvers is not None:
                resolvers.close()
            return
        if self.resolvers is not None or not isinstance(self.evaluator.package.data, bytes):
            return
        threading.Thread(target=self._start_resolvers, args=(header.get("warm"), int(header.get("ahead") or 0)),
                         name="evaluator-resolvers", daemon=True).start()

    def _start_resolvers(self, warm: int | None, ahead: int) -> None:
        import state_resolver
        evaluator = self.evaluator
        try:
            resolvers = state_resolver.Resolvers(evaluator.package.data, self.rom_path, self.map_path,
                                                 evaluator.delivered, warm=warm)
        except (OSError, ValueError):                 # the look-ahead thread resolves ahead, as before
            return
        self.resolvers = resolvers
        evaluator.attach(resolvers, ahead)

    def _ahead_thread(self) -> None:
        while True:
            header = self.ahead_jobs.get()
            if header is None:
                return
            try:
                self.evaluator.prepare(int(header["index"]))
                self.answer(header, {"done": True})
            except Exception as error:  # noqa: BLE001 - that frame resolves when it is drawn instead
                self.answer(header, error=str(error) or type(error).__name__)

    def close(self) -> None:
        self.ahead_jobs.put(None)
        resolvers, self.resolvers = self.resolvers, None
        if resolvers is not None:
            resolvers.close()
        try:
            self.memory.close()
        except BufferError:
            pass


def serve(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("--rom", "--map"):
        parser.add_argument(name, type=Path, required=True)
    parser.add_argument("--source", default="package")
    parser.add_argument("--memory", required=True)
    args = parser.parse_args(argv)
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    try:
        child = Child(args, sink)
    except (Exception, SystemExit) as error:  # noqa: BLE001 - the free camera says why (a bad map is a SystemExit)
        message = _receive(source)
        header = message[0] if message else {"id": 0}
        _send(sink, {"id": header.get("id", 0), "usable": False, "error": (str(error) or type(error).__name__)[:500]})
        return 1
    try:
        while (message := _receive(source)) is not None:
            child.handle(*message)
    finally:
        child.close()
    return 0


if __name__ == "__main__":
    import sim_boundary
    sim_boundary.enter()
    raise SystemExit(serve())
