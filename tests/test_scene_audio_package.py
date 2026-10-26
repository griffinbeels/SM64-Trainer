"""The scene package's source-audio layer and the viewer's sound table (M4 E4-audio).

tools/scene_lab/package.py carries an audio_layer.py payload as its source-audio layer (with_audio,
audio_session.py attach); tools/scene_lab/scene_audio.py maps it onto the viewer's frames and
serves its blocks. The recording is scene_sound_fixture.py's: synthetic tasks, presents and a
device clock, so every expected value is the simulation's truth, not the module's own fit.
"""
import io
import json
import sys

import av
import numpy as np
import pytest

import scene_sound_fixture as fx
from scene_sound_fixture import ac, pk

import audio_session  # noqa: E402  (tools/scene_lab, on the path the fixture set)
import scene_audio  # noqa: E402


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    return fx.recording(tmp_path_factory.mktemp("sound"))


def _sound(data: bytes, rom: bytes) -> tuple:
    package = pk.Package(data, rom)
    presents = [package.outline(index)["present"] for index in range(len(package))]
    return package, scene_audio.SceneSound.of(package, presents)


def _decode(file: bytes) -> np.ndarray:
    with av.open(io.BytesIO(file)) as box:
        return np.concatenate([frame.to_ndarray().reshape(-1, 2) for frame in box.decode(audio=0)])


def test_attached_sound_maps_every_frame_to_its_true_sample_and_keeps_the_body(recording):
    data, rom = recording["package"], recording["rom"]
    with_sound = pk.with_audio(data, rom, recording["payload"])
    before, after = pk.read_header(data), pk.read_header(with_sound)
    assert before.version == 0 and after.version == 1                     # a layer makes it version 1
    assert with_sound[pk.HEADER.size:before.index_offset] == data[pk.HEADER.size:before.index_offset]
    package, sound = _sound(with_sound, rom)
    plain = pk.Package(data, rom)
    assert [package.frame(index) for index in range(len(package))] == [plain.frame(i) for i in range(len(plain))]
    assert package.audio.pictures == recording["clock"].pictures
    table = sound.describe()
    assert table["available"] and table["rate"] == fx.RATE and table["shift_ms"] == 0
    errors = np.array(table["samples"]) - np.array(recording["truth"])
    assert np.abs(errors).max() <= fx.RATE // 1000                         # every frame within 1 ms of its truth
    loss_first = min(fx.LOST) * fx.CHUNK
    loss_end = (max(fx.LOST) + 1) * fx.CHUNK
    samples = table["samples"]
    expected = [k for k in range(len(samples) - 1) if samples[k] < loss_end and samples[k + 1] > loss_first]
    assert table["lost"] == expected and expected                          # marked, never hidden
    assert not recording["pcm"][loss_first:loss_end].any()                 # and stored as silence


def test_a_block_is_served_as_the_file_the_browser_decodes_once_it_matches_its_hash(recording):
    _package, sound = _sound(pk.with_audio(recording["package"], recording["rom"], recording["payload"]),
                             recording["rom"])
    step = sound.layer.block_samples
    for index in (0, len(sound.layer.blocks) - 1):
        file, media_type = sound.block(index)
        assert media_type == "audio/flac" and file[:4] == b"fLaC"
        assert np.array_equal(_decode(file), recording["pcm"][index * step:(index + 1) * step])
    for bad in (-1, len(sound.layer.blocks), True, "0"):
        with pytest.raises(LookupError):
            sound.block(bad)
    _clock, stored, payload, _truth = fx.sound(recording["presents"], codec="stored")
    _package, plain = _sound(pk.with_audio(recording["package"], recording["rom"], payload), recording["rom"])
    file, media_type = plain.block(1)
    assert media_type == "audio/wav" and np.array_equal(_decode(file), stored[step:2 * step])


