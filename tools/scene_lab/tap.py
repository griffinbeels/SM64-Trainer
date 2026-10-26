"""Arm, read and replay scene-tap sessions (plugin/gfxwrap/scene_tap.cpp).

    tap.py arm [--seconds N] [--no-reference] [--reference-every N] [--full-every N] [--version us|jp]
    tap.py disarm
    tap.py summary <session>
    tap.py frames <session> <out> [--pattern 0xA5] [--limit N] [--every N] [--pair origin|next]
    tap.py offline <lab_root> <frame_dir>... [--seconds N] [--no-scene-state]

`arm` writes the one-shot arming file the wrapper consumes when a practice ROM
next opens; its identity reads (gGlobalTimer, level, area) come from
memory/layout.py so the plugin still knows no game address. `frames` turns
each recorded task into a frame input (savestate_frames.py format) whose
RDRAM holds ONLY the captured closure, every other byte set to --pattern,
paired with the presented picture that followed it (ref.bgr); --every N keeps
only every N-th frame that has a reference picture. `arm` also
names the scene state spans (layout.py's scene_* and main_pool_* rows, plus the
act selector's save_buffer_* and usamune_state_* where the layout has them) the tap
copies with each task, so SM64's own renderer can re-run offline (E2-live:
tools/scene_lab/e2_georender.py from-tap; `scene_states` rebuilds each task's
RDRAM from a session). `offline` runs the REAL producer (scene_tap.cpp, through
tap_offline_host.cpp) over frame inputs and writes a session exactly as the
live tap would, under <lab_root> instead of %LOCALAPPDATA%. Sessions and
frames are private RAM images: keep them out of the repository.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import mmap
import os
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402
from sm64_events.memory.layout import layout_for  # noqa: E402

MAGIC = 0x31544C53
REC_PAD, REC_TASK, REC_PRESENT, REC_FULL, REC_REGIONS = 0, 1, 2, 3, 4
REGION_KINDS = ("task", "level_key", "level_delta")
REGION_FIELDS = ("task_index", "kind", "count", "data_bytes", "copy_ns", "scan_ns", "block_bytes", "reserved")
# The spans the scene renderer reads (layout.py rows, [start, end)): copied
# whole with every task, and the main pool as a level region (key + deltas).
SCENE_SPANS = (("scene_data_start", "scene_data_end"), ("scene_bss_start", "scene_bss_end"),
               ("scene_engine_bss_start", "scene_engine_bss_end"))
SCENE_LEVEL_SPANS = (("main_pool_start", "main_pool_end"),)
# Copied whole with every task too, each only where this version's layout names
# it: the act selector's coin score (gSaveBuffer) and Usamune's settings and
# counters its act-select hook prints. A version without them loses those menus.
SCENE_OPTIONAL_SPANS = (("save_buffer_start", "save_buffer_end"), ("usamune_state_start", "usamune_state_end"))
# SM64's gfx pool (GFX_POOL_SIZE: 6400 commands, every version) starts at a task's data pointer.
# The update allocates from its top before geo_process_root draws: obj_orient_graph's floor-aligned
# matrices, which the render reads through throwMatrix and then lets go of (E2-live). The tap copies
# this tail of the task's own pool; the evaluator reports a task whose update allocated more.
GFX_POOL_BYTES = 6400 * 8
POOL_TAIL_BYTES = 4096
HOST_SOURCES = (ROOT / "tools/scene_lab/tap_offline_host.cpp", ROOT / "plugin/gfxwrap/scene_tap.cpp",
                ROOT / "plugin/gfxwrap/scene_tap.h", ROOT / "plugin/gfxwrap/scene_closure.h",
                ROOT / "plugin/gfxwrap/zilmar.h")
TASK_FIELDS = ("refusals", "commands", "walk_ns", "copy_ns", "vi_origin", "runs", "extras",
               "closure_bytes", "last_cimg", "color_images", "task_index", "reserved")
VI_NAMES = ("status", "origin", "width", "intr", "v_current", "burst", "v_sync", "h_sync",
            "leap", "h_start", "v_start", "v_burst", "x_scale", "y_scale")
IDENTITY_FIELDS = {"global_timer": 4, "curr_level": 2, "curr_area": 2}   # name: bytes


def lab_root() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "SM64Trainer" / "scene-lab"


@dataclass
class Task:
    sequence: int
    qpc: int
    fields: dict
    task: tuple
    runs: list
    run_bytes: bytes
    extras: list = field(default_factory=list)   # (address, bytes)


@dataclass
class Regions:
    sequence: int
    qpc: int
    fields: dict
    kind: str           # REGION_KINDS
    segments: tuple     # the task's 16 RSP segment bases
    spans: list         # (physical begin, bytes)
    data: bytes         # the spans' bytes, in order, as Project64 stores them


@dataclass
class Present:
    sequence: int
    qpc: int
    vi: dict
    width: int
    height: int
    read_ns: int
    pixels: bytes


def read_records(path: Path):
    """Stream tap.bin's records. The file is mapped, not read whole: a session
    with reference pictures runs to gigabytes, and only the caller's records stay."""
    if not path.stat().st_size:
        return
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        at = 0
        while at + 32 <= len(data):
            magic, kind, size, sequence, qpc = struct.unpack_from("<IIIIq", data, at)
            if magic != MAGIC or size < 32 or at + size > len(data):
                raise ValueError(f"corrupt tap record at byte {at}")
            with memoryview(data)[at + 32:at + size] as body:
                record = _parse_record(kind, sequence, qpc, body)
            if record is not None:
                yield record
            at += size


