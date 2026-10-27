"""Record in the Scene viewer, made into a video (M4 round 6, item 3).

His words: "we should be able to RECORD THE SCENE VIEWER and export it as a video ... When done
recording, a link should appear to the file explorer location for the footage."

The sound mix rules run on synthetic tracks (tools/scene_lab/export_sound.py, the page's rules:
sceneaudio.js and sceneaudioworklet.js; their shared derivations are pinned against the real JS in
test_cross_language_parity.py). The output plan and the timeline checks are pure. The export job
runs through the real routes (server/scene_exports.py) and a real child process
(tools/scene_lab/scene_export.py) over fake_viewer_host.py, whose picture is the sha256 of
everything a request carried, and the "frames" encoder, whose file lists each written picture's
sha256: so "every output frame is its entry drawn again" is an equality, not a likeness. The
renderer and FFmpeg themselves are the GPU check's (the round's report).
"""
import json
import sys
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import export_sound as es  # noqa: E402
import fake_viewer_host  # noqa: E402
import scene_export  # noqa: E402
import scene_session  # noqa: E402
import scene_sound_fixture as fx  # noqa: E402
import stems_layer  # noqa: E402
from scene_sound_fixture import pk  # noqa: E402
from sm64_events.server import scene_exports, scene_lab_api  # noqa: E402
from test_scene_lab_stage_ghosts import CLIP, Loader, config, recording  # noqa: E402,F401

RATE = 48000
VERSION = 1                     # the scene recording's format the page writes today
FRAME_MS = 1000 / 30


# ---------------------------------------------------------------- the sound, as the page mixes it

def _table(frames: int = 60):
    """A scene whose frame k shows at k/30 s and sounds sample k * 1600: one unbroken run."""
    times = [k * FRAME_MS for k in range(frames)]
    samples = [k * 1600 for k in range(frames)]
    return es.sound_map(samples, times, RATE, 30), frames * 1600


def _entry(t_ms, play_ms, **sound):
    ghosts = sound.pop("ghosts", [])
    return {"t_ms": t_ms, "scene_frame": 0, "camera": None, "camera_used": None, "hud": True, "ghosts": ghosts,
            "play_ms": play_ms, "sound": {"music": True, "effects": True, "volume": 1.0, "muted": False,
                                          "ghost_mutes": [], **sound}}


def _constant(value, total):
    return np.full((total, 2), value, np.int16)


def test_nothing_sounds_while_paused_stepping_or_scrubbing():
    runs, total = _table()
    ramp = (np.arange(total) % 20000 - 10000).astype(np.int16)
    stage = np.stack([ramp, ramp], axis=1)
    pcm = es.mix([_entry(0, None), _entry(500, None)], 1000, RATE, runs, False, lambda track: stage, {})
    assert pcm.shape == (RATE, 2) and not pcm.any()


def test_a_play_sounds_each_pictures_own_sample_at_the_wall_clocks_pace():
    """Between entries the recorded time runs on at the wall clock's pace, so the sound follows the
    play sample by sample: output sample n of a play started at recorded time 0 is stream sample n."""
    runs, total = _table()
    ramp = (np.arange(total) % 20000 - 10000).astype(np.int16)
    stage = np.stack([ramp, -ramp], axis=1)
    entries = [_entry(t, t) for t in np.arange(0, 1000, 1000 / 144)]       # a 144 Hz display
    pcm = es.mix(entries, 1000, RATE, runs, False, lambda track: stage, {})
    middle = slice(es.FADE, RATE - es.FADE)
    assert np.array_equal(pcm[middle], stage[middle])
    assert abs(int(pcm[0, 0])) < abs(int(stage[0, 0])) or stage[0, 0] == 0    # a play starts with a fade


