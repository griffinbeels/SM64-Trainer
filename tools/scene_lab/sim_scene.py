"""Simulated frames into a scene package through the real producer (Ghost Handoff re-simulation).

    writer = SimScene(out_package, work_folder, rom_path)
    writer.add(ram)          # each frame's RDRAM at the scene tap's moment, as frame_sim holds it
    stats = writer.finish()  # package.py's write stats; the work folder is removed

A simulated frame becomes a frame input (savestate_frames.py's format: rdram.bin in Project64's word
order, dmem.bin with the frame's OSTask at 0xFC0, frame.json), the offline host feeds it through the
wrapper's own producer (plugin/gfxwrap/scene_tap.cpp) one ProcessDList at a time over its stdin, and
the frame folder is deleted once the host has read it, so a long scene never needs its frames on
disk at once. Each frame is then shown: one VI with the recording's own VI registers (`vi`, a
present of the session the stage came from), its origin turning over the three frame buffers as
the game's does, so the producer records a present and the viewer lists the frame. The host's
clock is not the game's, so the session's record times are rewritten (retime) to the game's 30
frames a second before package.py's own writer makes the package: the same records, regions and
checks as a recorded attempt's scene, played at the game's speed. No sound.
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame_sim as sim  # noqa: E402
import package  # noqa: E402
import tap  # noqa: E402
from savestate_frames import TASK_NAMES  # noqa: E402

from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402

PHYSICAL = {"ucode", "ucode_data", "dram_stack", "output_buff", "output_buff_size", "data_ptr", "yield_data_ptr"}
FRAME_BUFFER_NAMES = ("gFrameBuffer0", "gFrameBuffer1", "gFrameBuffer2")   # the VI origin turns over these
NTSC_FRAME = 1001 / 30000                             # seconds a game frame shows (two NTSC VIs)


class SimSceneError(RuntimeError):
    """The producer refused or stopped; the message says where."""


def rom_header_hex(rom: bytes) -> str:
    """The cartridge header as Project64 holds it (word-swapped), as frame.json carries it."""
    return sim.be32(rom[:0x40]).tobytes().hex()


def vi_template(session: Path, symbols: sim.Symbols) -> dict:
    """The VI registers of a recorded session's first picture of play (its origin inside a frame buffer,
    the screen started): what a simulated frame is shown with. The boot's first VIs show nothing."""
    buffers = [sim.phys(symbols[name]) for name in FRAME_BUFFER_NAMES]
    low, high = min(buffers), max(buffers) + (buffers[1] - buffers[0])
    for record in tap.read_records(session / "tap.bin"):
        if isinstance(record, tap.Present) and low <= record.vi["origin"] < high and record.vi["h_start"] \
                and record.vi["width"]:
            return dict(record.vi)
    raise SimSceneError(f"{session.name} has no picture of play to take the VI registers from")


