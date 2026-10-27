"""The stem renderer (M4 round 4, lab only): music and sound effects for every VI of live play,
and each attempt scene's stems on its own sound's samples.

    stems = StemsProcess(rom, symbol_map, lab_root=None)    # the cutter's handle; the child starts now
    future = stems.layer(audio_payload, first_qpc, session) # one scene's stems layer (stems_layer.py)
    stems.close()                                           # EOF: the child stops and exits

Griffin (round 4, item 11): "we need to be able to toggle music / game sound effects separately
(I might want to have footage with only mario / world sounds but no music, for example)".

The child (serve) follows the newest live spool session's VI records: live_spool.Lease asks the
wrapper for the per-VI audio spans and SP DMEM (tap.audio_state_lines), which it copies at every
UpdateScreen while the game's audio task for that VI still waits. At each VI where one waits
(stems_check.pending_task, G0), stems_check.Splitter renders it twice with the ROM's own audio
microcode in cxd4: music (sequence players 0 and 1) and sound effects (player 2, Mario's voice
included), each with the other side's notes mixing into scratch DMEM and each carrying its own
reverb ring. At the next VI it reads what Project64's RSP wrote for that task: the game's own
output. Every VI becomes one row of the stem store (<lab>/stems/<session>/, a file per
FILE_ROWS VIs, kept KEEP_SECONDS): its QPC, flags and the three streams. One VI in G1_EVERY also
renders the task as dispatched and compares it with Project64's output (G1, sampled), so every
scene carries its own evidence that the microcode made the sound he heard.

The rendering is continuous rather than per take: a stem's reverb ring needs STEM_SETTLE_S of
the stem's own past before music and effects add up to the mix, and a renderer that follows the
whole session has it for every attempt that starts after its first second.

A scene's stems (StemsProcess.layer, which the cutter asks for while it adds the scene's sound):
the child waits until its rows pass the end of the scene's sound, reads them from the store and
hands them to stems_layer.build, which lines the game's output up with the recording and writes
music and effects onto the recording's own samples.

Why a process of its own (below normal priority, hidden): the rendering is CPU work for as long
as he plays, and the cutter's takes and the server's poll loop each hold their own process's
lock; this child neither slows them nor waits on them. Its cxd4 calls release Python's lock, but
the note ownership and the list edits do not.

Evidence (2026-10-27, offline: a round-1 savestate's VIs, Whomp's Fortress with music and coin
and voice effects, through the real producer and spool writer while the child followed): every
VI became a row (1198 of 1199; the last waits for a next VI); the child used 2.7 to 3.4 ms of
one core per VI, reading the spool included, so 16 to 20% of one core at 59.94 VIs a second;
a VI took 2.0 ms p50, 4.0 ms p95 and 9.0 ms at most; the sampled G1 found all 39 checks
identical, and settled music + effects stayed within 7 units of the game's output. The store
takes 6.4 KB a VI (23 MB a minute; about 345 MB for the window). Not yet seen live: Project64's
own pacing beside it, and his machine's load.
"""
from __future__ import annotations

import json
import os
import queue
import struct
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import stems_check as sc  # noqa: E402
import stems_probe as sp  # noqa: E402
import tap  # noqa: E402

ROW = struct.Struct("<IIqIIII")         # vi index, flags, qpc, samples, spool tasks at the VI, G1 max abs, 0
TASK, REAL, SETTLED, SPLIT_FAILED, AFTER_GAP, MISSED, CHECKED, DIFFERS = 1, 2, 4, 8, 16, 32, 64, 128
FILE_ROWS = 900                         # a store file per ~15 s of VIs
KEEP_SECONDS = 960.0                    # rows kept: the spool's 15-minute window and a minute
G1_EVERY = 30                           # one VI in this many also checks the microcode against Project64
POLL_SECONDS = 0.02                     # a follower at the live edge re-reads this often
SESSION_SECONDS = 1.0                   # how often an idle child looks for a newer session
LAYER_WAIT_SECONDS = 30.0               # how long a layer request waits for the rows to pass its end
STEMS_DIR = "stems"
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


