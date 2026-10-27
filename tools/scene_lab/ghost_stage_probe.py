"""Several runners in one scene (M4 multi-runner probe, lab only, experimental).

    uv run --with unicorn python tools/scene_lab/ghost_stage_probe.py plan <package> --rom <z64>
        --map <MappingUS.map> --clip <a.ghost> --origin-frame 272 [--origin-igt 1] [--every 15]
    uv run --with unicorn python tools/scene_lab/ghost_stage_probe.py shots <package> --rom <z64>
        --map <MappingUS.map> --pj64-plugin <dir> --clip <a.ghost> [--clip ...] --origin-frame 272
        --out <dir> --shot 330 [--orbit 520:0,60[@x,y,z]] [--free 520:fx,fy,fz,px,py,pz]
    uv run --with unicorn python tools/scene_lab/ghost_stage_probe.py measure <package> ... --out <dir>
        --frames 332:421 [--counts 0,1,2,4]

The stage is one scene package: its world, objects and recorded camera. Every other runner is a
saved ghost recording (SM64GHO7), drawn into that stage by the code the Trainer draws ghosts with
in the live game: the Trainer renderer's own overlay (ghost_link -> ghost_draw -> compositor),
which the viewer host reaches with --ghost-consumer. The probe drives it over the renderer's own
ghost channel (ghost/native_channel.py, protocol v14) in FIXED mode: each picture names the
coordinate every ghost shows, so the viewer, not a live clock, decides the time.

Time. A star ghost's coordinate is its attempt's Usamune IGT: elapsed game frames since its
certified origin plus the origin's raw IGT. The stage's coordinate at frame N is the same count
from the stage's own origin: gGlobalTimer(N) - gGlobalTimer(origin frame) + origin IGT. Equal
coordinates are equal times since the same start (the level entry), which is the comparison
contract feasibility.md names. `plan` prints both runners' spawn actions so the origin can be
checked: the game plays the same spawn the same way, so the actions change on the same IGT.

The renderer reads what it draws with from the stage RDRAM, as it does in Project64: the camera
(the area's graph camera, which the evaluator moves for a free camera), the level and area (a
ghost in another area is not drawn) and the projection of the world pass. So every picture sends
the stage's whole scene-state RDRAM, not only the task closure. The capture refuses a game clock
that runs backward and starts a new epoch, so the first picture after a backward step draws no
ghost (`measure` shows it); shots go in ascending frame order.

`shots` writes pictures and report.json (per picture: the ghosts the renderer drew, its timings
and the renderer's own hook costs); `measure` writes measure.json and the step-back pictures.
Packages, clips and pictures hold private data: keep them out of the repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
import gpu_gate  # noqa: E402
import package as scene_package  # noqa: E402
import replay  # noqa: E402
import viewer  # noqa: E402
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402
from sm64_events.ghost import clip as gclip  # noqa: E402
from sm64_events.ghost.native_channel import NativeChannel  # noqa: E402
from sm64_events.memory.ghost_layout import GhostLayout  # noqa: E402
from sm64_events.memory.layout import layout_for  # noqa: E402

GLOBAL_TIMER = 0x8032D5D4      # layout.py US global_timer
MARIO_ACTION = 0x8033B17C      # gMarioStates (US) + 0x0C
CAPTURING, READY, REFUSED = 2, 4, 5   # gipc_state
POSE_POSITION_AT = 76          # gclip record: prefix 16 + gc_sample.pose 60, position first
HUES = (200, 30, 120, 300)     # one per ghost


def stage_timer(package, index: int) -> int:
    _task, _regions, ram, _known = package.scene_state(index)
    return struct.unpack_from("<I", ram, GLOBAL_TIMER & 0x7FFFFF)[0]


class Stage:
    """The stage package with its evaluator; frame -> coordinate by the stage's own origin."""

    def __init__(self, args):
        import evaluator as scene_evaluator
        self.evaluator = scene_evaluator.Evaluator.open(args.package, args.rom, args.map)
        self.package = self.evaluator.package
        self.origin_frame, self.origin_igt = args.origin_frame, args.origin_igt
        self.origin_timer = stage_timer(self.package, self.origin_frame)

    def coordinate(self, index: int) -> int:
        return stage_timer(self.package, index) - self.origin_timer + self.origin_igt

    def whole_ram(self, index: int, task: tuple, ram: bytes) -> viewer.Frame:
        """One run over all of RDRAM (host word order): the closure and the scene state together."""
        return viewer.Frame(f"frame {index}", tuple(task), self.package.outline(index)["vi"],
                            struct.pack("<II", 0, len(ram)), bytes(ram))

    def recorded(self, index: int) -> viewer.Frame:
        task, _regions, ram, _known = self.package.scene_state(index)
        return self.whole_ram(index, task.task, ram)

    def free(self, index: int, focus, position) -> viewer.Frame | None:
        view = self.evaluator.display_list(index, (tuple(focus), tuple(position)))
        if view is None or view.ram is None:
            return None
        task = list(view.task)
        task[12], task[13] = view.data_ptr & 0x1FFFFFFF, view.data_size
        return self.whole_ram(index, task, scene_package.swap_words(bytes(view.ram)))