def _parse_record(kind: int, sequence: int, qpc: int, body: memoryview):
    """One record as owned objects (copies), so the mapping can close behind it."""
    if kind == REC_TASK:
        fields = dict(zip(TASK_FIELDS, struct.unpack_from("<12I", body, 0), strict=True))
        task = struct.unpack_from("<16I", body, 48)
        offset = 112
        runs = [struct.unpack_from("<II", body, offset + 8 * i) for i in range(fields["runs"])]
        offset += 8 * fields["runs"]
        extra_spec = [struct.unpack_from("<II", body, offset + 8 * i) for i in range(fields["extras"])]
        offset += 8 * fields["extras"]
        run_bytes = bytes(body[offset:offset + fields["closure_bytes"]])
        offset += fields["closure_bytes"]
        extras = []
        for address, length in extra_spec:
            padded = (length + 3) & ~3
            extras.append((address & ~3, bytes(body[offset:offset + padded])))
            offset += padded
        return Task(sequence, qpc, fields, task, runs, run_bytes, extras)
    if kind == REC_PRESENT:
        vi = dict(zip(VI_NAMES, struct.unpack_from("<14I", body, 0), strict=True))
        width, height, read_ns, _format = struct.unpack_from("<4I", body, 56)
        return Present(sequence, qpc, vi, width, height, read_ns, bytes(body[72:72 + width * height * 3]))
    if kind == REC_FULL:
        (rdram_size,) = struct.unpack_from("<I", body, 0)
        return ("full", sequence, bytes(body[16:16 + rdram_size]))
    if kind == REC_REGIONS:
        fields = dict(zip(REGION_FIELDS, struct.unpack_from("<8I", body, 0), strict=True))
        segments = struct.unpack_from("<16I", body, 32)
        spans = [struct.unpack_from("<II", body, 96 + 8 * i) for i in range(fields["count"])]
        at = 96 + 8 * fields["count"]
        return Regions(sequence, qpc, fields, REGION_KINDS[fields["kind"]], segments, spans,
                       bytes(body[at:at + fields["data_bytes"]]))
    return None


def session_header(session: Path) -> dict:
    """session.txt as key=value strings (rdram_size, rom_header, qpc_frequency...)."""
    header = {}
    for line in (session / "session.txt").read_text(encoding="ascii").splitlines():
        key, _, value = line.partition("=")
        header[key] = value
    return header


def identity_spec(session: Path) -> list[tuple[str, int, int]]:
    """The identity extras the arming tool named: (name, physical address, bytes)."""
    identity = []
    for line in (session / "armed.txt").read_text(encoding="ascii").splitlines():
        if line.startswith("# identity="):
            for item in filter(None, line.removeprefix("# identity=").split(",")):
                name, address, size = item.split(":")
                identity.append((name, int(address, 16), int(size)))
    return identity


