"""Ghost sound plays from a trainer-owned process, and reaches a recording only when asked.

His report, 2026-10-03: with Ghosts in recording off, "we still incorrectly
include the ghost's AUDIO in the recording." The renderer writes ghost sound
to a shared ring (plugin/gfxwrap/ghost_audio.h; its own witness is
tests/test_ghost_audio.py); ghost/sound_player.py plays it outside Project64,
and the recorder taps that player only while the switch is on.
"""
import ctypes as C
import ctypes.wintypes as W
import os
import subprocess
import sys
import time

import numpy as np
import pytest

from sm64_events.ghost import native_layout as L
from sm64_events.ghost import sound_player as S
from sm64_events.ghost.capture import NativeCapture
from sm64_events.memory.layout import US
from sm64_events.replay import audio as A


class FakeRing:
    capacity = L.SOUND_RING_FRAMES

    def __init__(self, written=0):
        self.written, self.played, self.renderer_open = written, None, True
        self.pid, self.beats = None, 0
        self.samples = {}

    def write(self, count):
        for frame in range(self.written, self.written + count):
            self.samples[frame % self.capacity] = frame
        self.written += count

    def read(self, frame, count):
        assert frame % self.capacity + count <= self.capacity, "never across the ring's end"
        return np.array([self.samples.get((frame + i) % self.capacity, -1) for i in range(count)],
                        dtype=np.int64).tobytes()

    def publish(self, played):
        self.played = played

    def beat(self, pid=None):
        self.beats += 1
        if pid is not None:
            self.pid = pid


class FakeDevice:
    """Holds what it is given; `play(n)` lets n frames leave."""

    def __init__(self, buffers=S.BUFFERS):
        self.is_open, self.opens, self.closes = False, 0, 0
        self.held, self.frames, self.buffers = [], [], buffers

    def open(self):
        self.is_open, self.opens = True, self.opens + 1
        return True

    def write(self, pcm):
        if len(self.held) >= self.buffers:
            return False
        frames = np.frombuffer(pcm, dtype=np.int64).tolist()
        self.held.append(len(frames))
        self.frames.extend(frames)
        return True

    def queued(self):
        return sum(self.held)

    def play(self, count):
        while count and self.held:
            take = min(count, self.held[0])
            self.held[0] -= take
            count -= take
            if not self.held[0]:
                self.held.pop(0)

    def close(self):
        self.is_open, self.closes, self.held = False, self.closes + 1, []


def test_the_player_attaches_at_the_newest_frame_and_hands_everything_over():
    ring = FakeRing(written=5000)  # stale frames from before it attached
    device = FakeDevice()
    player = S.RingPlayer(ring, device, pid=77)
    assert ring.pid == 77 and ring.played == 5000, "it attaches at the newest frame"
    ring.write(2176)  # two ticks, the most the renderer keeps unplayed
    player.step()
    assert device.frames == list(range(5000, 7176)), "every written frame, in order, nothing stale"
    assert ring.played == 5000, "nothing played yet: the renderer still sees two ticks queued"
    device.play(1000)
    player.step()
    assert ring.played == 6000, "played is the device's own position"


def test_the_player_wraps_the_ring_without_crossing_its_end():
    ring = FakeRing(written=L.SOUND_RING_FRAMES - 100)
    device = FakeDevice()
    player = S.RingPlayer(ring, device, pid=1)
    ring.write(1088)
    player.step()
    assert device.frames == list(range(L.SOUND_RING_FRAMES - 100, L.SOUND_RING_FRAMES + 988))


def test_a_player_a_whole_ring_behind_skips_rather_than_replaying_stale_sound():
    ring = FakeRing()
    device = FakeDevice(buffers=0)  # a device that takes nothing
    player = S.RingPlayer(ring, device, pid=1)
    ring.write(L.SOUND_RING_FRAMES + 50)
    player.step()
    assert player.sent == ring.written and not device.frames