def ghost_position(frame: gclip.Frame) -> list[float]:
    return list(struct.unpack_from("<3f", frame.raw, POSE_POSITION_AT))


def plan(args) -> int:
    stage = Stage(args)
    clips = [(path, gclip.read_clip(path).canonical_frames()) for path in args.clip]
    print(f"stage origin frame {stage.origin_frame} (gGlobalTimer {stage.origin_timer}) = IGT {stage.origin_igt}")
    for path, rows in clips:
        changes = [(at, hex(row.live_action)) for at, row in sorted(rows.items())
                   if at - 1 not in rows or rows[at - 1].live_action != row.live_action][:6]
        print(f"ghost {path.name[:12]}: IGT {min(rows)}..{max(rows)}, first action changes {changes}")
    previous = None
    for index in range(stage.origin_frame, min(len(stage.package), stage.origin_frame + 60)):
        action = struct.unpack_from(">I", stage.evaluator.scene(index).ram, MARIO_ACTION & 0x7FFFFF)[0]
        if action != previous:
            print(f"  stage frame {index} IGT {stage.coordinate(index)} action {hex(action)}")
            previous = action
    for index in range(stage.origin_frame, len(stage.package), args.every):
        coordinate, pose = stage.coordinate(index), stage.evaluator.pose(index)
        camera = pose["position"]
        forward = [f - c for f, c in zip(pose["focus"], camera, strict=True)]
        line = [f"frame {index} IGT {coordinate} mario {[round(v) for v in pose['mario']]}"]
        for path, rows in clips:
            row = rows.get(coordinate)
            if row is None:
                line.append(f"{path.name[:6]}: none")
                continue
            where = ghost_position(row)
            to = [w - c for w, c in zip(where, camera, strict=True)]
            cosine = sum(a * b for a, b in zip(forward, to, strict=True)) / (math.hypot(*forward) * math.hypot(*to) or 1)
            line.append(f"{path.name[:6]}: {[round(v) for v in where]} "
                        f"{math.degrees(math.acos(max(-1.0, min(1.0, cosine)))):.0f}deg off-axis, "
                        f"{math.dist(where, pose['mario']):.0f}u from the stage's Mario, lvl {row.level}/{row.area}")
        print("  ".join(line))
    return 0


def clip_entries(paths: list[Path]) -> list[dict]:
    """The displayed set as ghost/native_channel.py takes it."""
    entries = []
    for number, path in enumerate(paths):
        entries.append({"clip_id": uuid.UUID(gclip.read_clip(path).clip_id).bytes,
                        "clip_sha256": hashlib.sha256(path.read_bytes()).digest(),
                        "clip_path": str(path.resolve()), "hue_degrees": HUES[number % len(HUES)],
                        "label": f"ghost {number + 1}"})
    return entries


