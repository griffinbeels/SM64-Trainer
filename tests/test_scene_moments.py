"""Save this moment (Ghost Handoff, lab only): the two routes over a stand-in builder and a fake game, the builder's
child process over a stand-in script, and the parts of tools/scene_lab/moment_slot.py that need no ROM.

The real build needs his private ROM, so `moment_slot.py compare <tap session>` is its evidence, run by hand:
every slot it made from a new recording's spans equaled handoff_slot's replay from the boot record. No process is
attached here, and nothing is written outside tmp_path.
"""
import asyncio
import json
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
sys.path.insert(0, str(ROOT / "tests"))
import handoff_slot as slots  # noqa: E402
import handoff_try as tool  # noqa: E402
import moment_slot  # noqa: E402
import test_handoff_try as fakes  # noqa: E402
from sm64_events.server import scene_lab_api, scene_moments  # noqa: E402
from sm64_events.slotlibrary.store import BUILT, SlotLibrary  # noqa: E402

PACKAGE = "take.sm64scene"
ANSWER = {"frame": 7, "level": fakes.LEVEL, "area": fakes.AREA, "mario_object": fakes.MARIO, "action": 0x03000888,
          "seed": None, "segments": fakes.SEGMENTS.hex(), "current_area": fakes.CURRENT_AREA, "worked_out": False,
          "random_before_site": 0, "random_in_frame": 3, "checked": {}, "warm_frames": 0, "seconds": 0.5}


def slot_bytes() -> bytes:
    data = bytearray(b"\xCD" * slots.SLOT_BYTES)
    struct.pack_into(">I", data, slots.TRAILER_MARIO, fakes.MARIO)
    struct.pack_into(">hh", data, slots.TRAILER_LEVEL, fakes.LEVEL, fakes.AREA)
    return bytes(data)


class Harness:
    def __init__(self, tmp_path):
        self.game = fakes.FakeGame()
        self.answer = {**ANSWER, "fingerprint": tool.routine_fingerprint(self.game.read, slots.US)}
        self.calls = []
        self.library = SlotLibrary(tmp_path / "library")
        self.backups = tmp_path / "backups"
        self.attach_error = None

    def builder(self, _config, path, frame, register=lambda child: None):
        self.calls.append((path.name, frame))
        register(None)
        if "refused" in self.answer:
            return self.answer, None
        return self.answer, slot_bytes()

    def connect(self):
        if self.attach_error:
            raise self.attach_error
        return self.game


@pytest.fixture
def lab(tmp_path):
    packages = tmp_path / "packages"
    packages.mkdir()
    (packages / PACKAGE).write_bytes(b"not read here")
    config = tmp_path / "lab.json"
    config.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(packages),
                                  "map": str(tmp_path / "map.txt"), "lab_root": str(tmp_path / "root")}), encoding="utf-8")
    harness = Harness(tmp_path)
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, config, moment_options={
        "builder": harness.builder, "game": harness.connect, "library": harness.library, "backups": harness.backups})
    with TestClient(app) as client:
        yield client, harness


def build(client, **body):
    return client.post("/api/scene-lab/moment", json={"package": PACKAGE, "frame": 7, **body})


def test_a_build_names_its_place_and_a_write_goes_to_the_slot_his_menu_shows(lab):
    client, harness = lab
    built = build(client).json()
    assert (built["level"], built["area"], built["mario_object"]) == (fakes.LEVEL, fakes.AREA, fakes.MARIO)
    assert built["exact"] is True and harness.calls == [(PACKAGE, 7)]
    answer = client.post(f"/api/scene-lab/moment/{built['id']}/write", json={"slot": 3}).json()
    assert answer["written"] is True and answer["slot"] == 3
    index_2 = slots.slot_address(2)
    assert bytes(harness.game.ram[index_2 - 0x80000000:index_2 - 0x80000000 + slots.SLOT_BYTES]) == slot_bytes()
    assert all(index_2 <= address < index_2 + slots.SLOT_BYTES for address, _ in harness.game.writes)


def test_a_written_state_keeps_its_backup_and_joins_the_slot_library(lab):
    client, harness = lab
    old = bytes(i & 0xFF for i in range(slots.SLOT_BYTES))
    harness.game.poke(slots.slot_address(0), old)
    built = build(client).json()
    answer = client.post(f"/api/scene-lab/moment/{built['id']}/write", json={"slot": 1}).json()
    assert tool.read_backup(harness.backups / answer["backup"])[1] == old
    kept = harness.library.scan().states
    assert [state.id for state in kept] == [answer["kept"]] and kept[0].source == BUILT
    assert kept[0].name == f"{PACKAGE} frame 7" and kept[0].current_area == fakes.CURRENT_AREA


