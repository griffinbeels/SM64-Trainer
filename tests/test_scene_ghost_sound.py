"""Ghost sound in the Scene viewer (M4 round 5, item 4): each ghost added to a scene plays its own
recorded sound effects on the stage's sound clock (tools/scene_lab/ghost_sound.py).

Clips are ghost_native_fixture's synthetic records, the scene a stand-in with the session's fields.
The engine is a fake (one impulse per cue at its tick's first sample, scaled by the tick's gain),
except in the last test, where SM64's own audio engine (ghost_sound_host.cpp) runs on a locally
supplied ROM, as the audio witness does.
"""
import io
import os
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import ghost_sound  # noqa: E402
import stage_ghosts  # noqa: E402
from ghost_native_fixture import frame, producer  # noqa: E402
from sm64_events.ghost.clip import STAR_CLOCK, decode_clip, encode_clip  # noqa: E402

JUMP, LAND = 0x24228081, 0x30008081
RATE, BLOCK = 48000, 4800


def clip_bytes(count=40, sounds=None, *, level=7, area=1, other_area=()):
    """A ghost of `count` game frames; `sounds` {frame index: bits}; frames in `other_area` in area 2."""
    sounds = sounds or {}
    rows = [frame(i + 1, sounds=tuple(sounds.get(i, ())), level=level, area=2 if i in other_area else area)
            for i in range(count)]
    return encode_clip(rows, producer(), clock_kind=STAR_CLOCK)


class FakeEngine:
    """The engine host's shape: ticks of 1056 or 1088 frames (as SM64's), an impulse of 10000 x gain at
    each cued tick's first sample."""

    def __init__(self, _rom=None, fail=False):
        self.calls, self.fail = [], fail

    def render(self, ticks):
        self.calls.append(ticks)
        if self.fail:
            raise RuntimeError("the ghost sound engine stopped (exit 3)")
        lengths = np.array([1088 if k % 3 else 1056 for k in range(len(ticks))], np.int64)
        pcm = np.zeros((int(lengths.sum()), 2), np.int16)
        starts = np.concatenate([[0], np.cumsum(lengths)])
        for k, (_position, gain, cues) in enumerate(ticks):
            if cues:
                pcm[starts[k]:starts[k] + 8] = int(10000 * gain)
        return lengths, pcm

    def close(self):
        return 0


