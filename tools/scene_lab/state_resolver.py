"""The free camera's state resolvers (M4 lab): the scene states Play needs next, resolved in child processes.

    resolvers = Resolvers(package.data, rom, symbol_map, deliver)   # COUNT children, ready in ~1 s
    resolvers.ahead([index, ...])       # the frames Play needs next, in order: the queue becomes these
    resolvers.take(index)               # True: that frame's state is coming to deliver() (next in line)
    resolvers.close()

deliver(index, answer, ram) runs on a resolver's thread for every frame it was given: `answer`
the state's small parts (serve's JSON line) and `ram` its resolved RDRAM (bytes), or both None
when that child could not resolve it. evaluator.Evaluator.delivered caches it as if it had
resolved the frame itself: the same code over the same package gives the same bytes.

Why processes: a frame's state (evaluator.Evaluator.state: the recorded state assembled, the
game's geo code traced, and a search where render-stepped state or a throwMatrix must be put back)
cost 15 ms p50 on his Hazy Maze Cave take, 40-55 ms on its search stretch, and Play needs 30 a
second (measured 2026-10-27, take of 830 frames, Unicorn 2.1.4). Resolved on the session's
look-ahead thread, that work shared the server's one Python lock (the GIL) with the worker
drawing the free camera's views: during Play with the camera orbiting, input to drawn p95 rose
to 155-200 ms and 13-20% of the frames were skipped, with or without ghosts. With every state
resolved before Play the same run held 30 fps at p95 32 ms. So each child holds its own
evaluator over the session's own package bytes (handed over in shared memory: the children never
open the file, which the cutter replaces when it adds the sound) and resolves the frames it is
given, below normal priority, one at a time; the server only copies each answer in. Two children
leapfrog in runs of frames (_choice), so the 40-55 ms stretches keep 30 fps too. They start once
the package is whole (a growing scene is not one package yet) and stop with the scene.

Run as a child (python_child: the venv's site-packages, no launcher):
    python -m state_resolver --memory NAME --package-bytes N --rom R --map M [--evaluator module:callable]
The shared memory NAME holds the package's N bytes, then one 8 MiB slot per child. It writes
{"ready": frames} once the package is open (or {"error"} and exits 1), then answers each request
line {"index", "offset"} with one line: {"index", "digests", "recorded_as", "status", "entry", "ms"}
and, for a frame with a scene, "words", "layout", "result", "notes", "restored", "pointers",
"recorded" and "ram_bytes", that many bytes written at that offset; or {"index", "error"}. EOF
exits 0. --evaluator names a stand-in factory(package bytes, rom path, map path) for tests.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from multiprocessing import shared_memory
from pathlib import Path

HERE = Path(__file__).resolve().parent
COUNT = 2                      # children: one keeps up with 15 ms frames, two with the 40-55 ms stretches
SLOT_BYTES = 8 << 20           # one resolved RDRAM (the expansion pak's 8 MiB)
STUCK_SECONDS = 10.0           # a frame running this long means the child hung: the rest resolve in process
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


class _Child:
    def __init__(self, process: subprocess.Popen, offset: int):
        self.process, self.offset = process, offset
        self.last: int | None = None          # the frame it resolved last (or is resolving)
        self.busy = False
        self.serving = False                  # ready, and its thread still hands it frames


class Resolvers:
    """The children and the queue they take frames from. ahead() and take() never wait: the
    caller waits for deliver() (evaluator.Evaluator.state)."""

    def __init__(self, package: bytes, rom: Path, symbol_map: Path, deliver, *, count: int = COUNT,
                 evaluator: str | None = None, extra_path: tuple = (), warm: int | None = None):
        from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
        self.deliver = deliver
        self.slots_at = -(-len(package) // 4096) * 4096    # the package's bytes first, then one slot a child
        self.lock = threading.Lock()
        self.wake = threading.Condition(self.lock)
        self.queue: deque[int] = deque()
        self.urgent: set[int] = set()            # frames a caller waits for: first in line, kept by ahead()
        self.running: dict[int, float] = {}      # frame -> when a child began it
        self.failed: set[int] = set()            # frames a child could not resolve: the caller resolves them
        self.step = 1                            # the way the viewer goes: 1 playing on, -1 stepping back
        self.ready = self.alive = 0
        self.closed = False
        self.memory = shared_memory.SharedMemory(create=True, size=self.slots_at + count * SLOT_BYTES)
        self.memory.buf[:len(package)] = package
        args = ["--memory", self.memory.name, "--package-bytes", str(len(package)), "--rom", str(rom),
                "--map", str(symbol_map)]
        if evaluator:
            args += ["--evaluator", evaluator]
        if warm is not None:
            args += ["--warm", str(int(warm))]
        argv, env = python_child("state_resolver", "--scene-lab-resolver", args, site_packages=True)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (*map(str, extra_path), str(HERE), env.get("PYTHONPATH"))))
        spawn = quiet_spawn_kwargs()
        spawn["creationflags"] = spawn.get("creationflags", 0) | _BELOW_NORMAL
        self.children: list[_Child] = []
        try:
            for number in range(count):
                process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, env=env, **spawn)
                self.children.append(_Child(process, self.slots_at + number * SLOT_BYTES))
        except OSError:
            self.close()
            raise
        self.alive = len(self.children)
        for number, child in enumerate(self.children):
            threading.Thread(target=self._serve, args=(child,), name=f"scene-lab-resolver-{number}", daemon=True).start()

    @property
    def pids(self) -> list[int]:
        return [child.process.pid for child in self.children]

    def _usable(self) -> bool:
        return not self.closed and self.alive > 0 and self.ready > 0

    def ahead(self, indices) -> bool:
        """The frames Play needs next, in order: the queue becomes these (after any a caller waits
        for); frames already running or failed are left out. False while no child is ready (or
        none is left): the caller resolves ahead itself."""
        indices = list(indices)
        with self.lock:
            if not self._usable():
                return False
            if len(indices) > 1:
                self.step = 1 if indices[1] > indices[0] else -1
            kept = [index for index in self.queue if index in self.urgent]
            self.queue = deque(kept + [index for index in indices if index not in self.running
                                       and index not in self.failed and index not in self.urgent])
            self.wake.notify_all()
            return True

    def take(self, index: int) -> bool:
        """True when frame `index` is running in a child or is now next in line (after any other
        frame a caller waits for): its state comes to deliver(). False when the caller must
        resolve it: no child ready or left, the frame failed in a child, a child has been on it for
        STUCK_SECONDS (all of them are ended), or a seek (a frame not queued) while every child is
        busy with frames queued before it. The frames queued before it are dropped (the viewer
        moved past them: Play skipped them, or it seeked), so a child never steps back to one: its
        package reader rebuilds a state behind its last one from the nearest checkpoint (~100 ms)."""
        with self.lock:
            if not self._usable() or index in self.failed:
                return False
            began = self.running.get(index)
            if began is not None:
                if time.monotonic() - began <= STUCK_SECONDS:
                    return True
                stuck = True
            elif index not in self.queue and all(child.busy for child in self.children if child.serving):
                return False                     # a seek while every child resolves frames it no longer needs
            else:
                stuck = False
                queued = list(self.queue)
                after = queued[queued.index(index) + 1:] if index in queued else []
                self.queue = deque([waited for waited in queued if waited in self.urgent] + [index]
                                   + [later for later in after if later not in self.urgent])
                self.urgent.add(index)
                self.wake.notify_all()
        if stuck:
            self.close()
            return False
        return True

    def _choice(self, child: _Child) -> int | None:
        """The frame `child` takes now, or None to wait. A frame's state needs the one before it
        assembled (the evaluator's previous game frame), so a child that goes on from its own last
        frame pays for one assembly instead of two (19 against 35 ms a frame on his take): it takes
        the frame after its last when that is queued, else the first queued one that no other
        child goes on to; one that another child goes on to waits for that child. So while Play
        runs ahead of the children one child resolves every frame, and when it falls behind the
        other takes frames further on and goes on from those. A frame a caller waits for goes
        first, to whichever child is free."""
        if not self.queue:
            return None
        if self.queue[0] in self.urgent:
            return self.queue[0]
        following = None if child.last is None else child.last + self.step
        if following in self.queue:
            return following
        others = {other.last + self.step for other in self.children
                  if other is not child and other.last is not None and other.serving}
        return next((index for index in self.queue if index not in others), None)

    def _next(self, child: _Child) -> int | None:
        with self.lock:
            while not self.closed and (index := self._choice(child)) is None:
                self.wake.wait()
            if self.closed:
                return None
            self.queue.remove(index)
            self.urgent.discard(index)
            self.running[index] = time.monotonic()
            child.last, child.busy = index, True
            return index

    def _serve(self, child: _Child) -> None:
        """One child's thread: waits for it to be ready, then hands it frames one at a time. A
        frame stays running until its state was delivered, so a caller asking for it meanwhile
        waits for that delivery instead of queueing it again."""
        try:
            hello = json.loads(child.process.stdout.readline() or b"{}")
        except ValueError:
            hello = {}
        if "ready" in hello:
            with self.lock:
                self.ready += 1
                child.serving = True
            gone = False
            while not gone and (index := self._next(child)) is not None:
                answer, ram, gone = self._resolve(child, index)
                if answer is None:
                    with self.lock:
                        self.failed.add(index)
                self.deliver(index, answer, ram)
                with self.lock:
                    self.running.pop(index, None)
                    child.busy = False
                    self.wake.notify_all()
        with self.lock:
            child.busy = child.serving = False
            self.wake.notify_all()               # the frames it would have gone on to are anyone's now
            self.alive -= 1
            orphans = list(self.queue) if self.alive == 0 else []
            if orphans:
                self.queue.clear()
                self.failed.update(orphans)
        for index in orphans:                    # nobody is left to resolve them: their waiters go on
            self.deliver(index, None, None)

    def _resolve(self, child: _Child, index: int):
        """(answer, RDRAM bytes or None, gone) from the child: answer None when it could not
        resolve the frame, gone once the child itself has ended (its pipe closed)."""
        try:
            request = {"index": index, "offset": child.offset}
            child.process.stdin.write((json.dumps(request) + "\n").encode("ascii"))
            child.process.stdin.flush()
            line = child.process.stdout.readline()
        except (OSError, ValueError):
            return None, None, True
        if not line:
            return None, None, True
        try:
            answer = json.loads(line)
        except ValueError:
            return None, None, False
        if answer.get("index") != index or "error" in answer or "status" not in answer:
            return None, None, False
        if answer["status"] is not None:
            return answer, None, False
        size = answer.get("ram_bytes")
        if not isinstance(size, int) or not 0 < size <= SLOT_BYTES:
            return None, None, False
        with self.lock:
            if self.closed:
                return None, None, True
            return answer, bytes(self.memory.buf[child.offset:child.offset + size]), False

    def close(self) -> None:
        """Ends the children (EOF, then a kill after a few seconds) and releases the memory."""
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.wake.notify_all()
        for child in self.children:
            try:
                child.process.stdin.close()
            except OSError:
                pass
        for child in self.children:
            try:
                child.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.process.kill()
                child.process.wait(timeout=5)
        try:
            self.memory.close()
            self.memory.unlink()
        except (BufferError, FileNotFoundError):
            pass


# ---------------------------------------------------------------- the child

def _plain(value):
    """JSON for the numbers numpy hands back (a frame's layout and run read RDRAM through it)."""
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (tuple, set)):
        return list(value)
    raise TypeError(f"{type(value).__name__} is not JSON")


def _answer(evaluator, index: int) -> tuple[dict, bytes | None]:
    before = Counter(evaluator.recorded)
    started = time.perf_counter()
    resolved = evaluator.state(index)
    package = evaluator.package
    recorded_as = next((key for key, count in evaluator.recorded.items() if count != before[key]), "absent")
    answer = {"index": index, "recorded_as": recorded_as, "status": resolved.status, "entry": resolved.entry,
              "digests": [package.index.frames[index][4].hex(), package.states.rows[index][4].hex()],
              "ms": round((time.perf_counter() - started) * 1000, 2)}
    if resolved.status is not None:
        return answer, None
    result = resolved.result
    answer.update(words=list(resolved.frame.task_words), layout=resolved.layout,
                  result={"run": result["run"], "differing": result["differing"]}, notes=resolved.notes,
                  restored=resolved.restored, pointers=resolved.pointers, recorded=resolved.recorded,
                  ram_bytes=len(resolved.frame.ram))
    return answer, resolved.frame.ram


def _open(args, data: bytes):
    if args.evaluator:
        module, _, name = args.evaluator.partition(":")
        return getattr(importlib.import_module(module), name)(data, args.rom, args.map)
    import e2_georender
    import evaluator
    import package
    rom = args.rom.read_bytes()
    opened = evaluator.Evaluator(package.Package(data, rom), rom, e2_georender.load_map(args.map), cache=2)
    opened.state_cache = 1                       # each frame is asked for once
    return opened


def serve(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--memory", required=True)
    parser.add_argument("--package-bytes", type=int, required=True)
    for name in ("--rom", "--map"):
        parser.add_argument(name, type=Path, required=True)
    parser.add_argument("--evaluator")
    parser.add_argument("--warm", type=int, help="a frame to resolve before saying ready")
    args = parser.parse_args(argv)
    sink = sys.stdout.buffer

    def write(message: dict) -> None:
        sink.write((json.dumps(message, default=_plain) + "\n").encode("utf-8"))
        sink.flush()
    try:
        # track= exists from Python 3.13; 3.12 has no resource tracker for shared memory on Windows
        memory = shared_memory.SharedMemory(name=args.memory, **({"track": False} if sys.version_info >= (3, 13) else {}))
        evaluator = _open(args, bytes(memory.buf[:args.package_bytes]))
    except Exception as error:  # noqa: BLE001 - the server resolves in process and shows nothing of this
        write({"error": f"{type(error).__name__}: {error}"[:500]})
        return 1
    if args.warm is not None and hasattr(evaluator, "state"):
        # A child's first frame is cold (the game's code translated, the state cursor seeking: ~100 ms,
        # measured 2026-10-27); resolved before ready, the frame the server will want first is then in
        # its cache and the ones after it a step away, so Play never waits on that first one.
        with contextlib.suppress(Exception):    # only a warm-up: a frame that fails resolves when asked
            evaluator.state(args.warm)
    write({"ready": len(evaluator)})
    for line in sys.stdin.buffer:
        index = None
        try:
            request = json.loads(line)
            index, offset = request["index"], request["offset"]
            answer, ram = _answer(evaluator, index)
            if ram is not None:
                if not (args.package_bytes <= offset and offset + len(ram) <= memory.size):
                    raise ValueError("the state does not fit its shared memory slot")
                memory.buf[offset:offset + len(ram)] = ram
            write(answer)
        except Exception as error:  # noqa: BLE001 - that frame resolves in the server instead
            write({"index": index, "error": f"{type(error).__name__}: {error}"[:500]})
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