class GhostSession:
    """One viewer_host.exe over the Trainer renderer with --ghost-consumer, and its ghost channel.
    The host's LOCALAPPDATA points into a private folder, so the renderer's per-user font cache stays
    out of his profile; the folder and the plugin copy go on close."""

    def __init__(self, args, clips: list[dict], out: Path, first: viewer.Frame):
        gpu_gate.require_gpu()
        pj64_plugin = Path(args.pj64_plugin)
        if not (pj64_plugin / replay.CONFIG_FILES[0]).is_file():
            raise SystemExit("--pj64-plugin must name Project64's Plugin folder")
        self.args, self.clips, self.channel = args, clips, None
        self.work = Path(tempfile.mkdtemp(prefix="mlt-ghost-host-", dir=out))
        plugin_dir, profile = self.work / "plugin", self.work / "profile"
        profile.mkdir(parents=True)
        viewer.prepare_plugin_dir(plugin_dir, pj64_plugin, None)
        self.renderer = replay.resolve_renderer("trainer", pj64_plugin)
        started = time.perf_counter()
        process = subprocess.Popen(
            [str(viewer.build()), "--ghost-consumer", "1", "--renderer", str(self.renderer),
             "--plugin-dir", str(plugin_dir), "--header", scene_package.rom_header_hex(args.rom.read_bytes())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(os.environ, LOCALAPPDATA=str(profile)), **quiet_spawn_kwargs())
        self.host = viewer.Host.__new__(viewer.Host)       # viewer.Host's pipe over this process
        self.host.process, self.host.lock, self.host.errors = process, threading.Lock(), []
        threading.Thread(target=self.host._drain_stderr, daemon=True).start()
        try:
            self.host.render(first)                         # an instance's first list renders wrong
            self.startup_ms = round((time.perf_counter() - started) * 1000, 1)
            layout = GhostLayout.from_layout(layout_for("us")).to_bytes()
            deadline = time.monotonic() + 10
            while self.channel is None:
                try:
                    self.channel = NativeChannel(process.pid, layout)
                except OSError:
                    if time.monotonic() > deadline:
                        raise SystemExit("the renderer opened no ghost channel (not the Trainer renderer?)") from None
                    time.sleep(0.05)
        except BaseException:
            self.close()
            raise

    @property
    def pid(self) -> int:
        return self.host.process.pid

    def command(self, coordinate: int, ghosts: bool) -> dict:
        # The live game's player-first fades judge each ghost against "live Mario", which in the
        # viewer is the stage's runner: --live-fades keeps them, as in game.
        fades = getattr(self.args, "live_fades", False)
        return {"enabled": ghosts and bool(self.clips), "mode": 1, "seek_igt": coordinate, "model_id": 1,
                "rom_path": str(self.args.rom.resolve()), "clips": self.clips,
                "opacity": getattr(self.args, "opacity", 1.0), "stop_at_star": False, "volume": 0.0,
                "names_enabled": getattr(self.args, "names", False),
                "proximity_enabled": fades, "occlusion_fade": fades}

    def settle(self, coordinate: int, ghosts: bool, frame: viewer.Frame) -> tuple[float, int]:
        """Publish the command and wait for the worker to apply it: (milliseconds, discarded renders).
        The capture configures, and the renderer takes the model and clips, only at its own list
        boundaries, so the wait renders this same frame (discarded) until the channel is ready."""
        asked = time.perf_counter()
        command = self.command(coordinate, ghosts)
        self.channel.poll(command)
        revision, limit, discarded = self.channel._request.revision, time.monotonic() + 30, 0
        while True:
            status, _records = self.channel.poll()
            if status.ack_revision == revision and status.state == (READY if command["enabled"] else CAPTURING):
                return (time.perf_counter() - asked) * 1000, discarded
            if status.state == REFUSED or time.monotonic() > limit:
                raise SystemExit(f"ghost channel refused or stalled: state {status.state} reason {status.reason} "
                                 f"configuration {status.configuration} prepared {status.clips_prepared}")
            if status.ack_revision == revision:
                self.host.render(frame, discard=True)
                discarded += 1
            else:
                time.sleep(0.0005)

    def picture(self, frame: viewer.Frame, coordinate: int, ghosts: bool) -> tuple[viewer.Picture, dict]:
        control_ms, discarded = self.settle(coordinate, ghosts, frame)
        picture = self.host.render(frame)
        return picture, {"control_ms": round(control_ms, 3), "settle_renders": discarded}

    def status_after(self):
        """The worker's status after the last render (it republishes when woken)."""
        self.channel.poll()
        time.sleep(0.02)
        status, _records = self.channel.poll()
        return status

    def close(self) -> int:
        try:
            if self.channel is not None:
                self.channel.close()
        finally:
            code = self.host.close()
            shutil.rmtree(self.work, ignore_errors=True)
        return code


def orbit_camera(pose: dict, around: list[float], yaw: float, pitch: float = 0.0,
                 distance: float | None = None) -> tuple[list[float], list[float]]:
    """A camera on a circle around `around`, starting from the recorded camera's bearing: at the
    recorded camera's height, or with `distance`, `pitch` degrees above the point."""
    camera = pose["position"]
    dx, dz = camera[0] - around[0], camera[2] - around[2]
    reach = distance or math.hypot(dx, dz)
    angle = math.atan2(dx, dz) + math.radians(yaw)
    height = reach * math.tan(math.radians(pitch)) if distance else camera[1] - around[1]
    return list(around), [around[0] + reach * math.sin(angle), around[1] + height, around[2] + reach * math.cos(angle)]


def save_png(picture: viewer.Picture, path: Path) -> None:
    from PIL import Image
    Image.frombuffer("RGB", (picture.width, picture.height), picture.pixels, "raw", "BGR", 0, -1).save(path)


def planned_shots(args, stage: Stage, index: int) -> list[tuple[str, object, bool, dict]]:
    """(name, frame builder, ghosts drawn, report extras) for every picture `index` asks for."""
    shots = []
    if index in args.shot:
        for ghosts, suffix in ((True, "ghosts"), (False, "stage-only")):
            shots.append((f"f{index}-recorded-{suffix}", lambda: stage.recorded(index), ghosts, {"camera": "recorded"}))
    pose = stage.evaluator.pose(index)
    for spec in args.orbit:
        at, _, yaws = spec.partition(":")
        if int(at) != index:
            continue
        yaws, _, pivot = yaws.partition("@")
        around = [float(value) for value in pivot.split(",")] if pivot else pose["mario"]
        for yaw in (float(value) for value in yaws.split(",")):
            focus, position = orbit_camera(pose, around, yaw, args.pitch, args.distance)
            shots.append((f"f{index}-orbit{yaw:+04.0f}", lambda f=focus, p=position: stage.free(index, f, p), True,
                          {"camera": "free", "focus": focus, "position": position}))
    for number, spec in enumerate(args.free):
        at, _, numbers = spec.partition(":")
        if int(at) != index:
            continue
        values = [float(value) for value in numbers.split(",")]
        for ghosts, suffix in ((True, "ghosts"), (False, "stage-only")):
            shots.append((f"f{index}-free{number}-{suffix}", lambda v=values: stage.free(index, v[:3], v[3:]), ghosts,
                          {"camera": "free", "focus": values[:3], "position": values[3:]}))
    return shots


def shots(args) -> int:
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    stage = Stage(args)
    clips = clip_entries(args.clip)
    frames = sorted(set(args.shot) | {int(spec.split(":")[0]) for spec in args.orbit + args.free})
    session = GhostSession(args, clips, out, stage.recorded(frames[0]))
    report = {"package": args.package.name, "clips": [Path(c["clip_path"]).name for c in clips],
              "renderer": session.renderer.name, "host_pid": session.pid, "startup_ms": session.startup_ms,
              "pictures": []}
    try:
        for index in frames:
            coordinate = stage.coordinate(index)
            for name, build, ghosts, extra in planned_shots(args, stage, index):
                frame = build()
                if frame is None:
                    print(f"{name}: the evaluator has no view of this frame")
                    continue
                picture, timing = session.picture(frame, coordinate, ghosts)
                save_png(picture, out / f"{name}.png")
                status = session.status_after()
                entry = {"name": name, "coordinate": coordinate, "ghosts": ghosts, **timing,
                         "render_ms": round(picture.render_ms, 3), "read_ms": round(picture.read_ms, 3),
                         "displayed": status.displayed, "clips_visible": status.clips_visible, **extra,
                         "hooks": {field: getattr(status, field) for field, _kind in status._fields_
                                   if field.startswith("hook_")}}
                report["pictures"].append(entry)
                print(json.dumps({key: value for key, value in entry.items() if key != "hooks"}), flush=True)
    finally:
        report["host_exit"] = session.close()
        (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 3) if ordered else 0.0


def measure_count(args, stage: Stage, clips: list[dict], out: Path) -> dict:
    """Recorded-camera playback over --frames with these clips in one fresh host, then one step back."""
    first, last = (int(part) for part in args.frames.split(":"))
    session = GhostSession(args, clips, out, stage.recorded(first))
    row = {"render_ms": [], "read_ms": [], "control_ms": [], "list_us": [], "hook_us": []}
    result = {"ghosts": len(clips), "frames": last - first + 1}
    try:
        session.settle(stage.coordinate(first), True, stage.recorded(first))   # model and clips: not timed
        for index in range(first, last + 1):
            picture, timing = session.picture(stage.recorded(index), stage.coordinate(index), True)
            status, _records = session.channel.poll()           # the previous list's hook costs
            row["render_ms"].append(picture.render_ms)
            row["read_ms"].append(picture.read_ms)
            row["control_ms"].append(timing["control_ms"])
            row["list_us"].append(status.hook_list_micros)
            row["hook_us"].append(status.hook_micros)
        result["visible_last"] = session.status_after().clips_visible
        if clips:
            back = first + (last - first) // 2
            steps = []
            for attempt in (1, 2):
                picture, _timing = session.picture(stage.recorded(back), stage.coordinate(back), True)
                save_png(picture, out / f"back{back}-{len(clips)}ghosts-render{attempt}.png")
                status = session.status_after()
                steps.append({"displayed": status.displayed, "visible": status.clips_visible})
            result["step_back"] = {"from": last, "to": back, "renders": steps}
    finally:
        result["host_exit"] = session.close()
    result.update({key: {"p50": percentile(values, 0.5), "p95": percentile(values, 0.95)}
                   for key, values in row.items()})
    return result


def measure(args) -> int:
    """What a ghost costs per picture: recorded-camera playback with the first k clips, one fresh host
    per k (the capture's clock may not run backward inside one host)."""
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    stage = Stage(args)
    results = []
    for count in args.counts:
        results.append(measure_count(args, stage, clip_entries(args.clip[:count]), out))
        print(json.dumps(results[-1]), flush=True)
    (out / "measure.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "shots", "measure"):
        sub = commands.add_parser(name)
        sub.add_argument("package", type=Path)
        sub.add_argument("--rom", type=Path, required=True)
        sub.add_argument("--map", type=Path, required=True)
        sub.add_argument("--clip", type=Path, action="append", required=True)
        sub.add_argument("--origin-frame", type=int, required=True)
        sub.add_argument("--origin-igt", type=int, default=1)
        if name != "plan":
            sub.add_argument("--pj64-plugin", required=True)
            sub.add_argument("--out", type=Path, required=True)
    commands.choices["plan"].add_argument("--every", type=int, default=15)
    shots_parser = commands.choices["shots"]
    shots_parser.add_argument("--shot", type=int, action="append", default=[])
    shots_parser.add_argument("--orbit", action="append", default=[],
                              help="frame:yaw,yaw,...[@x,y,z] (around the stage's Mario unless a point is given)")
    shots_parser.add_argument("--free", action="append", default=[], help="frame:fx,fy,fz,px,py,pz")
    shots_parser.add_argument("--pitch", type=float, default=0.0)
    shots_parser.add_argument("--distance", type=float)
    shots_parser.add_argument("--opacity", type=float, default=1.0)
    shots_parser.add_argument("--names", action="store_true")
    shots_parser.add_argument("--live-fades", action="store_true",
                              help="keep the live game's proximity and occlusion fades against the stage's runner")
    measure_parser = commands.choices["measure"]
    measure_parser.add_argument("--frames", required=True, help="first:last stage frame, ascending")
    measure_parser.add_argument("--counts", type=lambda text: [int(v) for v in text.split(",")], default=[0, 1, 2, 4])
    args = parser.parse_args(argv)
    return {"plan": plan, "shots": shots, "measure": measure}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
