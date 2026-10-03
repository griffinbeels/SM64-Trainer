"""THE GHOST SOUND PLAYER: plays ghost sound from outside Project64's process.

His report, 2026-10-03: with Ghosts in recording off, "we still incorrectly
include the ghost's AUDIO in the recording. We should NOT be recording the
ghost's audio when this is off." The recorder hears the game through a
process tap of Project64 (replay/audio.py), which takes every stream that
process plays, so ghost sound played inside Project64 was always recorded.
The renderer still makes the sound; it writes it to a shared ring
(plugin/gfxwrap/ghost_audio.h, restated in ghost/native_layout.py), and this
small process -- a child of the trainer, never of Project64 -- plays it on
the output Project64's own session uses, so per-app routing (his Wave Link
"Game" output) still carries both. The recorder taps this process too, and
only while Ghosts in recording is on (`ProcessAudioSource.extra_pid`).

Pacing is the renderer's, unchanged: it refills a tick at a time when what
is written and not yet played falls to 34 ms. This player hands everything
written to its device at once and publishes the device's own position as
played, so "not yet played" is measured at the speaker, as it was with
waveOut inside the renderer.
"""
from __future__ import annotations

import ctypes as C
import ctypes.wintypes as W
import functools
import logging
import os
import subprocess
import sys
import time

from sm64_events.ghost import native_layout as L

SOUND_PLAYER_FLAG = "--ghost-sound"
MODULE = "sm64_events.ghost.sound_player"
CHUNK_FRAMES = 1024  # one device buffer, 32 ms
BUFFERS = 8  # more than the renderer ever keeps unplayed (two ticks)
IDLE_CLOSE_S = 3.0  # the renderer closes after 3 s of silence; the device follows once drained

FILE_MAP_ALL_ACCESS, SYNCHRONIZE, EVENT_MODIFY_STATE = 0xF001F, 0x00100000, 0x0002
WAIT_TIMEOUT, WAVE_MAPPER, WAVE_FORMAT_PCM, CALLBACK_NULL = 0x102, 0xFFFFFFFF, 1, 0
WHDR_DONE, WHDR_PREPARED, TIME_SAMPLES = 0x1, 0x2, 0x2


def ring_name(pj64_pid: int) -> str:
    return f"{L.SOUND_RING_PREFIX}{pj64_pid}"


class Ring:
    """The player's side of the shared ring: read samples, publish played."""

    def __init__(self, pj64_pid: int):
        k = _kernel()
        name = ring_name(pj64_pid)
        bytes_ = L.SOUND_RING_HEADER_BYTES + L.SOUND_RING_FRAMES * 4
        self._mapping = k.OpenFileMappingW(FILE_MAP_ALL_ACCESS, False, name)
        if not self._mapping:
            raise FileNotFoundError(f"no ghost sound ring for Project64 {pj64_pid}")
        self._view = k.MapViewOfFile(self._mapping, FILE_MAP_ALL_ACCESS, 0, 0, bytes_)
        self._written_event = k.OpenEventW(SYNCHRONIZE, False, name + "_written")
        self._played_event = k.OpenEventW(EVENT_MODIFY_STATE, False, name + "_played")
        if not self._view or not self._written_event or not self._played_event:
            self.close()
            raise OSError(f"ghost sound ring for Project64 {pj64_pid} could not be opened")
        magic = C.string_at(self._view, 8)
        version, rate, capacity = (C.c_uint32 * 3).from_address(self._view + 8)
        if (magic, version, rate, capacity) != (L.SOUND_RING_MAGIC, L.SOUND_RING_VERSION,
                                                L.SOUND_RING_RATE, L.SOUND_RING_FRAMES):
            self.close()
            raise OSError(f"ghost sound ring is not version {L.SOUND_RING_VERSION}")
        self.capacity = capacity

    def _field(self, at, kind):
        return kind.from_address(self._view + at)

    @property
    def written(self) -> int:
        return self._field(L.SOUND_RING_WRITTEN_AT, C.c_int64).value

    @property
    def renderer_open(self) -> bool:
        return bool(self._field(L.SOUND_RING_OPEN_AT, C.c_uint32).value)

    def publish(self, played: int) -> None:
        self._field(L.SOUND_RING_PLAYED_AT, C.c_int64).value = played
        _kernel().SetEvent(self._played_event)

    def beat(self, pid: int | None = None) -> None:
        self._field(L.SOUND_RING_PLAYER_BEAT_AT, C.c_uint32).value = _kernel().GetTickCount() & 0xFFFFFFFF
        if pid is not None:
            self._field(L.SOUND_RING_PLAYER_PID_AT, C.c_uint32).value = pid

    def read(self, frame: int, count: int) -> bytes:
        """`count` frames from `frame`, never across the ring's end."""
        at = L.SOUND_RING_HEADER_BYTES + (frame % self.capacity) * 4
        return C.string_at(self._view + at, count * 4)

    def wait_written(self, milliseconds: int) -> None:
        _kernel().WaitForSingleObject(self._written_event, milliseconds)

    def close(self) -> None:
        k = _kernel()
        if getattr(self, "_view", None):
            try:
                self.beat(0)
            except (OSError, ValueError):
                pass
            k.UnmapViewOfFile(C.c_void_p(self._view))
            self._view = None
        for handle in ("_written_event", "_played_event", "_mapping"):
            value = getattr(self, handle, None)
            if value:
                k.CloseHandle(value)
                setattr(self, handle, None)