def test_pressing_again_uses_the_same_build(lab):
    client, harness = lab
    assert build(client).json()["id"] == build(client).json()["id"]
    assert len(harness.calls) == 1
    build(client, frame=8)
    assert len(harness.calls) == 2


def test_a_refused_build_says_why_and_leaves_nothing_to_write(lab):
    client, harness = lab
    harness.answer = {"refused": "never-reached", "reason": moment_slot.WORDS["never-reached"], "detail": ""}
    answer = build(client).json()
    assert answer["refused"] == "never-reached" and answer["reason"].startswith("Usamune cannot save on this frame.")
    assert "id" not in answer and harness.game.writes == []


def test_an_older_recording_says_so(lab):
    client, harness = lab
    harness.answer["worked_out"] = True
    assert build(client).json()["exact"] is False


@pytest.mark.parametrize(("change", "kind", "words"), [
    (fakes.set_level, "place", "The game is in Castle Grounds. This save state is for Bob-omb Battlefield."),
    (fakes.set_area, "place", "The game is in Bob-omb Battlefield: Area 2."),
    (fakes.clear_mario, "no-mario", "Enter the level"),
    (fakes.set_area_pointer, "loaded", "Leave the level, enter it again"),
    (fakes.set_segment, "loaded", "Leave the level, enter it again"),
])
def test_the_live_checks_refusal_reaches_the_page_in_plain_words_and_nothing_is_written(lab, change, kind, words):
    client, harness = lab
    built = build(client).json()
    change(harness.game)
    answer = client.post(f"/api/scene-lab/moment/{built['id']}/write", json={"slot": 4}).json()
    assert answer["refused"] == kind and words in answer["reason"]
    assert harness.game.writes == [] and harness.library.scan().states == ()
    for private in ("gMarioObject", "pointer", "segment table", "0x"):
        assert private not in answer["reason"]


def test_no_running_game_is_a_sentence(lab):
    client, harness = lab
    built = build(client).json()
    harness.attach_error = tool.Refusal("no Project64 with a running game found")
    answer = client.post(f"/api/scene-lab/moment/{built['id']}/write", json={"slot": 4}).json()
    assert answer["reason"] == "Project64 is not running a game. Start the game, then press again."


def test_only_a_listed_package_and_a_known_build_are_accepted(lab):
    client, _harness = lab
    assert build(client, package="../../secret.sm64scene").status_code == 404
    assert client.post("/api/scene-lab/moment", json={"frame": 1}).status_code == 422
    assert client.post("/api/scene-lab/moment/nope/write", json={"slot": 1}).status_code == 404
    built = build(client).json()
    for slot in (0, 10):
        assert client.post(f"/api/scene-lab/moment/{built['id']}/write", json={"slot": slot}).status_code == 422


def test_a_growing_attempt_scene_is_refused_with_its_reason(lab, tmp_path, monkeypatch):
    client, _harness = lab
    scenes = client.app.state.scene_lab.scenes
    monkeypatch.setattr(scenes, "path", lambda _attempt: (tmp_path / "a", tmp_path / "a.grow"))
    answer = client.post("/api/scene-lab/moment", json={"attempt": 3, "frame": 1})
    assert answer.status_code == 409 and answer.json()["detail"] == scene_moments.GROWING


def test_only_a_few_builds_are_kept(lab):
    client, _harness = lab
    tokens = [build(client, frame=frame).json()["id"] for frame in range(scene_moments.KEEP_BUILT + 1)]
    assert client.post(f"/api/scene-lab/moment/{tokens[0]}/write", json={"slot": 1}).status_code == 404
    assert client.post(f"/api/scene-lab/moment/{tokens[-1]}/write", json={"slot": 1}).status_code == 200


def test_a_build_leaves_the_event_loop_free(tmp_path):
    """The loop only awaits: a stand-in that takes half a second must not delay a timer by anything like that."""
    moments = scene_moments.SceneMoments(None)
    ran = threading.Event()

    def slow():
        time.sleep(0.5)
        ran.set()

    async def scenario():
        late = 0.0
        moments.builder = lambda *_args: (slow(), ({"refused": "x", "reason": "y"}, None))[1]
        worst = asyncio.create_task(watch())
        await asyncio.to_thread(moments.builder, None, None, 0, None)
        late = await worst
        return late

    async def watch():
        worst = 0.0
        while not ran.is_set():
            before = time.perf_counter()
            await asyncio.sleep(0.01)
            worst = max(worst, time.perf_counter() - before)
        return worst

    assert asyncio.run(scenario()) < 0.25