def test_a_damaged_block_is_refused_when_it_decodes_and_the_package_still_opens(recording):
    data = pk.with_audio(recording["package"], recording["rom"], recording["payload"])
    package = pk.Package(data, recording["rom"])
    offset = package.index.layers["source-audio"][0][0][0]
    block_offset, stored, _samples, _digest = package.audio.blocks[1]
    flipped = bytearray(data)
    flipped[offset + block_offset + stored // 2] ^= 0x5A
    _package, sound = _sound(bytes(flipped), recording["rom"])
    sound.block(0)
    with pytest.raises(ValueError):
        sound.block(1)


def test_attaching_again_replaces_the_sound(recording):
    data, rom = recording["package"], recording["rom"]
    once = pk.with_audio(data, rom, recording["payload"])
    _clock, _stored, other, _truth = fx.sound(recording["presents"], seed=11)
    twice = pk.with_audio(once, rom, other)
    package = pk.Package(twice, rom)
    assert [layer["name"] for layer in package.index.meta["layers"]] == ["source-audio"]
    assert len(twice) == len(once) - len(recording["payload"]) + len(other)   # the old payload is gone
    assert package.audio.payload == other


def test_sound_survives_a_resolution_layer_written_after_it(tmp_path):
    import test_scene_state_package as states

    rom = states._rom()
    session = states._session(tmp_path, rom)
    out = tmp_path / "state.sm64scene"
    pk.write_package(pk.load_source(session, states=True), rom, out, pk.ZLIB, group=3,
                     checkpoint_every=states.EVERY)
    presents = [ac.Present(present.sequence, fx.START + i * fx.FRAME_TICKS)
                for i, present in enumerate(_tap_presents(session))]
    _clock, _stored, payload, _truth = fx.sound(presents, lost=frozenset())
    with_sound = pk.with_audio(out.read_bytes(), rom, payload)
    resolved = pk.with_resolution(with_sound, rom, [None] * len(pk.Package(with_sound, rom)))
    package = pk.Package(resolved, rom)
    assert [layer["name"] for layer in package.index.meta["layers"]] == ["scene-state", "source-audio",
                                                                       "scene-resolution"]
    assert package.audio.payload == payload


def _tap_presents(session) -> list:
    return [record for record in pk.tap.read_records(session / "tap.bin") if isinstance(record, pk.tap.Present)]


def test_sound_from_another_recording_is_refused(recording, tmp_path):
    shifted = [ac.Present(present.sequence + 1000, present.qpc) for present in recording["presents"]]
    _clock, _stored, foreign, _truth = fx.sound(shifted)
    with pytest.raises(pk.PackageError, match="belongs to another recording: 40 of the 40 frames"):
        pk.with_audio(recording["package"], recording["rom"], foreign)
    with pytest.raises(pk.PackageError, match="cannot be attached: not an audio layer"):
        pk.with_audio(recording["package"], recording["rom"], b"X" * 200)


def _with_rows(data: bytes, rows: list, meta=None, body_change=None) -> bytes:
    """The package re-assembled with these source-audio rows (and body), every hash recomputed."""
    header = pk.read_header(data)
    index = pk.parse_index(pk.decompress(pk.ZLIB, data[header.index_offset:], header.index_raw), header.version)
    edited = json.loads(json.dumps(index.meta))
    if meta:
        meta(edited)
    body = data[pk.HEADER.size:header.index_offset]
    body = body_change(body) if body_change else body
    raw = pk.encode_index(edited, index.loads, index.chunks, index.blocks, index.frames, (pk.AUDIO, rows))
    return pk.assemble(header.rom_sha, header.rdram_size, len(index.frames), body, raw, header.version)


def test_hostile_sound_rows_and_payloads_are_refused_with_a_reason(recording):
    rom = recording["rom"]
    data = pk.with_audio(recording["package"], rom, recording["payload"])
    package = pk.Package(data, rom)
    (offset, length), first_chunk = package.index.layers["source-audio"][0][0], package.index.chunks[0]
    cases = {
        "overlaps a chunk": _with_rows(data, [(first_chunk[0], length)]),
        "outside the package body": _with_rows(data, [(offset, length + 8)]),
        "too many source-audio payloads": _with_rows(data, [(offset, length), (offset, length)]),
        "source-audio layer is damaged: not an audio layer": _with_rows(
            data, [(offset, length)], body_change=lambda body: body[:offset - pk.HEADER.size] + b"X"
            + body[offset - pk.HEADER.size + 1:]),
        "unknown package layer 'source-audio' version 2": _with_rows(
            data, [(offset, length)], meta=lambda meta: meta["layers"][0].update(version=2)),
    }
    for message, bad in cases.items():
        with pytest.raises(pk.PackageError, match=message):
            pk.Package(bad, rom)


def test_the_attach_command_writes_the_package_in_place_and_refuses_another_recording(recording, tmp_path,
                                                                                         monkeypatch, capsys):
    target, layer, rom = tmp_path / "scene.sm64scene", tmp_path / "sound.audio", tmp_path / "rom.z64"
    target.write_bytes(recording["package"])
    layer.write_bytes(recording["payload"])
    rom.write_bytes(recording["rom"])
    monkeypatch.setattr(sys, "argv", ["audio_session.py", "attach", str(target), str(layer), str(target),
                                      "--rom", str(rom)])
    assert audio_session.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["frames"] == {"sound": fx.FRAMES - 1, "lost": 1, "none": 0}
    assert pk.Package(target.read_bytes(), recording["rom"]).audio.payload == recording["payload"]
    shifted = [ac.Present(present.sequence + 1000, present.qpc) for present in recording["presents"]]
    layer.write_bytes(fx.sound(shifted)[2])
    assert audio_session.main() == 2
    assert "refused: this source audio belongs to another recording" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.partial"))


def test_the_one_timing_offset_moves_every_frame_by_its_samples(recording):
    package = pk.Package(pk.with_audio(recording["package"], recording["rom"], recording["payload"]),
                         recording["rom"])
    presents = [package.outline(index)["present"] for index in range(len(package))]
    heard = scene_audio.SceneSound.of(package, presents)
    with_event = scene_audio.SceneSound.of(package, presents, shift_ms=50.0)
    moved = np.array(with_event.samples) - np.array(heard.samples)
    assert np.abs(moved - 50 * fx.RATE // 1000).max() <= 1
