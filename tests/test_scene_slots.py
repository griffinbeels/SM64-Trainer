"""The Ghost Lab's save-state strip (server/scene_slots.py): what Usamune's nine slots hold, read only, over a fake
game; the picture a Lab write keeps; the last load.

No process is attached and nothing is written outside tmp_path: the game is test_handoff_try's FakeGame (big-endian
RAM that logs every write), the Slot Library a tmp one. "loaded" is proved here with fakes only; it needs his live
check (load a slot in Usamune, see it marked).
"""
import base64
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
sys.path.insert(0, str(ROOT / "tests"))
import test_handoff_try as fakes  # noqa: E402
from test_scene_moments import PACKAGE, build, lab  # noqa: E402,F401 - `lab` is the routes' fixture
from sm64_events.memory.addresses import MARIO_ACTION_OFF, MARIO_POS_OFF  # noqa: E402
from sm64_events.server import scene_slots  # noqa: E402
from sm64_events.slotlibrary import usamune  # noqa: E402
from sm64_events.slotlibrary.refit import RefitReport  # noqa: E402
from sm64_events.slotlibrary.store import CAPTURED, SlotLibrary  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + bytes(range(200)) + b"\xff\xd9"
LONG_JUMP, IDLE, DIVE = 0x03000888, 0x0C400201, 0x0188088A


def slot_data(*, level=fakes.LEVEL, area=fakes.AREA, mario=fakes.MARIO, fill=0xCD, action=IDLE,
              place=(0.0, 0.0, 0.0)) -> bytes:
    """One occupied slot: its body's Mario (action, place) and its trailer (gMarioObject, level, area)."""
    data = bytearray(bytes([fill]) * usamune.SLOT_BYTES)
    struct.pack_into(">I", data, MARIO_ACTION_OFF, action)
    struct.pack_into(">3f", data, MARIO_POS_OFF, *place)
    struct.pack_into(">I", data, usamune.TRAILER_MARIO, mario)
    struct.pack_into(">hh", data, usamune.TRAILER_LEVEL, level, area)
    return bytes(data)


def put(game, slot: int, data: bytes) -> None:
    game.poke(usamune.slot_address(slot - 1), data)


def strip(tmp_path, game, **options) -> scene_slots.SceneSlots:
    return scene_slots.SceneSlots(game=lambda: game, library=options.pop("library", SlotLibrary(tmp_path / "library")),
                                  root=tmp_path / "root", **options)


def by_slot(answer: dict) -> dict:
    return {row["slot"]: row for row in answer["slots"]}


# ---------------------------------------------------------------- what each slot holds


def test_every_slot_reads_as_empty_or_saved_with_its_place_and_nothing_is_written(tmp_path):
    game = fakes.FakeGame()
    put(game, 1, slot_data())
    put(game, 3, slot_data(level=16, area=1))
    answer = strip(tmp_path, game).read_blocking()
    rows = by_slot(answer)
    assert (answer["game"], answer["reason"], answer["loaded"], len(answer["slots"])) == ("us", None, None, 9)
    assert rows[1] == {"slot": 1, "state": "saved", "level": fakes.LEVEL, "area": fakes.AREA,
                       "place": "Bob-omb Battlefield", "version": "us", "translated": False, "picture": None,
                       "frame": None, "mine": False}
    assert rows[3]["place"] == "Castle Grounds"
    assert [row["state"] for row in answer["slots"]] == ["saved", "empty", "saved"] + ["empty"] * 6
    assert game.writes == []


def test_the_strips_reader_refuses_every_write(tmp_path):
    game = fakes.FakeGame()
    slots = strip(tmp_path, game)
    with pytest.raises(PermissionError):
        slots._reader().write(usamune.slot_address(0), b"\0\0\0\0")
    assert game.writes == []


@pytest.mark.parametrize(("problem", "sentence"), [
    (lambda game: fakes.no_rom(game), scene_slots.NO_GAME),
    (lambda game: fakes.vanilla_rom(game), "The running game is not Usamune."),
    (lambda game: fakes.four_meg_console(game), "The game runs without the 8 MB of RAM that Usamune's save states need."),
])
def test_a_game_that_cannot_be_read_is_one_sentence_and_nine_unknown_slots(tmp_path, problem, sentence):
    game = fakes.FakeGame()
    put(game, 1, slot_data())
    problem(game)
    answer = strip(tmp_path, game).read_blocking()
    assert (answer["game"], answer["reason"], answer["loaded"]) == (None, sentence, None)
    assert {row["state"] for row in answer["slots"]} == {"unknown"} and all(row["version"] is None for row in answer["slots"])


