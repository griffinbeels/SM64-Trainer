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


from ghost_sound_double import FakeEngine  # noqa: E402  (the engine's shape; the track maker child loads it too)


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


COIN = 0x38118081                       # SOUND_GENERAL_COIN: SOUND_ARG_LOAD(3, 8, 0x11, 0x80, 8)


def split_scene_with_a_coin(coin_pcm=None, *, seconds=8.0, coin_frame=120, early_vis=1):
    """A split scene (stems_scene_fixture: music in every VI, the recording heard 137 ms late on a
    drifting clock) whose only sound effect is one coin, made by the VI `early_vis` before the picture
    of frame `coin_frame` (frame k's picture is 2 ms after VI 2k): {"package", "presents", "frame"}."""
    import stems_layer
    import stems_scene_fixture as sfx
    lengths, music, _effects = sfx.streams(seconds)
    music = (music.astype(np.float32) * 0.4).astype(np.int16)
    effects = np.zeros_like(music)
    first = sum(lengths[:2 * coin_frame - early_vis])                    # that VI's task's first sample
    coin = coin_pcm if coin_pcm is not None else np.full((1600, 2), 9000, np.int16)
    effects[first:first + len(coin)] = coin[:len(effects) - first]
    pcm, first_qpc, _positions = sfx.heard(music, effects)
    presents = sfx.presents(seconds)
    layer = sfx.layer(pcm, first_qpc, presents)
    stems = stems_layer.read(stems_layer.build(layer, presents[0][1], sfx.rows(lengths, music, effects)), layer)
    assert stems.available, stems.reason
    return {"package": SimpleNamespace(audio=layer, stems=stems), "presents": presents, "frame": coin_frame}


def heard_at(track, threshold):
    loud = np.flatnonzero(np.abs(track.astype(np.int32)).max(axis=1) > threshold)
    return int(loud[0]) if len(loud) else None


