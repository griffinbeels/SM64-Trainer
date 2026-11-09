# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""Simulated frames into a scene package through the real producer (Ghost Handoff re-simulation).

    writer = SimScene(out_package, work_folder, rom_path, symbols, vi, sidecar=..., on_publish=...)
    writer.add(ram)          # each frame's RDRAM at the scene tap's moment, as frame_sim holds it
    stats = writer.finish()  # package.py's write stats; the work folder is removed

A simulated frame becomes a frame input (savestate_frames.py's format: rdram.bin in Project64's word
order, dmem.bin with the frame's OSTask at 0xFC0, frame.json), the offline host feeds it through the
wrapper's own producer (plugin/gfxwrap/scene_tap.cpp) one ProcessDList at a time over its stdin, and
shows it: one VI with the stage's VI registers (`vi`), its origin turning over the three frame buffers
as the game's does, so the producer records a present and the viewer lists the frame. No sound.

The package is written while the frames come (the fast load, 2026-11-08): a packer thread follows the
host's tap.bin as it grows (tap.SegmentReader), moves each record to the game's clock (Clock: the host
runs as fast as the simulation feeds it, the viewer plays by record times), and hands each frame to
package.PackageWriter. With `sidecar`, the package is written in place and published there every
PUBLISH_SECONDS (attempt_cutter.py's growing takes, which the viewer already opens: scene_session.Growth),
so a scene can play while it is still being made; on_publish(frames) hears each publication. The host
is fed RING frames ahead of its answers, so the simulation, the producer and the packer run at once.

Measured on another player's 324-frame Whomp's Fortress ghost (2026-11-08), per frame: simulation 10 ms,
frame input 8.5 ms (8 MB written; now into reused folders), producer 16.3 ms, packer 11 ms with zlib 6
(42 ms with the LZMA preset this used before, which made 13.2 s of a 34.7 s build).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame_sim as sim  # noqa: E402
import live_spool  # noqa: E402
import package  # noqa: E402
import tap  # noqa: E402
from savestate_frames import TASK_NAMES  # noqa: E402

from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402

PHYSICAL = {"ucode", "ucode_data", "dram_stack", "output_buff", "output_buff_size", "data_ptr", "yield_data_ptr"}
FRAME_BUFFER_NAMES = ("gFrameBuffer0", "gFrameBuffer1", "gFrameBuffer2")   # the VI origin turns over these
NTSC_FRAME = 1001 / 30000                             # seconds a game frame shows (two NTSC VIs)
RING = 4                                              # frames handed to the host ahead of its answers
ZLIB_LEVEL = 6                                        # package.compress: the attempt cutter's takes use it too
PUBLISH_SECONDS = 0.25                                # how often a growing package is published
FIRST_PUBLISH_FRAMES = 8                              # the first publication: a quarter second to play
FOLLOW_SECONDS = 0.004                                # the packer's wait for more of tap.bin


class SimSceneError(RuntimeError):
    """The producer refused or stopped; the message says where."""


def rom_header_hex(rom: bytes) -> str:
    """The cartridge header as Project64 holds it (word-swapped), as frame.json carries it."""
    return sim.be32(rom[:0x40]).tobytes().hex()


class Clock:
    """Each record of a simulated session at the game's clock, as the session grows: task k at k frames, its
    present half a frame later, every other record with the task before it. feed() takes the bytes tap.bin
    gained and returns its whole records retimed; a record not yet whole waits for the next feed."""

    def __init__(self, frequency: int):
        self.period = round(frequency * NTSC_FRAME)
        self.task, self.at_task = -1, 0
        self.pending = b""

    def feed(self, data: bytes) -> bytes:
        buffer = bytearray(self.pending + data)
        end = 0
        for at, kind, size, _sequence, _qpc, _flags in tap._walk(buffer, torn_ok=True):
            if kind == tap.REC_TASK:
                self.task += 1
                self.at_task = self.task * self.period
            struct.pack_into("<q", buffer, at + 16, self.at_task + (self.period // 2 if kind == tap.REC_PRESENT else 0))
            end = at + size
        self.pending = bytes(buffer[end:])
        return bytes(buffer[:end])


class Seed:
    """The packer's knowledge of a ROM kept between builds (package.Attributor's `shared`): its decoded segment
    loads (provenance.segment_loads, 0.95 s every package otherwise) and the ROM windows and misses the packer
    discovered. A file per ROM in `folder`, plain data (a JSON head, then the loads' bytes), never code."""

    MAGIC, VERSION = b"SM64SEED", 1

    def __init__(self, folder: Path | None, rom: bytes):
        self.rom = rom
        self.path = None if folder is None else Path(folder) / f"seed-{hashlib.sha256(rom).hexdigest()[:16]}-v{self.VERSION}.bin"
        self.known: tuple[int, int] | None = None              # (windows, misses) as read: keep() writes only news
        self.loads: list | None = None
        self.read_ok = False

    def attributor(self, rdram_size: int) -> package.Attributor:
        loads, windows, misses = self._read()
        if loads is None:
            import provenance  # noqa: PLC0415
            loads = provenance.segment_loads(self.rom)
        else:
            self.read_ok = True
        self.loads = loads
        attributor = package.Attributor(self.rom, rdram_size, loads)
        for base, start, end in windows:
            if (base, start, end) not in attributor.windows:
                attributor.windows.add((base, start, end))
                attributor.static.append((base, package.Candidate(package.LOAD_ROM, start, end, self.rom[start:end])))
        attributor.misses.update(misses)
        self.known = (len(attributor.windows), len(attributor.misses))
        return attributor

    def _read(self) -> tuple[list | None, list, list]:
        try:
            data = self.path.read_bytes() if self.path is not None else b""
            if not data.startswith(self.MAGIC):
                return None, [], []
            size = struct.unpack_from("<I", data, len(self.MAGIC))[0]
            at = len(self.MAGIC) + 4
            head = json.loads(data[at:at + size])
            body, loads = at + size, []
            for row in head["loads"]:
                loads.append({**{key: row[key] for key in ("segment", "start", "end", "kind")},
                              "data": data[body + row["at"]:body + row["at"] + row["bytes"]]})
            return loads, [tuple(window) for window in head["windows"]], [bytes.fromhex(miss) for miss in head["misses"]]
        except (OSError, ValueError, KeyError, TypeError, struct.error):
            return None, [], []

    def keep(self, attributor: package.Attributor) -> None:
        """Writes what this build learned, when it learned anything."""
        if self.path is None or self.loads is None or (
                self.read_ok and self.known == (len(attributor.windows), len(attributor.misses))):
            return
        rows, blobs, at = [], [], 0
        for load in self.loads:
            rows.append({**{key: load[key] for key in ("segment", "start", "end", "kind")}, "at": at, "bytes": len(load["data"])})
            blobs.append(bytes(load["data"]))
            at += len(load["data"])
        head = json.dumps({"loads": rows, "windows": sorted(attributor.windows),
                           "misses": sorted(miss.hex() for miss in attributor.misses)}).encode()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.path.with_name(self.path.name + ".partial")
            with partial.open("wb") as handle:
                handle.write(self.MAGIC + struct.pack("<I", len(head)) + head)
                for blob in blobs:
                    handle.write(blob)
            partial.replace(self.path)
        except OSError:
            pass                                               # the next build decodes the ROM again


class Packer(threading.Thread):
    """Follows a one-shot session's tap.bin while the host writes it and writes the package frame by frame."""

    def __init__(self, session: Path, rom: bytes, out: Path, sidecar: Path | None, on_publish, seed: Seed,
                 shared: package.Attributor):
        super().__init__(name="sim-scene-packer", daemon=True)
        self.session, self.rom, self.out, self.sidecar, self.on_publish = session, rom, out, sidecar, on_publish
        self.seed, self.shared = seed, shared
        self.ended = threading.Event()          # the host has exited: whatever tap.bin holds now is all
        self.stopped = threading.Event()        # abandon: stop at once
        self.error: BaseException | None = None
        self.writer: package.PackageWriter | None = None
        self.published = 0
        self.stats: dict | None = None

    def _records(self, rdram_size: int, frequency: int):
        clock, reader, offset = Clock(frequency), tap.SegmentReader(rdram_size), 0
        path = self.session / "tap.bin"
        while not self.stopped.is_set():
            done = self.ended.is_set()
            try:
                with path.open("rb") as handle:
                    handle.seek(offset)
                    data = handle.read()
            except FileNotFoundError:
                data = b""
            if data:
                offset += len(data)
                for _at, _flags, record in reader.feed(clock.feed(data)):
                    yield record
                continue
            if done:
                return
            self.ended.wait(FOLLOW_SECONDS)

    def run(self) -> None:
        try:
            header = tap.session_header(self.session)
            frequency = int(header.get("qpc_frequency") or 0) or 1
            rdram_size = int(header["rdram_size"])
            source = package.Source("scene-tap", rdram_size, header.get("rom_header", ""),
                                    tap.identity_spec(self.session), header.get("wrapper_build"))
            source.states = package.TapStates() if package.has_state(self.session) else None
            source.captures = live_spool.captures(source, self._records(rdram_size, frequency), frequency)
            self.out.parent.mkdir(parents=True, exist_ok=True)
            shared = self.shared
            if shared.rdram_size != rdram_size:
                raise SimSceneError(f"the producer's session is {rdram_size} bytes of RDRAM, not {shared.rdram_size}")
            writer = self.writer = package.PackageWriter(source, self.rom, self.out, package.ZLIB, level=ZLIB_LEVEL,
                                                         shared=shared, in_place=self.sidecar is not None)
            published_at = 0.0
            for capture in source.captures:
                writer.add(capture)
                if self.sidecar is not None:
                    due = writer.state_frames >= FIRST_PUBLISH_FRAMES if not self.published else \
                        time.monotonic() - published_at >= PUBLISH_SECONDS
                    if due and writer.state_frames > self.published:
                        self._publish(writer)
                        published_at = time.monotonic()
            if self.stopped.is_set():
                return
            self.stats = writer.finish()
            if self.sidecar is not None:
                self.sidecar.unlink(missing_ok=True)       # the file is whole: a growing session reads it now
            self.seed.keep(shared)
        except BaseException as error:  # noqa: BLE001 - finish() raises it on the caller's thread
            self.error = error

    def _publish(self, writer) -> None:
        try:
            self.published = writer.publish(self.sidecar) or self.published
        except PermissionError:                             # an open scene was reading the last one: next time
            return
        if self.on_publish is not None:
            self.on_publish(self.published)

    def abandon(self) -> None:
        self.stopped.set()
        self.ended.set()
        self.join(timeout=30)
        if self.writer is not None:
            self.writer.abandon()
        if self.sidecar is not None:
            self.sidecar.unlink(missing_ok=True)


class SimScene:
    def __init__(self, out: Path, work: Path, rom_path: Path, symbols: sim.Symbols, vi: dict, version: str | None = None, *,
                 sidecar: Path | None = None, on_publish=None, seeds: Path | None = None):
        self.out, self.work, self.rom_path, self.symbols = Path(out), Path(work), Path(rom_path), symbols
        self.sidecar, self.on_publish = sidecar, on_publish
        buffers = [sim.phys(symbols[name]) for name in FRAME_BUFFER_NAMES]
        offset = (vi["origin"] - min(buffers)) % (buffers[1] - buffers[0])    # the line the VI starts at
        self.vi, self.origins = dict(vi), [buffer + offset for buffer in buffers]
        self.rom = self.rom_path.read_bytes()
        # The arming (scene spans, identity extras) is the ROM's own build: US spans over JP RAM would be wrong bytes
        # with nothing to say so, so the build comes from the header and a caller's word must agree with it.
        built = sim.version_of(self.rom)
        if built is None or version not in (None, built):
            raise SimSceneError(f"the ROM's build is {built}, not {version}")
        version = built
        self.header = rom_header_hex(self.rom)
        # Before the first frame: made while the packer runs, it would take the GIL from the simulation.
        self.seed = Seed(seeds, self.rom)
        self.shared = self.seed.attributor(sim.RDRAM_BYTES)
        self.lab = self.work / "lab"
        armed = self.lab / "SM64Trainer" / "scene-lab"
        armed.mkdir(parents=True, exist_ok=True)
        options = SimpleNamespace(version=version, seconds=600, no_reference=True, reference_every=1,
                                  full_every=0, no_scene_state=False, no_audio_state=True)
        (armed / "armed.txt").write_text(tap.arming_text(options), encoding="ascii")
        self.frames = self.work / "frames"
        self.frames.mkdir(parents=True, exist_ok=True)
        self.swapped = np.empty(sim.RDRAM_BYTES // 4, "<u4")   # rdram.bin in Project64's word order
        self.count = 0
        self.host = None
        # Commands sent whose answers are not read yet, each with the frame folder its answer frees. Every frame
        # gets a folder of its own: the host keeps the frames it read by folder name (tap_offline_host.cpp load).
        self.waiting: deque[tuple[str, Path | None]] = deque()
        self.packer: Packer | None = None

    def _frame_input(self, ram: np.ndarray) -> Path:
        task_at = struct.unpack_from(">I", ram, sim.phys(self.symbols["gGfxSPTask"]))[0]
        words = struct.unpack_from(">16I", ram, sim.phys(task_at))
        task = dict(zip(TASK_NAMES, words, strict=True))
        if task["type"] != 1 or not task["data_ptr"]:
            raise SimSceneError(f"frame {self.count}: no graphics task at {task_at:08x}")
        folder = self.frames / f"{self.count:06d}"
        folder.mkdir()
        np.copyto(self.swapped, ram.view(">u4"))             # each word's bytes reversed, as be32 does
        with (folder / "rdram.bin").open("wb") as handle:
            handle.write(memoryview(self.swapped).cast("B"))
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

    def _send(self, line: str, frees: Path | None = None) -> None:
        self.host.stdin.write(line + "\n")
        self.host.stdin.flush()
        self.waiting.append((line, frees))

    def _answer(self) -> None:
        line, frees = self.waiting.popleft()
        answer = self.host.stdout.readline()
        if not answer.startswith("ok"):
            raise SimSceneError(f"the producer answered {answer.strip()!r} to {line!r}")
        if frees is not None:                                 # the host has read that frame: its folder can go
            shutil.rmtree(frees, ignore_errors=True)

    def add(self, ram: np.ndarray) -> None:
        if self.packer is not None and self.packer.error is not None:
            raise self.packer.error
        while len(self.waiting) >= 2 * RING:                  # at most RING frames ahead of the host
            self._answer()
        folder = self._frame_input(ram)
        if self.host is None:
            self.host = subprocess.Popen([str(tap.build_offline_host()), str(self.lab), "--stdin"],
                                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                         text=True, **quiet_spawn_kwargs())
            self._send(f"header {folder}")
            self._answer()
        self._send(f"frame {folder}")
        self._send(f"vi {folder}", frees=folder)
        if self.packer is None:
            while self.waiting:                               # the session folder exists once the first frame is fed
                self._answer()
            self.packer = Packer(self._session(), self.rom, self.out, self.sidecar, self.on_publish, self.seed, self.shared)
            self.packer.start()
        self.count += 1

    def _session(self) -> Path:
        sessions = sorted((self.lab / "SM64Trainer" / "scene-lab").glob("tap-*"))
        if len(sessions) != 1:
            raise SimSceneError(f"expected one session, found {len(sessions)}")
        return sessions[0]

    def close(self) -> None:
        """Every answer read, then the producer ended (its writer drains into tap.bin before it exits)."""
        if self.host is None:
            raise SimSceneError("no frames")
        while self.waiting:
            self._answer()
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

    def finish(self) -> dict:
        try:
            self.close()
            packer = self.packer
            packer.ended.set()
            packer.join()
            if packer.error is not None:
                raise packer.error
            return packer.stats
        except BaseException:
            self.abandon()
            raise
        finally:
            shutil.rmtree(self.work, ignore_errors=True)

    def abandon(self) -> None:
        if self.host is not None and self.host.poll() is None:
            self.host.kill()
            self.host.wait()
        if self.packer is not None:
            self.packer.abandon()
        shutil.rmtree(self.work, ignore_errors=True)