def test_the_switches_pick_the_track_as_the_page_does():
    runs, total = _table()
    tracks = {"mix": _constant(3000, total), "music": _constant(1000, total), "effects": _constant(2000, total)}
    sections = [{}, {"music": False}, {"effects": False}, {"music": False, "effects": False}]
    entries = [_entry(index * 400, index * 400, **switches) for index, switches in enumerate(sections)]
    split = es.mix(entries, 1600, RATE, runs, True, tracks.get, {})
    unsplit = es.mix(entries, 1600, RATE, runs, False, tracks.get, {})
    at = [round((index * 400 + 200) * RATE / 1000) for index in range(4)]       # each section's middle
    assert [int(split[n, 0]) for n in at] == [3000, 2000, 1000, 0]
    assert [int(unsplit[n, 0]) for n in at] == [3000] * 4     # without a split the recording always plays
    switch = round(400 * RATE / 1000)
    assert int(split[switch, 0]) < 1000                         # a switch fades out and in, never clicks


def test_each_ghost_plays_under_the_stage_unless_muted():
    runs, total = _table()
    tracks = {"mix": _constant(1000, total)}
    ghosts = {"a": _constant(300, total), "b": _constant(50, total)}
    entries = [_entry(0, 0, ghosts=["a", "b"]),
               _entry(300, 300, ghosts=["a", "b"], ghost_mutes=["a"]),
               _entry(600, 600, ghosts=["b"], music=False, effects=False),
               _entry(900, None, ghosts=["a", "b"])]
    pcm = es.mix(entries, 1200, RATE, runs, True, tracks.get, ghosts)
    sample = lambda ms: int(pcm[round(ms * RATE / 1000), 0])  # noqa: E731
    assert sample(150) == 1350                       # the stage and both ghosts
    assert sample(450) == 1050                       # "a" muted (its level moved over FADE samples)
    assert sample(750) == 50                         # the stage off, "a" gone from the scene: "b" alone
    assert sample(1050) == 0                         # paused: nothing, ghosts included


def test_volume_and_mute_scale_everything():
    runs, total = _table()
    tracks = {"mix": _constant(1000, total)}
    entries = [_entry(0, 0, volume=0.5), _entry(300, 300, volume=0.5, muted=True), _entry(600, 600, volume=0.25)]
    pcm = es.mix(entries, 900, RATE, runs, False, tracks.get, {"g": _constant(1000, total)})
    sample = lambda ms: int(pcm[round(ms * RATE / 1000), 0])  # noqa: E731
    assert (sample(200), sample(500), sample(800)) == (500, 0, 250)


def test_a_restarted_play_fades_across_its_jump():
    runs, total = _table()
    tracks = {"mix": _constant(8000, total)}
    entries = [_entry(0, 0), _entry(500, 0)]          # Restart: the recorded time goes back to 0
    pcm = es.mix(entries, 1000, RATE, runs, False, tracks.get, {})
    jump = round(500 * RATE / 1000)
    assert int(pcm[jump - 1, 0]) < 8000 // es.FADE * 2 and int(pcm[jump, 0]) < 8000 // es.FADE * 2
    assert int(pcm[jump + es.FADE + 5, 0]) == 8000 and int(pcm[jump - es.FADE - 5, 0]) == 8000


def test_the_sound_stops_at_a_gap_in_the_recording():
    """Pictures with no sample (-1) end a run; between runs nothing sounds, as in the worklet."""
    times = [k * FRAME_MS for k in range(30)]
    samples = [k * 1600 if not 10 <= k < 20 else -1 for k in range(30)]
    runs = es.sound_map(samples, times, RATE, 30)
    assert len(runs) == 2
    pcm = es.mix([_entry(0, 0)], 1000, RATE, runs, False, lambda track: _constant(4000, 30 * 1600), {})
    assert int(pcm[round(500 * RATE / 1000), 0]) == 0 and int(pcm[round(150 * RATE / 1000), 0]) == 4000


# ---------------------------------------------------------------- the output plan

def test_each_output_frame_shows_the_entry_on_screen_at_its_time():
    entries = [{"t_ms": t} for t in (0.0, 7.0, 16.0, 40.0, 41.0, 100.0)]
    assert scene_export.plan(entries, 60, 120.0) == [0, 2, 2, 4, 4, 4, 5]    # k/60 s: 0, 16.7, 33.3, 50 ...
    assert scene_export.plan(entries, 30, 120.0) == [0, 2, 4, 5]
    assert scene_export.duration_ms(entries) == pytest.approx(100 + 1000 / 60)
    assert scene_export.duration_ms(entries, 250.0) == 250.0


