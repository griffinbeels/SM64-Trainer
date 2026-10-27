"""The attempt scene cutter's takes (tools/scene_lab/attempt_cutter.py Brain) on a synthetic session.

The recording is scene_attempt_fixture.py's: 400 tasks, game frames 5000 on, BoB (level 9) until
task 300 and castle inside after, read through the spool double with the live edge where each
test puts it. The Brain runs in this process (the child's main loop is its tick); the reference
is the whole session written as one package.
"""
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import attempt_cutter  # noqa: E402
import attempt_span  # noqa: E402
import package  # noqa: E402
import scene_attempt_fixture as fixture  # noqa: E402
import scene_session  # noqa: E402
from scene_spool_double import RecordedSpool  # noqa: E402

from sm64_events.memory.addresses import ACT_STAR_DANCE_EXIT  # noqa: E402

TASKS, LOAD = 400, 300


def place(index: int) -> tuple[int, int, int]:
    return 5000 + index, (9 if index < LOAD else 6), 1


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    folder = tmp_path_factory.mktemp("attempt-cut")
    rom = fixture.rom()
    session = fixture.write_session(folder, rom, [place(index) for index in range(TASKS)])
    whole = folder / "whole.sm64scene"
    package.write_package(package.tap_source(session, "next", states=True), rom, whole, package.ZLIB)
    return {"rom": rom, "session": session, "whole": package.Package(whole.read_bytes(), rom),
            "loads": package.provenance.segment_loads(rom)}