class RingPlayer:
    """Hand every written frame to the device; publish what it has played.

    Pure pacing over a ring and a device, so a test drives it with fakes."""

    def __init__(self, ring, device, pid: int, clock=time.monotonic):
        self._ring, self._device, self._clock = ring, device, clock
        self.sent = ring.written  # attach at the newest frame: nothing stale plays
        self._played = self.sent
        self._idle_since = None
        ring.publish(self.sent)
        ring.beat(pid)

    def step(self) -> None:
        ring, device = self._ring, self._device
        ring.beat()
        written = ring.written
        if written - self.sent > ring.capacity:  # fell a whole ring behind: skip, never replay stale
            self.sent = written
        while self.sent < written:
            if not device.is_open and not device.open():
                self.sent = written  # no device now: drop, the next write retries
                break
            count = min(written - self.sent, CHUNK_FRAMES, ring.capacity - self.sent % ring.capacity)
            if not device.write(ring.read(self.sent, count)):
                break  # every buffer busy; the rest goes next step
            self.sent += count
        queued = device.queued() if device.is_open else 0
        self._played = max(self._played, self.sent - queued)
        ring.publish(self._played)
        if device.is_open and queued == 0 and not ring.renderer_open:
            now = self._clock()
            if self._idle_since is None:
                self._idle_since = now
            if now - self._idle_since >= IDLE_CLOSE_S:
                device.close()
                self._idle_since = None
        else:
            self._idle_since = None

    @property
    def busy(self) -> bool:
        return self._device.is_open or self._ring.renderer_open

    def close(self) -> None:
        if self._device.is_open:
            self._device.close()


class WAVEFORMATEX(C.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", W.WORD), ("nChannels", W.WORD), ("nSamplesPerSec", W.DWORD),
                ("nAvgBytesPerSec", W.DWORD), ("nBlockAlign", W.WORD), ("wBitsPerSample", W.WORD),
                ("cbSize", W.WORD)]


class WAVEHDR(C.Structure):
    _fields_ = [("lpData", C.c_void_p), ("dwBufferLength", W.DWORD), ("dwBytesRecorded", W.DWORD),
                ("dwUser", C.c_size_t), ("dwFlags", W.DWORD), ("dwLoops", W.DWORD),
                ("lpNext", C.c_void_p), ("reserved", C.c_size_t)]


class MMTIME(C.Structure):
    _fields_ = [("wType", W.UINT), ("u", C.c_uint32 * 2)]


class WAVEOUTCAPSW(C.Structure):
    _fields_ = [("wMid", W.WORD), ("wPid", W.WORD), ("vDriverVersion", W.UINT),
                ("szPname", W.WCHAR * 32), ("dwFormats", W.DWORD), ("wChannels", W.WORD),
                ("wReserved1", W.WORD), ("dwSupport", W.DWORD)]


