"""The scene viewer's sound through its real routes (M4 E4-audio): the open scene's table and its blocks.

The server (src/sm64_events/server/scene_lab_api.py) and the session (tools/scene_lab/scene_session.py)
run as shipped over fake_viewer_host.py; the package is scene_sound_fixture.py's synthetic recording
with and without its source-audio layer. No ROM, recorded audio, GPU, emulator or browser is involved.
"""
import io
import json

import av
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import scene_sound_fixture as fx
from scene_sound_fixture import pk

import fake_viewer_host  # noqa: E402  (tools/scene_lab, on the path the fixture set)
import gpu_gate  # noqa: E402
import scene_session  # noqa: E402
import stems_layer  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    return fx.recording(tmp_path_factory.mktemp("lab-sound"))


@pytest.fixture
def lab(tmp_path, recording, monkeypatch):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    packages = tmp_path / "packages"
    packages.mkdir()
    with_sound = pk.with_audio(recording["package"], recording["rom"], recording["payload"])
    (packages / "sound.sm64scene").write_bytes(with_sound)
    (packages / "silent.sm64scene").write_bytes(recording["package"])
    package = pk.Package(with_sound, recording["rom"])
    payload_at = package.index.layers["source-audio"][0][0][0]
    block_at, stored, _samples, _digest = package.audio.blocks[2]
    damaged = bytearray(with_sound)
    damaged[payload_at + block_at + stored // 2] ^= 0x5A
    (packages / "damaged.sm64scene").write_bytes(bytes(damaged))
    pcm = recording["pcm"].astype(np.int32)
    music = (pcm // 4).astype(np.int16)
    split = stems_layer.encode(package.audio, music, (pcm - music).astype(np.int16))
    (packages / "split.sm64scene").write_bytes(pk.with_stems(with_sound, recording["rom"], split))
    (tmp_path / "rom.z64").write_bytes(recording["rom"])
    (tmp_path / "Plugin").mkdir()
    config = tmp_path / "lab.json"
    config.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(packages),
                                  "pj64_plugin": str(tmp_path / "Plugin"), "map": str(tmp_path / "Map.map")}),
                      encoding="utf-8")
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, config, session_options={
        "host_factory": scene_session.command_host(fake_viewer_host.command())}, watch_seconds=0.05)
    return app


def _decode(file: bytes) -> np.ndarray:
    with av.open(io.BytesIO(file)) as box:
        return np.concatenate([frame.to_ndarray().reshape(-1, 2) for frame in box.decode(audio=0)])


def test_an_open_scene_describes_its_sound_and_serves_each_block_it_names(lab, recording):
    with TestClient(lab) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "sound.sm64scene"}).json()
        audio = opened["audio"]
        assert audio["available"] and audio["rate"] == fx.RATE and audio["codec"] == "flac"
        assert len(audio["samples"]) == len(opened["frames"]) == fx.FRAMES and audio["lost"] == [12]
        presents = recording["presents"]                  # Play holds each picture for its recorded time
        assert [frame["recorded_ms"] for frame in opened["frames"]] == pytest.approx(
            [(present.qpc - presents[0].qpc) / 10_000 for present in presents], abs=0.002)
        step = audio["block_samples"]
        for block in range(audio["blocks"]):
            reply = client.get(f"/api/scene-lab/audio/{block}", params={"session": opened["session"]})
            assert reply.status_code == 200 and reply.headers["content-type"] == "audio/flac"
            assert np.array_equal(_decode(reply.content), recording["pcm"][block * step:(block + 1) * step])
        assert client.get(f"/api/scene-lab/audio/{audio['blocks']}",
                          params={"session": opened["session"]}).status_code == 404
        stale = client.get("/api/scene-lab/audio/0", params={"session": opened["session"] + 1})
        assert stale.status_code == 409
        assert client.get("/api/scene-lab/audio/0").status_code == 422          # the session is required
        client.post("/api/scene-lab/close")
        closed = client.get("/api/scene-lab/audio/0", params={"session": opened["session"]})
        assert closed.status_code == 409 and closed.json()["detail"] == "Closed."


def test_a_split_scene_serves_its_music_and_effects_on_the_recordings_blocks(lab, recording):
    with TestClient(lab) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "split.sm64scene"}).json()
        stems = opened["audio"]["stems"]
        assert stems == {"available": True, "tracks": ["music", "effects"], "lost": []}
        step, session = opened["audio"]["block_samples"], opened["session"]
        pcm = recording["pcm"].astype(np.int32)
        music = (pcm // 4).astype(np.int16)
        for track, expected in (("music", music), ("effects", (pcm - music).astype(np.int16)),
                                ("mix", recording["pcm"])):
            reply = client.get("/api/scene-lab/audio/1", params={"session": session, "track": track})
            assert reply.status_code == 200 and reply.headers["content-type"] == "audio/flac"
            assert np.array_equal(_decode(reply.content), expected[step:2 * step]), track
        wrong = client.get("/api/scene-lab/audio/1", params={"session": session, "track": "voices"})
        assert wrong.status_code == 404 and "no such track" in wrong.json()["detail"]
        opened = client.post("/api/scene-lab/open", json={"package": "sound.sm64scene"}).json()
        assert opened["audio"]["stems"]["available"] is False
        assert client.get("/api/scene-lab/audio/1", params={"session": opened["session"],
                                                           "track": "music"}).status_code == 404


def test_a_recording_without_sound_says_so_and_serves_none(lab):
    with TestClient(lab) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "silent.sm64scene"}).json()
        assert opened["audio"] == {"available": False, "reason": "This recording has no sound."}
        missing = client.get("/api/scene-lab/audio/0", params={"session": opened["session"]})
        assert missing.status_code == 404 and missing.json()["detail"] == "This recording has no sound."


def test_a_damaged_block_is_refused_and_the_others_still_play(lab):
    with TestClient(lab) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "damaged.sm64scene"}).json()
        assert client.get("/api/scene-lab/audio/1", params={"session": opened["session"]}).status_code == 200
        damaged = client.get("/api/scene-lab/audio/2", params={"session": opened["session"]})
        assert damaged.status_code == 422 and "sound is damaged" in damaged.json()["detail"]
