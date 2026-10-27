"""An attempt scene's music and sound effects on its own sound's samples (tools/scene_lab/stems_layer.py).

Round 4, item 11, his words: "we need to be able to toggle music / game sound effects separately".
The stems come from the game's timeline (the stem renderer's rows: per VI the game's output, its
music and its effects); the scene plays the recording Project64's output made. These tests hold
that the layer puts each stem where the recording heard it (stems_scene_fixture.py: a known delay,
drift, gain and a dropped VI, so every expected value is the simulation's truth), refuses a
recording of something else with its numbers, marks what has no split, and that the package,
the viewer's sound table and its block route carry the two tracks beside the recording.
Synthetic sound only: no ROM, emulator or recorded audio.
"""
from concurrent.futures import Future

import numpy as np
import pytest

import scene_sound_fixture as sfx
import stems_scene_fixture as fx
from scene_sound_fixture import pk

import scene_audio  # noqa: E402  (tools/scene_lab, on the path the fixtures set)
import stems_layer  # noqa: E402
import stems_live  # noqa: E402


def tracks(stems: stems_layer.Stems) -> dict:
    return {name: np.concatenate([stems.block(name, i) for i in range(len(stems.blocks[name]))]).astype(np.float64)
            for name in stems_layer.TRACKS}


def snr(signal: np.ndarray, error: np.ndarray) -> float:
    return float(10 * np.log10((signal ** 2).sum() / max(1e-9, (error ** 2).sum())))


def truth(scene: dict, name: str) -> np.ndarray:
    """What the recording heard of one stem: the simulation's own map, gain and interpolation."""
    positions, out = scene["positions"], np.zeros(scene["pcm"].shape)
    inside = (positions >= 0) & (positions <= len(scene[name]) - 1)
    out[inside] = stems_layer.sc.interp(scene[name].astype(np.float32), positions[inside]) * fx.GAIN
    return out


def valid(stems: stems_layer.Stems, total: int) -> np.ndarray:
    keep = np.ones(total, bool)
    for first, count in stems.gaps:
        keep[first:first + count] = False
    return keep


@pytest.fixture(scope="module")
def steady():
    return fx.scene(12.0)


def test_each_stem_lands_on_the_recording_sample_that_heard_it(steady):
    stems = stems_layer.read(stems_layer.build(steady["layer"], steady["first_qpc"], steady["rows"]), steady["layer"])
    assert stems.available, stems.reason
    stats = stems.stats
    assert stats["pieces"] == 1 and abs(stats["gain"] - fx.GAIN) < 0.005
    assert abs(stats["delay_ms"] - fx.DELAY_S * 1000) < 10                  # the clocks' guess, before the fine fit
    assert abs(stats["drift_ppm"] + fx.PPM) < 2
    split, keep = tracks(stems), valid(stems, steady["layer"].total_samples)
    for name in stems_layer.TRACKS:
        expected = truth(steady, name)
        assert snr(expected[keep], split[name][keep] - expected[keep]) > 55, name
    mix = steady["pcm"].astype(np.float64)
    assert snr(mix[keep], (split["music"] + split["effects"] - mix)[keep]) > 50
    # The only gaps are the recording's edges, where it heard no game sound at all.
    heard = (steady["positions"] >= 0) & (steady["positions"] <= len(steady["music"]) - 1)
    assert not (keep & ~heard).any() and (~keep & heard).sum() < 0.01 * heard.sum()
    assert not split["music"][~keep].any() and not split["effects"][~keep].any()


def test_sound_project64_dropped_is_a_second_piece_found_where_it_happened():
    scene = fx.scene(12.0, drop_at_s=6.0)
    stems = stems_layer.read(stems_layer.build(scene["layer"], scene["first_qpc"], scene["rows"]), scene["layer"])
    assert stems.available and stems.stats["pieces"] == 2
    split, keep = tracks(stems), valid(stems, scene["layer"].total_samples)
    times = np.arange(scene["layer"].total_samples) / fx.RATE
    near = keep & (np.abs(times - (0.5 + 6.0 + fx.DELAY_S)) < 0.5)          # the second around the drop
    expected = truth(scene, "music")
    assert snr(expected[near], split["music"][near] - expected[near]) > 40


def test_a_recording_of_other_sound_is_refused_with_its_numbers(steady):
    lengths, music, effects = fx.streams(12.0, seed=11)
    stems = stems_layer.read(stems_layer.build(steady["layer"], steady["first_qpc"], fx.rows(lengths, music, effects)),
                             steady["layer"])
    assert not stems.available
    assert stems.reason == stems_layer.MISMATCH and stems.stats["windows"] > 5
    assert stems.stats.get("correlated") == 0 or stems.stats["snr_db_median"] < stems_layer.PASS_DB


def test_the_renderer_seeing_the_microcode_differ_from_project64_refuses_the_split(steady):
    lengths, music, effects = fx.streams(12.0)
    stems = stems_layer.read(stems_layer.build(steady["layer"], steady["first_qpc"],
                                               fx.rows(lengths, music, effects, differs={300})), steady["layer"])
    assert not stems.available and stems.reason == stems_layer.DIFFERS and stems.stats["g1_differs"] == 1


def test_lost_and_unsplit_vis_play_silent_and_are_marked(steady):
    lengths, music, effects = fx.streams(12.0)
    rows = fx.rows(lengths, music, effects, lost=set(range(300, 306)), failed={420})
    stems = stems_layer.read(stems_layer.build(steady["layer"], steady["first_qpc"], rows), steady["layer"])
    assert stems.available and stems.stats["lost_vis"] == 6 and stems.stats["split_failed"] == 1
    starts = np.cumsum([0, *lengths])
    keep = valid(stems, steady["layer"].total_samples)
    for first_vi, last_vi in ((300, 305), (420, 420)):
        inside = (steady["positions"] >= starts[first_vi] + 2) & (steady["positions"] < starts[last_vi + 1] - 2)
        assert inside.any() and not keep[inside].any(), (first_vi, last_vi)
    assert keep.mean() > 0.9