def retime(session: Path) -> None:
    """Every record of a simulated session at the game's clock: task k at k frames, its present half a
    frame later, every other record with the task before it."""
    frequency = int(tap.session_header(session).get("qpc_frequency") or 10_000_000)
    period = round(frequency * NTSC_FRAME)
    path = session / "tap.bin"
    data = bytearray(path.read_bytes())
    task, at_task = -1, 0
    for at, kind, _size, _sequence, _qpc, _flags in list(tap._walk(data, torn_ok=False)):
        if kind == tap.REC_TASK:
            task += 1
            at_task = task * period
        when = at_task + (period // 2 if kind == tap.REC_PRESENT else 0)
        struct.pack_into("<q", data, at + 16, when)
    path.write_bytes(bytes(data))


class SimScene:
    def __init__(self, out: Path, work: Path, rom_path: Path, symbols: sim.Symbols, vi: dict, version: str = "us"):
        self.out, self.work, self.rom_path, self.symbols = Path(out), Path(work), Path(rom_path), symbols
        buffers = [sim.phys(symbols[name]) for name in FRAME_BUFFER_NAMES]
        offset = (vi["origin"] - min(buffers)) % (buffers[1] - buffers[0])    # the line the VI starts at
        self.vi, self.origins = dict(vi), [buffer + offset for buffer in buffers]
        self.rom = self.rom_path.read_bytes()
        self.header = rom_header_hex(self.rom)
        self.lab = self.work / "lab"
        armed = self.lab / "SM64Trainer" / "scene-lab"
        armed.mkdir(parents=True, exist_ok=True)
        options = SimpleNamespace(version=version, seconds=600, no_reference=True, reference_every=1,
                                  full_every=0, no_scene_state=False, no_audio_state=True)
        (armed / "armed.txt").write_text(tap.arming_text(options), encoding="ascii")
        self.frames = self.work / "frames"
        self.frames.mkdir(exist_ok=True)
        self.count = 0
        self.host = None

    def _frame_input(self, ram: np.ndarray) -> Path:
        task_at = struct.unpack_from(">I", ram, sim.phys(self.symbols["gGfxSPTask"]))[0]
        words = struct.unpack_from(">16I", ram, sim.phys(task_at))
        task = dict(zip(TASK_NAMES, words, strict=True))
        if task["type"] != 1 or not task["data_ptr"]:
            raise SimSceneError(f"frame {self.count}: no graphics task at {task_at:08x}")
        folder = self.frames / f"{self.count:06d}"
        folder.mkdir()
        (folder / "rdram.bin").write_bytes(sim.be32(ram.tobytes()).tobytes())
        dmem = bytearray(0x1000)
        struct.pack_into("<16I", dmem, 0xFC0, *[task[name] & 0x1FFFFFFF if name in PHYSICAL and task[name] else task[name]
                                                for name in TASK_NAMES])
        (folder / "dmem.bin").write_bytes(bytes(dmem))
        timer = struct.unpack_from(">I", ram, sim.phys(self.symbols["gGlobalTimer"]))[0]
        (folder / "frame.json").write_text(json.dumps({
            "schema": "scene-lab-frame/1", "source": {"kind": "frame-sim", "global_timer": timer},
            "vi": {**self.vi, "origin": self.origins[self.count % 3]}, "rom_header": self.header, "task": task}) + "\n",
            encoding="utf-8")
        return folder

    def _say(self, line: str) -> None:
        self.host.stdin.write(line + "\n")
        self.host.stdin.flush()
        answer = self.host.stdout.readline()
        if not answer.startswith("ok"):
            raise SimSceneError(f"the producer answered {answer.strip()!r} to {line!r}")

    def add(self, ram: np.ndarray) -> None:
        folder = self._frame_input(ram)
        if self.host is None:
            self.host = subprocess.Popen([str(tap.build_offline_host()), str(self.lab), "--stdin"],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, **quiet_spawn_kwargs())
            self._say(f"header {folder}")
        self._say(f"frame {folder}")
        self._say(f"vi {folder}")
        shutil.rmtree(folder)
        self.count += 1

    def close(self) -> Path:
        """End the producer; the session folder it wrote."""
        if self.host is None:
            raise SimSceneError("no frames")
        self.host.stdin.write("quit\n")
        self.host.stdin.flush()
        try:
            output, errors = self.host.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            self.host.kill()
            self.host.wait()
            raise SimSceneError("the producer did not finish its session") from None
        if self.host.returncode != 0:
            raise SimSceneError(f"the producer exited {self.host.returncode}: {output[-300:]}{errors[-300:]}")
        sessions = sorted((self.lab / "SM64Trainer" / "scene-lab").glob("tap-*"))
        if len(sessions) != 1:
            raise SimSceneError(f"expected one session, found {len(sessions)}")
        return sessions[0]

    def finish(self) -> dict:
        try:
            session = self.close()
            retime(session)
            source = package.load_source(session, "next", states=True)
            self.out.parent.mkdir(parents=True, exist_ok=True)
            return package.write_package(source, self.rom, self.out)   # <out>.partial until whole
        finally:
            self.abandon()

    def abandon(self) -> None:
        if self.host is not None and self.host.poll() is None:
            self.host.kill()
            self.host.wait()
        shutil.rmtree(self.work, ignore_errors=True)