def paired(records, pair: str = "next"):
    """Every task once, in record order, with the present that showed it or None.

    With frame buffer emulation off (Project64's configuration) the renderer
    presents its newest list at the next new VI origin, whatever that origin
    names, so "next" pairs a picture with the newest task. Measured on the
    first live session (2026-10-24): "next" matched 217 of 219 sampled
    pictures pixel for pixel, "origin" 25 (SM64 shows the previous task's
    buffer, so origin pairing is one frame late). "origin" pairs with the
    newest task drawn into the image VI scans, for a renderer that emulates
    frame buffers. Tasks a later picture overtook were never shown.
    """
    pending: list[Task] = []
    for record in records:
        if isinstance(record, Task):
            pending.append(record)
            continue
        if not isinstance(record, Present) or not pending:
            continue
        shown = pending if pair == "next" else [
            task for task in pending if abs(record.vi["origin"] - task.fields["last_cimg"]) < 0x1000]
        if not shown:
            continue
        task = shown[-1]
        for earlier in pending:
            if earlier.sequence < task.sequence:
                yield earlier, None
        yield task, record
        pending = [later for later in pending if later.sequence > task.sequence]
    for task in pending:
        yield task, None


def word_value(extras: list, address: int, size: int) -> int | None:
    """Read a big-endian game value from word-swapped extra bytes."""
    for base, data in extras:
        if base <= address and address + size <= base + len(data):
            raw = bytearray()
            for byte in range(address, address + size):
                index = (byte ^ 3) - base
                raw.append(data[index])
            return int.from_bytes(raw, "big")
    return None


def scene_state_lines(layout) -> list[str]:
    """Arming lines for the scene renderer's state (E2-live), or none when this
    version's layout lacks the spans."""
    lines = []
    for kind, spans in (("region", SCENE_SPANS), ("level_region", SCENE_LEVEL_SPANS)):
        for start_field, end_field in spans:
            start, end = layout.value(start_field), layout.value(end_field)
            if start is None or end is None:
                return []
            lines.append(f"{kind}={start & 0x1FFFFFFF:x}:{end - start}")
    for start_field, end_field in SCENE_OPTIONAL_SPANS:
        start, end = layout.value(start_field), layout.value(end_field)
        if start is not None and end is not None:
            lines.append(f"region={start & 0x1FFFFFFF:x}:{end - start}")
    lines.append(f"task_region={GFX_POOL_BYTES - POOL_TAIL_BYTES:x}:{POOL_TAIL_BYTES}")
    return lines


def arming_text(args) -> str:
    layout = layout_for(args.version)
    lines = [f"seconds={args.seconds}", f"reference={0 if args.no_reference else 1}",
             f"reference_every={args.reference_every}", f"full_every={args.full_every}"]
    names = []
    for name, size in IDENTITY_FIELDS.items():
        address = getattr(layout, name)
        if address:
            lines.append(f"extra={address & 0x1FFFFFFF:x}:{size}")
            names.append(f"{name}:{address & 0x1FFFFFFF:x}:{size}")
    lines.append("# identity=" + ",".join(names))   # the tap ignores comment lines
    if not args.no_scene_state:
        lines += scene_state_lines(layout)
    return "\n".join(lines) + "\n"


def arm(args) -> int:
    lab_root().mkdir(parents=True, exist_ok=True)
    (lab_root() / "armed.txt").write_text(arming_text(args), encoding="ascii")
    print("armed:", lab_root() / "armed.txt")
    return 0


def disarm(_args) -> int:
    (lab_root() / "armed.txt").unlink(missing_ok=True)
    return 0


class SceneState:
    """The RDRAM one task's scene renderer read, rebuilt from a session.

    The level regions accumulate across tasks (a key replaces them, a delta
    patches them); a task's own regions and its closure lie on top. Bytes stay
    in Project64's word-swapped order; `known` marks every byte the session
    captured, and everything else reads `pattern`."""

    def __init__(self, rdram_size: int):
        self.level = np.zeros(rdram_size, np.uint8)
        self.level_known = np.zeros(rdram_size, bool)

    def apply_level(self, regions: Regions) -> None:
        if regions.kind == "level_key":
            self.level_known[:] = False
        at = 0
        for begin, size in regions.spans:
            self.level[begin:begin + size] = np.frombuffer(regions.data, np.uint8, size, at)
            self.level_known[begin:begin + size] = True
            at += size

    def task_ram(self, task: Task, regions: Regions | None, pattern: int = 0x5A):
        ram = np.where(self.level_known, self.level, np.uint8(pattern))
        known = self.level_known.copy()
        layers = [] if regions is None else [(regions.spans, regions.data)]
        layers.append(([(begin, end - begin) for begin, end in task.runs], task.run_bytes))
        for spans, data in layers:
            at = 0
            for begin, size in spans:
                ram[begin:begin + size] = np.frombuffer(data, np.uint8, size, at)
                known[begin:begin + size] = True
                at += size
        return ram, known