def test_no_rows_or_a_damaged_payload_never_passes_as_stems(steady):
    empty = stems_layer.read(stems_layer.build(steady["layer"], steady["first_qpc"], []), None)
    assert not empty.available and empty.reason == stems_layer.NO_ROWS
    good = bytearray(stems_layer.build(steady["layer"], steady["first_qpc"], steady["rows"]))
    good[-5] ^= 0xFF
    with pytest.raises(stems_layer.StemsError, match="hash"):
        stems_layer.read(bytes(good), steady["layer"])
    other = fx.scene(6.0)["layer"]
    with pytest.raises(stems_layer.StemsError, match="tile"):
        stems_layer.read(stems_layer.build(steady["layer"], steady["first_qpc"], steady["rows"]), other)


# ---------------------------------------------------------------- the package and the viewer's table

@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    return sfx.recording(tmp_path_factory.mktemp("stems-sound"))


def _split(recording: dict, with_sound: bytes) -> tuple[bytes, np.ndarray, np.ndarray]:
    audio = pk.Package(with_sound, recording["rom"]).audio
    pcm = recording["pcm"].astype(np.int32)
    music = (pcm // 3).astype(np.int16)
    effects = (pcm - music).astype(np.int16)
    payload = stems_layer.encode(audio, music, effects, [(1000, 480)], {"windows": 3})
    return pk.with_stems(with_sound, recording["rom"], payload), music, effects


def test_the_package_carries_music_and_effects_beside_its_sound_and_serves_each_block(recording):
    rom = recording["rom"]
    with_sound = pk.with_audio(recording["package"], rom, recording["payload"])
    data, music, effects = _split(recording, with_sound)
    package = pk.Package(data, rom)
    assert package.stems.available and package.stems.stats == {"windows": 3}
    assert package.audio.payload == pk.Package(with_sound, rom).audio.payload         # the sound is untouched
    presents = [package.outline(index)["present"] for index in range(len(package))]
    sound = scene_audio.SceneSound.of(package, presents)
    table = sound.describe()["stems"]
    assert table["available"] and table["tracks"] == ["music", "effects"]
    samples = sound.describe()["samples"]
    assert table["lost"] == [k for k in range(len(samples) - 1) if samples[k] < 1480 and samples[k + 1] > 1000]
    data_m, media = sound.block(0, "music")
    assert media == "audio/flac"
    import av
    import io
    with av.open(io.BytesIO(data_m)) as box:
        decoded = np.concatenate([frame.to_ndarray().reshape(-1, 2) for frame in box.decode(audio=0)])
    assert np.array_equal(decoded, music[:len(decoded)])
    assert sound.block(0)[0] != data_m                                              # the mix is the recording
    with pytest.raises(LookupError):
        sound.block(0, "voices")
    replaced = pk.with_stems(data, rom, stems_layer.unavailable("Not this time."))
    assert pk.Package(replaced, rom).stems.reason == "Not this time."
    assert len(replaced) < len(data)                                                # the old payload went


def test_a_scene_without_the_split_says_why_and_stems_need_its_sound_first(recording):
    rom = recording["rom"]
    with_sound = pk.with_audio(recording["package"], rom, recording["payload"])
    package = pk.Package(with_sound, rom)
    presents = [package.outline(index)["present"] for index in range(len(package))]
    plain = scene_audio.SceneSound.of(package, presents)
    assert plain.describe()["stems"] == {"available": False, "reason": scene_audio.NO_STEMS}
    with pytest.raises(LookupError):
        plain.block(0, "music")
    said = pk.with_stems(with_sound, rom, stems_layer.unavailable("The renderer was not running."))
    package = pk.Package(said, rom)
    assert scene_audio.SceneSound.of(package, presents).describe()["stems"] == {
        "available": False, "reason": "The renderer was not running."}
    with pytest.raises(pk.PackageError, match="source audio first"):
        pk.with_stems(recording["package"], rom, stems_layer.unavailable("No sound."))


def test_the_cutters_step_attaches_the_childs_layer_or_the_reason_there_is_none(recording, tmp_path):
    rom = recording["rom"]
    with_sound = pk.with_audio(recording["package"], rom, recording["payload"])
    audio = pk.Package(with_sound, rom).audio

    class Child:
        def __init__(self, answer):
            self.answer, self.asked = answer, []

        def layer(self, payload, first_qpc, session, folder):
            self.asked.append((payload == recording["payload"], first_qpc, session))
            future = Future()
            future.set_result(self.answer)
            return future
    none = stems_live.attach(with_sound, rom, recording["payload"], None, "No symbol map.", 5, "live-1", tmp_path)
    assert pk.Package(none, rom).stems.reason == "No symbol map."
    out = tmp_path / "stems.bin"
    out.write_bytes(stems_layer.encode(audio, np.zeros((audio.total_samples, 2), np.int16),
                                       np.zeros((audio.total_samples, 2), np.int16)))
    child = Child({"path": str(out)})
    split = stems_live.attach(with_sound, rom, recording["payload"], child, None, 5, "live-1", tmp_path)
    assert pk.Package(split, rom).stems.available and child.asked == [(True, 5, "live-1")]
    assert not out.exists()                                                          # the answer's file is the cutter's to drop
    refused = stems_live.attach(with_sound, rom, recording["payload"], Child({"reason": "Too quiet."}), None, 5,
                                "live-1", tmp_path)
    assert pk.Package(refused, rom).stems.reason == "Too quiet."
