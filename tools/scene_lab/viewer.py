"""E3 lab: deliver REAL-renderer pictures of a recorded scene into a browser page.

    uv run python tools/scene_lab/viewer.py serve <tap_session | frame_dir...> --pj64-plugin <dir>
        [--resolution WxH] [--port N]
    uv run python tools/scene_lab/viewer.py check <tap_session | frame_dir...> --pj64-plugin <dir>
        --against <replay.py render dir> [--resolution WxH]

serve starts ONE viewer_host.c (x86, built and cached like replay_host) over the
Trainer's renderer where the bundle keeps it (replay.trainer_renderer(), never a
copy), warms it, and serves viewer.html on a free localhost port; the page asks a
WebSocket for {frame, camera, encoding} and receives ReadScreen's picture.
Nothing renders unless the page asks: the host blocks on its pipe, so a
paused page costs no work. check is the server-side byte proof: every frame
in forward, reverse and shuffled order, and each again after a moved camera,
must hash like replay.py's render of the same frame (`tap.py frames --pair
next` then `replay.py render --warmup <first frame>`).

A tap session pairs each task with the next presented picture (tap.py's
--pair next), which is right for offline hosts and savestate-derived
sessions alike. A frame dir sends its closure.json ranges, or its whole
rdram.bin when it has none. --resolution picks the renderer settings folder
of that window size (replay.settings_folder); Project64's Plugin folder is
read, never written.
Sessions are private RAM images: nothing here writes them anywhere.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import io
import json
import math
import os
import queue
import random
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

# Module level, not inside make_app: with postponed annotations FastAPI
# resolves `websocket: WebSocket` through module globals (a local import
# turns it into a query parameter and every handshake gets 403).
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import recamera  # noqa: E402
import replay  # noqa: E402  (also puts src/ on sys.path)
import tap  # noqa: E402
from gpu_gate import require_gpu  # noqa: E402
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402

REQUEST = struct.Struct("<5I16I14I16d")
RESPONSE = struct.Struct("<9I")
REQUEST_MAGIC, RESPONSE_MAGIC = 0x51525756, 0x53525756
RENDER, QUIT, GHOST_SETUP, GHOST_LOAD = 1, 2, 3, 4      # request kinds
CAMERA, DISCARD, GHOSTS = 1, 2, 4                         # render flags
# The scene viewer's ghosts (plugin/gfxwrap/ghost_viewer.h): a render's ghosts block is
# u32 count, gv_look, then count gv_ghost; a load's payload is its 64-byte label, then the clip.
GHOST_LOOK = struct.Struct("<2f2I")      # opacity, name box opacity, flags, reserved
GHOST_ENTRY = struct.Struct("<Ii2I")     # slot, frame, hue degrees, reserved
GHOST_SLOTS = 16                         # GV_GHOSTS
LABEL_BYTES = 64
GHOST_FLAGS = {"tint": 1, "shadow": 2, "names": 4, "inputs": 8, "values": 16, "white_name": 32}
GV_ABSENT = -100                         # viewer_host.c: the renderer has no ghost table
IDENTITY = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0)
ENCODINGS = ("raw", "jpeg")


@dataclass
class Frame:
    """One graphics task as the host takes it: OSTask words, VI registers, closure."""
    label: str
    task: tuple
    vi: tuple
    runs: bytes          # packed <II (begin, end) RDRAM storage ranges
    data: bytes          # their bytes, concatenated
    identity: dict = field(default_factory=dict)
    ghosts: bytes | None = None   # the ghosts block this picture draws (ghost_block), or none


def ghost_block(look: tuple, ghosts: list[tuple[int, int, int]]) -> bytes:
    """The ghosts block of one picture: look = (opacity, name box opacity, flags) and each
    ghost's (slot, frame, hue degrees), frame being the game frames since the stage's first."""
    if len(ghosts) > GHOST_SLOTS:
        raise ValueError(f"a picture draws at most {GHOST_SLOTS} ghosts")
    return (struct.pack("<I", len(ghosts)) + GHOST_LOOK.pack(*look, 0)
            + b"".join(GHOST_ENTRY.pack(slot, frame, hue, 0) for slot, frame, hue in ghosts))


@dataclass
class Picture:
    width: int
    height: int
    pixels: bytes | memoryview   # ReadScreen's BGR rows, bottom-up (a view of `slot` when it has one)
    render_ms: float     # ProcessDList + UpdateScreen inside the host
    read_ms: float       # ReadScreen
    camera_ms: float
    matrices: int
    camera_failed: bool
    host_ms: float       # request written to picture read, as Python sees it
    slot: int | None = None   # the Host's slots slot the pixels were read into; its owner releases it
    status: int = 0      # the response's status word (viewer_host.c)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pixels).hexdigest()

    @property
    def ghosts(self) -> dict:
        """What the picture's ghosts block did: asked, chosen (a row to show), drawn, whether
        their image reached the screen, pending (the overlay did not reach them yet) and refused
        (the renderer drew none: a slot it does not hold)."""
        return {"asked": self.status >> 24 & 0xFF, "chosen": self.status >> 16 & 0xFF,
                "drawn": self.status >> 8 & 0xFF, "displayed": bool(self.status & 4),
                "pending": bool(self.status & 2), "refused": bool(self.status & 8)}


def _fill(stream, view: memoryview) -> int:
    """Reads into `view` until it is full or the stream ends; the bytes it filled."""
    done = 0
    while done < len(view):
        got = stream.readinto(view[done:])
        if not got:
            break
        done += got
    return done


def load_session(session: Path) -> tuple[list[Frame], str]:
    header = dict(line.partition("=")[::2] for line in
                  (session / "session.txt").read_text(encoding="ascii").splitlines())
    identity = []
    armed = session / "armed.txt"
    for line in armed.read_text(encoding="ascii").splitlines() if armed.is_file() else ():
        if line.startswith("# identity="):
            for item in filter(None, line.removeprefix("# identity=").split(",")):
                name, address, size = item.split(":")
                identity.append((name, int(address, 16), int(size)))
    frames, pending = [], None
    for record in tap.read_records(session / "tap.bin"):   # mapped, not read whole
        if isinstance(record, tap.Task):
            pending = record
        elif isinstance(record, tap.Present) and pending is not None:
            frames.append(Frame(
                f"task {pending.fields['task_index']}", pending.task,
                tuple(record.vi[name] for name in tap.VI_NAMES),
                struct.pack(f"<{2 * len(pending.runs)}I", *(edge for run in pending.runs for edge in run)),
                pending.run_bytes,
                {name: tap.word_value(pending.extras, address, size) for name, address, size in identity}))
            pending = None
    return frames, header.get("rom_header", "")


def load_frame_dirs(dirs: list[Path]) -> tuple[list[Frame], str]:
    frames, headers = [], set()
    for directory in dirs:
        meta = json.loads((directory / "frame.json").read_text(encoding="utf-8"))
        rdram = (directory / "rdram.bin").read_bytes()
        ranges = [(0, len(rdram))]
        closure = directory / "closure.json"
        if closure.is_file():   # whole words, as replay.py poison widens them
            ranges = [(int(entry["begin"]) & ~3, min((int(entry["end"]) + 3) & ~3, len(rdram)))
                      for entry in json.loads(closure.read_text(encoding="utf-8"))["ranges"]]
        task = struct.unpack_from("<16I", (directory / "dmem.bin").read_bytes(), 0xFC0)
        frames.append(Frame(directory.name, task, tuple(meta["vi"][name] for name in tap.VI_NAMES),
                            struct.pack(f"<{2 * len(ranges)}I", *(edge for run in ranges for edge in run)),
                            b"".join(rdram[begin:end] for begin, end in ranges),
                            meta.get("identity") or {}))
        headers.add(meta["rom_header"])
    if len(headers) != 1:
        raise SystemExit("frame dirs from different ROMs: one renderer instance opens one ROM")
    return frames, headers.pop()


def load_sources(paths: list[Path]) -> tuple[list[Frame], str]:
    if len(paths) == 1 and (paths[0] / "tap.bin").is_file():
        frames, header = load_session(paths[0])
    else:
        frames, header = load_frame_dirs(paths)
    if not frames or len(header) != 128:
        raise SystemExit("no frames, or no 64-byte ROM header, in the given sources")
    return frames, header


def build() -> Path:
    """viewer_host.exe. The installed app runs its own copy (core/paths.py bundled_lab_file) and
    never compiles; a checkout compiles the one recipe (tools/build_lab.py) into the
    scene lab's build cache, keyed by the build id it carries."""
    if getattr(sys, "frozen", False):
        from sm64_events.core.paths import bundled_lab_file
        path = bundled_lab_file("viewer_host.exe")
        if path is None:
            raise RuntimeError("This copy of SM64 Trainer is missing viewer_host.exe, the program that draws "
                               "Ghost Lab pictures. Reinstall SM64 Trainer.")
        return path
    spec = importlib.util.spec_from_file_location("scene_lab_build", ROOT / "tools/build_lab.py")
    recipes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipes)
    return recipes.cached("viewer_host.exe")