def test_the_child_is_hidden_answers_one_line_and_its_failure_is_a_sentence(tmp_path, monkeypatch):
    """run_builder through a real process: a stand-in script in place of the simulator."""
    script = tmp_path / "stand_in.py"
    script.write_text("import json, sys\n"
                      "out = sys.argv[sys.argv.index('--out') + 1]\n"
                      "open(out, 'wb').write(b'slot')\n"
                      "print('noise')\n"
                      "print(json.dumps({'frame': int(sys.argv[sys.argv.index('--frame') + 1])}))\n", encoding="utf-8")

    def child_of(_module, _flag, args, **_options):
        return [sys.executable, str(script), *args], {"PATH": ""}
    monkeypatch.setattr("sm64_events.core.childproc.python_child", child_of)
    config = type("Config", (), {"rom": tmp_path / "rom", "map": tmp_path / "map"})
    seen = []
    answer, data = scene_moments.run_builder(config, tmp_path / "scene", 41, seen.append)
    assert answer == {"frame": 41} and data == b"slot"
    assert seen[-1] is None and seen[0].pid != 0

    script.write_text("import sys\nprint('broke', file=sys.stderr)\nsys.exit(3)\n", encoding="utf-8")
    answer, data = scene_moments.run_builder(config, tmp_path / "scene", 41)
    assert data is None and answer["reason"] == scene_moments.NO_BUILDER


# --- moment_slot: what needs no ROM ------------------------------------------------------------------------------

def moment(index, timer, level=9, area=1, task=None, spans=()):
    return moment_slot.Moment(index, index if task is None else task, timer, level, area, list(spans), np.zeros(4, np.uint8))


@pytest.mark.parametrize(("later", "expected"), [
    (moment(6, 101), True),
    (moment(6, 101, level=16), False),
    (moment(6, 101, area=2), False),
    (moment(6, 102), False),             # the game's clock jumped: a load or a reset
    (moment(6, 50), False),
    (moment(6, 101, task=7), False),     # a task the recording lost
])
def test_only_the_next_frame_in_the_same_place_can_be_simulated(later, expected):
    assert moment_slot.next_frame(moment(5, 100), later) is expected


def test_a_recording_covers_a_span_only_when_one_piece_holds_all_of_it():
    held = moment(1, 1, spans=[(0x100, np.zeros(0x20, np.uint8))])
    assert moment_slot.covers(held, 0x100, 0x120) and moment_slot.covers(held, 0x110, 0x114)
    assert not moment_slot.covers(held, 0x11C, 0x124) and not moment_slot.covers(held, 0x90, 0x94)


def test_the_base_image_holds_the_rom_segments_and_usamunes_code_and_nothing_else(tmp_path):
    rom = bytes(i % 251 for i in range(0x4000))
    map_path = tmp_path / "map"
    map_path.write_text("  0x0000000080246000   _mainSegmentStart = ADDR (.main)\n"
                        "  0x0000000000001000   _mainSegmentRomStart = __romPos\n"
                        "  0x0000000000001100   _mainSegmentRomEnd = (__romPos + SIZEOF (.main))\n"
                        "  0x0000000080378800   _engineSegmentStart = ADDR (.engine)\n"
                        "  0x0000000000001100   _engineSegmentRomStart = __romPos\n"
                        "  0x0000000000001180   _engineSegmentRomEnd = (__romPos + SIZEOF (.engine))\n", encoding="utf-8")
    ram = moment_slot.base_image(rom, map_path, [(0x80400000, 0x40, 0x2000)])
    assert ram[0x246000:0x246100].tobytes() == rom[0x1000:0x1100]
    assert ram[0x378800:0x378880].tobytes() == rom[0x1100:0x1180]
    assert ram[0x400000:0x400040].tobytes() == rom[0x2000:0x2040]
    assert int(np.count_nonzero(ram)) <= 0x100 + 0x80 + 0x40


def test_a_refusal_is_a_sentence_for_every_code():
    for code in moment_slot.WORDS:
        words = str(moment_slot.Refused(code))
        assert words.endswith(".") and "0x" not in words and len(words.split()) <= 25


def test_no_frame_before_the_first_is_refused_before_anything_runs():
    with pytest.raises(moment_slot.Refused) as first:
        moment_slot.build(None, b"", Path("unused"), 0, 0)
    assert first.value.code == "no-previous-frame"


def test_a_frame_the_recording_kept_no_state_for_is_refused_before_anything_runs():
    class Bare:
        def __len__(self):
            return 10

        def moment(self, index):
            return moment(index, 100 + index)

    with pytest.raises(moment_slot.Refused) as bare:
        moment_slot.build(Bare(), b"", Path("unused"), 4, 0)
    assert bare.value.code == "no-state"


def test_a_frame_after_a_jump_is_refused_before_anything_runs():
    class Jumps:
        def __len__(self):
            return 10

        def moment(self, index):
            return moment(index, 1000 * index)

    with pytest.raises(moment_slot.Refused) as jumped:
        moment_slot.build(Jumps(), b"", Path("unused"), 4, 0)
    assert jumped.value.code == "jump"
