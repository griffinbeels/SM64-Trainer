"""E3 delivery numbers: drive the Trainer's scene viewer path once and report it against plan section 8.

    uv run --with unicorn python tools/scene_lab/e3_measure.py --lab-config <lab.json> --package <name.sm64scene>
        [--out <report.json>] [--steps 60] [--seeks 40] [--drag-moves 180] [--play-seconds 4] [--seed 1]
        [--ignore-gpu-user <image.exe> ...] [--while-playing]
    ... e3_measure.py ... --fake      # a dry run of this instrument on protocol doubles: no renderer, no GPU

It refuses first, before building or starting anything, while the GPU hold stands or a game
may be running (gpu_gate.require_gpu(games=True): a full-screen program, Project64, or any
other process keeping a GPU 3D engine busy; --ignore-gpu-user names a non-game image such as
WindowsTerminal.exe). Then it builds viewer_host.exe (timed apart, so a compile never counts
as opening), serves the real routes (server/scene_lab_api.py behind the Trainer's local
access check, mount_scene_lab with its loop probe on) on a free loopback port in this
process, and drives them over HTTP and the WebSocket as the page did before it kept two
pictures in flight (2026-10-26): one picture in flight, a newer input replacing the waiting
one, input time kept from the earliest input. The page's own numbers, two in flight, are in
the UI observation log (surface scene_viewer).

Reported (milliseconds unless named): open (POST open) and open to first picture; recorded
camera step forward/back and random seek; free camera start (the evaluator's first build and
pose), first free picture, free step and free random seek (cold state seeks); camera input to
picture during a 60 Hz drag (received, and received plus JPEG decode); playback pictures per
second at 30 fps, recorded and free, each started from the frame on screen as the page plays (the
first picture apart, and how many after it took longer than a frame); exact-pixel ("raw")
pictures; server event-loop lateness
while a scene is open; the host's memory and CPU; and the host's exit after Close. The page's
own decode and draw are not in these numbers: read them from the viewer's readout. Results
are this desktop under its current load, not a universal minimum; the machine record is in
the report. Nothing here writes outside --out.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import platform
import random
import socket
import statistics
import sys
import threading
import time
import winreg
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
import gpu_gate  # noqa: E402

FRAME_BUDGET_MS = 1000 / 30                                    # plan section 8: one 30 Hz state step
TARGETS = {"warm_seek_p95_ms": 100.0, "cold_seek_ms": 1000.0, "camera_input_to_picture_p95_ms": 50.0,
           "playback_step_ms": FRAME_BUDGET_MS, "free_playback_step_ms": FRAME_BUDGET_MS}


def spread(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def at(p: float) -> float:
        return round(ordered[min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1)], 2)
    return {"count": len(values), "p50": at(.5), "p95": at(.95), "max": round(ordered[-1], 2),
            "mean": round(statistics.fmean(values), 2)}


def machine() -> dict:
    """OS, CPU and display adapters with driver versions, read from the registry."""
    record = {"os": platform.platform(), "logical_cpus": os.cpu_count(), "python": platform.python_version()}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            record["cpu"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except OSError:
        pass
    adapters = []
    display = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    for index in range(16):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{display}\\{index:04d}") as key:
                adapters.append({"name": winreg.QueryValueEx(key, "DriverDesc")[0],
                                 "driver": winreg.QueryValueEx(key, "DriverVersion")[0]})
        except OSError:
            continue
    record["display_adapters"] = adapters
    return record


class Server:
    """The real scene viewer routes, behind the Trainer's local access check, on a free port."""

    def __init__(self, config: Path, fake: bool, fake_host: bool = False):
        import uvicorn
        from fastapi import FastAPI

        from sm64_events.server.local_access import mount_local_access
        from sm64_events.server.scene_lab_api import mount_scene_lab
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        app = FastAPI()
        options = {}
        if fake or fake_host:
            import fake_viewer_host
            import scene_session
            options = {"host_factory": scene_session.command_host(fake_viewer_host.command(delay_ms=4))}
            if fake:
                options["evaluator_factory"] = lambda package, rom: fake_viewer_host.FakeEvaluator(delay_s=0.004)
        self.lab = mount_scene_lab(app, config, session_options=options, measure_loop=True)
        mount_local_access(app, port=self.port, protected=True)
        self.server = uvicorn.Server(uvicorn.Config(app, log_level="warning", ws_per_message_deflate=False,
                                                    ws_max_size=1 << 20, lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, kwargs={"sockets": [self.listener]},
                                       name="e3-measure-server", daemon=True)

    def __enter__(self) -> Server:
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise SystemExit("the measurement server did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=60)


class Page:
    """What the viewer page does, over the same HTTP routes and socket."""

    def __init__(self, port: int):
        import httpx
        self.port, self.origin = port, f"http://127.0.0.1:{port}"
        self.http = httpx.Client(base_url=self.origin, timeout=600)
        self.token = self.http.get("/api/local-access").json()["request_token"]
        self.ws = None
        self.sequence = 0
        self.session = None

    def post(self, path: str, body: dict | None = None) -> dict:
        response = self.http.post(path, json=body, headers={"X-SM64-Request-Token": self.token})
        if response.status_code != 200:
            raise SystemExit(f"{path}: {response.status_code} {response.text}")
        return response.json()

    def connect(self) -> None:
        from websockets.sync.client import connect
        self.ws = connect(f"ws://127.0.0.1:{self.port}/api/scene-lab/ws", origin=self.origin,
                          max_size=None, compression=None, open_timeout=30)

    def send(self, frame: int, camera=None, encoding: str = "jpeg", kind: str | None = None) -> int:
        self.sequence += 1
        message = {"id": self.sequence, "session": self.session, "frame": frame}
        message.update({"kind": kind} if kind else {"camera": camera, "encoding": encoding})
        self.ws.send(json.dumps(message))
        return self.sequence

    def reply(self, timeout: float | None = 600) -> dict:
        data = self.ws.recv(timeout=timeout)
        if isinstance(data, str):
            message = json.loads(data)
            if message.get("closed") or (message.get("error") and not message.get("superseded")):
                raise SystemExit(f"the viewer refused a request: {message}")
            return message
        length = int.from_bytes(data[:4], "little")
        header = json.loads(data[4:4 + length])
        header["payload"] = data[4 + length:]
        return header

    def picture(self, frame: int, camera=None, encoding: str = "jpeg") -> tuple[dict, float]:
        started = time.perf_counter()
        ident = self.send(frame, camera, encoding)
        while True:
            header = self.reply()
            if header.get("id") == ident and "payload" in header:
                return header, (time.perf_counter() - started) * 1000

    def pose(self, frame: int) -> tuple[dict, float]:
        started = time.perf_counter()
        ident = self.send(frame, kind="pose")
        while True:
            message = self.reply()
            if message.get("id") == ident and message.get("kind") == "pose":
                return message, (time.perf_counter() - started) * 1000


def orbit_camera(pose: dict, yaw: float, pitch: float, dist: float | None = None) -> dict:
    """sceneviewer.js's orbit: yaw about the world's up axis around the recorded focus."""
    focus = pose["focus"]
    offset = [pose["position"][i] - focus[i] for i in range(3)]
    radius = dist or max(50.0, math.hypot(*offset))
    base_yaw = math.atan2(offset[0], offset[2])
    base_pitch = math.asin(max(-1.0, min(1.0, offset[1] / radius)))
    y, p = base_yaw + yaw, max(-1.45, min(1.45, base_pitch + pitch))
    return {"focus": list(focus), "position": [focus[0] + radius * math.cos(p) * math.sin(y),
                                              focus[1] + radius * math.sin(p),
                                              focus[2] + radius * math.cos(p) * math.cos(y)]}


def drag(page: Page, frame: int, pose: dict, moves: int) -> dict:
    """A 60 Hz drag: one picture in flight, the newest camera replacing the waiting input, each
    picture timed from the EARLIEST input it answers (as the page times it)."""
    from PIL import Image
    received, decoded, sent = [], [], 0
    next_tick, waiting, flight, made = time.perf_counter(), None, None, 0
    while made < moves or flight is not None or waiting is not None:
        now = time.perf_counter()
        if made < moves and now >= next_tick:
            made += 1
            next_tick += 1 / 60
            camera = orbit_camera(pose, math.radians(made * 0.75), math.radians(10 * math.sin(made / 20)))
            waiting = (waiting[0] if waiting else now, camera)
        if flight is None and waiting is not None:
            flight = (page.send(frame, waiting[1]), waiting[0])
            waiting, sent = None, sent + 1
        timeout = max(0.0, next_tick - time.perf_counter()) if made < moves else 600
        try:
            header = page.reply(timeout=timeout)
        except TimeoutError:
            continue
        if flight and header.get("id") == flight[0] and "payload" in header:
            arrived = time.perf_counter()
            received.append((arrived - flight[1]) * 1000)
            Image.open(io.BytesIO(header["payload"])).load()
            decoded.append((time.perf_counter() - flight[1]) * 1000)
            flight = None
    return {"inputs": moves, "pictures": sent, "input_to_received_ms": spread(received),
            "input_to_decoded_ms": spread(decoded)}


def playback(page: Page, start: int, count: int, seconds: float, camera=None) -> dict:
    """30 fps playback as the page runs it: the frame due now is asked for whenever nothing is in
    flight; frames whose time passed while one was drawn are skipped. The first picture is a seek
    when `start` is not the frame last shown, so it is reported apart, with how many of the rest
    took longer than one frame."""
    shown, started = 0, time.perf_counter()
    last, times = None, []
    while time.perf_counter() - started < seconds:
        due = (start + int((time.perf_counter() - started) * 30)) % count     # wraps at the end
        if due == last:
            time.sleep(0.001)
            continue
        times.append(page.picture(due, camera)[1])
        shown, last = shown + 1, due
    elapsed = time.perf_counter() - started
    return {"seconds": round(elapsed, 2), "pictures": shown, "pictures_per_second": round(shown / elapsed, 1),
            "first_picture_ms": round(times[0], 1) if times else None, "picture_ms": spread(times[1:]),
            "late_pictures": sum(ms > FRAME_BUDGET_MS for ms in times[1:])}


def host_resources(pid: int) -> dict:
    from sm64_events.core.procmem import descendants, machine_snapshot, tree_summary
    snapshot = machine_snapshot()
    if snapshot is None or pid not in snapshot.procs:
        return {"pid": pid, "running": False}
    members = [snapshot.procs[pid], *descendants(snapshot, pid)]
    summary = tree_summary(members)
    return {"pid": pid, "running": True, "processes": summary["count"],
            "rss_mib": round(summary["rss_bytes"] / 2**20, 1), "private_mib": round(summary["private_bytes"] / 2**20, 1),
            "threads": summary["threads"], "cpu_s": round(snapshot.procs[pid].cpu / 1e7, 2)}


def verdicts(report: dict) -> dict:
    """Plan section 8's starting targets. Warm seek: recorded-camera random seeks (every task
    decodes on its own). Cold seek: the slowest seek either camera made (a free-camera seek
    rebuilds the scene state from a checkpoint). Playback: the median step, each camera."""
    seeks = report["recorded"]["seek_ms"]
    free = report.get("free_camera", {})
    camera = free.get("drag", {}).get("input_to_decoded_ms", {})
    found = {"warm_seek_p95_ms": seeks.get("p95"),
             "cold_seek_ms": max([value for value in (seeks.get("max"), free.get("seek_ms", {}).get("max"))
                                  if value is not None], default=None),
             "camera_input_to_picture_p95_ms": camera.get("p95"),
             "playback_step_ms": report["recorded"]["step_forward_ms"].get("p50"),
             "free_playback_step_ms": free.get("step_ms", {}).get("p50")}
    return {name: {"target": TARGETS[name], "measured": value,
                   "pass": None if value is None else value <= TARGETS[name]} for name, value in found.items()}


def recorded_camera(page: Page, count: int, steps: int, args, rng: random.Random) -> dict:
    """Step forward and back, random seeks, exact-pixel pictures and 30 fps playback."""
    timings = {"step_forward_ms": [page.picture(frame)[1] for frame in range(1, steps + 1)],
               "step_back_ms": [page.picture(frame)[1] for frame in range(steps - 1, -1, -1)],
               "seek_ms": [page.picture(rng.randrange(count))[1] for _ in range(args.seeks)]}
    raw = [page.picture(rng.randrange(count), encoding="raw") for _ in range(5)]
    report = {name: spread(values) for name, values in timings.items()}
    report.update(raw_picture_ms=spread([ms for _header, ms in raw]), raw_bytes=len(raw[0][0]["payload"]),
                  jpeg_bytes=len(page.picture(0)[0]["payload"]), playback=playback(page, 0, count, args.play_seconds))
    return report


def free_camera(page: Page, count: int, steps: int, args, rng: random.Random) -> dict:
    """The evaluator's start, the first free picture, a drag, free steps and seeks, playback."""
    anchor = rng.randrange(max(1, count - steps))          # room for every free step after it
    pose, start_ms = page.pose(anchor)
    report: dict = {"start_ms": round(start_ms, 1), "pose_reason": pose.get("reason")}
    if not pose.get("pose"):
        return report
    camera = orbit_camera(pose["pose"], math.radians(30), math.radians(10))
    header, first_ms = page.picture(anchor, camera)
    report.update(first_picture_ms=round(first_ms, 1), first_picture_camera=header["camera"],
                  first_picture_reason=header["reason"], drag=drag(page, anchor, pose["pose"], args.drag_moves))
    stepped = [page.picture(frame, camera) for frame in range(anchor, min(count, anchor + steps))]
    sought = [page.picture(rng.randrange(count), camera) for _ in range(args.seeks)]
    timings = [header["timings"] for header, _ms in stepped]
    report.update(step_ms=spread([ms for _header, ms in stepped]), seek_ms=spread([ms for _header, ms in sought]),
                  recorded_fallbacks=sum(header["camera"] != "free" for header, _ms in stepped + sought),
                  evaluate_ms=spread([t["evaluate_ms"] for t in timings if "evaluate_ms" in t]),
                  host_ms=spread([t["host_ms"] for t in timings]))
    page.picture(anchor, camera)          # the page plays from the frame on screen; the seeks measured the rest
    report["playback"] = playback(page, anchor, count, args.play_seconds, camera)
    return report


def measure(args) -> dict:
    drawn = not (args.fake or args.fake_host)
    if drawn:                            # first, before anything is built or started
        gpu_gate.require_gpu(games=not args.while_playing, ignore=tuple(args.ignore_gpu_user))
    report: dict = {"package": args.package, "fake": args.fake, "fake_host": args.fake_host, "machine": machine(),
                    "note": "page decode and draw excluded; see the viewer's readout"}
    if args.while_playing:
        report["while_playing"] = gpu_gate.game_reasons(tuple(args.ignore_gpu_user))
    if drawn:
        import viewer
        started = time.perf_counter()
        viewer.build()                   # a compile, when the host is not cached, never counts as opening
        report["host_build_ms"] = round((time.perf_counter() - started) * 1000, 1)
    rng = random.Random(args.seed)
    with Server(args.lab_config, args.fake, args.fake_host) as server:
        page = Page(server.port)
        started = time.perf_counter()
        opened = page.post("/api/scene-lab/open", {"package": args.package})
        report["open_ms"] = round((time.perf_counter() - started) * 1000, 1)
        page.session = opened["session"]
        page.connect()
        page.picture(0)
        report["open_to_first_picture_ms"] = round((time.perf_counter() - started) * 1000, 1)
        count = len(opened["frames"])
        report["frames"], report["free_camera_available"] = count, opened["free_camera"]
        steps = min(args.steps, count - 1)
        report["recorded"] = recorded_camera(page, count, steps, args, rng)
        if opened["free_camera"]["available"]:
            report["free_camera"] = free_camera(page, count, steps, args, rng)
        report["loop"] = page.http.get("/api/scene-lab/status").json().get("loop")
        report["host"] = host_resources(opened["renderer_pid"])
        page.ws.close()
        closed = page.post("/api/scene-lab/close")
        report["host_exit"] = closed["host_exit"]
        report["host_after_close"] = host_resources(opened["renderer_pid"])
    report["targets"] = verdicts(report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lab-config", type=Path, required=True, help="the lab config SM64_SCENE_LAB would name")
    parser.add_argument("--package", required=True, help="a package name the lab's folder lists")
    parser.add_argument("--out", type=Path, help="write the report here as well as to stdout")
    parser.add_argument("--steps", type=int, default=60)
    parser.add_argument("--seeks", type=int, default=40)
    parser.add_argument("--drag-moves", type=int, default=180, help="60 Hz camera inputs (180 = 3 s)")
    parser.add_argument("--play-seconds", type=float, default=4.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--ignore-gpu-user", action="append", default=[],
                        help="an image name whose GPU use is not a game (repeatable)")
    parser.add_argument("--while-playing", action="store_true",
                        help="Griffin said to run beside his game: skip the game check (the hold still "
                             "applies) and record what was running, since it loads the numbers")
    parser.add_argument("--fake", action="store_true", help="protocol doubles instead of the renderer and "
                        "evaluator: checks this instrument, measures nothing real")
    parser.add_argument("--fake-host", action="store_true", help="the real evaluator (the game's code under "
                        "Unicorn) with the renderer's protocol double (4 ms a picture): the server's own cost "
                        "of a free camera, CPU only, beside a running game")
    args = parser.parse_args(argv)
    report = measure(args)
    text = json.dumps(report, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