def test_no_project64_and_a_game_gone_mid_read_say_it_is_not_running(tmp_path):
    def gone():
        raise usamune.Refusal("no Project64 with a running game found")
    slots = scene_slots.SceneSlots(game=gone, library=None, root=tmp_path / "root")
    assert slots.read_blocking()["reason"] == scene_slots.NO_GAME

    class Vanishing(fakes.FakeGame):
        def read(self, address, size):
            if size == usamune.SLOT_BYTES:
                raise OSError("the process is gone")
            return super().read(address, size)
    answer = strip(tmp_path, Vanishing()).read_blocking()
    assert (answer["game"], answer["reason"]) == (None, scene_slots.NO_GAME)


# ---------------------------------------------------------------- which build made it


def test_version_is_the_lab_writes_then_the_librarys_then_the_running_games_and_none_without_a_place(tmp_path):
    game = fakes.FakeGame()
    library = SlotLibrary(tmp_path / "library")
    jp_made = slot_data(fill=0x11)
    library.add(jp_made, source=CAPTURED, fingerprint="a" * 64, rom="SM64 USAMUNE v1.93j")
    put(game, 2, jp_made)                                   # a state the library knows came from JP
    put(game, 4, slot_data(fill=0x22))                      # saved in this (US) game
    put(game, 5, slot_data(fill=0x33))
    put(game, 6, slot_data(level=99))                       # occupied, but no place in the game
    slots = strip(tmp_path, game, library=library)
    slots.note_written(game, 5, {"written": True, "build": "jp", "frame": 41,
                                 "translated": {"from": "jp", "to": "us", "words": "x"}}, JPEG)
    rows = by_slot(slots.read_blocking())
    assert (rows[2]["version"], rows[2]["mine"]) == ("jp", False)
    assert (rows[4]["version"], rows[4]["mine"], rows[4]["translated"]) == ("us", False, False)
    assert (rows[5]["version"], rows[5]["translated"], rows[5]["mine"], rows[5]["frame"]) == ("jp", True, True, 41)
    assert (rows[6]["state"], rows[6]["place"], rows[6]["version"]) == ("saved", None, None)
    assert game.writes == []


def test_a_lab_picture_shows_while_the_slot_holds_its_bytes_and_goes_after_a_save_in_the_game(tmp_path):
    game = fakes.FakeGame()
    put(game, 3, slot_data(fill=0x44))
    slots = strip(tmp_path, game)
    slots.note_written(game, 3, {"written": True, "build": "us", "frame": 7}, JPEG)
    row = by_slot(slots.read_blocking())[3]
    name = row["picture"].rsplit("/", 1)[1]
    assert row["picture"] == f"/api/scene-lab/slots/picture/{name}" and (row["mine"], row["frame"]) == (True, 7)
    assert slots.pictures.path(name).read_bytes() == JPEG
    put(game, 3, slot_data(fill=0x45))                      # he saved over it in the game
    row = by_slot(slots.read_blocking())[3]
    assert (row["picture"], row["mine"], row["frame"], row["translated"], row["version"]) == (None, False, None, False, "us")


def test_a_refit_carries_the_mark_and_the_last_load_with_the_bytes_it_moved(tmp_path):
    game = fakes.FakeGame()
    before, after = slot_data(fill=0x50, action=LONG_JUMP), slot_data(fill=0x51, action=LONG_JUMP)
    put(game, 2, before)
    slots = strip(tmp_path, game)
    slots.note_written(game, 2, {"written": True, "build": "us", "frame": 9}, JPEG)
    assert slots.identify_load(LONG_JUMP) == 2
    put(game, 2, after)
    slots.observe_refit(RefitReport(originals=((1, before),), written=((1, after),)))
    answer = slots.read_blocking()
    assert (by_slot(answer)[2]["mine"], answer["loaded"]) == (True, 2)