def test_the_device_closes_once_the_renderer_closes_and_it_has_drained():
    now = [0.0]
    ring, device = FakeRing(), FakeDevice()
    player = S.RingPlayer(ring, device, pid=1, clock=lambda: now[0])
    ring.write(1000)
    player.step()
    ring.renderer_open = False
    player.step()
    assert device.is_open, "still holding sound"
    device.play(1000)
    for now[0] in (1.0, 2.0, 3.9):
        player.step()
    assert device.is_open, "drained for under 3 s"
    now[0] = 4.0
    player.step()
    assert not device.is_open and device.closes == 1
    ring.renderer_open = True
    ring.write(100)
    player.step()
    assert device.is_open and device.opens == 2, "a new write reopens it"


def test_the_mix_adds_the_ghost_to_the_game_and_never_lets_it_drift():
    mix = A.GhostMix(rate=48000, most_s=0.25)
    game = np.full(960, 0.25, dtype=np.float32)
    assert mix.add_to(game) is game, "nothing waiting: the game's own samples, untouched"
    mix.feed(np.full(600, 0.5, dtype=np.float32))
    mixed = mix.add_to(game)
    assert np.allclose(mixed[:600], 0.75) and np.allclose(mixed[600:], 0.25)
    mix.feed(np.ones(48000 * 2, dtype=np.float32))  # a second of ghost while the game tap stalled
    assert len(mix._waiting) == 24000, "at most a quarter second waits"


class FakeTap:
    taps = []

    def __init__(self, pid, on_data):
        self.pid, self.on_data, self.stopped = pid, on_data, False
        FakeTap.taps.append(self)

    def start(self):
        pass

    def stop(self):
        self.stopped = True


def test_the_recording_taps_the_player_only_while_asked(monkeypatch):
    FakeTap.taps = []
    monkeypatch.setitem(sys.modules, "proctap", type(sys)("proctap"))
    sys.modules["proctap"].ProcessAudioCapture = FakeTap
    monkeypatch.setattr(A.ProcessAudioSource, "EXTRA_EVERY_S", 0.01)
    wanted = [None]
    got = []
    source = A.ProcessAudioSource(pid=111, extra_pid=lambda: wanted[0])
    source.start(lambda pcm: got.append(pcm))
    try:
        game = FakeTap.taps[0]
        assert game.pid == 111
        time.sleep(0.1)
        assert len(FakeTap.taps) == 1, "off: the player is never tapped"
        wanted[0] = 222
        deadline = time.monotonic() + 2
        while len(FakeTap.taps) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        player = FakeTap.taps[1]
        assert player.pid == 222
        player.on_data(np.full(200, 0.5, dtype=np.float32).tobytes(), -1)
        game.on_data(np.full(200, 0.25, dtype=np.float32).tobytes(), -1)
        deadline = time.monotonic() + 2
        while not got and time.monotonic() < deadline:
            time.sleep(0.01)
        assert int(got[-1][0][0]) == int(0.75 * 32767), "on: the ghost is mixed into the game"
        wanted[0] = None
        deadline = time.monotonic() + 2
        while not player.stopped and time.monotonic() < deadline:
            time.sleep(0.01)
        assert player.stopped, "off again: the player's tap closes"
    finally:
        source.stop()


class FakeSound:
    pid = 4242

    def __init__(self):
        self.started, self.stopped = [], 0

    def start(self, pid):
        self.started.append(pid)

    def stop(self):
        self.stopped += 1


def test_the_capture_names_its_player_only_for_ghosts_in_the_recording():
    capture = NativeCapture(US, lambda *_: None, producer_pid=1, sound_player=FakeSound())
    assert capture.sound_pid() is None, "no ghosts playing"
    capture.set_playback(enabled=True, record_ghosts=False)
    assert capture.sound_pid() is None, "Ghosts in recording off: the recording never hears it"
    capture.set_playback(enabled=True, record_ghosts=True)
    assert capture.sound_pid() == 4242
    capture.set_playback(enabled=False)
    assert capture.sound_pid() is None


def test_one_player_per_project64_and_it_ends_when_asked():
    spawned = []

    class Child:
        def __init__(self, command, **_):
            self.command, self.pid, self.ended = command, 900 + len(spawned), False
            spawned.append(self)

        def poll(self):
            return 0 if self.ended else None

        def terminate(self):
            self.ended = True

        def wait(self, _timeout):
            return 0

    player = S.SoundPlayerProcess(spawn=Child)
    player.start(10)
    player.start(10)
    assert len(spawned) == 1 and player.pid == 900
    assert spawned[0].command[-4:] == ["--pj64", "10", "--parent", str(os.getpid())]
    player.start(11)
    assert spawned[0].ended and len(spawned) == 2, "a new Project64 gets a new player"
    player.stop()
    assert spawned[1].ended and player.pid is None


