"""Arm, read and replay scene-tap sessions (plugin/gfxwrap/scene_tap.cpp).

    tap.py arm [--seconds N] [--no-reference] [--reference-every N] [--full-every N] [--version us|jp]
    tap.py disarm
    tap.py summary <session>
    tap.py frames <session> <out> [--pattern 0xA5] [--limit N] [--pair origin|next]

`arm` writes the one-shot arming file the wrapper consumes when a practice ROM
next opens; its identity reads (gGlobalTimer, level, area) come from
memory/layout.py so the plugin still knows no game address. `frames` turns
each recorded task into a frame input (savestate_frames.py format) whose
RDRAM holds ONLY the captured closure, every other byte set to --pattern,
paired with the presented picture that followed it (ref.bgr). Sessions and
frames are private RAM images: keep them out of the repository.
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.memory.layout import layout_for  # noqa: E402

MAGIC = 0x31544C53
REC_PAD, REC_TASK, REC_PRESENT, REC_FULL = 0, 1, 2, 3
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


def paired(records, pair: str = "origin"):
    """Every task once, in record order, with the present that showed it or None.

    The picture shows the newest task drawn into the image VI now scans; tasks
    a later picture overtook were never shown. --pair next (offline hosts whose
    VI origin is synthetic) takes the newest task.
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


def arm(args) -> int:
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
    lab_root().mkdir(parents=True, exist_ok=True)
    (lab_root() / "armed.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
    print("armed:", lab_root() / "armed.txt")
    return 0


def disarm(_args) -> int:
    (lab_root() / "armed.txt").unlink(missing_ok=True)
    return 0


def summary(args) -> int:
    session = Path(args.session)
    tasks = presents = refused = 0
    walk, copy, closure, reads = [], [], [], []
    for record in read_records(session / "tap.bin"):
        if isinstance(record, Task):
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
        "summary": (session / "summary.txt").read_text(encoding="ascii") if (session / "summary.txt").is_file() else None,
    }, indent=2))
    return 0


def frames(args) -> int:
    session, out = Path(args.session), Path(args.out)
    header = session_header(session)
    rdram_size = int(header["rdram_size"])
    pattern = bytes([args.pattern]) * rdram_size
    identity = identity_spec(session)
    written = 0
    for task, record in paired(read_records(session / "tap.bin"), args.pair):
        if record is None:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("arm")
    p.add_argument("--seconds", type=int, default=20)
    p.add_argument("--no-reference", action="store_true")
    p.add_argument("--reference-every", type=int, default=1)
    p.add_argument("--full-every", type=int, default=0)
    p.add_argument("--version", choices=("us", "jp"), default="us")
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
    p.add_argument("--pair", choices=("origin", "next"), default="origin")
    p.set_defaults(run=frames)
    args = parser.parse_args()
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