class Host:
    """One viewer_host.exe and its pipe. The host answers requests in the order they were
    written. A reader thread takes each answer off the pipe the moment the host writes it, so
    the host never waits on a full pipe: a caller may write the next request (send) while the
    host draws the one before, and another thread collects the pictures in order (receive).
    render() is send then receive. Closing stdin is the shutdown, and a host whose parent dies
    sees EOF. `command` replaces the built executable (fake_viewer_host.py, a protocol double,
    in tests and dry runs)."""

    def __init__(self, renderer: Path, settings: Path, header: str, command: list[str] | None = None,
                 ghosts: bool = False):
        self.process = subprocess.Popen(
            [*(command or [str(build())]), "--renderer", str(renderer), "--settings", str(settings),
             "--header", header, *(["--ghosts", "1"] if ghosts else [])],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **quiet_spawn_kwargs())
        self.lock = threading.Lock()                      # writing a request
        self.counting = threading.Lock()                  # self.waiting
        self.waiting = 0                                  # requests whose picture nobody has received yet
        self.sent: deque[tuple[float, bool]] = deque()    # (write time, a call) of requests the reader has yet to read
        self.answers: queue.SimpleQueue = queue.SimpleQueue()   # Pictures in order, then what ended the pipe
        # A call's answer (a ghost setup or load, or a render its caller waits on itself, call()):
        # its own queue, so a call made while pictures are in flight never takes their answers.
        self.calls: queue.SimpleQueue = queue.SimpleQueue()
        # Where the reader puts pixels: None, a new bytes per picture; else slots with claim(size) ->
        # (slot, writable view) or None when none is free (picture_encoder.PictureSlots).
        self.slots = None
        self.errors: list[str] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self.reader = threading.Thread(target=self._read_answers, name="viewer-host-reader", daemon=True)
        self.reader.start()

    def _drain_stderr(self) -> None:
        for line in self.process.stderr:
            self.errors.append(line.decode("utf-8", "replace").rstrip())

    def _read_answers(self) -> None:
        """The reader thread: each answer in order, its host_ms from its request's write to its
        last byte, until the pipe ends; then the error every later receive raises."""
        stdout = self.process.stdout
        try:
            while True:
                head = stdout.read(RESPONSE.size)
                if len(head) != RESPONSE.size:
                    break
                magic, status, width, height, render_us, read_us, camera_us, matrices, size = RESPONSE.unpack(head)
                if magic != RESPONSE_MAGIC or size != width * height * 3 or not self.sent:
                    self._end(RuntimeError("viewer_host answered out of protocol"))
                    return
                sent, call = self.sent.popleft()
                claimed = self.slots.claim(size) if self.slots is not None and not call else None
                if claimed is None:
                    slot, pixels = None, stdout.read(size) if size else b""
                    if len(pixels) != size:
                        break
                else:
                    slot, pixels = claimed
                    if _fill(stdout, pixels) != size:
                        break
                host_ms = (time.perf_counter() - sent) * 1000
                (self.calls if call else self.answers).put(
                    Picture(width, height, pixels, render_us / 1000, read_us / 1000, camera_us / 1000, matrices,
                            bool(status & 1), host_ms, slot, status))
        except (OSError, ValueError) as error:
            self._end(RuntimeError(f"viewer_host's pipe failed: {error}"))
            return
        try:
            code = self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            code = None
        self._end(RuntimeError(f"viewer_host exited ({code}): {' | '.join(self.errors)}"))

    def _end(self, error: Exception) -> None:
        self.answers.put(error)
        self.calls.put(error)

    def send(self, frame: Frame, camera=None, discard: bool = False, *, call: bool = False) -> None:
        """Writes one request; the next receive() answers the oldest request not yet received
        (with `call`, the next _answer() instead)."""
        flags = ((CAMERA if camera is not None else 0) | (DISCARD if discard else 0)
                 | (GHOSTS if frame.ghosts is not None else 0))
        request = REQUEST.pack(REQUEST_MAGIC, RENDER, flags, len(frame.runs) // 8, len(frame.data),
                               *frame.task, *frame.vi, *(camera if camera is not None else IDENTITY))
        self._write(request + frame.runs, frame.data, frame.ghosts or b"", call)

    def _write(self, head: bytes, data: bytes, tail: bytes, call: bool) -> None:
        with self.lock:
            if not call:
                with self.counting:
                    self.waiting += 1
            self.sent.append((time.perf_counter(), call))
            self.process.stdin.write(head)
            self.process.stdin.write(data)
            if tail:
                self.process.stdin.write(tail)
            self.process.stdin.flush()

    def _answer(self) -> Picture:
        answer = self.calls.get()
        if isinstance(answer, Exception):
            self.calls.put(answer)
            raise answer
        return answer

    def call(self, frame: Frame, discard: bool = True) -> Picture:
        """A render whose answer this caller waits for itself, beside any pictures in flight
        (a ghost warm-up: the scene's worker draws while the delivery thread collects)."""
        self.send(frame, discard=discard, call=True)
        return self._answer()

    def _ghost_request(self, kind: int, word: int, payload: bytes) -> int:
        request = REQUEST.pack(REQUEST_MAGIC, kind, 0, 0, len(payload), word, *(0,) * 15, *(0,) * 14, *IDENTITY)
        self._write(request, payload, b"", True)
        return struct.unpack("<i", struct.pack("<I", self._answer().status))[0]

    def ghost_setup(self, layout: bytes, rom: bytes) -> int:
        """Builds the ghost model from the ROM and binds the capture layout the stage is read
        with: 0, or a GV result (ghost_viewer.h; GV_ABSENT for a renderer without ghosts)."""
        return self._ghost_request(GHOST_SETUP, 0, layout + rom)

    def ghost_load(self, slot: int, clip: bytes, label: str) -> int:
        """Loads a saved recording into a slot under a label (empty bytes empty the slot):
        0, or a GV result (-3: a clip saved playback would refuse)."""
        encoded = label.encode("utf-8")
        if len(encoded) >= LABEL_BYTES:
            raise ValueError("a ghost label is at most 63 bytes")
        return self._ghost_request(GHOST_LOAD, slot, encoded.ljust(LABEL_BYTES, b"\0") + clip)

    def receive(self) -> Picture:
        """The picture for the oldest request not yet received, waiting for the host if need be.
        RuntimeError when nothing was sent, and once the host has ended or broken protocol."""
        with self.counting:
            if not self.waiting:
                raise RuntimeError("viewer_host has no request to answer")
            self.waiting -= 1
        answer = self.answers.get()
        if isinstance(answer, Exception):
            self.answers.put(answer)                      # every later receive raises it too
            raise answer
        return answer

    def render(self, frame: Frame, camera=None, discard: bool = False) -> Picture:
        self.send(frame, camera, discard)
        return self.receive()

    def close(self) -> int:
        """Ends the host: it finishes the requests it was sent, then sees EOF. The reader keeps
        taking its answers, so it never waits on a full pipe to exit."""
        try:
            self.process.stdin.close()
        except OSError:
            pass                                          # it already ended: its exit code says how
        try:
            code = self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            code = self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        return code