# ---------------------------------------------------------------- reading the spool's VI records

def vi_records(data, position: int, *, torn_ok: bool = True):
    """(end offset, VI record) for each whole VI record in a live-spool segment's bytes, other
    records skipped undecoded. A segment after the first (`position` > 0) starts with the
    writer's keys, which repeat the state the records before it built: skipped."""
    for at, kind, size, sequence, qpc, flags in tap._walk(data, torn_ok):
        if position and flags & tap.WRITER_KEY:
            continue
        body = memoryview(data)[at + 32:at + size]
        if kind == tap.REC_PACKED:
            inner, inner_bytes, codec, stream_bytes, payload_bytes, _pieces, dedupe, _ = tap.PACKED.unpack_from(body, 0)
            if inner != tap.REC_VI:
                continue
            if dedupe:
                raise ValueError(f"VI record {sequence} is packed against the segment's image")
            payload = bytes(body[32:32 + payload_bytes])
            stream = tap._xpress(payload, stream_bytes) if codec == tap.CODEC_XPRESS else payload
            if len(stream) != inner_bytes:
                raise ValueError(f"VI record {sequence} unpacks to the wrong length")
            body, kind = memoryview(stream), inner
        if kind == tap.REC_VI:
            yield at + size, tap._parse_record(kind, sequence, qpc, body)


def segment_numbers(session: Path) -> list[int]:
    numbers = []
    for path in session.glob("seg-*.tap"):
        try:
            numbers.append(int(path.stem.removeprefix("seg-")))
        except ValueError:
            continue
    return sorted(numbers)


def session_vi_records(session: Path, *, first_segment: int | None = None):
    """Every VI record of a session's segments at rest (the checker's input), in order: the
    first segment's keys rebuild the state, later segments' keys are skipped."""
    numbers = [n for n in segment_numbers(session) if first_segment is None or n >= first_segment]
    for position, number in enumerate(numbers):
        data = (session / f"seg-{number:06d}.tap").read_bytes()
        for _end, record in vi_records(data, position):
            yield record


class ViFollower:
    """One live session's VI records as the writer flushes them, from the newest segment when
    it starts. poll() never blocks: the records whole now. A segment the writer deleted before
    it was read (the follower fell behind the spool's window) restarts it at the oldest one
    left, and `gaps` counts it; `ended` once the session's summary exists and all is read."""

    def __init__(self, session: Path):
        self.session = Path(session)
        self.number: int | None = None
        self.offset = 0
        self.pending = b""
        self.position = 0
        self.gaps = 0
        self.ended = False

    def poll(self) -> list:
        numbers = segment_numbers(self.session)
        if self.number is None:
            if not numbers:
                self.ended = (self.session / "summary.txt").is_file()
                return []
            self.number, self.position = numbers[-1], 0
        try:
            with (self.session / f"seg-{self.number:06d}.tap").open("rb") as handle:
                handle.seek(self.offset)
                data = handle.read()
        except FileNotFoundError:
            return self._restart(numbers)
        if data:
            buffer = self.pending + data if self.pending else data
            whole = 0
            for at, _kind, size, *_ in tap._walk(buffer, True):
                whole = at + size
            records = [record for _end, record in vi_records(buffer[:whole], self.position, torn_ok=False)]
            self.pending = bytes(buffer[whole:])
            self.offset += len(data)
            return records
        later = [n for n in numbers if n > self.number]
        if later:                       # the writer closed this one before it opened the next
            if later[0] != self.number + 1:
                return self._restart(numbers)
            self.number, self.offset, self.pending, self.position = later[0], 0, b"", self.position + 1
            return self.poll()
        self.ended = (self.session / "summary.txt").is_file()
        return []

    def _restart(self, numbers: list[int]) -> list:
        numbers = [n for n in numbers if self.number is None or n > self.number] or numbers
        self.gaps += 1
        self.number = numbers[0] if numbers else None
        self.offset, self.pending, self.position = 0, b"", 0
        return []


# ---------------------------------------------------------------- the stem store