def scene(timers, *, first_sample=4800, shown=(), volume=0.5):
    """A stand-in for an open scene: one picture per timer, each shown 1/30 s after the last (a lag
    frame shows later), the recording's sample of each, no scene state (the ghost then sounds centred)."""
    samples, at = [], first_sample
    for k, timer in enumerate(timers):
        if k:
            at += (timer - timers[k - 1]) * RATE // 30
        samples.append(at)
    total = samples[-1] + RATE
    layer = SimpleNamespace(rate=RATE, total_samples=total, block_samples=BLOCK, blocks=[None] * -(-total // BLOCK))
    outlines = [{"identity": {"global_timer": timer, "curr_level": 7, "curr_area": 1}} for timer in timers]
    return SimpleNamespace(package=SimpleNamespace(states=None), rom=bytes(0x3E) + b"E" + bytes(0x41),
                           frames=list(range(len(timers))), outlines=outlines,
                           sound=SimpleNamespace(samples=samples, layer=layer), ghost_origin=0,
                           ghosts=SimpleNamespace(shown=list(shown)), ghost_look=stage_ghosts.Look(volume=volume))


def shown(ref, data):
    return stage_ghosts.Shown(ref, 0, "YOUR RUN", "You", 40, clip=data)


def onsets(track):
    loud = np.flatnonzero(np.abs(track[:, 0].astype(np.int32)) > 50)
    return [int(at) for i, at in enumerate(loud) if not i or at - loud[i - 1] > 100]


def test_a_ghost_plays_its_own_sounds_on_the_pictures_of_their_frames_across_a_lag_frame():
    """Round 5, item 4: "for sound effects, we should play the sound effects back for each of the ghosts
    independently". A sound lands on the picture of the frame that recorded it, a lag frame included,
    at the player's ghost volume."""
    data = clip_bytes(sounds={3: (JUMP,), 9: (LAND,), 20: (JUMP, LAND)})
    timers = [500, 501, 502, 503, 504, 505, 507, 508, 509, 510, 511, 512] + list(range(513, 530))
    stand_in = scene(timers, shown=[shown("attempt:7", data)], volume=0.5)
    engine = FakeEngine()
    sounds = ghost_sound.SceneGhostSounds(host_factory=lambda rom: engine)
    track = sounds.track(stand_in, stand_in.ghosts.shown[0])
    ticks = engine.calls[0]
    assert [k for k, tick in enumerate(ticks) if tick[2]] == [3, 9, 20] and ticks[20][2] == [JUMP, LAND]
    assert {round(gain, 6) for _, gain, _ in ticks[:28]} == {0.5}             # its volume, centred, no camera known
    picture = {timer - 500: stand_in.sound.samples[k] for k, timer in enumerate(timers)}
    # within 12 samples (0.25 ms): the resampler's kernel rings a few samples ahead of an impulse
    assert [abs(at - picture[g]) <= 12 for at, g in zip(onsets(track), (3, 9, 20), strict=True)] == [True] * 3
    assert 4500 < np.abs(track).max() < 6000                                 # 10000 x 0.5, the resampler's overshoot
    sounds.track(stand_in, stand_in.ghosts.shown[0])
    assert len(engine.calls) == 1                                            # made once per scene and ghost


def test_hidden_frames_are_silent_and_a_star_ghost_fades_with_its_dance():
    """As the in-game ghost: nothing before its start, nothing while it stands in another area,
    a star ghost's dance tail fades over STAR_FADE_FRAMES past its last record, any other is gone."""
    data = clip_bytes(count=10, sounds={0: (JUMP,), 4: (JUMP,)}, other_area={4})
    clip = decode_clip(data)
    centred = lambda g: (None, None, None, 7, 1)  # noqa: E731
    ticks = ghost_sound.plan_ticks(clip, range(-2, 30), centred, 1.0, dance_tail=True)
    gains, cues = [gain for _, gain, _ in ticks], [bool(c) for _, _, c in ticks]
    assert gains[:2] == [0.0, 0.0] and not any(cues[:2])                    # before its start
    assert cues[2] and not cues[6] and gains[6] == 0.0                       # frame 4 is in another area
    tail = gains[2 + 10:2 + 10 + ghost_sound.STAR_FADE_FRAMES]
    assert tail[0] == pytest.approx(1 - 1 / ghost_sound.STAR_FADE_FRAMES) and tail == sorted(tail, reverse=True)
    assert gains[2 + 9 + ghost_sound.STAR_FADE_FRAMES:] == [0.0] * (30 - 9 - ghost_sound.STAR_FADE_FRAMES)
    ended = ghost_sound.plan_ticks(clip, range(0, 14), centred, 1.0, dance_tail=False)
    assert [gain for _, gain, _ in ended[10:]] == [0.0] * 4


def test_where_a_ghost_sounds_from_follows_the_stage_camera_and_its_distance_from_mario():
    """cameraToObject's space (+x right, -z ahead) under SM64's look-at, and min(1, NEAR / d)."""
    camera, focus = (0.0, 0.0, 0.0), (100.0, 0.0, 0.0)                      # looking along +x
    ahead = ghost_sound.camera_relative(camera, focus, (500.0, 0.0, 0.0))
    right = ghost_sound.camera_relative(camera, focus, (0.0, 0.0, 300.0))
    assert ahead == pytest.approx((0.0, 0.0, -500.0), abs=1e-6)
    assert right == pytest.approx((300.0, 0.0, 0.0), abs=1e-6)
    assert ghost_sound.camera_relative(camera, (0.0, 50.0, 0.0), (1.0, 2.0, 3.0)) is None   # straight up
    assert ghost_sound.distance_gain((0, 0, 0), (300, 0, 0)) == 1.0
    assert ghost_sound.distance_gain((0, 0, 0), (800, 0, 0)) == pytest.approx(0.5)
    assert ghost_sound.distance_gain(None, (9000, 0, 0)) == 1.0


def test_blocks_tile_the_scenes_sound_and_a_ghost_without_sound_says_so():
    data = clip_bytes(sounds={2: (JUMP,)})
    stand_in = scene(list(range(100, 140)), shown=[shown("attempt:7", data)])
    sounds = ghost_sound.SceneGhostSounds(host_factory=FakeEngine)
    track = sounds.track(stand_in, stand_in.ghosts.shown[0])
    body, media = sounds.block(stand_in, "attempt:7", 1)
    with wave.open(io.BytesIO(body)) as file:
        assert (file.getframerate(), file.getnchannels(), file.getnframes()) == (RATE, 2, BLOCK)
        assert np.array_equal(np.frombuffer(file.readframes(BLOCK), "<i2").reshape(-1, 2), track[BLOCK:2 * BLOCK])
    assert media == "audio/wav"
    with pytest.raises(LookupError, match="not in the scene"):
        sounds.block(stand_in, "attempt:8", 0)
    with pytest.raises(LookupError, match="no such block"):
        sounds.block(stand_in, "attempt:7", len(stand_in.sound.layer.blocks))
    broken = ghost_sound.SceneGhostSounds(host_factory=lambda rom: FakeEngine(fail=True))
    with pytest.raises(LookupError, match=ghost_sound.NO_SOUND):
        broken.block(stand_in, "attempt:7", 0)


def test_the_route_serves_a_ghost_track_through_the_session(monkeypatch):
    """scene_session.audio_block hands a "ghost:<ref>" track to the scene's ghost sounds."""
    import scene_session
    calls = []

    class Sounds:
        def block(self, scene, ref, index):
            calls.append((ref, index))
            return b"RIFF", "audio/wav"
    session = SimpleNamespace(sound=object(), ghost_sounds=Sounds())
    assert scene_session.SceneSession.audio_block(session, 3, "ghost:attempt:7") == (b"RIFF", "audio/wav")
    assert calls == [("attempt:7", 3)]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 audio engine")
def test_the_games_own_engine_sounds_a_cue_from_its_tick_at_its_gain():
    """SM64's audio engine through ghost_sound_host.cpp: silence before the cued tick, sound from it,
    a quarter of the energy at gain 0.5, nothing at gain 0, and a ROM without SM64's audio refused."""
    rom_path = os.environ.get("SM64_MODEL_ROM")
    if not rom_path:
        pytest.skip("set SM64_MODEL_ROM (local US ROM) to run SM64's own audio engine")
    rom = Path(rom_path).read_bytes()
    host = ghost_sound.EngineHost(rom)
    try:
        def ticks(gain):
            return [((0.0, 0.0, -300.0), gain, [JUMP] if k == 10 else []) for k in range(60)]
        lengths, full = host.render(ticks(1.0))
        _, half = host.render(ticks(0.5))
        _, none = host.render(ticks(0.0))
    finally:
        host.close()
    start = int(lengths[:10].sum())
    assert not full[:start].any() and np.abs(full[start:start + int(lengths[10])]).max() > 100
    energy = lambda pcm: float(np.sum(pcm.astype(np.float64) ** 2))  # noqa: E731
    assert 0.2 < energy(half) / energy(full) < 0.3 and np.abs(none).max() <= 1      # rounding at most
    assert int(lengths.sum()) == pytest.approx(60 * ghost_sound.ENGINE_RATE / 30, abs=40)
    other = bytearray(rom)
    other[0x57B720 + 64] ^= 0xFF                                             # CTL no longer SM64's audio
    refused = ghost_sound.EngineHost(bytes(other))
    with pytest.raises(RuntimeError, match="stopped"):
        refused.render(ticks(1.0))
    refused.close()