def test_the_picture_is_drawn_from_the_camera_the_server_drew_when_the_page_knew_it():
    pose = {"focus": [1, 2, 3], "position": [4, 5, 6], "roll": 0, "fov": 45.0}
    rig = {"rig": {"kind": "orbit", "target": "stage"}}
    assert scene_export.picture_camera({"camera": rig, "camera_used": pose}) == pose
    assert scene_export.picture_camera({"camera": rig, "camera_used": None}) == rig
    assert scene_export.picture_camera({"camera": None, "camera_used": None}) is None
    same = {"scene_frame": 3, "camera": rig, "camera_used": pose, "hud": False, "ghosts": ["a"]}
    assert scene_export.picture_key(same) == scene_export.picture_key({**same, "camera": {"ghost": "a"}})
    assert scene_export.picture_key(same) != scene_export.picture_key({**same, "hud": True})


class _PathWaitingSession:
    """A session whose Mario's path is read when the test says (the path reader's child stands in), and
    which notes when each picture went out and from which camera."""

    def __init__(self, fails: bool = False):
        import threading
        from concurrent.futures import Future
        self.path, self.fails, self.asked, self.sent = Future(), fails, 0, []
        self.sound, self.silence = None, "No sound here."
        self.lock = threading.Lock()

    def prepare_path(self):
        self.asked += 1
        return self.path

    def answer(self):
        if self.fails:
            self.path.set_exception(LookupError("Mario's path could not be read."))
        else:
            self.path.set_result(None)

    def begin(self, frame, camera, encoding, hud):
        self.sent.append((self.path.done(), camera))
        return (frame, json.dumps(camera, sort_keys=True))

    def finish(self, pending):
        pixels = json.dumps(pending).encode("utf-8").ljust(4 * 3 * 3, b" ")[:36]
        return {"width": 4, "height": 3, "sha256": json.dumps(pending)}, pixels

    def set_ghosts(self, entries, loader):
        return {"shown": [], "refused": []}

    def close(self):
        return 0


@pytest.mark.parametrize("fails", [False, True])
def test_a_rig_without_its_pose_waits_for_marios_path_before_any_picture_goes_out(tmp_path, monkeypatch, fails):
    """m6-rigs: an export's own session reads Mario's path in a child as it opens; a rig entry the page
    saw before its path was read carries no pose (camera_used null), so the drawing waits for the path
    and no rig picture goes out first. A path that cannot be read is one note, and the video is made."""
    import threading
    session = _PathWaitingSession(fails)
    monkeypatch.setattr(scene_export, "_session", lambda job, folder: session)
    rig = {"rig": {"kind": "front", "target": "stage"}}
    pose = {"focus": [1.0, 2.0, 3.0], "position": [4.0, 5.0, 6.0], "roll": 0, "fov": 45.0}
    timeline = [{**_good(scene_frame=k, t_ms=k * FRAME_MS), "camera": camera, "camera_used": used}
                for k, (camera, used) in enumerate([(None, None), (rig, None), (rig, pose), (rig, None)])]
    timer = threading.Timer(0.3, session.answer)
    timer.start()
    try:
        stats = scene_export.run({"version": VERSION, "timeline": timeline, "fps": 30, "encoder": {"kind": "frames"},
                                  "output": str(tmp_path / "clip.json")}, tmp_path, lambda event: None,
                                 threading.Event())
    finally:
        timer.cancel()
    assert session.asked == 1 and session.sent
    assert all(ready for ready, camera in session.sent if camera == rig), session.sent
    assert (scene_export.NO_RIG_PATH in stats["notes"]) is fails
    # A recording whose rigs all came with their pose never waits for the path.
    waited = _PathWaitingSession()
    monkeypatch.setattr(scene_export, "_session", lambda job, folder: waited)
    scene_export.run({"version": VERSION, "timeline": [{**entry, "camera_used": pose} for entry in timeline[1:]],
                      "fps": 30, "encoder": {"kind": "frames"}, "output": str(tmp_path / "again.json")}, tmp_path,
                     lambda event: None, threading.Event())
    assert waited.asked == 0 and waited.sent


