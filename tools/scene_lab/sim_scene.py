"""Simulated frames into a scene package through the real producer (Ghost Handoff re-simulation).

    writer = SimScene(out_package, work_folder, rom_path)
    writer.add(ram)          # each frame's RDRAM at the scene tap's moment, as frame_sim holds it
    stats = writer.finish()  # package.py's write stats; the work folder is removed

A simulated frame becomes a frame input (savestate_frames.py's format: rdram.bin in Project64's word
order, dmem.bin with the frame's OSTask at 0xFC0, frame.json), the offline host feeds it through the
wrapper's own producer (plugin/gfxwrap/scene_tap.cpp) one ProcessDList at a time over its stdin, and
the frame folder is deleted once the host has read it, so a long scene never needs its frames on
disk at once. The session that producer writes becomes the package through package.py's own
writer: the same records, regions and checks as a recorded attempt's scene. No sound and no
presents: the viewer then plays at its FPS.
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
FRAMEBUFFERS = (0x0038F800, 0x003B5000, 0x003DA800)   # gFrameBuffer0..2: the VI origin a frame shows


class SimSceneError(RuntimeError):
    """The producer refused or stopped; the message says where."""


def rom_header_hex(rom: bytes) -> str:
    """The cartridge header as Project64 holds it (word-swapped), as frame.json carries it."""
    return sim.be32(rom[:0x40]).tobytes().hex()


class SimScene:
    def __init__(self, out: Path, work: Path, rom_path: Path, symbols: sim.Symbols, version: str = "us"):
        self.out, self.work, self.rom_path, self.symbols = Path(out), Path(work), Path(rom_path), symbols
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
            "vi": {"origin": FRAMEBUFFERS[self.count % 3]}, "rom_header": self.header, "task": task}) + "\n",
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
