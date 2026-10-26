"""Record Project64's own audio beside a scene-tap session (M4 E4-audio, lab only).

    audio_capture.py <out.cap> [--seconds N] [--wait-seconds N] [--budget-mib N]

Waits for one Project64 process, taps its render stream through the
recorder's own source (replay/audio.py ProcessAudioSource: process loopback,
the game and nothing else) and writes every chunk in audio_session.py's
capture format. Each chunk is stamped when it reaches this tool with
time.perf_counter_ns() as QPC ticks: the counter the scene tap stamps
presents with, so audio_session.py joins the two with no anchor and no wall
clock (e4-audio-design.md, "The clock relation"). The stamp follows the
source's pump, so it carries the pump's hand-off delay too; the clock fit
keeps only the lower envelope of the stamps, and a delay never moves that.

It stops when Project64 exits, after --seconds, or when the source fails, and
keeps <out.cap>.json beside the capture: its state, why it stopped, what it
kept and lost, and the per-chunk cost. The rules:

- it holds the machine-wide recorder lock (core/recorder_lock.py) for the
  whole capture and refuses to start while another recorder holds it, so it
  never competes with the trainer's own capture;
- isolated audio only: when the process tap cannot start it refuses, rather
  than fall back to device loopback, which records everything on the endpoint;
- the source's delivery thread never waits: a chunk past the queue's byte
  budget is kept as a LOST record (its sample count and arrival, no bytes),
  and back-to-back losses merge into one;
- no window and no console it depends on, so it runs under pythonw.

The count of samples runs on across a watchdog reopen of the tap, so a reopen
shows in the clock fit as a hole (a step in time), which splits the clock the
same way a new epoch would. Capture files hold private game audio: keep them
out of the repository.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import audio_clock as ac  # noqa: E402
import audio_session as asn  # noqa: E402
from sm64_events.core import recorder_lock  # noqa: E402

RATE = 48000                  # ProcessAudioSource's pump speaks 48 kHz s16 stereo
POLL_S = 0.25
STATUS_EVERY_S = 5.0
COSTS_KEPT = 1 << 16
SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102


class CaptureRefused(RuntimeError):
    """The capture did not start; the message says why in plain words."""


def qpc_frequency() -> int:
    frequency = ctypes.c_int64()
    if not ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(frequency)):
        raise OSError("QueryPerformanceFrequency failed")
    return frequency.value


def qpc_stamp(frequency: int):
    """perf_counter_ns() as QPC ticks. On Windows it is QPC scaled to ns, so at
    this machine's 10 MHz the division is exact; elsewhere it rounds by a tick."""
    return lambda: time.perf_counter_ns() * frequency // 1_000_000_000


class Fanout:
    """Stamps each chunk as it arrives and queues it for the writer, never waiting.

    `on_pcm` runs on the source's delivery thread and is the only producer;
    the writer only pops. Neither takes a lock: deque append and popleft are
    atomic, and each byte counter has one writer. Losses are counted in
    samples on the producer side and merged until the next kept chunk, so the
    queue holds at most one LOST record between two kept ones and stays
    bounded by `budget_bytes` of PCM."""

    def __init__(self, stamp, budget_bytes: int):
        self._stamp = stamp
        self._budget = budget_bytes
        self._queue: deque = deque()
        self._ready = threading.Event()
        self._lost_run: ac.Chunk | None = None
        self._next = 0
        self.bytes_in = self.bytes_out = self.peak_bytes = 0
        self.kept_chunks = self.lost_records = self.lost_samples = 0
        self.costs_ns: deque = deque(maxlen=COSTS_KEPT)

    def on_pcm(self, data) -> None:
        began = time.perf_counter_ns()
        arrival = self._stamp()
        pcm = np.asarray(data, np.int16).reshape(-1, 2)
        samples = len(pcm)
        if not samples:
            return
        first, self._next = self._next, self._next + samples
        queued = self.bytes_in - self.bytes_out
        if queued + samples * 4 <= self._budget:
            self._flush_lost()
            self._queue.append((ac.Chunk(0, first, samples, arrival), pcm.tobytes()))
            self.bytes_in += samples * 4
            self.kept_chunks += 1
            self.peak_bytes = max(self.peak_bytes, queued + samples * 4)
        else:
            self._lose(first, samples, arrival)
        self._ready.set()
        self.costs_ns.append(time.perf_counter_ns() - began)

    def _lose(self, first: int, samples: int, arrival: int) -> None:
        run = self._lost_run
        if run is not None and run.samples + samples > ac.MAX_CHUNK_SAMPLES:
            self._flush_lost()
            run = None
        self._lost_run = (ac.Chunk(0, first, samples, arrival, True) if run is None
                          else ac.Chunk(0, run.first, run.samples + samples, arrival, True))
        self.lost_samples += samples

    def _flush_lost(self) -> None:
        if self._lost_run is not None:
            self._queue.append((self._lost_run, None))
            self.lost_records += 1
            self._lost_run = None

    def close(self) -> None:
        """Call once the source has stopped delivering: queues a pending loss."""
        self._flush_lost()
        self._ready.set()

    def records(self, done: threading.Event):
        """The writer's side: (Chunk, bytes | None) in order until `done` and drained."""
        while True:
            try:
                chunk, data = self._queue.popleft()
            except IndexError:
                if done.is_set() and not self._queue:
                    return
                self._ready.wait(0.05)
                self._ready.clear()
                continue
            if data is not None:
                self.bytes_out += len(data)
            yield chunk, data

    def cost_us(self) -> dict:
        costs = sorted(self.costs_ns)
        if not costs:
            return {}
        pick = lambda p: round(costs[min(len(costs) - 1, int(p * (len(costs) - 1) + 0.5))] / 1000, 1)  # noqa: E731
        return {"p50": pick(.5), "p99": pick(.99), "max": round(costs[-1] / 1000, 1)}