def coin_against_ghost(split, engine_factory, shift_ms=None, like_for_like=False):
    """(the scene's sound, the stage's coin, its own run's ghost's coin) in ms of the scene's playback:
    the page plays recording sample S[k] at picture k and the recording's own pace between, so one
    sample axis. The same coin on both sides (like_for_like) is placed by cross-correlation, so its
    attack counts the same on both."""
    import scene_audio
    package, presents = split["package"], split["presents"]
    sound = scene_audio.SceneSound.of(package, [sequence for sequence, _ in presents], shift_ms)
    timers = [1000 + k for k in range(len(presents))]
    stand_in = scene(timers, volume=1.0)
    stand_in.sound = sound
    data = clip_bytes(count=len(presents), sounds={split["frame"]: (COIN,)})
    stand_in.ghosts.shown = [shown("attempt:same-run", data)]
    ghost = ghost_sound.SceneGhostSounds(host_factory=engine_factory).track(stand_in, stand_in.ghosts.shown[0])
    stems = package.stems
    effects = np.concatenate([stems.block("effects", i) for i in range(len(stems.blocks["effects"]))])
    rate = package.audio.rate
    stage, ghosted = heard_at(effects, 2000), heard_at(ghost, 200)
    if like_for_like:
        import stems_check
        window = slice(ghosted - rate // 4, ghosted + rate // 2)
        reference = ghost[window].astype(np.float32).mean(axis=1)
        search = effects[window.start - rate // 4:window.stop + rate // 4].astype(np.float32).mean(axis=1)
        lag, _ncc = stems_check.xcorr_lag(reference, search)
        stage = ghosted + lag - rate // 4
    return sound, stage * 1000 / rate, ghosted * 1000 / rate


def test_on_a_split_scene_a_coin_and_its_own_runs_ghost_coin_land_within_a_frame():
    """The lead's decision (round 5, item 4): with each ghost's sound on its pictures, the stage's own
    sound goes on its pictures too where its stems fit measured Project64's delay. The coin is made a
    VI before its picture (the late side of how SM64 hands sound and picture to Project64); as heard,
    the stage's coin came 137 ms after its ghost's, an echo."""
    split = split_scene_with_a_coin()
    sound, stage, ghost = coin_against_ghost(split, FakeEngine)
    assert sound.measured and sound.shift_ms == pytest.approx(137, abs=4)                 # the fixture's delay
    timing = sound.describe()["timing"]
    assert timing["on_pictures"] and f"{sound.shift_ms:.0f} ms" in timing["why"]
    assert abs(stage - ghost) < 1000 / 30                                   # within one game frame
    import stems_scene_fixture as sfx
    picture_ms = split["frame"] * 1000 / 30 + 2                              # its picture, after the coin's VI
    coin_vi_ms = (2 * split["frame"] - 1) * 1000 / sfx.VI_HZ
    assert stage - ghost == pytest.approx(coin_vi_ms - picture_ms, abs=5)    # the stage's coin, a VI early
    heard, stage, ghost = coin_against_ghost(split, FakeEngine, shift_ms=0.0)
    assert not heard.measured and stage - ghost == pytest.approx(sfx.DELAY_S * 1000 - (picture_ms - coin_vi_ms), abs=5)


def test_a_scene_without_a_measured_delay_plays_as_heard_and_says_so():
    import scene_audio
    split = split_scene_with_a_coin()
    refused = SimpleNamespace(audio=split["package"].audio,
                              stems=SimpleNamespace(available=False, reason="G1", stats={"g1_checked": 3}))
    for package in (refused, SimpleNamespace(audio=split["package"].audio, stems=None)):
        sound = scene_audio.SceneSound.of(package, [sequence for sequence, _ in split["presents"]])
        timing = sound.describe()["timing"]
        assert (sound.shift_ms, timing["on_pictures"]) == (0.0, False)
        assert timing["why"] == scene_audio.AS_HEARD


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 audio engine")
def test_with_the_games_own_coin_the_stage_and_its_ghost_land_within_a_frame():
    """As above with the real coin: SM64's engine makes the stage's coin (in the game's output) and
    the ghost's, so the two onsets compare like for like."""
    rom_path = os.environ.get("SM64_MODEL_ROM")
    if not rom_path:
        pytest.skip("set SM64_MODEL_ROM (local US ROM) to run SM64's own audio engine")
    rom = Path(rom_path).read_bytes()
    host = ghost_sound.EngineHost(rom)
    try:
        # cued on a running engine (its first ticks after opening answer a tick later), from that tick on
        lengths, coin = host.render([((0.0, 0.0, -300.0), 1.0, [COIN] if k == 10 else []) for k in range(40)])
    finally:
        host.close()
    split = split_scene_with_a_coin(coin[int(lengths[:10].sum()):].copy())
    hosts = []

    def engine(_rom):
        hosts.append(ghost_sound.EngineHost(rom))
        return hosts[-1]
    try:
        sound, stage, ghost = coin_against_ghost(split, engine, like_for_like=True)
    finally:
        for made in hosts:
            made.close()
    print(f"stage coin {stage:.1f} ms, ghost coin {ghost:.1f} ms, apart {stage - ghost:.1f} ms, shift {sound.shift_ms} ms")
    assert sound.measured and abs(stage - ghost) < 1000 / 30


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


# ---------------------------------------------------------------- the track maker child

def state_scene(tmp_path):
    """A stand-in scene over test_scene_state_package.py's synthetic scene-state package, its ROM
    marked US so the stage's camera is read, and a symbol map placing gLakituState where that
    package captured a valid camera and focus: (scene, map path)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ghost_sound_state_synthetic", ROOT / "tests/test_scene_state_package.py")
    synthetic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(synthetic)
    rom = bytearray(synthetic._rom())
    rom[0x3E] = ord("E")
    rom = bytes(rom)
    out = tmp_path / "state.sm64scene"
    synthetic.pk.write_package(synthetic.pk.load_source(synthetic._session(tmp_path, rom), states=True), rom, out,
                               synthetic.pk.ZLIB, group=3, checkpoint_every=synthetic.EVERY)
    package = synthetic.pk.Package(out.read_bytes(), rom)

    def valid(data):
        values = np.frombuffer(data, "<f4")
        return bool(np.isfinite(values).all() and (np.abs(values) < 1e5).all() and (np.abs(values) > 1e-3).any())
    base = next(0x80330400 + at for at in range(0, 0x1000, 4)
                if valid(package.scene_bytes(0, [(0x330400 + at + ghost_sound.LAKITU_POS, 12)])[0])
                and valid(package.scene_bytes(0, [(0x330400 + at + ghost_sound.LAKITU_FOCUS, 12)])[0]))
    symbols = tmp_path / "map.txt"
    symbols.write_text(f"                0x00000000{base:08x}                gLakituState\n", encoding="ascii")
    data = clip_bytes(count=len(package), sounds={2: (JUMP,), 6: (LAND,)})
    stand_in = scene([500 + index for index in range(len(package))], shown=[shown("attempt:7", data)], volume=1.0)
    stand_in.package, stand_in.rom = package, rom
    return stand_in, symbols


def child_sounds(symbols, engine="PositionEngine"):
    return ghost_sound.SceneGhostSounds(map_path=symbols, maker_factory=lambda: ghost_sound.TrackMaker(
        engine=f"ghost_sound_double:{engine}", extra_path=(ROOT / "tests",)))


def test_a_track_made_in_the_below_normal_child_is_the_one_made_here_and_ends_with_the_scene(tmp_path):
    """Making a track shares nothing with the server's pictures (round 5, item 2: Play started while
    tracks were made hitched): the child makes the same samples, the stage's camera read from the
    scene's own package included (PositionEngine carries where each tick sounded from)."""
    import psutil
    from ghost_sound_double import PositionEngine
    stand_in, symbols = state_scene(tmp_path)
    here = ghost_sound.SceneGhostSounds(host_factory=PositionEngine, map_path=symbols)
    there = child_sounds(symbols)
    ghost = stand_in.ghosts.shown[0]
    try:
        made_here, made_there = here.track(stand_in, ghost), there.track(stand_in, ghost)
        assert np.array_equal(made_here, made_there)
        assert len(np.unique(made_here[:, 1])) > 2                    # the camera was read: z moves
        assert {key: there.stats[ghost.ref][key] for key in ("ticks", "cues")} == {
            key: here.stats[ghost.ref][key] for key in ("ticks", "cues")}
        child = there.maker.process
        if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
            assert psutil.Process(child.pid).nice() == psutil.BELOW_NORMAL_PRIORITY_CLASS
        assert there.track(stand_in, ghost) is made_there              # made once per scene and ghost
    finally:
        here.close()
        there.close()
    assert child.poll() is not None


def test_a_ghost_the_child_cannot_sound_plays_silent_and_the_child_goes_on(tmp_path):
    stand_in, symbols = state_scene(tmp_path)
    there = child_sounds(symbols, "FailingEngine")
    try:
        with pytest.raises(LookupError, match=ghost_sound.NO_SOUND):
            there.block(stand_in, "attempt:7", 0)
        assert there.maker.process.poll() is None
    finally:
        there.close()


def test_the_scenes_ghost_sounds_make_their_tracks_in_the_child(monkeypatch):
    """As scene_session.py makes them (no engine given): every track goes to a TrackMaker."""
    asked = []

    class Maker:
        def make(self, inputs, package):
            asked.append((inputs.clip, package))
            return np.zeros((inputs.total, 2), np.int16), {"ticks": 0}

        def close(self):
            pass
    monkeypatch.setattr(ghost_sound, "TrackMaker", Maker)
    data = clip_bytes(sounds={2: (JUMP,)})
    stand_in = scene(list(range(100, 140)), shown=[shown("attempt:7", data)])
    sounds = ghost_sound.SceneGhostSounds()
    sounds.track(stand_in, stand_in.ghosts.shown[0])
    assert asked == [(data, None)]                                    # no scene state: no package to read