# ---------------------------------------------------------------- the server's checks

def _good(**changes):
    return {"t_ms": 0, "scene_frame": 1, "camera": None, "camera_used": None, "hud": True, "ghosts": [],
            "play_ms": None, "sound": {"music": True, "effects": True, "volume": 0.5, "muted": False,
                                       "ghost_mutes": []}, **changes}


def test_a_timeline_is_checked_and_reduced_to_what_an_export_reads():
    checked = scene_exports.checked_timeline([_good(extra="ignored"), _good(t_ms=16.7, camera={"ghost": "a"})], 8)
    assert "extra" not in checked[0] and checked[1]["camera"] == {"ghost": "a"}
    pose = {"focus": [0, 1, 2], "position": [3, 4, 5], "fov": 50}
    assert scene_exports.checked_timeline([_good(camera=pose, camera_used=pose)], 8)[0]["camera_used"] == pose
    for wrong in ([], [_good(scene_frame=8)], [_good(t_ms=5), _good(t_ms=4)], [_good(camera={"focus": [0, 1]})],
                  [_good(sound={"volume": 2})], [_good(play_ms="soon")], [_good(ghosts="a")], ["entry"]):
        with pytest.raises(ValueError):
            scene_exports.checked_timeline(wrong, 8)


def test_the_file_is_named_for_the_course_and_star_and_when():
    when = scene_exports.datetime(2026, 10, 27, 14, 5, 33)
    heading = {"title": "x", "target": {"course_name": "Whomp's Fortress", "star_name": 'Shoot into the "Wild" Blue'}}
    assert scene_exports.file_stem(heading, "take.sm64scene", when) == \
        "Whomp's Fortress - Shoot into the Wild Blue 2026-10-27 14.05.33"
    assert scene_exports.file_stem({"title": "Lab recording", "target": None}, "take-9731-46.sm64scene", when) == \
        "take-9731-46 2026-10-27 14.05.33"
    assert scene_exports.file_stem({"title": "x", "target": {"segment_name": "BitDW: pipe"}}, None, when) == \
        "BitDW pipe 2026-10-27 14.05.33"


# ---------------------------------------------------------------- the job, through the routes and the child

def _app(tmp_path, packages: dict, rom: Path, loader=None, **export):
    folder = tmp_path / "packages"
    folder.mkdir()
    for name, data in packages.items():
        (folder / name).write_bytes(data)
    (tmp_path / "Plugin").mkdir(exist_ok=True)
    path = tmp_path / "lab.json"
    path.write_text(json.dumps({"rom": str(rom), "packages": str(folder), "pj64_plugin": str(tmp_path / "Plugin"),
                                "map": str(tmp_path / "MappingUS.map")}), encoding="utf-8")
    app = FastAPI()
    fake_host = export.pop("fake_host", {})
    scene_lab_api.mount_scene_lab(app, path, watch_seconds=0.05, ghost_loader=loader, session_options={
        "host_factory": scene_session.command_host(fake_viewer_host.command(**fake_host))},
        export_options={"clips_dir": tmp_path / "clips", "keep_folders": export.pop("keep_folders", False),
                        "jobs_dir": tmp_path / "jobs",
                        "options": {"host": "fake", "fake_host": fake_host,
                                    "encoder": {"kind": "frames", **export}}})
    return app


def _finished(client, job: str, seconds: float = 60) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status = client.get(f"/api/scene-lab/export/{job}").json()
        if status["state"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"the export did not end: {status}")


def _body(opened: dict, timeline: list, **more) -> dict:
    """An export request as the page sends it (scenerecord.js): the recording's format version first."""
    return {"version": VERSION, "session": opened["session"], "timeline": timeline, "fps": 60, **more}


