"""A growing attempt scene plays the recorder's own sound from its first picture
(tools/scene_lab/scene_audio.py LiveSound, through scene_session.SceneSession's growth).

The recording is scene_sound_fixture.py's synthetic tap session (40 pictures, a click on every
tenth); live_audio.LiveAudio's ring is fed the device stream test_scene_live_audio.py builds,
on a scripted QPC clock, as the recorder's wrapped source would. The scene's package is written
in place and published as a take writes it (package.PackageWriter.publish), read through the
spool double, so its index carries the QPC its present times count from. The sound's one fit
runs the cutter child's own function (attempt_cutter.Brain._fit) on this thread. No audio
device, ROM, renderer or emulator is involved.
"""
import importlib.util
import random
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import scene_sound_fixture as fx
from scene_sound_fixture import pk

import attempt_cutter  # noqa: E402  (tools/scene_lab, on the path the fixture set)
import attempt_scenes  # noqa: E402
import audio_clock as ac  # noqa: E402
import fake_viewer_host  # noqa: E402
import gpu_gate  # noqa: E402
import live_audio as la  # noqa: E402
import scene_audio  # noqa: E402
import scene_session  # noqa: E402
from scene_spool_double import RecordedSpool  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scene_live_audio_tests", ROOT / "tests/test_scene_live_audio.py")
live_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live_tests)
FREQ, RATE = fx.FREQ, fx.RATE


class ChildFit:
    """AttemptScenes.fit as the session sees it, answered by the cutter child's own fit
    (attempt_cutter.Brain._fit) on this thread; counts the fits asked for."""

    def __init__(self, folder: Path):
        self.folder, self.asked = folder, []

    def __call__(self, stamps, origin_qpc: int, frequency: int) -> Future:
        path = self.folder / f"fit-{len(self.asked)}.npy"
        np.save(path, stamps)
        self.asked.append(path)
        future, said = Future(), []
        attempt_cutter.Brain._fit(SimpleNamespace(emit=said.append),
                                  {"token": 1, "stamps": str(path), "origin": origin_qpc, "frequency": frequency})
        future.set_result({key: value for key, value in said[0].items() if key != "fit"})
        return future


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
            "config": config, "presents": presents, "rom": rom, "fitter": ChildFit(tmp_path)}


def _open(growing):
    return scene_session.SceneSession.open(
        growing["config"], growing["path"], sidecar=growing["grow"], live=growing["live"],
        fitter=growing["fitter"], host_factory=scene_session.command_host(fake_viewer_host.command()),
        evaluator_factory=lambda package, rom: fake_viewer_host.FakeEvaluator())


def _errors(growing, audio) -> np.ndarray:
    """Each frame's sample against the device's true one, in samples, on the scene's own stream
    (which starts at the ring's first chunk the scene took)."""
    presents = growing["presents"]
    first = growing["live"].records(presents[0].qpc, presents[-1].qpc)[0][1]
    truth = np.array(growing["truth"][:len(audio["samples"])]) - first
    return np.array(audio["samples"]) - truth


def _pcm(file: bytes) -> bytes:
    import io
    import wave
    with wave.open(io.BytesIO(file)) as wav:
        return wav.readframes(wav.getnframes())


def _whole(growing, session, writer, captures):
    for capture in captures:
        writer.add(capture)
    writer.finish()
    growing["grow"].unlink()
    return session.grow()


def test_a_growing_scene_has_its_sound_from_the_first_picture_and_keeps_it_as_it_grows(growing):
    writer, captures = growing["writer"], growing["captures"]
    for _ in range(20):
        writer.add(next(captures))
    assert writer.publish(growing["grow"]) == 20                    # closures only: no state to wait for
    session = _open(growing)
    try:
        audio = session.describe()["audio"]
        assert session.describe()["growing"] and audio["available"] and audio["live"] and audio["codec"] == "stored"
        assert len(audio["samples"]) == 20 and np.abs(_errors(growing, audio)).max() <= RATE // 1000
        first_block, media = session.audio_block(0)
        assert media == "audio/wav" and len(first_block) > 44
        grown = _whole(growing, session, writer, captures)
        assert grown["growing"] is False and grown["from"] == 20 and len(grown["frames"]) == 20
        audio = grown["audio"]                                        # frames past the fit: its line, extended
        assert len(audio["samples"]) == fx.FRAMES and np.abs(_errors(growing, audio)).max() <= RATE // 1000
        assert session.audio_block(0)[0] == first_block               # the stream only grew
        assert len(growing["fitter"].asked) == 1 and not session.settled   # one fit; the cutter adds the layer
    finally:
        session.close()