class ProcessWatch:
    """Whether one process is still running, through a handle held from attach
    (so a reused pid can never pass for Project64)."""

    def __init__(self, pid: int):
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        self._handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not self._handle:
            raise OSError(f"cannot watch process {pid} (error {ctypes.GetLastError()})")

    def alive(self) -> bool:
        return bool(self._handle) and \
            ctypes.windll.kernel32.WaitForSingleObject(ctypes.c_void_p(self._handle), 0) == WAIT_TIMEOUT

    def close(self) -> None:
        if self._handle:
            ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(self._handle))
            self._handle = None


def find_project64() -> tuple[int | None, str]:
    """(pid, note): the one running Project64, or None and why not."""
    from sm64_events.core.capturelayer_win import WinProcesses

    target = WinProcesses().setup_target()
    return target["pid"], target.get("message", "")


def recorder_source(pid: int):
    """The recorder's own primary source; constructing it starts nothing."""
    from sm64_events.replay.audio import ProcessAudioSource

    return ProcessAudioSource(pid, rate=RATE)


def _utc() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Status:
    """<out.cap>.json, rewritten whole at each change so a reader never sees half."""

    def __init__(self, path: Path):
        self.path = path
        self.fields: dict = {"tool_pid": os.getpid()}

    def update(self, **fields) -> None:
        self.fields.update(fields, updated_utc=_utc())
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.fields, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
        print(json.dumps({k: self.fields[k] for k in ("state", "reason") if k in self.fields}), flush=True)


def capture(out: Path, *, seconds: float, wait_seconds: float, budget_bytes: int,
            find_pid=find_project64, watch=ProcessWatch, make_source=recorder_source,
            acquire_lock=None, frequency: int | None = None, stamp=None,
            now=time.monotonic, sleep=time.sleep) -> dict:
    """Wait for Project64, record its audio to `out` until it exits or `seconds`
    pass, and return the final status. Raises CaptureRefused before writing any
    audio when the lock is held, no Project64 appears or isolation fails."""
    status = Status(out.with_name(out.name + ".json"))
    lock = (acquire_lock or recorder_lock.acquire_recorder_lock)()
    if lock is None:
        _refuse(status, "another recorder holds the machine-wide recorder lock (is the trainer open?); "
                        "close it and start again")
    try:
        status.update(state="waiting", reason=None, out=str(out))
        pid = _wait_for_project64(status, find_pid, wait_seconds, now, sleep)
        source = _isolated_source(status, pid, make_source)
        frequency = frequency or qpc_frequency()
        fanout = Fanout(stamp or qpc_stamp(frequency), budget_bytes)
        try:
            process = watch(pid)
        except OSError as exc:
            _refuse(status, f"Project64 could not be watched for its exit ({exc})", exc)
        try:
            try:
                source.start(fanout.on_pcm)
            except Exception as exc:  # noqa: BLE001 - every start failure is a refusal with its reason
                _refuse(status, f"Project64's own audio could not be isolated ({exc}); the device-wide "
                                "fallback was refused, so nothing was captured", exc)
            status.update(state="recording", rate=RATE, qpc_frequency=frequency, started_utc=_utc())
            return _record(out, status, source, fanout, process, frequency, seconds, now, sleep)
        finally:
            process.close()
    finally:
        lock.close()