class StemStore:
    """Rows on disk under <lab>/stems/<session name>/, one file per FILE_ROWS VIs, named by its
    first row's VI and QPC, so a span reads only the files it touches. Row: ROW, then int16
    stereo for each stream it carries (the game's output when REAL, music and effects when TASK)."""

    def __init__(self, root: Path, session: str):
        self.folder = Path(root) / STEMS_DIR / session
        self.folder.mkdir(parents=True, exist_ok=True)
        self.handle = None
        self.rows = 0
        self.newest_qpc: int | None = None
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)

    def append(self, vi: int, flags: int, qpc: int, tasks: int, g1: int, streams: list) -> None:
        samples = len(streams[0]) if streams else 0
        data = ROW.pack(vi, flags, qpc, samples, tasks, g1, 0) + b"".join(
            np.ascontiguousarray(stream, "<i2").tobytes() for stream in streams)
        with self.lock:
            if self.handle is None or self.rows >= FILE_ROWS:
                self._roll(vi, qpc)
            self.handle.write(data)
            self.handle.flush()
            self.rows += 1
            self.newest_qpc = qpc
            self.changed.notify_all()

    def _roll(self, vi: int, qpc: int) -> None:
        if self.handle is not None:
            self.handle.close()
        self.handle = (self.folder / f"vi-{vi:08d}-{qpc:020d}.stm").open("ab")
        self.rows = 0
        cutoff = time.time() - KEEP_SECONDS
        for path in self.folder.glob("vi-*.stm"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

    def close(self) -> None:
        with self.lock:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            self.changed.notify_all()

    def wait_past(self, qpc: int, timeout: float, alive) -> bool:
        """Blocks until a row at or past `qpc` is stored, the renderer stops (`alive()` false)
        or `timeout` passes; True when the rows reach it."""
        deadline = time.monotonic() + timeout
        with self.lock:
            while self.newest_qpc is None or self.newest_qpc < qpc:
                left = deadline - time.monotonic()
                if left <= 0 or not alive():
                    return False
                self.changed.wait(min(left, 0.25))
        return True


def read_rows(folder: Path, qpc_from: int, qpc_to: int) -> list[dict]:
    """The stored rows whose QPC lies in [qpc_from, qpc_to], in VI order: {"vi", "flags", "qpc",
    "tasks", "g1", "real", "music", "effects"} (arrays or None)."""
    files = []
    for path in folder.glob("vi-*.stm"):
        try:
            _, vi, qpc = path.stem.split("-")
            files.append((int(vi), int(qpc), path))
        except ValueError:
            continue
    files.sort()
    chosen = [path for i, (_vi, qpc, path) in enumerate(files)
              if qpc <= qpc_to and (i + 1 == len(files) or files[i + 1][1] >= qpc_from)]
    rows = []
    for path in chosen:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        at = 0
        while at + ROW.size <= len(data):
            vi, flags, qpc, samples, tasks, g1, _ = ROW.unpack_from(data, at)
            count = (bool(flags & REAL) + 2 * bool(flags & TASK)) * samples * 2
            end = at + ROW.size + 2 * count
            if end > len(data):
                break                                   # the row being written
            values = np.frombuffer(data, "<i2", count, at + ROW.size).reshape(-1, samples, 2) if count else None
            at = end
            if not qpc_from <= qpc <= qpc_to:
                continue
            streams = list(values) if values is not None else []
            real = streams.pop(0) if flags & REAL else None
            music, effects = streams if flags & TASK else (None, None)
            rows.append({"vi": vi, "flags": flags, "qpc": qpc, "tasks": tasks, "g1": g1, "samples": samples,
                         "real": real, "music": music, "effects": effects})
    return rows


# ---------------------------------------------------------------- rendering

class StemRenderer:
    """VI records and their states in, one stored row per VI out (StemStore.append). A task's
    row waits for the next VI, which holds what Project64's RSP wrote for it."""

    def __init__(self, splitter: sc.Splitter, sym: dict, store: StemStore):
        self.splitter, self.sym, self.store = splitter, sym, store
        self.last: sc.Pending | None = None
        self.last_vi: int | None = None
        self.held: dict | None = None
        self.counts = {"vis": 0, "tasks": 0, "missed": 0, "split_failed": 0, "checked": 0, "differs": 0, "gaps": 0}
        self.wall_ns: list[int] = []                 # recent VIs' rendering time, each
        self.cpu = [None, 0.0, 0]                    # this thread's CPU: (first reading, last, VIs between)

    def restart(self) -> None:
        """The next record does not follow the last (the follower restarted)."""
        self.splitter.restart()
        if self.held is not None:
            self._store(self.held, None)
            self.held = None
        self.last = self.last_vi = None

    def visit(self, record: tap.ViRegions, state: tap.ViState) -> None:
        began = time.perf_counter_ns()
        vi = record.fields["vi_index"]
        ram = sc.WordRam(state.ram, self.sym)
        gap = self.last_vi is not None and vi != self.last_vi + 1
        if gap:
            self.counts["gaps"] += 1
            self.splitter.restart()
        if self.held is not None:
            held, self.held = self.held, None
            self._store(held, None if gap or vi != held["vi"] + 1 else state)
        pending = sc.pending_task(ram, self.sym, vi)
        reason = sc.judge(pending, self.last if self.last_vi == vi - 1 else None)
        flags = AFTER_GAP if gap or self.last_vi is None else 0
        if reason is not None:
            flags |= MISSED
            self.counts["missed"] += 1
        row = {"vi": vi, "flags": flags, "qpc": record.qpc, "tasks": record.fields["tasks"], "g1": 0}
        if pending.ok:
            self.counts["tasks"] += 1
            if self.counts["tasks"] % G1_EVERY == 0:
                self.splitter.mix(pending, state)
                row["mix"] = sp.ai_samples(self.splitter.work, pending.ai_buffer, pending.ai_length)
            renders, seen = self.splitter.split(pending, state, ram)
            row["flags"] |= TASK | (SETTLED if seen["settled"] else 0)
            if seen["unknown_owner_commands"] or seen["unattributed_envmixers"] or seen["edit_errors"]:
                row["flags"] |= SPLIT_FAILED
                self.counts["split_failed"] += 1
            empty = np.zeros((pending.ai_length, 2), np.int16)
            row.update(pending=pending, music=renders["music"] if renders["music"] is not None else empty,
                       effects=renders["effects"] if renders["effects"] is not None else empty,
                       region=sc.rsp_regions(ram, self.sym, pending)[0])
            self.held = row
        else:
            self._store(row, None)
        self.last, self.last_vi = pending, vi
        self.counts["vis"] += 1
        self.wall_ns.append(time.perf_counter_ns() - began)
        if len(self.wall_ns) > 4096:
            del self.wall_ns[:2048]
        now = time.thread_time()                      # 15.6 ms ticks on Windows: a mean over many VIs
        if self.cpu[0] is None:
            self.cpu = [now, now, 0]
        else:
            self.cpu[1:] = [now, self.cpu[2] + 1]

    def _store(self, row: dict, following: tap.ViState | None) -> None:
        """A row to the store: a task's with the game's output `following` (the next VI's state) holds."""
        flags, streams = row["flags"], []
        if flags & TASK:
            if following is not None:
                _name, begin, size = row["region"]
                real = sp.swap32(following.ram[begin:begin + size].copy()).view(">i2").astype(np.int16).reshape(-1, 2)
                flags |= REAL
                streams.append(real)
                if "mix" in row:
                    flags |= CHECKED
                    row["g1"] = int(np.abs(real.astype(np.int32) - row["mix"]).max()) if len(real) else 0
                    self.counts["checked"] += 1
                    if row["g1"]:
                        flags |= DIFFERS
                        self.counts["differs"] += 1
            streams += [row["music"], row["effects"]]
        self.store.append(row["vi"], flags, row["qpc"], row["tasks"], row["g1"], streams)

    def finish(self) -> None:
        if self.held is not None:
            self._store(self.held, None)
            self.held = None

    def cost(self) -> dict:
        """Per VI, in ms: the recent VIs' rendering time (p50, p95, max) and the render thread's
        CPU over every VI since it began (its mean)."""
        values = sorted(self.wall_ns)
        if not values:
            return {}
        pick = lambda p: round(values[min(len(values) - 1, int(p * (len(values) - 1) + 0.5))] / 1e6, 3)  # noqa: E731
        first, last, vis = self.cpu
        return {"ms_p50": pick(.5), "ms_p95": pick(.95), "ms_max": round(values[-1] / 1e6, 3),
                "cpu_ms_mean": round((last - first) * 1000 / vis, 3) if vis else None, "vis": self.counts["vis"]}


# ---------------------------------------------------------------- the child

def build_dll() -> tuple[Path | None, str | None]:
    """(the cxd4 harness DLL, None), compiled once per input hash from the vendored source
    (tools/scene_lab/cxd4, stems_probe.build_lle) into the scene lab's build cache, or (None, why)."""
    cache = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "SM64Trainer/build-cache/scene-lab"
    try:
        return sp.build_lle(HERE / "cxd4", cache), None
    except SystemExit:
        return None, "Splitting music from sound effects needs the x64 MSVC toolchain to build its sound program runner."
    except Exception as error:  # noqa: BLE001 - a failed build is a reason, never a dead child
        return None, f"The sound program runner could not be built: {error}"


class Child:
    """The child's state: the session it follows, its renderer and store, and the layer requests
    the cutter sends. The render loop runs on its own thread; requests on theirs."""

    def __init__(self, rom: bytes, symbol_map: Path, root: Path, emit):
        self.rom, self.symbol_map, self.root, self.emit = rom, Path(symbol_map), Path(root), emit
        self.sym = sp.load_symbols(self.symbol_map)
        dll, self.problem = build_dll()
        self.lle = sp.Lle(dll, bytes(0x1000), bytes(0x1000)) if dll else None
        self.session: Path | None = None
        self.follower: ViFollower | None = None
        self.store: StemStore | None = None
        self.renderer: StemRenderer | None = None
        self.state: tap.ViState | None = None
        self.gaps = 0
        self.stop = threading.Event()
        self.reported = time.monotonic()

    def run(self) -> None:
        """The render loop, until stop: follow the newest session's VIs and store a row for each."""
        try:
            while not self.stop.is_set():
                if self.follower is None or self.follower.ended:
                    if self.renderer is not None:
                        self.renderer.finish()      # the session's last task: no next VI will come
                    if not self._next_session():
                        self.stop.wait(SESSION_SECONDS)
                        continue
                records = self.follower.poll()
                if self.follower.gaps != self.gaps:
                    self.gaps = self.follower.gaps
                    self.renderer.restart()
                for record in records:
                    self.state.apply(record)
                    self.renderer.visit(record, self.state)
                if time.monotonic() - self.reported > 30:
                    self.reported = time.monotonic()
                    self.emit({"stems": {"session": self.session.name, **self.renderer.counts,
                                         **self.renderer.cost()}})
                if not records:
                    self.stop.wait(POLL_SECONDS)
        except Exception as error:  # noqa: BLE001 - said once; layer requests then get the reason
            self.problem = f"The stem renderer stopped: {error}"
            self.emit({"problem": self.problem})
        finally:
            self._close()

    def _next_session(self) -> bool:
        """Follows the newest live session when it is not the one followed; True when it did."""
        import live_spool
        sessions = live_spool._sessions(self.root)
        newest = sessions[-1] if sessions else None
        if newest is None or newest == self.session:
            return False
        self._close()
        self.session = newest
        spans = sc.vi_spans(newest)
        if not spans:                       # recorded without the audio spans: nothing to render
            self.follower = None
            return False
        header = tap.session_header(newest)
        self.state = tap.ViState(int(header["rdram_size"]))
        self.store = StemStore(self.root, newest.name)
        self.renderer = StemRenderer(sc.Splitter(self.lle, self.rom, self.symbol_map, spans, self.sym), self.sym,
                                     self.store)
        self.follower, self.gaps = ViFollower(newest), 0
        names = {path.name for path in sessions}
        for folder in (self.root / STEMS_DIR).glob("live-*"):
            if folder.name not in names:    # its session left the spool: its rows go too
                for path in folder.glob("*.stm"):
                    path.unlink(missing_ok=True)
                try:
                    folder.rmdir()
                except OSError:
                    pass
        return True

    def _close(self) -> None:
        if self.renderer is not None:
            self.renderer.finish()
        if self.store is not None:
            self.store.close()
        self.renderer = self.store = None

    def layer(self, note: dict) -> None:
        """One scene's stems layer: waits for the rows to pass the end of its sound, builds it
        (stems_layer.build) and writes it to note["out"]; {"layer": token, "path"} or
        {"layer": token, "reason"}."""
        import audio_layer as al
        import stems_layer
        token = note.get("token")
        try:
            if self.lle is None:
                raise LookupError(self.problem)
            audio = al.read_layer(Path(note["audio"]).read_bytes())
            first_qpc, session = int(note["first_qpc"]), str(note["session"])
            start = first_qpc + audio.segments[0].anchor - int(stems_layer.DELAY_S[1] * audio.frequency)
            end = first_qpc + audio.segments[-1].end
            store = self.store
            if store is not None and self.session is not None and self.session.name == session:
                store.wait_past(end, LAYER_WAIT_SECONDS, lambda: not self.stop.is_set() and self.store is store
                                and not (self.follower is not None and self.follower.ended))
            rows = read_rows(self.root / STEMS_DIR / session, start, end - int(stems_layer.DELAY_S[0] * audio.frequency))
            payload = stems_layer.build(audio, first_qpc, rows)
            Path(note["out"]).write_bytes(payload)
            self.emit({"layer": token, "path": str(note["out"])})
        except LookupError as error:
            self.emit({"layer": token, "reason": str(error)})
        except Exception as error:  # noqa: BLE001 - this scene has no stems, and says why
            self.emit({"layer": token, "reason": f"The music and sound effects could not be split: {error}"})


def serve(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="stems_live")
    parser.add_argument("--rom", required=True, type=Path)
    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--lab-root", type=Path)
    args = parser.parse_args(argv)
    sink, sink_lock = sys.stdout, threading.Lock()

    def emit(event: dict) -> None:
        with sink_lock:
            sink.write(json.dumps(event) + "\n")
            sink.flush()

    import live_spool
    try:
        child = Child(args.rom.read_bytes(), args.map, live_spool.resolve_lab_root(args.lab_root), emit)
    except (OSError, SystemExit) as error:
        emit({"ready": False, "problem": f"The stem renderer could not start: {error}"})
        return 1
    emit({"ready": child.problem is None, "problem": child.problem})
    renderer = threading.Thread(target=child.run, name="stems-render", daemon=True)
    if child.lle is not None:
        renderer.start()
    for line in sys.stdin:
        try:
            note = json.loads(line)
        except ValueError:
            continue
        if isinstance(note, dict) and note.get("op") == "layer":
            threading.Thread(target=child.layer, args=(note,), name="stems-layer", daemon=True).start()
    child.stop.set()
    if renderer.is_alive():
        renderer.join(timeout=10)
    return 0


# ---------------------------------------------------------------- the cutter's handle

class StemsProcess:
    """The child, hidden and below normal priority, and its pipes. layer() returns a Future of
    {"path"} or {"reason"}; when the child is gone every request answers with the reason."""

    def __init__(self, rom: Path, symbol_map: Path, *, lab_root: Path | None = None, on_event=None):
        from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
        args = ["--rom", str(rom), "--map", str(symbol_map)]
        if lab_root is not None:
            args += ["--lab-root", str(lab_root)]
        argv, env = python_child("stems_live", "--scene-lab-stems", args, site_packages=True)
        env = dict(env if env is not None else os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(HERE), env.get("PYTHONPATH"))))
        spawn = quiet_spawn_kwargs()
        spawn["creationflags"] = spawn.get("creationflags", 0) | _BELOW_NORMAL
        self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env=env, **spawn)
        self.on_event = on_event or (lambda event: None)
        self.problem: str | None = None
        self.pending: dict[int, Future] = {}
        self.lock = threading.Lock()
        self.serial = 0
        self.outgoing: queue.SimpleQueue = queue.SimpleQueue()
        self.threads = [threading.Thread(target=self._write, name="stems-notes", daemon=True),
                        threading.Thread(target=self._read, name="stems-events", daemon=True)]
        for thread in self.threads:
            thread.start()

    @property
    def pid(self) -> int:
        return self.process.pid

    def layer(self, audio_payload: bytes, first_qpc: int, session: str, folder: Path) -> Future:
        """One scene's stems layer from the child: its sound's audio_layer payload, its first
        present's QPC and the spool session it was cut from; files go to `folder` (the caller
        deletes the answer's path)."""
        future: Future = Future()
        with self.lock:
            if self.problem is not None:
                future.set_result({"reason": self.problem})
                return future
            self.serial += 1
            token = self.serial
            self.pending[token] = future
        audio = Path(folder) / f"stems-audio-{os.getpid()}-{token}.bin"
        audio.write_bytes(audio_payload)
        future.add_done_callback(lambda _: audio.unlink(missing_ok=True))
        self.outgoing.put({"op": "layer", "token": token, "audio": str(audio), "first_qpc": int(first_qpc),
                           "session": session, "out": str(Path(folder) / f"stems-{os.getpid()}-{token}.bin")})
        return future

    def _write(self) -> None:
        while True:
            note = self.outgoing.get()
            try:
                if note is None:
                    self.process.stdin.close()
                    return
                self.process.stdin.write((json.dumps(note) + "\n").encode("utf-8"))
                self.process.stdin.flush()
            except (OSError, ValueError):
                return

    def _read(self) -> None:
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if "layer" in event:
                with self.lock:
                    future = self.pending.pop(event["layer"], None)
                if future is not None:
                    future.set_result({key: value for key, value in event.items() if key != "layer"})
                continue
            if event.get("problem"):
                with self.lock:
                    self.problem = event["problem"]
            self.on_event(event)
        code = self.process.wait()
        with self.lock:
            self.problem = self.problem or f"The stem renderer stopped (exit {code})."
            pending, self.pending = self.pending, {}
        for future in pending.values():
            future.set_result({"reason": self.problem})
        self.on_event({"exited": code})

    def close(self, timeout: float = 10.0) -> int | None:
        """Ends the child and its two threads: nothing of it runs once this returns (the
        cutter's interpreter ends right after, and a pipe thread still writing then failed
        fast, 0xC0000409)."""
        self.outgoing.put(None)
        try:
            code = self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            code = self.process.wait(timeout=5)
        for thread in self.threads:
            thread.join(timeout=5)
        return code


def attach(data: bytes, rom: bytes, audio_payload: bytes, stems: StemsProcess | None, problem: str | None,
           first_qpc: int, session: str, folder: Path, wait: float = LAYER_WAIT_SECONDS + 15) -> bytes:
    """The package bytes (with their source-audio layer) with the scene's stems layer: the
    child's, or one that says why there is none. The cutter's one step for the stems."""
    import package
    import stems_layer
    if stems is None:
        answer = {"reason": problem or "This scene lab does not split music from sound effects."}
    else:
        try:
            answer = stems.layer(audio_payload, first_qpc, session, folder).result(timeout=wait)
        except TimeoutError:
            answer = {"reason": "The music and sound effects took too long to split."}
    payload = None
    if answer.get("path"):
        path = Path(answer["path"])
        try:
            payload = path.read_bytes()
        except OSError as error:
            answer = {"reason": f"The split music and sound effects could not be read: {error}"}
        finally:
            path.unlink(missing_ok=True)
    return package.with_stems(data, rom, payload if payload is not None else stems_layer.unavailable(answer["reason"]))


if __name__ == "__main__":
    raise SystemExit(serve())