def test_the_sound_the_cutter_adds_takes_over_in_the_same_blocks(growing, tmp_path):
    writer, captures = growing["writer"], growing["captures"]
    for _ in range(20):
        writer.add(next(captures))
    writer.publish(growing["grow"])
    session = _open(growing)
    try:
        live_block = _pcm(session.audio_block(0)[0])
        _whole(growing, session, writer, captures)
        assert session.grow() is None                                  # nothing new until the cutter adds it
        presents = growing["presents"]
        capture = tmp_path / "sound-1.cap"
        assert growing["live"].write_span(presents[0].qpc, presents[-1].qpc, capture)["written"]
        assert la.attach_span(growing["path"], capture, [(p.sequence, p.qpc) for p in presents], FREQ,
                              growing["rom"]) is None
        grown = session.grow()
        audio = grown["audio"]
        assert grown["frames"] == [] and audio["codec"] == "flac" and "live" not in audio
        assert np.abs(_errors(growing, audio)).max() <= RATE // 1000
        layer_block = session.audio_block(0)
        assert layer_block[1] == "audio/flac" and session.settled
        decoded = np.asarray(session.sound.layer.block(0), np.int16).tobytes()
        assert decoded[:len(live_block)] == live_block                 # what the page holds stays valid
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
        assert audio == {"available": False, "reason": la.NOT_STARTED} and not growing["fitter"].asked
    finally:
        session.close()


def test_frames_past_the_fit_land_within_a_millisecond_of_the_whole_scenes_fit(tmp_path):
    """A scene fit 10 s in that grows 6 s more, the recorder's clock 150 ppm off the QPC: its
    newer frames, extrapolated from the fit, against the fit the attached layer makes."""
    ppm, chunk, origin = 150.0, 480, fx.START
    clock = live_tests.Clock(0)
    live = la.LiveAudio(seconds=60.0, frequency=FREQ, stamp=clock)
    source = live_tests.FakeSource(7)
    live.wrap(lambda pid: source)(7).start(lambda pcm: None)
    start, period = origin - 3 * FREQ, FREQ / (RATE * (1 + ppm / 1e6))
    rng, arrival, noise = random.Random(3), 0, np.zeros((chunk, 2), np.int16)
    chunks = []
    for first in range(0, int(20 * FREQ / period) // chunk * chunk, chunk):
        arrival = max(arrival, int(start + (first + chunk) * period + 3_000 + rng.expovariate(1 / 20_000)))
        chunks.append(arrival)
    fed = iter(chunks)

    def feed_until(qpc):
        for arrival in fed:
            clock.now = arrival
            source.on_pcm(noise)
            if arrival >= qpc:
                return

    outlines = [{"present_us": round(k * 1e6 / 30), "present": 2 * k + 1} for k in range(16 * 30)]
    qpc = [origin + outline["present_us"] * FREQ // 1_000_000 for outline in outlines]
    sound = scene_audio.LiveSound(live, {"origin_qpc": origin, "frequency": FREQ}, ChildFit(tmp_path))
    feed_until(qpc[299] + FREQ // 4)
    sound.request(outlines, list(range(300)))
    assert sound.adopt() and sound.extend(outlines, list(range(300)), 0, False)
    for frames in (360, 420, 480):                     # every 2 s, as grown messages come
        feed_until(qpc[frames - 1] + FREQ // 4)
        sound.extend(outlines, list(range(frames)), frames - 60, False)
    records = live.records(qpc[0], qpc[-1])
    whole = ac.build_clock([ac.Chunk(*record[:5]) for record in records],
                           [ac.Present(outline["present"], at) for outline, at in zip(outlines, qpc, strict=True)],
                           rate=RATE, frequency=FREQ)
    expected = np.array([picture.sample for picture in whole.pictures])
    assert len(sound.samples) == 480 and min(sound.samples) >= 0
    assert np.abs(np.array(sound.samples) - expected).max() <= RATE // 1000
    assert bytes(sound.pcm) == la.pcm_of(records)[:len(sound.pcm)]


def test_the_catalog_hands_the_fit_to_the_cutter_and_answers_the_scene_that_asked(tmp_path):
    class Child:
        def __init__(self, folder, on_event):
            self.on_event, self.notes, self.answer = on_event, [], True

        def send(self, note):
            self.notes.append(note)
            if note["op"] == "fit" and self.answer:
                attempt_cutter.Brain._fit(SimpleNamespace(emit=self.on_event), note)

        def close(self):
            pass

    scenes = attempt_scenes.AttemptScenes(lambda: tmp_path / "attempts", Child, restart_seconds=3600)
    scenes.start()
    try:
        stamps = np.array([[0, first, 480, 1_000_000 + first * 10_000 // 48, 0] for first in range(0, 480 * 400, 480)],
                          np.int64)
        answer = scenes.fit(stamps, 1_500_000, FREQ).result(timeout=5)
        expected = la.fit_stamps(stamps, 1_500_000, FREQ)
        assert {key: answer[key] for key in expected} == expected and answer["fit_ms"] >= 0
        assert scenes.cutter.notes[-1]["op"] == "fit" and not list((tmp_path / "attempts").glob("*.npy"))
        scenes.cutter.answer = False
        unanswered = scenes.fit(stamps, 1_500_000, FREQ)
        scenes._event({"exited": 1})                    # the child died with the fit in hand
        assert unanswered.result(timeout=5) == {"reason": attempt_scenes.STOPPED}
        assert not list((tmp_path / "attempts").glob("*.npy"))
    finally:
        scenes.stop()


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