def _timeline():
    """Every kind of moment: play, a pause held on one picture, a free camera moved during the pause,
    the HUD off, a ghost added, the HUD on again while paused, at a 60 Hz display."""
    pose = lambda x: {"focus": [0, 0, 0], "position": [x, 300, 900]}  # noqa: E731
    moments = ([(1, None, True, ["a"])] * 2 + [(2, None, True, ["a"])] * 2 + [(3, None, True, ["a"])] * 6
               + [(3, pose(100 + 10 * k), True, ["a"]) for k in range(4)] + [(4, None, True, ["a"])] * 2
               + [(5, None, False, ["a"])] * 2 + [(6, None, False, ["a", "b"])] * 2
               + [(6, None, True, ["a", "b"])] * 2)        # the HUD back on, paused: only the HUD differs
    return [{"t_ms": k * 1000 / 60, "scene_frame": frame, "camera": camera, "camera_used": None, "hud": hud,
             "ghosts": ghosts, "play_ms": None, "sound": {"music": True, "effects": True, "volume": 1.0,
                                                          "muted": False, "ghost_mutes": []}}
            for k, (frame, camera, hud, ghosts) in enumerate(moments)]


def _direct(config, recording, entries, loader) -> list[str]:
    """Each entry drawn by a session of the test's own over the same fake host and evaluator."""
    session = scene_session.SceneSession.open(
        config, recording["package"], host_factory=scene_session.command_host(fake_viewer_host.command()),
        evaluator_factory=lambda package, rom: fake_viewer_host.FakeEvaluator(), encoder_factory=lambda: None)
    try:
        drawn, shown = [], None
        for entry in entries:
            if entry["ghosts"] != shown:
                session.set_ghosts([{"ref": ref, "label": None} for ref in entry["ghosts"]], loader)
                shown = entry["ghosts"]
            drawn.append(session.picture(entry["scene_frame"], scene_export.picture_camera(entry), "raw",
                                         entry["hud"])[0]["sha256"])
        return drawn
    finally:
        session.close()


def test_an_export_draws_every_entry_again_and_lands_beside_the_saved_replays(config, recording, tmp_path):
    loader = Loader({"a": (CLIP, "YOUR PB"), "b": (CLIP, "Alice")})
    entries = _timeline()
    with TestClient(_app(tmp_path, {"scene.sm64scene": recording["package"].read_bytes()}, recording["rom"],
                         loader)) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        body = _body(opened, entries, duration_ms=len(entries) * 1000 / 60)
        job = client.post("/api/scene-lab/export", json=body).json()["job"]
        status = _finished(client, job)
    assert status["state"] == "done", status
    path = Path(status["path"])
    assert path.parent == tmp_path / "clips" and path.name.startswith("scene ") and path.suffix == ".mp4"
    assert status["message"] == f"Saved {path.name}. This recording has no sound, so the video is silent."
    assert not list((tmp_path / "clips").glob("*.part"))
    made = json.loads(path.read_text(encoding="utf-8"))
    assert made["fps"] == 60 and len(made["pictures"]) == len(entries)       # one output frame per 60 Hz entry
    expected = _direct(config, recording, entries, Loader({"a": (CLIP, "YOUR PB"), "b": (CLIP, "Alice")}))
    assert made["pictures"] == expected                     # every output frame is its entry, drawn again
    # Each held frame is one picture drawn; the HUD switched while paused draws again (the synthetic
    # recording has no HUD to leave out, so those two pictures are alike).
    assert status["stats"]["drawn"] == 11 and len(set(expected)) == 10
    assert loader.asked == ["a", "b"]                        # the catalog's clips, loaded once for the child