class Run:
    """A Brain over the double with the live edge at `through`, its events, and a clock."""

    def __init__(self, recording, folder: Path, through: int):
        self.spool = RecordedSpool(recording["session"], through=through)
        self.refs = self.spool.refs
        self.events: list[dict] = []
        self.brain = attempt_cutter.Brain(recording["rom"], recording["loads"], folder, lambda: self.spool,
                                          self.events.append)

    def start(self, index: int) -> None:
        self.brain.note({"op": "start", "kind": "anchor", "frame": self.refs[index].global_timer,
                         "utc": self.refs[index].utc.isoformat()})

    def complete(self, attempt_id: int, first: int, closed: int, outcome: str) -> None:
        mark = attempt_span.AttemptMark(attempt_id, self.refs[first].global_timer, self.refs[closed].global_timer,
                                        self.refs[first].utc, self.refs[closed].utc, outcome)
        self.brain.note({"op": "complete", "attempt": mark.as_json()})

    def until(self, test, seconds: float = 60.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.brain.tick()
            found = test()
            if found:
                return found
            time.sleep(0.02)
        raise AssertionError(f"timed out; events {self.events}")

    def ended(self, attempt_id: int) -> dict:
        """The attempt's last word: its scene whole, or why it has none."""
        return self.until(lambda: next((event for event in self.events if event.get("id") == attempt_id and (
            event["state"] == "unavailable" or event["state"] == "ready" and not event.get("growing"))), None))

    def close(self) -> None:
        self.brain.close()


def _same_frames(cut: package.Package, whole: package.Package, first: int) -> None:
    for offset in range(len(cut)):
        a, b = cut.frame(offset), whole.frame(first + offset)
        for name in ("task", "runs", "run_bytes", "fields", "extras", "vi", "shown"):
            assert getattr(a, name) == getattr(b, name), (first + offset, name)
        assert package.state_fingerprint(*cut.scene_state(offset)[1:]) == \
            package.state_fingerprint(*whole.scene_state(first + offset)[1:]), first + offset


def _identity(data: package.Package, index: int) -> int:
    return data.outline(index)["identity"]["global_timer"]


def test_a_take_written_while_he_plays_stops_at_the_fade_and_matches_the_whole_recording(recording, tmp_path):
    """The star grab completes at 150; the take writes on through the fade, up to the last BoB
    picture the recording holds, and no further: the castle's first frame is never written."""
    run = Run(recording, tmp_path, through=40)
    try:
        run.start(20)
        run.until(lambda: run.brain.takes and run.brain.takes[0].position >= 39)
        run.spool.through = 160
        run.complete(8, 20, 150, "success")
        growing = run.until(lambda: next((event for event in run.events if event.get("id") == 8), None))
        take = run.brain.takes[0]
        assert growing == {"id": 8, "state": "ready", "growing": True, "path": str(take.path), "grow": str(take.grow)}
        assert take.reserved == 8 and take.limit == 160
        run.spool.through = 299                                  # the fade's last picture, castle not yet
        run.until(lambda: take.position == 299)
        run.spool.through = 310                                  # recorded, but the span rule has not looked yet
        time.sleep(0.3)
        assert take.position == 299 and take.cut is None         # held at the newest task known inside the span
        published = scene_session.Growth(take.path, take.grow).read(recording["rom"])
        assert 0 < len(published) <= 280                        # what an open scene shows while it grows
        _same_frames(published, recording["whole"], 20)
        ready = run.ended(8)
    finally:
        run.close()
    assert ready["state"] == "ready" and (ready["first"], ready["last"], ready["why"]) == (20, 299, "fade")
    assert ready["take_kind"] == "anchor" and ready["frames"] == 280 and ready["path"] == str(take.path)
    cut = package.Package(Path(ready["path"]).read_bytes(), recording["rom"])
    assert "dropped_frames" not in cut.index.meta["stats"]
    assert (_identity(cut, 0), _identity(cut, len(cut) - 1)) == (5020, 5299)
    _same_frames(cut, recording["whole"], 20)
    assert [path.name for path in tmp_path.iterdir()] == [take.path.name]      # its publication is gone


def test_the_next_attempts_reset_ends_the_scene_on_the_picture_before_it(recording, tmp_path, monkeypatch):
    """His L-reset at 100: the take holds the reset's own picture until the completion says
    where the scene ends, so it never writes the next attempt's first frame."""
    monkeypatch.setattr(attempt_cutter, "HOLD_SECONDS", 30.0)
    monkeypatch.setattr(attempt_cutter, "GRACE_SECONDS", 30.0)   # live, the completion follows the reset's note at once
    run = Run(recording, tmp_path, through=60)
    try:
        run.start(20)
        run.until(lambda: run.brain.takes and run.brain.takes[0].position >= 59)
        run.start(100)
        run.spool.through = 120
        first = run.brain.takes[0]
        run.until(lambda: first.position == 99)
        time.sleep(0.2)
        assert first.position == 99                               # held at the announced start
        run.complete(9, 20, 100, "reset")
        ready = run.ended(9)
        run.until(lambda: [take.first.index for take in run.brain.takes if not take.ended] == [100])
    finally:
        run.close()
    assert (ready["state"], ready["last"], ready["why"], ready["frames"]) == ("ready", 99, "next start", 80)
    cut = package.Package(Path(ready["path"]).read_bytes(), recording["rom"])
    assert "dropped_frames" not in cut.index.meta["stats"]
    _same_frames(cut, recording["whole"], 20)


def test_an_attempt_with_no_take_is_cut_from_behind_the_edge(recording, tmp_path):
    run = Run(recording, tmp_path, through=TASKS - 1)
    try:
        run.complete(3, 200, 250, "death")
        ready = run.ended(3)
    finally:
        run.close()
    assert (ready["state"], ready["take_kind"], ready["first"], ready["last"]) == ("ready", "behind", 200, 299)
    _same_frames(package.Package(Path(ready["path"]).read_bytes(), recording["rom"]), recording["whole"], 200)


def test_an_unclaimed_take_is_abandoned_and_its_file_removed(recording, tmp_path, monkeypatch):
    monkeypatch.setattr(attempt_cutter, "GRACE_SECONDS", 0.0)
    run = Run(recording, tmp_path, through=80)
    try:
        run.start(10)
        run.until(lambda: run.brain.takes and run.brain.takes[0].position >= 40)
        abandoned = run.brain.takes[0]
        run.start(60)                                              # a reset no attempt closed (no-op spam)
        run.until(lambda: abandoned.ended)
        assert [take.first.index for take in run.brain.takes if not take.ended] == [60]
    finally:
        run.close()
    assert not list(tmp_path.iterdir())                                     # no package, no publication


def test_the_child_writes_what_he_waits_for_first(recording, tmp_path):
    """A take finishing its claimed span goes before every other; live takes behind the recording
    go before takes cut from behind it."""
    brain = Run(recording, tmp_path, through=200).brain
    refs = RecordedSpool(recording["session"], through=200).refs
    finishing = attempt_cutter.Take(brain, refs[10], "anchor")
    finishing.cut = attempt_span.Cut(refs[10], refs[90], refs[80], "next start")
    live = attempt_cutter.Take(brain, refs[91], "anchor")
    behind = attempt_cutter.Take(brain, refs[0], "behind", last=50)
    behind.cut = attempt_span.Cut(refs[0], refs[50], refs[50], "next start")
    brain.takes, brain.edge = [finishing, live, behind], refs[200]
    finishing.written, live.written = 70, 100                       # live writes 91..190: 10 behind the edge
    assert [brain.should_wait(take) for take in (finishing, live, behind)] == [False, True, True]
    finishing.ended = True
    assert [brain.should_wait(take) for take in (live, behind)] == [False, True]
    live.written = 108                                               # within LAG_FRAMES of the edge
    assert brain.should_wait(behind) is False


def test_a_hole_in_the_recording_makes_the_attempt_unavailable(recording, tmp_path):
    run = Run(recording, tmp_path, through=TASKS - 1)
    run.spool.holes = [(120, 121)]
    try:
        run.complete(4, 100, 150, "reset")
        ended = run.ended(4)
    finally:
        run.close()
    assert ended == {"id": 4, "state": "unavailable",
                     "reason": "The scene recording lost 2 pictures during this attempt."}


def test_a_forgotten_attempt_leaves_no_file(recording, tmp_path):
    run = Run(recording, tmp_path, through=TASKS - 1)
    try:
        run.complete(5, 0, 290, "reset")
        run.until(lambda: run.brain.takes)
        run.brain.note({"op": "forget", "id": 5})
        run.until(lambda: not [take for take in run.brain.takes if not take.ended])
    finally:
        run.close()
    assert not any(event.get("state") == "ready" for event in run.events)
    assert not list(tmp_path.iterdir())


# ---------------------------------------------------------------- the star dance (round 5)

GRAB, FADE, PAUSE = 150, 80, 92        # a ground grab at 150; the exit fade at actionTimer 80; his pause at 92


def mario(index: int) -> tuple[int, int, int]:
    """(action, actionTimer, warp op): running, then the exit dance from GRAB, paused at PAUSE."""
    if index < GRAB:
        return 0x04000440, 0, 0                                  # ACT_WALKING
    counted = min(index - GRAB, PAUSE)
    return ACT_STAR_DANCE_EXIT, counted, 17 if counted >= FADE else 0


@pytest.fixture(scope="module")
def dancing(tmp_path_factory):
    folder = tmp_path_factory.mktemp("attempt-dance")
    rom = fixture.rom()
    session = fixture.write_session(folder, rom, [place(index) for index in range(TASKS)],
                                    marios=[mario(index) for index in range(TASKS)])
    whole = folder / "whole.sm64scene"
    package.write_package(package.tap_source(session, "next", states=True), rom, whole, package.ZLIB)
    return {"rom": rom, "session": session, "whole": package.Package(whole.read_bytes(), rom),
            "loads": package.provenance.segment_loads(rom)}


def test_a_live_take_stops_writing_at_the_star_dances_end_and_never_shows_the_pause(dancing, tmp_path):
    """The exit fade begins at task 230 (actionTimer 80); his pause from 242 froze the picture
    until the castle loads at 300. The take holds at 230 while the recording runs on, and the
    scene is 20..230: what an open scene shows never passes it."""
    run = Run(dancing, tmp_path, through=40)
    try:
        run.start(20)
        run.until(lambda: run.brain.takes and run.brain.takes[0].position >= 39)
        run.spool.through = 160
        run.complete(8, 20, GRAB, "success")
        run.until(lambda: next((event for event in run.events if event.get("id") == 8), None))
        take = run.brain.takes[0]
        assert take.end == GRAB
        run.spool.through = 299
        ready = run.ended(8)
        assert take.position == GRAB + FADE                     # it never wrote the frame after
        published = scene_session.Growth(take.path, take.grow).read(dancing["rom"])
        assert published is None or len(published) <= GRAB + FADE - 20 + 1
    finally:
        run.close()
    assert (ready["first"], ready["last"], ready["why"], ready["frames"]) == (20, GRAB + FADE, "dance", 211)
    assert ready["seconds"] == pytest.approx(211 / 30, abs=0.05)
    cut = package.Package(Path(ready["path"]).read_bytes(), dancing["rom"])
    assert "dropped_frames" not in cut.index.meta["stats"] and len(cut) == 211
    _same_frames(cut, dancing["whole"], 20)


def test_a_take_cut_from_behind_stops_itself_at_the_dance_it_reads(dancing, tmp_path):
    """No take followed this attempt, so the span rule decided without Mario (the fade at 299);
    the take reads him as it writes and ends the scene where the dance does."""
    run = Run(dancing, tmp_path, through=TASKS - 1)
    try:
        run.complete(6, 100, GRAB, "success")
        ready = run.ended(6)
    finally:
        run.close()
    assert (ready["take_kind"], ready["first"], ready["last"], ready["why"]) == ("behind", 100, GRAB + FADE, "dance")
    cut = package.Package(Path(ready["path"]).read_bytes(), dancing["rom"])
    assert len(cut) == GRAB + FADE - 100 + 1
    _same_frames(cut, dancing["whole"], 100)


def test_an_attempt_no_dance_ends_keeps_its_fade_while_mario_is_read(dancing, tmp_path):
    """A death at 120, before the grab: no dance follows its end, so the fade ends it as before."""
    run = Run(dancing, tmp_path, through=60)
    try:
        run.start(20)
        run.until(lambda: run.brain.takes and run.brain.takes[0].position >= 59)
        run.spool.through = TASKS - 1
        run.complete(7, 20, 120, "death")
        ready = run.ended(7)
    finally:
        run.close()
    assert (ready["take_kind"], ready["last"], ready["why"]) == ("anchor", 299, "fade")


# ---------------------------------------------------------------- his recorded BoB minute (this machine only)

RECORDED = Path(os.environ.get("LOCALAPPDATA", "")) / "SM64Trainer/scene-lab/tap-20261025-184022"
ROM = Path(r"C:\Users\griff\Desktop\games\Project64 1.6 (wermi's build v7)\Project64 1.6\roms\Usamune US.z64")


@pytest.mark.skipif(not (RECORDED / "tap.bin").is_file() or not ROM.is_file(),
                    reason="his recorded BoB session and ROM are private to the lab machine")
def test_his_bob_star_grab_opens_from_its_anchor_to_the_star_dances_end(tmp_path, monkeypatch):
    """Read-only over tap-20261025-184022: an attempt anchored at task 1950 whose star grab
    (task 2105, a midair grab: the dance from 2127) completes it; its scene ends at task 2207,
    where the game starts the exit fade (actionTimer 80, read from the recording's own scene
    state), not at 2240, the iris's black picture before castle inside, and opens in the viewer
    (fake renderer) on exactly those frames."""
    import fake_viewer_host
    import gpu_gate
    import scene_session
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    rom = ROM.read_bytes()
    run = Run({"rom": rom, "session": RECORDED, "loads": package.provenance.segment_loads(rom)}, tmp_path,
              through=2300)
    try:
        run.complete(41, 1950, 2105, "success")
        ready = run.ended(41)
    finally:
        run.close()
    assert (ready["state"], ready["first"], ready["last"], ready["why"]) == ("ready", 1950, 2207, "dance")
    config = SimpleNamespace(rom=ROM, map=None)
    session = scene_session.SceneSession.open(
        config, Path(ready["path"]), host_factory=scene_session.command_host(fake_viewer_host.command()),
        evaluator_factory=lambda package, rom: fake_viewer_host.FakeEvaluator())
    try:
        frames = session.describe()["frames"]
    finally:
        session.close()
    refs = run.refs
    assert frames[0]["game_frame"] == refs[1950].global_timer and frames[-1]["game_frame"] == refs[2207].global_timer
    assert {(frame["level"], frame["area"]) for frame in frames} == {(9, 1)}