def _refuse(status: Status, reason: str, cause: Exception | None = None):
    status.update(state="refused", reason=reason)
    raise CaptureRefused(reason) from cause


def _wait_for_project64(status: Status, find_pid, wait_seconds: float, now, sleep) -> int:
    deadline = now() + wait_seconds
    pid, note = find_pid()
    while pid is None:
        if now() >= deadline:
            _refuse(status, f"no single Project64 within {wait_seconds:.0f} s ({note})")
        sleep(POLL_S)
        pid, note = find_pid()
    status.update(pid=pid)
    return pid


def _isolated_source(status: Status, pid: int, make_source):
    """The source, only if it taps Project64's own process audio."""
    try:
        source = make_source(pid)
    except Exception as exc:  # noqa: BLE001 - a source that cannot be built is a refusal too
        _refuse(status, f"Project64's audio source could not be built ({exc})", exc)
    mode = getattr(source, "mode", None)
    status.update(mode=mode)
    if mode != "process":
        _refuse(status, f"the audio source is {mode!r}, not Project64's own process audio; device "
                        "loopback would record everything on that output, so nothing was captured")
    return source


def _record(out: Path, status: Status, source, fanout: Fanout, process, frequency: int, seconds: float,
            now, sleep) -> dict:
    """Write the capture until Project64 exits, `seconds` pass or a part fails; always stop the source."""
    done, failure = threading.Event(), []

    def write() -> None:
        try:
            asn.write_capture(out, RATE, frequency, fanout.records(done))
        except Exception as exc:  # noqa: BLE001 - reported in the status, never silent
            failure.append(f"writer failed: {exc}")

    writer = threading.Thread(target=write, name="audio-capture-writer", daemon=True)
    writer.start()
    started = shown = now()
    reason = None
    try:
        while (reason := _stop_reason(source, process, writer, failure, now() - started, seconds)) is None:
            sleep(POLL_S)
            if now() - shown >= STATUS_EVERY_S:
                shown = now()
                status.update(**_counts(fanout))
    finally:
        try:
            source.stop()
        except Exception as exc:  # noqa: BLE001 - the capture still closes; the status names it
            failure.append(f"the source did not stop cleanly: {exc}")
        fanout.close()
        done.set()
        writer.join()
    status.update(state="stopped", reason=reason, errors=failure, stopped_utc=_utc(),
                  capture_bytes=out.stat().st_size if out.exists() else 0, **_counts(fanout))
    return status.fields


def _stop_reason(source, process, writer, failure: list, elapsed: float, seconds: float) -> str | None:
    if not process.alive():
        return "Project64 exited"
    if elapsed >= seconds:
        return f"{seconds:.0f} s passed"
    if failure or not writer.is_alive():
        return failure[0] if failure else "writer stopped"
    try:
        source.check_health()
    except Exception as exc:  # noqa: BLE001 - a dead source ends the capture, named
        return f"source failed: {exc}"
    return None


def _counts(fanout: Fanout) -> dict:
    return {"kept_chunks": fanout.kept_chunks, "kept_seconds": round(fanout.bytes_in / 4 / RATE, 3),
            "lost_records": fanout.lost_records, "lost_samples": fanout.lost_samples,
            "peak_queued_bytes": fanout.peak_bytes, "chunk_cost_us": fanout.cost_us()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", help="the capture file (audio_session.py format); <out>.json beside it")
    parser.add_argument("--seconds", type=float, default=600.0, help="longest recording after attach")
    parser.add_argument("--wait-seconds", type=float, default=1800.0, help="how long to wait for Project64")
    parser.add_argument("--budget-mib", type=float, default=16.0, help="PCM the writer may fall behind by")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        capture(out, seconds=args.seconds, wait_seconds=args.wait_seconds,
                budget_bytes=int(args.budget_mib * (1 << 20)))
    except CaptureRefused as refused:
        print(f"refused: {refused}", file=sys.stderr or sys.stdout)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