def test_the_sound_reaches_the_file_on_its_pictures(tmp_path):
    """A play over the fixture's sound with its music turned off midway: the file's sound is the
    effects stem from then on, and the click recorded at frame 10's present plays while the video
    shows frame 10 (the fixture's clicks: scene_sound_fixture.py)."""
    (tmp_path / "rec").mkdir()
    sound = fx.recording(tmp_path / "rec")
    with_sound = pk.with_audio(sound["package"], sound["rom"], sound["payload"])
    package = pk.Package(with_sound, sound["rom"])
    pcm = sound["pcm"].astype(np.int32)
    music = (pcm // 4).astype(np.int16)
    effects = (pcm - music).astype(np.int16)
    split = pk.with_stems(with_sound, sound["rom"], stems_layer.encode(package.audio, music, effects))
    rom = tmp_path / "rom.z64"
    rom.write_bytes(sound["rom"])
    with TestClient(_app(tmp_path, {"split.sm64scene": split}, rom, keep_folders=True)) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "split.sm64scene"}).json()
        times = [frame["recorded_ms"] for frame in opened["frames"]]
        entries = []
        for k in range(75):                          # 1.25 s at 60 Hz: Play from frame 0, Music off at 0.6 s
            t = k * 1000 / 60
            frame = max(i for i, at in enumerate(times) if at <= t) if t < times[-1] + FRAME_MS else len(times) - 1
            entries.append({"t_ms": t, "scene_frame": frame, "camera": None, "camera_used": None, "hud": True,
                            "ghosts": [], "play_ms": t if t < times[-1] else None,
                            "sound": {"music": t < 600, "effects": True, "volume": 1.0, "muted": False,
                                      "ghost_mutes": []}})
        job = client.post("/api/scene-lab/export", json=_body(opened, entries)).json()["job"]
        status = _finished(client, job)
        folder = client.app.state.scene_lab.exports.jobs[job].folder
    assert status["state"] == "done", status
    with wave.open(str(folder / "sound.wav")) as file:
        assert file.getframerate() == fx.RATE
        heard = np.frombuffer(file.readframes(file.getnframes()), "<i2").reshape(-1, 2)
    assert len(heard) == round(75 / 60 * fx.RATE)
    shows = json.loads((folder / "pictures.json").read_text(encoding="utf-8"))["entries"]
    frame_10 = shows.index(next(k for k, at in enumerate(entries) if at["scene_frame"] == 10)) / 60
    click = np.flatnonzero(np.abs(heard[:, 0]) > 20000)
    assert click.size, "the click at frame 10 never sounded"
    first_click = click[(click / fx.RATE > frame_10 - 0.1)][0] / fx.RATE
    assert abs(first_click - frame_10) < 1 / 60 + 0.002        # within the display frame that showed it
    rms = lambda a, b: float(np.sqrt(np.mean(heard[round(a * fx.RATE):round(b * fx.RATE), 0].astype(float) ** 2)))  # noqa: E731
    # Music off: the effects stem alone (0.75 of the tone; the fixture's lost 30 ms lie before 0.45 s)
    assert rms(0.75, 0.95) / rms(0.45, 0.60) == pytest.approx(0.75, abs=0.02)


def test_cancel_stops_the_child_and_leaves_no_file(config, recording, tmp_path):
    entries = [{**entry, "scene_frame": k % 8, "hud": k % 2 == 0, "ghosts": [],
                "camera": {"focus": [0, 0, 0], "position": [k, 300, 900]}}
               for k, entry in enumerate(_timeline() * 6)]
    for k, entry in enumerate(entries):
        entry["t_ms"] = k * 1000 / 60
    with TestClient(_app(tmp_path, {"scene.sm64scene": recording["package"].read_bytes()}, recording["rom"],
                         Loader({}), fake_host={"delay_ms": 100})) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        job = client.post("/api/scene-lab/export", json=_body(opened, entries)).json()["job"]
        deadline = time.monotonic() + 30
        while "Drawing" not in client.get(f"/api/scene-lab/export/{job}").json()["message"]:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert client.post(f"/api/scene-lab/export/{job}/cancel").json() == {"state": "running"}
        status = _finished(client, job, 20)
        child = client.app.state.scene_lab.exports.jobs[job].process
    assert status["state"] == "cancelled" and status["message"] == "Cancelled."
    assert child.poll() is not None
    assert not list((tmp_path / "clips").iterdir())          # no video and no partial file


def test_an_encoder_failure_says_why_and_removes_the_partial_file(config, recording, tmp_path):
    with TestClient(_app(tmp_path, {"scene.sm64scene": recording["package"].read_bytes()}, recording["rom"],
                         Loader({"a": (CLIP, "x"), "b": (CLIP, "y")}), fail_after=1)) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        job = client.post("/api/scene-lab/export", json=_body(opened, _timeline())).json()["job"]
        status = _finished(client, job)
    assert status["state"] == "error" and status["message"].startswith("The video encoder stopped")
    assert not list((tmp_path / "clips").iterdir())