def test_a_player_that_died_comes_back_at_most_every_few_seconds(caplog):
    spawned, now = [], [0.0]

    class Child:
        def __init__(self, command, **_):
            self.pid, self.returncode = 900 + len(spawned), None
            spawned.append(self)

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = 1

        def wait(self, _timeout):
            return self.returncode

    player = S.SoundPlayerProcess(spawn=Child, clock=lambda: now[0])
    player.start(10)
    spawned[0].returncode = 3  # it crashed
    now[0] = 1.0
    player.start(10)
    assert len(spawned) == 1, "no respawn storm"
    now[0] = 5.5
    player.start(10)
    assert len(spawned) == 2 and player.pid == 901
    assert "ended (exit 3)" in caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shared memory and waveOut")
def test_the_real_player_process_attaches_and_reports_what_it_played(tmp_path):
    """The real child, a ring this test makes as the renderer would, and only
    silence: it attaches, beats, and publishes played as frames leave it."""
    k = C.WinDLL("kernel32", use_last_error=True)
    k.CreateFileMappingW.restype = W.HANDLE
    k.CreateFileMappingW.argtypes = (W.HANDLE, C.c_void_p, W.DWORD, W.DWORD, W.DWORD, W.LPCWSTR)
    k.MapViewOfFile.restype = C.c_void_p
    k.MapViewOfFile.argtypes = (W.HANDLE, W.DWORD, W.DWORD, W.DWORD, C.c_size_t)
    k.CreateEventW.restype = W.HANDLE
    k.CreateEventW.argtypes = (C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR)
    k.SetEvent.argtypes = (W.HANDLE,)
    k.CloseHandle.argtypes = (W.HANDLE,)
    k.UnmapViewOfFile.argtypes = (C.c_void_p,)
    name = S.ring_name(os.getpid())
    size = L.SOUND_RING_HEADER_BYTES + L.SOUND_RING_FRAMES * 4
    mapping = k.CreateFileMappingW(W.HANDLE(-1), None, 0x04, 0, size, name)
    view = k.MapViewOfFile(mapping, S.FILE_MAP_ALL_ACCESS, 0, 0, size)
    written_event = k.CreateEventW(None, False, False, name + "_written")
    played_event = k.CreateEventW(None, False, False, name + "_played")
    assert mapping and view and written_event and played_event
    C.memmove(view, L.SOUND_RING_MAGIC, 8)
    (C.c_uint32 * 3).from_address(view + 8)[:] = [L.SOUND_RING_VERSION, L.SOUND_RING_RATE, L.SOUND_RING_FRAMES]
    C.c_uint32.from_address(view + L.SOUND_RING_OPEN_AT).value = 1
    written = C.c_int64.from_address(view + L.SOUND_RING_WRITTEN_AT)
    played = C.c_int64.from_address(view + L.SOUND_RING_PLAYED_AT)
    player_pid = C.c_uint32.from_address(view + L.SOUND_RING_PLAYER_PID_AT)
    written.value = 4000  # the player starts at the newest frame
    command, env = S.child_command(os.getpid())
    from sm64_events.core.childproc import quiet_spawn_kwargs
    child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, **quiet_spawn_kwargs())
    try:
        deadline = time.monotonic() + 20
        while player_pid.value != child.pid and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.05)
        assert player_pid.value == child.pid, child.stderr.read() if child.poll() is not None else "no attach"
        assert played.value == 4000
        start = time.monotonic()
        while time.monotonic() - start < 1.0:  # a renderer writing silence a tick at a time
            if written.value - played.value <= 1088:
                written.value += 1088
                k.SetEvent(written_event)
            time.sleep(0.005)
        assert played.value > 4000 + 16000, f"about a second played: {played.value - 4000}"
        assert written.value - played.value <= 2 * 1088 + S.CHUNK_FRAMES
    finally:
        child.terminate()
        child.wait(5)
        child.stderr.close()
        k.UnmapViewOfFile(view)
        for handle in (written_event, played_event, mapping):
            k.CloseHandle(handle)
