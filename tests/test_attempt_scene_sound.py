"""A growing attempt scene plays the recorder's own sound from its first picture
(tools/scene_lab/scene_audio.py LiveSound, through scene_session.SceneSession's growth).

The recording is scene_sound_fixture.py's synthetic tap session (40 pictures, a click on every
tenth); live_audio.LiveAudio's ring is fed the device stream test_scene_live_audio.py builds,
on a scripted QPC clock, as the recorder's wrapped source would. The scene's package is written
in place and published as a take writes it (package.PackageWriter.publish), read through the
spool double, so its index carries the QPC its present times count from. No audio device,
ROM, renderer or emulator is involved.
"""
import importlib.util
import time
from pathlib import Path

import numpy as np
import pytest

import scene_sound_fixture as fx
from scene_sound_fixture import pk

import fake_viewer_host  # noqa: E402  (tools/scene_lab, on the path the fixture set)
import gpu_gate  # noqa: E402
import live_audio as la  # noqa: E402
import scene_session  # noqa: E402
from scene_spool_double import RecordedSpool  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scene_live_audio_tests", ROOT / "tests/test_scene_live_audio.py")
live_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live_tests)
FREQ, RATE = fx.FREQ, fx.RATE


@pytest.fixture
def growing(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    rom, segment7 = fx.synthetic._rom()
    session, presents = fx.tap_session(tmp_path / "tap-live", rom, segment7)
    clock = live_tests.Clock(0)
    live = la.LiveAudio(seconds=60.0, frequency=FREQ, stamp=clock)
    source = live_tests.FakeSource(4242)
    live.wrap(lambda pid: source)(4242).start(lambda pcm: None)
    chunks, truth = live_tests._device_stream(presents)
    for arrival, pcm in chunks:
        clock.now = arrival
        source.on_pcm(pcm)
    (tmp_path / "rom.z64").write_bytes(rom)
    config = type("Config", (), {"rom": tmp_path / "rom.z64", "map": None})()
    spool = RecordedSpool(session)
    follow = spool.follow(0)
    captures = iter(follow.captures)
    path, grow = tmp_path / "take-0-1.sm64scene", tmp_path / "take-0-1.grow"
    writer = pk.PackageWriter(follow, rom, path, pk.ZLIB, level=6, in_place=True, frequency=spool.frequency)
    return {"live": live, "truth": truth, "captures": captures, "writer": writer, "path": path, "grow": grow,
            "config": config, "presents": presents}


def _open(growing):
    return scene_session.SceneSession.open(
        growing["config"], growing["path"], sidecar=growing["grow"], live=growing["live"],
        host_factory=scene_session.command_host(fake_viewer_host.command()),
        evaluator_factory=lambda package, rom: fake_viewer_host.FakeEvaluator())


def _errors(growing, audio) -> np.ndarray:
    """Each frame's sample against the device's true one, in samples, on the scene's own stream
    (which starts at the ring's first chunk the scene took)."""
    presents = growing["presents"]
    first = growing["live"].span(presents[0].qpc, presents[-1].qpc)[0][0].first
    truth = np.array(growing["truth"][:len(audio["samples"])]) - first
    return np.array(audio["samples"]) - truth


def test_a_growing_scene_has_its_sound_from_the_first_picture_and_keeps_it_as_it_grows(growing):
    writer, captures = growing["writer"], growing["captures"]
    for _ in range(20):
        writer.add(next(captures))
    assert writer.publish(growing["grow"]) == 20                    # closures only: no state to wait for
    session = _open(growing)
    try:
        audio = session.describe()["audio"]
        assert session.describe()["growing"] and audio["available"] and audio["codec"] == "stored"
        assert len(audio["samples"]) == 20 and np.abs(_errors(growing, audio)).max() <= RATE // 1000
        first_block, media = session.audio_block(0)
        assert media == "audio/wav" and len(first_block) > 44
        for capture in captures:
            writer.add(capture)
        writer.finish()
        growing["grow"].unlink()
        grown = session.grow()
        assert grown["growing"] is False and grown["from"] == 20 and len(grown["frames"]) == 20
        audio = grown["audio"]
        assert len(audio["samples"]) == fx.FRAMES and np.abs(_errors(growing, audio)).max() <= RATE // 1000
        assert session.audio_block(0)[0] == first_block               # the stream only grew
    finally:
        session.close()


def test_without_the_recorders_sound_a_growing_scene_says_why_it_is_silent(growing, tmp_path):
    writer, captures = growing["writer"], growing["captures"]
    for _ in range(11):
        writer.add(next(captures))
    writer.publish(growing["grow"])
    growing["live"] = la.LiveAudio(seconds=60.0, frequency=FREQ, stamp=live_tests.Clock(0))   # never fed
    session = _open(growing)
    try:
        audio = session.describe()["audio"]
        assert audio == {"available": False, "reason": la.NOT_STARTED}
    finally:
        session.close()


def test_a_finished_scene_gains_the_recorders_sound_without_waiting_for_it(growing, tmp_path):
    """The cutter says where a finished scene's pictures lie on the QPC clock; the server takes
    those seconds from the ring and the cutter adds them to the package, after "ready"."""
    import attempt_cutter
    import attempt_span
    rom = growing["writer"].rom
    spool = RecordedSpool(growing["path"].parent / "tap-live", recording=False)
    events = []
    brain = attempt_cutter.Brain(rom, pk.provenance.segment_loads(rom), tmp_path / "attempts", lambda: spool,
                                 events.append)
    refs = spool.refs
    brain.note({"op": "complete", "attempt": attempt_span.AttemptMark(
        9, refs[0].global_timer, refs[30].global_timer, refs[0].utc, refs[30].utc, "success").as_json()})
    try:
        for _ in range(500):
            brain.tick()
            ready = next((event for event in events if event.get("state") == "ready"), None)
            if ready:
                break
            time.sleep(0.02)
        assert ready, events
        truth = [growing["presents"][0].qpc, growing["presents"][-1].qpc]
        assert all(abs(a - b) <= FREQ // 1_000_000 for a, b in zip(ready["presents_qpc"], truth, strict=True))  # 1 us
        assert ready["qpc_frequency"] == FREQ and pk.Package(Path(ready["path"]).read_bytes(), rom).audio is None
        capture = tmp_path / "sound-9.cap"
        assert growing["live"].write_span(*ready["presents_qpc"], capture)["written"]
        brain.note({"op": "sound", "id": 9, "capture": str(capture)})
        for _ in range(500):
            said = next((event for event in events if "sound" in event), None)
            if said:
                break
            time.sleep(0.02)
        assert said == {"id": 9, "sound": None}
        assert pk.Package(Path(ready["path"]).read_bytes(), rom).audio is not None
    finally:
        brain.close()