def test_one_export_at_a_time_and_only_of_the_open_scene(config, recording, tmp_path):
    with TestClient(_app(tmp_path, {"scene.sm64scene": recording["package"].read_bytes()}, recording["rom"],
                         Loader({}), fake_host={"delay_ms": 100})) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        body = _body(opened, [{**entry, "ghosts": []} for entry in _timeline()])
        stale = client.post("/api/scene-lab/export", json={**body, "session": opened["session"] + 1})
        assert stale.status_code == 409 and "no longer open" in stale.json()["detail"]
        assert client.post("/api/scene-lab/export", json={**body, "fps": 24}).status_code == 422
        beyond = [{**body["timeline"][0], "scene_frame": len(opened["frames"])}]
        assert client.post("/api/scene-lab/export", json={**body, "timeline": beyond}).status_code == 422
        assert client.post("/api/scene-lab/export", content=b"{not json").status_code == 422
        job = client.post("/api/scene-lab/export", json=body).json()["job"]
        busy = client.post("/api/scene-lab/export", json=body)
        assert busy.status_code == 409 and busy.json()["detail"] == scene_exports.BUSY
        assert client.get("/api/scene-lab/export/nope").status_code == 404
        client.post(f"/api/scene-lab/export/{job}/cancel")
        assert _finished(client, job, 20)["state"] == "cancelled"


def test_a_recording_names_its_format_and_a_reader_refuses_another(config, recording, tmp_path):
    """Recordings outlive changes to the viewer, so the format carries a version from the first one
    and both readers, the lab and the export child, refuse one they do not know rather than guess."""
    with TestClient(_app(tmp_path, {"scene.sm64scene": recording["package"].read_bytes()}, recording["rom"],
                         Loader({}))) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        body = _body(opened, [{**entry, "ghosts": []} for entry in _timeline()])
        for other in ({key: value for key, value in body.items() if key != "version"}, {**body, "version": 2}):
            refused = client.post("/api/scene-lab/export", json=other)
            assert refused.status_code == 422 and refused.json()["detail"] == scene_export.OTHER_VERSION
    assert not (tmp_path / "clips").exists() or not list((tmp_path / "clips").iterdir())
    with pytest.raises(scene_export.ExportError, match="another version"):
        scene_export.run({"version": 2, "timeline": body["timeline"], "fps": 60}, tmp_path, lambda event: None,
                         scene_export.threading.Event())


def test_a_held_picture_never_plays_its_sound_twice_and_the_music_goes_on_where_it_stopped():
    """Play, Pause 12 ms into picture 10, Play again, as the page records it (its clock resumes where
    it paused: SceneLink.play's resumeMs): the sound is silent while paused and goes on from the
    sample it stopped at, so nothing heard before the pause is heard again."""
    runs, total = _table()
    ramp = np.arange(total, dtype=np.int64)
    stage = np.stack([(ramp % 30000 - 15000), ramp // 30000 * 1000], axis=1).astype(np.int16)
    paused = 10 * FRAME_MS + 12                                   # the recorded time sounding at Pause
    entries = [_entry(t, t) for t in np.arange(0, paused, 1000 / 60)] + [_entry(paused, None)]
    entries += [_entry(t, paused + t - 1000) for t in np.arange(1000, 1400, 1000 / 60)]
    pcm = es.mix(entries, 1400, RATE, runs, False, lambda track: stage, {})
    heard = pcm[:, 1].astype(np.int64) // 1000 * 30000 + pcm[:, 0].astype(np.int64) + 15000   # the stream sample
    before = slice(es.FADE, round(paused * 48) - es.FADE)
    after = slice(round(1000 * 48) + es.FADE, round(1400 * 48) - es.FADE)
    assert not pcm[round(paused * 48) + es.FADE:round(1000 * 48)].any()      # silent while paused
    assert np.all(np.diff(heard[before]) == 1) and np.all(np.diff(heard[after]) == 1)
    assert heard[after][0] == round(paused * 48) + es.FADE             # on from where it stopped, no repeat
    assert heard[after][0] > heard[before][-1]