def scene_states(records, rdram_size: int):
    """(task, its task regions or None, RDRAM, known mask) for every task, in
    order, with the level regions as they stood at that task."""
    state = SceneState(rdram_size)
    pending: list = []
    for record in records:
        if isinstance(record, Task):
            if pending:
                yield (pending[0], pending[1], *state.task_ram(pending[0], pending[1]))
            pending = [record, None]
        elif isinstance(record, Regions):
            if record.kind != "task":
                state.apply_level(record)
            elif pending and record.fields["task_index"] == pending[0].fields["task_index"]:
                pending[1] = record
    if pending:
        yield (pending[0], pending[1], *state.task_ram(pending[0], pending[1]))


def summary(args) -> int:
    session = Path(args.session)
    tasks = presents = refused = 0
    walk, copy, closure, reads = [], [], [], []
    region = {kind: {"bytes": [], "us": [], "scan_us": []} for kind in REGION_KINDS}
    for record in read_records(session / "tap.bin"):
        if isinstance(record, Regions):
            region[record.kind]["bytes"].append(record.fields["data_bytes"])
            region[record.kind]["us"].append(record.fields["copy_ns"] / 1000)
            region[record.kind]["scan_us"].append(record.fields["scan_ns"] / 1000)
        elif isinstance(record, Task):
            tasks += 1
            refused += bool(record.fields["refusals"])
            walk.append(record.fields["walk_ns"] / 1000)
            copy.append(record.fields["copy_ns"] / 1000)
            closure.append(record.fields["closure_bytes"])
        elif isinstance(record, Present):
            presents += 1
            reads.append(record.read_ns / 1000)

    def pct(values, p):
        values = sorted(values)
        return values[min(len(values) - 1, int(p * (len(values) - 1) + 0.5))] if values else 0

    print(json.dumps({
        "tasks": tasks, "presents": presents, "refused": refused,
        "walk_us": {"p50": pct(walk, .5), "p99": pct(walk, .99), "max": max(walk, default=0)},
        "copy_us": {"p50": pct(copy, .5), "p99": pct(copy, .99), "max": max(copy, default=0)},
        "closure_kib": {"p50": pct(closure, .5) / 1024, "max": max(closure, default=0) / 1024},
        "reference_read_us": {"p50": pct(reads, .5), "p99": pct(reads, .99)},
        "regions": {kind: {"records": len(v["bytes"]), "kib_p50": pct(v["bytes"], .5) / 1024,
                           "kib_max": max(v["bytes"], default=0) / 1024, "us_p50": pct(v["us"], .5),
                           "us_p99": pct(v["us"], .99), "scan_us_p50": pct(v["scan_us"], .5)}
                    for kind, v in region.items() if v["bytes"]},
        "summary": (session / "summary.txt").read_text(encoding="ascii") if (session / "summary.txt").is_file() else None,
    }, indent=2))
    return 0


def frames(args) -> int:
    session, out = Path(args.session), Path(args.out)
    header = session_header(session)
    rdram_size = int(header["rdram_size"])
    pattern = bytes([args.pattern]) * rdram_size
    identity = identity_spec(session)
    written = referenced = 0
    for task, record in paired(read_records(session / "tap.bin"), args.pair):
        if record is None:
            continue
        if args.every:
            if not record.pixels:
                continue
            referenced += 1
            if (referenced - 1) % args.every:
                continue
        rdram = bytearray(pattern)
        at = 0
        for begin, end in task.runs:
            rdram[begin:end] = task.run_bytes[at:at + end - begin]
            at += end - begin
        frame = out / f"{written:05d}-task{task.fields['task_index']:05d}"
        frame.mkdir(parents=True, exist_ok=True)
        (frame / "rdram.bin").write_bytes(rdram)
        dmem = bytearray(0x1000)
        struct.pack_into("<16I", dmem, 0xFC0, *task.task)
        (frame / "dmem.bin").write_bytes(dmem)
        if record.pixels:
            (frame / "ref.bgr").write_bytes(record.pixels)
        (frame / "frame.json").write_text(json.dumps({
            "schema": "scene-lab-frame/1",
            "source": {"kind": "scene-tap", "session": session.name, "task_sequence": task.sequence,
                       "present_sequence": record.sequence, "pattern": args.pattern},
            "identity": {name: word_value(task.extras, address, size) for name, address, size in identity},
            "vi": record.vi,
            "rom_header": header.get("rom_header", ""),
            "task": dict(zip(("type", "flags", "ucode_boot", "ucode_boot_size", "ucode", "ucode_size",
                              "ucode_data", "ucode_data_size", "dram_stack", "dram_stack_size",
                              "output_buff", "output_buff_size", "data_ptr", "data_size",
                              "yield_data_ptr", "yield_data_size"), task.task, strict=True)),
            "tap": task.fields,
            "reference": {"width": record.width, "height": record.height, "format": "bgr24-bottom-up"},
        }, indent=2) + "\n", encoding="utf-8")
        written += 1
        if args.limit and written >= args.limit:
            break
    print(f"{written} frames -> {out}")
    return 0