def test_marks_and_the_last_load_outlive_the_server(tmp_path):
    game = fakes.FakeGame()
    put(game, 8, slot_data(fill=0x60, action=DIVE))
    first = strip(tmp_path, game)
    first.note_written(game, 8, {"written": True, "build": "us", "frame": 3}, JPEG)
    first.identify_load(DIVE)
    again = by_slot(strip(tmp_path, game).read_blocking())
    assert again[8]["mine"] and again[8]["picture"] and strip(tmp_path, game).read_blocking()["loaded"] == 8


# ---------------------------------------------------------------- the page's picture


@pytest.mark.parametrize(("sent", "kept"), [
    (base64.b64encode(JPEG).decode(), True),
    ("data:image/jpeg;base64," + base64.b64encode(JPEG).decode(), True),
    ("data:image/png;base64," + base64.b64encode(JPEG).decode(), False),
    (base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(100) + b"\xff\xd9").decode(), False),   # not a JPEG's start
    (base64.b64encode(JPEG[:-2]).decode(), False),                                  # no end marker
    (base64.b64encode(b"\xff\xd8\xff" + bytes(scene_slots.PICTURE_MAX_BYTES) + b"\xff\xd9").decode(), False),
    ("not base64 at all!", False),
    ("A" * (scene_slots.PICTURE_MAX_TEXT + 1), False),
    (None, False), (12, False), ("", False),
], ids=["jpeg", "data-url", "png-url", "png", "no-end", "oversize", "garbage", "too-long", "none", "number", "empty"])
def test_only_a_small_jpeg_is_kept(sent, kept):
    assert (scene_slots.picture_bytes(sent) is not None) is kept


def test_picture_names_are_the_servers_own_and_the_newest_are_kept(tmp_path, monkeypatch):
    pictures = scene_slots.SlotPictures(tmp_path / "pictures")
    monkeypatch.setattr(scene_slots, "KEEP_PICTURES", 3)
    names = [pictures.keep(JPEG[:-2] + bytes([n]) + b"\xff\xd9") for n in range(5)]
    assert all(scene_slots.PICTURE_NAME.fullmatch(name) for name in names)
    assert [pictures.path(name) is not None for name in names] == [False, False, True, True, True]
    (tmp_path / "secret.jpg").write_bytes(b"not a picture of his")       # what a name from the page must not reach
    for bad in ("../secret", "ABC", names[-1].upper(), names[-1] + ".jpg", "0" * 64):
        assert pictures.path(bad) is None


# ---------------------------------------------------------------- the poll


def test_the_stamp_changes_only_when_the_answer_does(tmp_path):
    game = fakes.FakeGame()
    put(game, 1, slot_data())
    slots = strip(tmp_path, game)
    first, same = slots.read_blocking()["stamp"], slots.read_blocking()["stamp"]
    put(game, 9, slot_data(fill=0x70))
    changed = slots.read_blocking()["stamp"]
    assert first == same and changed != first and slots.read_blocking()["stamp"] == changed


# ---------------------------------------------------------------- the last load


def live_place(game, place) -> None:
    game.poke(usamune.US.body_start + MARIO_POS_OFF, struct.pack(">3f", *place))


def test_the_load_names_the_one_slot_with_the_games_place_mario_word_and_action(tmp_path):
    game = fakes.FakeGame()
    put(game, 1, slot_data(action=IDLE, fill=0x01))
    put(game, 2, slot_data(action=LONG_JUMP, fill=0x02))
    put(game, 3, slot_data(action=LONG_JUMP, level=16, fill=0x03))          # another place
    put(game, 4, slot_data(action=LONG_JUMP, mario=fakes.MARIO + 0x260, fill=0x04))   # Usamune's gate refuses it
    slots = strip(tmp_path, game, run=lambda work: work())
    slots.observe_event(SimpleNamespace(type="state_loaded", payload={"action": LONG_JUMP}))
    assert slots.read_blocking()["loaded"] == 2
    slots.observe_event(SimpleNamespace(type="spawned", payload={"action": IDLE}))   # not a load: nothing changes
    assert slots.read_blocking()["loaded"] == 2
    assert slots.identify_load(DIVE) is None and slots.read_blocking()["loaded"] is None
    assert game.writes == []