class WaveOutDevice:
    """Shared-mode waveOut at the ring's rate, opened on `device_id()` each time."""

    def __init__(self, rate: int = L.SOUND_RING_RATE, device_id=lambda: WAVE_MAPPER):
        self._rate, self._device_id = rate, device_id
        self._handle = W.HANDLE()
        self._headers = [WAVEHDR() for _ in range(BUFFERS)]
        self._data = [C.create_string_buffer(CHUNK_FRAMES * 4) for _ in range(BUFFERS)]
        self.is_open = False
        self._submitted = self._last = self._base = 0

    def open(self) -> bool:
        fmt = WAVEFORMATEX(WAVE_FORMAT_PCM, 2, self._rate, self._rate * 4, 4, 16, 0)
        winmm = C.windll.winmm
        if winmm.waveOutOpen(C.byref(self._handle), W.UINT(self._device_id()), C.byref(fmt), 0, 0,
                             CALLBACK_NULL) != 0:
            if winmm.waveOutOpen(C.byref(self._handle), W.UINT(WAVE_MAPPER), C.byref(fmt), 0, 0,
                                 CALLBACK_NULL) != 0:
                return False
        for header in self._headers:
            C.memset(C.byref(header), 0, C.sizeof(header))
            header.dwFlags = WHDR_DONE
        self._submitted = self._last = self._base = 0
        self.is_open = True
        return True

    def write(self, pcm: bytes) -> bool:
        winmm = C.windll.winmm
        for header, data in zip(self._headers, self._data, strict=True):
            if not header.dwFlags & WHDR_DONE:
                continue
            if header.dwFlags & WHDR_PREPARED:
                winmm.waveOutUnprepareHeader(self._handle, C.byref(header), C.sizeof(header))
            C.memmove(data, pcm, len(pcm))
            C.memset(C.byref(header), 0, C.sizeof(header))
            header.lpData, header.dwBufferLength = C.cast(data, C.c_void_p), len(pcm)
            if (winmm.waveOutPrepareHeader(self._handle, C.byref(header), C.sizeof(header))
                    or winmm.waveOutWrite(self._handle, C.byref(header), C.sizeof(header))):
                header.dwFlags = WHDR_DONE
                return False
            self._submitted += len(pcm) // 4
            return True
        return False

    def queued(self) -> int:
        """Frames handed over and not yet played, from the device's position."""
        time_ = MMTIME(TIME_SAMPLES)
        if C.windll.winmm.waveOutGetPosition(self._handle, C.byref(time_), C.sizeof(time_)) \
                or time_.wType != TIME_SAMPLES:
            return sum(h.dwBufferLength // 4 for h in self._headers if not h.dwFlags & WHDR_DONE)
        sample = time_.u[0]
        if sample < self._last:
            self._base += 1 << 32  # the 32-bit sample counter wrapped
        self._last = sample
        return max(0, self._submitted - (self._base + sample))

    def close(self) -> None:
        winmm = C.windll.winmm
        winmm.waveOutReset(self._handle)
        for header in self._headers:
            if header.dwFlags & WHDR_PREPARED:
                winmm.waveOutUnprepareHeader(self._handle, C.byref(header), C.sizeof(header))
        winmm.waveOutClose(self._handle)
        self.is_open = False


def device_for(pj64_pid: int):
    """The waveOut device on the endpoint Project64's own audio session uses."""
    def find() -> int:
        try:
            from sm64_events.replay.audio import device_name_hosting_pid
            endpoint = device_name_hosting_pid(pj64_pid)
        except Exception:  # noqa: BLE001 - routing is a preference; the default device still plays
            endpoint = None
        if not endpoint:
            return WAVE_MAPPER
        winmm = C.windll.winmm
        for index in range(winmm.waveOutGetNumDevs()):
            caps = WAVEOUTCAPSW()
            if winmm.waveOutGetDevCapsW(index, C.byref(caps), C.sizeof(caps)) == 0:
                name = caps.szPname  # at most 31 characters of the endpoint's name
                if name and endpoint.startswith(name):
                    return index
        return WAVE_MAPPER
    return find


@functools.cache
def _kernel():
    k = C.WinDLL("kernel32", use_last_error=True)
    k.OpenFileMappingW.restype = W.HANDLE
    k.OpenFileMappingW.argtypes = (W.DWORD, W.BOOL, W.LPCWSTR)
    k.MapViewOfFile.restype = C.c_void_p
    k.MapViewOfFile.argtypes = (W.HANDLE, W.DWORD, W.DWORD, W.DWORD, C.c_size_t)
    k.UnmapViewOfFile.argtypes = (C.c_void_p,)
    k.OpenEventW.restype = W.HANDLE
    k.OpenEventW.argtypes = (W.DWORD, W.BOOL, W.LPCWSTR)
    k.OpenProcess.restype = W.HANDLE
    k.OpenProcess.argtypes = (W.DWORD, W.BOOL, W.DWORD)
    k.WaitForSingleObject.argtypes = (W.HANDLE, W.DWORD)
    k.SetEvent.argtypes = (W.HANDLE,)
    k.CloseHandle.argtypes = (W.HANDLE,)
    k.GetTickCount.restype = W.DWORD
    k.GetCurrentThread.restype = W.HANDLE
    k.SetThreadPriority.argtypes = (W.HANDLE, C.c_int)
    return k


def _alive(handle) -> bool:
    return bool(handle) and _kernel().WaitForSingleObject(handle, 0) == WAIT_TIMEOUT


def main(argv) -> int:
    """`--pj64 PID --parent PID`: play that Project64's ghost sound until it or
    the trainer is gone. Never a window, never focus."""
    args = dict(zip(argv[::2], argv[1::2], strict=True))
    pj64_pid, parent_pid = int(args["--pj64"]), int(args["--parent"])
    k = _kernel()
    pj64, parent = k.OpenProcess(SYNCHRONIZE, False, pj64_pid), k.OpenProcess(SYNCHRONIZE, False, parent_pid)
    k.SetThreadPriority(k.GetCurrentThread(), 2)  # HIGHEST, as the renderer's audio thread
    ring = None
    while ring is None:
        if not (_alive(pj64) and _alive(parent)):
            return 0
        try:
            ring = Ring(pj64_pid)
        except FileNotFoundError:
            time.sleep(0.25)  # the renderer makes the ring when its sound engine starts
    # Resolved once: a lookup on every reopen would delay the first sound after an idle close.
    chosen = device_for(pj64_pid)()
    player = RingPlayer(ring, WaveOutDevice(device_id=lambda: chosen), os.getpid())
    try:
        while _alive(pj64) and _alive(parent):
            # A write wakes it at once; the timeout only paces the played
            # position while sound plays, and keeps the beat well inside
            # SOUND_RING_PLAYER_TIMEOUT_MS while nothing does.
            ring.wait_written(10 if player.busy else 100)
            player.step()
    finally:
        player.close()
        ring.close()
    return 0


def child_command(pj64_pid: int):
    """(argv, env) for the player of that Project64, a child of this trainer."""
    from sm64_events.core.childproc import python_child
    # site-packages: finding Project64's output reads Core Audio through pycaw.
    return python_child(MODULE, SOUND_PLAYER_FLAG,
                        ("--pj64", str(pj64_pid), "--parent", str(os.getpid())), site_packages=True)


class SoundPlayerProcess:
    """The trainer's handle on one player child for one Project64.

    `start` is cheap to repeat: it respawns a player that died, at most once
    every `RESPAWN_S`, and says once why the last one ended."""

    RESPAWN_S = 5.0

    def __init__(self, spawn=subprocess.Popen, clock=time.monotonic):
        self._spawn, self._clock, self._child, self.pj64_pid = spawn, clock, None, None
        self._started_at = None

    @property
    def pid(self) -> int | None:
        child = self._child
        return child.pid if child is not None and child.poll() is None else None

    def start(self, pj64_pid: int) -> None:
        if self.pid is not None and self.pj64_pid == pj64_pid:
            return
        if (self._child is not None and self.pj64_pid == pj64_pid and self._started_at is not None
                and self._clock() - self._started_at < self.RESPAWN_S):
            return
        if self._child is not None and self._child.poll() is not None:
            logging.getLogger("sm64.ghost.sound").warning(
                "ghost sound player for Project64 %s ended (exit %s); starting another",
                self.pj64_pid, self._child.returncode)
        self.stop()
        self._started_at = self._clock()
        from sm64_events.core.childproc import quiet_spawn_kwargs
        command, env = child_command(pj64_pid)
        self._child = self._spawn(command, env=env, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  **quiet_spawn_kwargs())
        self.pj64_pid = pj64_pid

    def stop(self, timeout_s: float = 2.0) -> None:
        child, self._child, self.pj64_pid = self._child, None, None
        if child is None or child.poll() is not None:
            return
        child.terminate()
        try:
            child.wait(timeout_s)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout_s)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