def orbit(yaw_degrees: float, pitch_degrees: float = 0.0, pivot: float = 1500.0) -> tuple:
    """recamera.orbit as the 16 row-major numbers the host takes."""
    matrix = recamera.orbit(math.radians(yaw_degrees), math.radians(pitch_degrees), pivot)
    return tuple(float(value) for value in matrix.reshape(16))


class Viewer:
    """The server's state: the frames, the one host, and a log of what it served."""

    def __init__(self, args):
        pj64_plugin = Path(args.pj64_plugin or os.environ.get("SM64_PJ64_PLUGIN", ""))
        if not (pj64_plugin / replay.CONFIG_FILES[0]).is_file():
            raise SystemExit("--pj64-plugin (or SM64_PJ64_PLUGIN) must name Project64's Plugin folder")
        self.frames, header = load_sources(args.sources)
        self.resolution = tuple(int(part) for part in args.resolution.lower().split("x")) if args.resolution else None
        renderer = replay.trainer_renderer()
        settings = replay.settings_folder(pj64_plugin, self.resolution)
        self.source = ", ".join(path.name for path in args.sources)
        self.renderer = renderer.name
        started = time.perf_counter()
        self.host = Host(renderer, settings, header)
        warm = self.host.render(self.frames[0], discard=True)   # an instance's first list renders wrong
        self.startup_ms = (time.perf_counter() - started) * 1000
        self.warm_ms = warm.host_ms
        self.served: deque[dict] = deque(maxlen=10000)

    def close(self) -> None:
        code = self.host.close()
        print(f"viewer_host exited {code}")

    def serve_one(self, message: dict) -> bytes:
        """One page request -> [u32 header length][header JSON][payload]."""
        index, camera, encoding = message.get("frame"), message.get("camera"), message.get("encoding", "raw")
        if not isinstance(index, int) or not 0 <= index < len(self.frames) or encoding not in ENCODINGS:
            raise ValueError("frame out of range or unknown encoding")
        if camera is not None and (not isinstance(camera, list) or len(camera) != 16 or not all(
                isinstance(value, (int, float)) and math.isfinite(value) for value in camera)):
            raise ValueError("camera must be 16 finite numbers or null")
        received = time.perf_counter()
        picture = self.host.render(self.frames[index], camera)
        hashing = time.perf_counter()
        digest = picture.sha256   # of ReadScreen's own bytes, before any encoding
        started = time.perf_counter()
        if encoding == "jpeg":
            from PIL import Image
            buffer = io.BytesIO()
            Image.frombuffer("RGB", (picture.width, picture.height), picture.pixels, "raw", "BGR", 0, -1) \
                .save(buffer, "JPEG", quality=90)
            payload = buffer.getvalue()
        else:
            payload = picture.pixels
        header = {"id": message.get("id"), "frame": index, "width": picture.width, "height": picture.height,
                  "encoding": encoding, "format": "bgr24-bottom-up" if encoding == "raw" else "image/jpeg",
                  "sha256": digest, "camera": camera is not None, "camera_failed": picture.camera_failed,
                  "matrices": picture.matrices, "render_ms": round(picture.render_ms, 3),
                  "read_ms": round(picture.read_ms, 3), "camera_ms": round(picture.camera_ms, 3),
                  "host_ms": round(picture.host_ms, 3),
                  "hash_ms": round((started - hashing) * 1000, 3),
                  "encode_ms": round((time.perf_counter() - started) * 1000, 3),
                  "server_ms": round((time.perf_counter() - received) * 1000, 3),
                  "request_bytes": REQUEST.size + len(self.frames[index].runs) + len(self.frames[index].data),
                  "payload_bytes": len(payload)}
        self.served.append({key: header[key] for key in ("frame", "camera", "sha256", "encoding", "render_ms",
                                                          "read_ms", "host_ms", "hash_ms", "encode_ms",
                                                          "server_ms", "payload_bytes")})
        encoded = json.dumps(header).encode("utf-8")
        return struct.pack("<I", len(encoded)) + encoded + payload

    def stats(self) -> dict:
        import psutil
        process = psutil.Process(self.host.process.pid)
        times, memory = process.cpu_times(), process.memory_info()
        return {"host_pid": process.pid, "host_cpu_s": round(times.user + times.system, 3),
                "host_rss_mib": round(memory.rss / 2**20, 1),
                "host_private_mib": round(getattr(memory, "private", 0) / 2**20, 1),
                "host_threads": process.num_threads(), "served": len(self.served),
                "server_cpu_s": round(sum(psutil.Process().cpu_times()[:2]), 3)}