def test_two_slots_alike_are_told_apart_only_by_one_saved_place_near_mario(tmp_path):
    game = fakes.FakeGame()
    put(game, 1, slot_data(action=IDLE, place=(0.0, 0.0, 0.0), fill=0x01))
    put(game, 5, slot_data(action=IDLE, place=(3000.0, 0.0, 0.0), fill=0x05))
    live_place(game, (40.0, 0.0, 0.0))
    slots = strip(tmp_path, game)
    assert slots.identify_load(IDLE) == 1
    put(game, 5, slot_data(action=IDLE, place=(90.0, 0.0, 0.0), fill=0x05))    # both near: no answer
    assert slots.identify_load(IDLE) is None and slots.read_blocking()["loaded"] is None


def test_the_last_load_goes_when_its_slot_is_saved_over(tmp_path):
    game = fakes.FakeGame()
    put(game, 6, slot_data(action=DIVE, fill=0x06))
    slots = strip(tmp_path, game)
    assert slots.identify_load(DIVE) == 6 and slots.read_blocking()["loaded"] == 6
    put(game, 6, slot_data(action=DIVE, fill=0x07))
    assert slots.read_blocking()["loaded"] is None


# ---------------------------------------------------------------- the routes


def test_the_routes_show_a_lab_write_with_its_picture_and_never_write_on_a_read(lab):
    client, harness = lab
    built = build(client).json()
    sent = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode()
    answer = client.post(f"/api/scene-lab/moment/{built['id']}/write", json={"slot": 3, "picture": sent}).json()
    assert answer["written"] is True and set(answer) >= {"written", "slot", "frame", "backup", "kept", "build"}
    writes = len(harness.game.writes)
    read = client.get("/api/scene-lab/slots").json()
    assert len(harness.game.writes) == writes
    row = by_slot(read)[3]
    assert (row["state"], row["mine"], row["frame"], row["version"], row["place"]) == (
        "saved", True, 7, "us", "Bob-omb Battlefield")
    picture = client.get(row["picture"])
    assert picture.status_code == 200 and picture.content == JPEG and picture.headers["content-type"] == "image/jpeg"
    for bad in ("nope", "0" * 64, "..%2Fslot-marks.json"):
        assert client.get(f"/api/scene-lab/slots/picture/{bad}").status_code == 404


def test_a_bad_picture_never_costs_the_write(lab):
    client, _harness = lab
    built = build(client).json()
    answer = client.post(f"/api/scene-lab/moment/{built['id']}/write",
                         json={"slot": 2, "picture": base64.b64encode(b"GIF89a" + bytes(40)).decode()}).json()
    assert answer["written"] is True
    row = by_slot(client.get("/api/scene-lab/slots").json())[2]
    assert (row["mine"], row["picture"]) == (True, None)


def test_outside_the_live_server_the_strip_never_reaches_for_project64(tmp_path, monkeypatch):
    """The UI fixture mounts the lab without the live recording: its strip says no game and attaches nothing, so a
    page under test never reads his running Project64. The live server attaches."""
    import json

    from fastapi import FastAPI

    from sm64_events.server import scene_lab_api, scene_moments
    attached = []
    monkeypatch.setattr(scene_moments, "_attach_game", lambda: attached.append(1) or fakes.FakeGame())
    (tmp_path / "packages").mkdir()
    config = tmp_path / "lab.json"
    config.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(tmp_path / "packages"),
                                  "lab_root": str(tmp_path / "root")}), encoding="utf-8")
    fixture = scene_lab_api.mount_scene_lab(FastAPI(), config)
    assert fixture.moments.slots.read_blocking()["reason"] == scene_slots.NO_GAME and attached == []
    live = scene_lab_api.SceneLab(config, live=True)
    assert scene_moments.SceneMoments(live).slots.read_blocking()["game"] == "us" and attached == [1]


def test_the_live_server_feeds_the_strip_the_refits_and_the_loads(lab, tmp_path):
    """follow_spawns wires the strip: each state_loaded reaches it, and the refit tells it what it moved."""
    client, harness = lab
    scene_lab = client.app.state.scene_lab
    seen = []
    tracker = SimpleNamespace(broadcaster=SimpleNamespace(observe=seen.append))
    scene_lab.follow_spawns(tracker, game=harness.connect, library=None, log_file=tmp_path / "refit.jsonl")
    assert scene_lab.moments.slots.observe_event in seen
    assert scene_lab.refitter.on_report == scene_lab.moments.slots.observe_refit