def build_offline_host() -> Path:
    """tap_offline_host.exe (x86, the wrapper's compiler flags), cached by source hash."""
    spec = importlib.util.spec_from_file_location("scene_lab_build", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    digest = hashlib.sha256(b"".join(path.read_bytes() for path in HOST_SOURCES)).hexdigest()[:16]
    work = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "SM64Trainer/build-cache/scene-lab" / digest
    target = work / "tap_offline_host.exe"
    if target.is_file():
        return target
    work.mkdir(parents=True, exist_ok=True)
    vcvars = compiler.find_vcvars32()
    if vcvars is None:
        raise RuntimeError("x86 MSVC toolchain is required")
    flags = [flag for flag in compiler.COMMON_FLAGS if not flag.startswith("/std:")]
    compiler._cl(vcvars, [*flags, "/std:c++17", "/EHsc", f"/I{ROOT / 'plugin/gfxwrap'}",
                          str(HOST_SOURCES[0]), str(HOST_SOURCES[1]), f"/Fe:{target}", f"/Fo{work}\\",
                          "/link", "kernel32.lib", "user32.lib"], work)
    return target


def offline(args) -> int:
    """Arm under <lab_root>, feed the frames through the real producer, print the session."""
    root = Path(args.lab_root).resolve()
    lab = root / "SM64Trainer" / "scene-lab"
    lab.mkdir(parents=True, exist_ok=True)
    before = set(lab.glob("tap-*"))
    (lab / "armed.txt").write_text(arming_text(args), encoding="ascii")
    result = subprocess.run([str(build_offline_host()), str(root), *(str(Path(f).resolve()) for f in args.frames)],
                            capture_output=True, text=True, check=False, timeout=600, **quiet_spawn_kwargs())
    if result.returncode != 0:
        raise SystemExit(f"tap_offline_host failed ({result.returncode}): {result.stdout}{result.stderr}")
    sessions = sorted(set(lab.glob("tap-*")) - before)
    if len(sessions) != 1 or not (sessions[0] / "tap.bin").is_file():
        raise SystemExit(f"expected one new session under {lab}, found {sessions}")
    print(sessions[0])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("arm")
    p.add_argument("--seconds", type=int, default=20)
    p.add_argument("--no-reference", action="store_true")
    p.add_argument("--reference-every", type=int, default=1)
    p.add_argument("--full-every", type=int, default=0)
    p.add_argument("--version", choices=("us", "jp"), default="us")
    p.add_argument("--no-scene-state", action="store_true",
                   help="do not copy the scene renderer's state spans (E2-live)")
    p.set_defaults(run=arm)
    sub.add_parser("disarm").set_defaults(run=disarm)
    p = sub.add_parser("summary")
    p.add_argument("session")
    p.set_defaults(run=summary)
    p = sub.add_parser("frames")
    p.add_argument("session")
    p.add_argument("out")
    p.add_argument("--pattern", type=lambda value: int(value, 0), default=0xA5)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--every", type=int, default=0, help="keep every N-th frame with a reference picture")
    p.add_argument("--pair", choices=("origin", "next"), default="next")
    p.set_defaults(run=frames)
    p = sub.add_parser("offline")
    p.add_argument("lab_root", help="stands in for %%LOCALAPPDATA%%; use a fresh folder per run")
    p.add_argument("frames", nargs="+")
    p.add_argument("--seconds", type=int, default=600)
    p.add_argument("--version", choices=("us", "jp"), default="us")
    p.add_argument("--no-scene-state", action="store_true")
    p.set_defaults(run=offline, no_reference=True, reference_every=1, full_every=0)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