def make_app(viewer: Viewer) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def page():
        return FileResponse(HERE / "viewer.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/session")
    def session():
        return {"source": viewer.source, "renderer": viewer.renderer, "fps": 30,
                "resolution": "x".join(map(str, viewer.resolution)) if viewer.resolution else "Project64's",
                "startup_ms": round(viewer.startup_ms, 1), "warm_ms": round(viewer.warm_ms, 1),
                "frames": [{"label": frame.label, "identity": frame.identity,
                            "closure_bytes": len(frame.data)} for frame in viewer.frames]}

    @app.get("/api/stats")
    def stats():
        return viewer.stats()

    @app.get("/api/served")
    def served():
        return list(viewer.served)

    @app.post("/api/report")
    async def report(request: Request):
        """The page's measurement run, printed so a headless run needs no DevTools."""
        print("report:", json.dumps(await request.json()), flush=True)
        return {"ok": True}

    @app.websocket("/ws")
    async def pictures(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                try:
                    reply = await asyncio.to_thread(viewer.serve_one, message)
                except ValueError as error:
                    await websocket.send_json({"id": message.get("id"), "error": str(error)})
                    continue
                await websocket.send_bytes(reply)
        except WebSocketDisconnect:
            pass

    return app


def serve(args) -> int:
    import uvicorn
    viewer = Viewer(args)
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", args.port))
        # Pictures are already as small as their encoding makes them; deflating
        # megabytes per message would cost more than the loopback saves.
        server = uvicorn.Server(uvicorn.Config(make_app(viewer), log_level="warning", ws_max_size=1 << 20,
                                               ws_per_message_deflate=False))
        print(f"{len(viewer.frames)} frames from {viewer.source}; host warm in {viewer.startup_ms:.0f} ms", flush=True)
        print(f"viewer: http://127.0.0.1:{listener.getsockname()[1]}/", flush=True)
        server.run(sockets=[listener])
    finally:
        viewer.close()
    return 0


def check(args) -> int:
    viewer = Viewer(args)
    try:
        frames = viewer.frames
        expected = {}
        for index in range(len(frames)):
            raw = args.against / f"{index:04d}.raw"
            expected[index] = hashlib.sha256(raw.read_bytes()).hexdigest() if raw.is_file() else None
        shuffled = list(range(len(frames)))
        random.Random(1).shuffle(shuffled)
        orders = {"forward": list(range(len(frames))), "reverse": list(reversed(range(len(frames)))),
                  "shuffled": shuffled}
        report, timings, mismatches = {}, [], []
        for name, order in orders.items():
            for index in order:
                picture = viewer.host.render(frames[index])
                timings.append(picture)
                if picture.sha256 != expected[index]:
                    mismatches.append(f"{name} frame {index}")
            report[name] = len(order)
        # recamera.py's defaults (yaw 30, pivot 1500): --against-camera holds
        # replay.py's render of recamera.py's output for the same frames.
        moved, camera_matches = 0, []
        for index in range(len(frames)):
            turned = viewer.host.render(frames[index], orbit(30.0))
            back = viewer.host.render(frames[index])
            moved += turned.sha256 != expected[index] and turned.matrices > 0
            if args.against_camera:
                raw = args.against_camera / f"{index:04d}.raw"
                camera_matches.append(raw.is_file() and hashlib.sha256(raw.read_bytes()).hexdigest() == turned.sha256)
            if back.sha256 != expected[index]:
                mismatches.append(f"recorded after camera, frame {index}")

        def pct(values, p):
            values = sorted(values)
            return round(values[min(len(values) - 1, int(p * (len(values) - 1) + 0.5))], 2)

        print(json.dumps({
            "frames": len(frames), "with_reference": sum(value is not None for value in expected.values()),
            "renders": report, "camera_moved_picture": f"{moved}/{len(frames)}",
            "camera_matches_recamera": f"{sum(camera_matches)}/{len(camera_matches)}" if args.against_camera else None,
            "size": f"{timings[0].width}x{timings[0].height}", "mismatches": mismatches,
            "render_ms": {"p50": pct([t.render_ms for t in timings], .5), "p95": pct([t.render_ms for t in timings], .95)},
            "read_ms": {"p50": pct([t.read_ms for t in timings], .5), "p95": pct([t.read_ms for t in timings], .95)},
            "host_roundtrip_ms": {"p50": pct([t.host_ms for t in timings], .5),
                                  "p95": pct([t.host_ms for t in timings], .95)},
            "startup_ms": round(viewer.startup_ms), "stats": viewer.stats(),
        }, indent=2))
        cameras_ok = not args.against_camera or all(camera_matches)
        return 0 if not mismatches and all(expected.values()) and cameras_ok else 1
    finally:
        viewer.close()


def main() -> int:
    require_gpu()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name, handler in (("serve", serve), ("check", check)):
        command = commands.add_parser(name)
        command.add_argument("sources", type=Path, nargs="+", help="one tap session, or frame dirs")
        command.add_argument("--pj64-plugin", help="Project64's Plugin folder (read only); default SM64_PJ64_PLUGIN")
        command.add_argument("--resolution", help="windowed WxH (its own renderer settings folder); default Project64's")
        command.set_defaults(handler=handler)
    commands.choices["serve"].add_argument("--port", type=int, default=0, help="default: a free port")
    commands.choices["check"].add_argument("--against", type=Path, required=True,
                                           help="replay.py render output for the same frames")
    commands.choices["check"].add_argument("--against-camera", type=Path,
                                           help="replay.py render of recamera.py (defaults) for the same frames")
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
