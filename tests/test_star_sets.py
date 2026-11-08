"""Star sets: the course default and each star's save states, written as he moves between courses and stars.

Griffin's words (2026-11-08) are in slotlibrary/keeper.py. Everything here runs against the game-memory stand-in
(test_slot_library.FakeGame: Usamune US standing in Bob-omb Battlefield, course 1); nothing attaches to a process.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import test_slot_library as libfakes  # noqa: E402
import test_slot_relocate as world  # noqa: E402
from sm64_events.slotlibrary import refit, usamune  # noqa: E402
from sm64_events.slotlibrary.coursesets import CourseSets  # noqa: E402
from sm64_events.slotlibrary.keeper import Context, SetKeeper  # noqa: E402
from sm64_events.slotlibrary.store import Damaged, SlotLibrary, state_id  # noqa: E402

BOB, WF = 1, 2                       # course numbers; the stand-in game stands in BoB (level 9)
make_slot, keep = libfakes.make_slot, libfakes.keep
JPEG = b"\xff\xd8\xff\xe0" + bytes(16) + b"\xff\xd9"
LOADED = SimpleNamespace(type="state_loaded", payload={"load_signature": "igt_rewound"})


@pytest.fixture
def game():
    return libfakes.FakeGame()


@pytest.fixture
def library(tmp_path):
    return SlotLibrary(tmp_path / "library")


@pytest.fixture
def sets(tmp_path):
    return CourseSets(tmp_path / "library" / "course-sets.json")


class Target:
    """The practice target as the tracker holds it: ("star", course, star) or None."""

    def __init__(self, value=None):
        self.value = value

    def __call__(self):
        return self.value


def pick(course: int, star: int) -> SimpleNamespace:
    return SimpleNamespace(type="target_changed", payload={"course_id": course, "star_id": star})


def grab(course: int, star: int) -> SimpleNamespace:
    return SimpleNamespace(type="star_collected", payload={"course_id": course, "star_id": star})


def stocked(game, library, sets, target=None):
    """A keeper that has taken stock of the slots, as the server's first watch does."""
    keeper = SetKeeper(library, sets, None, target=target or Target())
    keeper.watch(game)
    return keeper


# --- which set applies -------------------------------------------------------------------------------------

def test_entering_a_course_writes_its_default_set_and_leaves_every_other_slot(game, library, sets):
    a, b = keep(library, game, make_slot(0xA1)), keep(library, game, make_slot(0xA3))
    sets.put(library, BOB, None, 1, a.id)
    sets.put(library, BOB, None, 3, b.id)
    game.save(5, make_slot(0xC6))                           # his own save in slot 6
    keeper = stocked(game, library, sets)
    report = keeper.apply(game)
    assert report.context == Context(BOB, None) and report.written == [1, 3] and report.emptied == []
    assert (game.slot(0), game.slot(2), game.slot(5)) == (make_slot(0xA1), make_slot(0xA3), make_slot(0xC6))
    writes = len(game.writes)
    assert keeper.apply(game) is None and len(game.writes) == writes, "the same context writes nothing again"


def test_the_targets_star_in_this_course_is_taken_on_entry(game, library, sets):
    star = keep(library, game, make_slot(0xB1))
    sets.put(library, BOB, 3, 1, star.id)
    keeper = stocked(game, library, sets, Target(("star", BOB, 3)))
    assert keeper.apply(game).context == Context(BOB, 3)
    assert game.slot(0) == make_slot(0xB1)
    elsewhere = stocked(game, library, sets, Target(("star", WF, 3)))
    assert elsewhere.apply(game).context == Context(BOB, None), "a target in another course is not this course's"


def test_a_star_pick_overrides_the_default_slot_by_slot(game, library, sets):
    a, b, star = (keep(library, game, make_slot(fill)) for fill in (0xA1, 0xA3, 0xB1))
    sets.put(library, BOB, None, 1, a.id)
    sets.put(library, BOB, None, 3, b.id)
    sets.put(library, BOB, 3, 1, star.id)                   # star 3 has its own slot 1 only
    keeper = stocked(game, library, sets)
    keeper.apply(game)
    assert keeper.observe(pick(BOB, 3))
    report = keeper.apply(game)
    assert report.context == Context(BOB, 3) and report.written == [1, 3]
    assert (game.slot(0), game.slot(2)) == (make_slot(0xB1), make_slot(0xA3))
    view = keeper.view()
    context = view["context"]
    assert (context["course_id"], context["course"], context["star_id"]) == (BOB, "Bob-omb Battlefield", 3)
    assert context["star"]
    assert [entry["set"] for entry in view["slots"][:3]] == ["star", None, "course"]
    assert view["slots"][0]["state"]["id"] == star.id and view["slots"][0]["under"]["id"] == a.id
    assert view["slots"][0]["state"]["place"] == "Bob-omb Battlefield" and len(view["slots"]) == 9


def test_a_star_grab_switches_and_another_stars_slots_are_emptied(game, library, sets):
    a, three, five = (keep(library, game, make_slot(fill)) for fill in (0xA1, 0xB4, 0xB5))
    sets.put(library, BOB, None, 1, a.id)
    sets.put(library, BOB, 3, 4, three.id)
    sets.put(library, BOB, 5, 2, five.id)
    game.save(5, make_slot(0xC6))                           # his own save: no set names it
    keeper = stocked(game, library, sets, Target(("star", BOB, 3)))
    keeper.apply(game)
    assert game.slot(3) == make_slot(0xB4)
    keeper.observe(grab(BOB, 5))
    report = keeper.apply(game)
    assert report.context == Context(BOB, 5) and report.emptied == [4] and report.written == [1, 2]
    assert game.read(usamune.slot_address(3) + usamune.TRAILER_MARIO, 4) == bytes(4), "star 3's slot reads empty"
    assert game.slot(1) == make_slot(0xB5) and game.slot(0) == make_slot(0xA1)
    assert game.slot(5) == make_slot(0xC6), "his own save is left alone"


def test_a_pick_or_grab_in_another_course_does_not_switch_this_one(game, library, sets):
    keeper = stocked(game, library, sets)
    keeper.apply(game)
    keeper.observe(pick(WF, 2))
    assert keeper.apply(game) is None and keeper.context == Context(BOB, None)
    assert not keeper.observe(SimpleNamespace(type="target_changed", payload={"kind": "segment", "segment_id": 4}))


def test_nothing_is_written_while_no_set_names_anything(game, library, sets):
    game.save(2, make_slot(0xC3))
    keeper = stocked(game, library, sets)
    report = keeper.apply(game)
    assert report.written == [] and report.emptied == [] and game.writes == []


# --- his saves join the set he is in ----------------------------------------------------------------------

def test_his_new_save_joins_the_current_set_at_its_slot(game, library, sets):
    game.save(0, make_slot(0xC1))                           # there before: taken stock of, never assigned
    keeper = stocked(game, library, sets, Target(("star", BOB, 3)))
    keeper.apply(game)
    game.save(6, make_slot(0xE7))
    assigned = keeper.watch(game)
    assert [slot for slot, _state in assigned] == [7]
    assert sets.slots(BOB, 3) == {7: state_id(make_slot(0xE7))} and sets.slots(BOB, None) == {}
    assert keeper.watch(game) == [], "the same bytes are not a second save"


def test_the_trainers_own_writes_are_not_his_saves(game, library, sets):
    """Neither a set write nor a refit after a reset: both make bytes the library has never seen."""
    world.live_world(game)
    state = keep(library, game, world.make_slot())
    sets.put(library, BOB, None, 1, state.id)
    keeper = stocked(game, library, sets)
    keeper.apply(game)
    assert keeper.watch(game) == []
    world.live_world(game, live=world.OBJ(77))              # a practice reset moved Mario
    assert [o.outcome for o in refit.refit_slots(game, keeper.ledger).slots] == [refit.REFIT]
    assert keeper.watch(game) == [] and sets.slots(BOB, None) == {1: state.id}
    assert len(library.scan().states) == 1


def test_a_save_of_another_level_is_kept_but_joins_no_set_here(game, library, sets):
    keeper = stocked(game, library, sets)
    keeper.apply(game)
    game.save(2, make_slot(0xE3, level=24))                 # a Whomp's Fortress state, written while in BoB
    assert keeper.watch(game) == [] and sets.courses() == {}
    assert library.has(state_id(make_slot(0xE3, level=24)))


def test_a_ghost_lab_write_joins_the_watched_ghosts_star(game, library, sets):
    keeper = stocked(game, library, sets)
    keeper.apply(game)
    state = keep(library, game, make_slot(0xD2))
    game.save(1, make_slot(0xD2))
    assert keeper.claim(1, state.id, game.slot(1), BOB, 4) == "star"
    assert sets.slots(BOB, 4) == {2: state.id} and keeper.watch(game) == []
    assert keeper.claim(2, state.id, game.slot(1), None, None) == "course", "no star named: the current context"


# --- across resets ----------------------------------------------------------------------------------------

def test_a_refit_after_a_reset_keeps_the_slot_its_set_state(game, library, sets):
    world.live_world(game)                                  # the game keeps Mario at world.LIVE
    state = keep(library, game, world.make_slot())          # saved with Mario elsewhere
    sets.put(library, BOB, 3, 5, state.id)
    keeper = stocked(game, library, sets, Target(("star", BOB, 3)))
    refitter = refit.SlotRefitter(lambda: game, library, keeper=keeper, steady_seconds=0)
    first = refitter.run_once()
    assert keeper.applied == Context(BOB, 3) and first.not_ready is None
    world.live_world(game, live=world.OBJ(77))              # a practice reset moved Mario
    second = refitter.run_once(("practice_reset",))
    assert [(o.slot, o.outcome) for o in second.slots] == [(4, refit.REFIT)]
    assert keeper.ledger.state_in(4, game.slot(4)) == state.id, "the refit bytes are still that set's state"
    assert len(library.scan().states) == 1, "a refit adds no kept state"
    keeper.observe(pick(BOB, 6))
    assert refitter.run_once(("target_changed",)) and keeper.applied == Context(BOB, 6)
    assert game.read(usamune.slot_address(4) + usamune.TRAILER_MARIO, 4) == bytes(4)


def test_the_worker_wakes_for_a_star_pick_only_with_a_keeper(game, library, sets):
    bare = refit.SlotRefitter(lambda: game, library)
    bare.observe(pick(BOB, 3))
    assert bare._due is None
    keeper = SetKeeper(library, sets, None)
    worker = refit.SlotRefitter(lambda: game, library, keeper=keeper)
    worker.observe(pick(BOB, 3))
    assert worker._due is not None


# --- what the strip shows of each slot ----------------------------------------------------------------------

def test_the_context_names_the_practice_targets_key(game, library, sets):
    from sm64_events.server.scene_ghosts import Target as GhostTarget
    keeper = stocked(game, library, sets)
    keeper.apply(game)
    assert keeper.view()["context"]["entity_key"] is None, "the course default is no star"
    keeper.observe(pick(BOB, 3))
    keeper.apply(game)
    assert keeper.view()["context"]["entity_key"] == "1-3" == GhostTarget("star", course_id=1, star_id=3).entity_key


def loaded_slots(keeper) -> list[int]:
    return [entry["slot"] for entry in keeper.view()["slots"] if entry["loaded_last"]]


def test_the_slot_he_loaded_last_is_marked_while_it_holds_that_state_in_this_context(game, library, sets):
    a, b, star = (keep(library, game, make_slot(fill)) for fill in (0xA1, 0xA3, 0xB1))
    sets.put(library, BOB, None, 1, a.id)
    sets.put(library, BOB, None, 3, b.id)
    sets.put(library, BOB, 5, 1, star.id)
    told = []
    keeper = SetKeeper(library, sets, None, target=Target(), on_change=told.append)
    keeper.watch(game)
    keeper.apply(game)
    selector = usamune.US.settings + usamune.LOAD_SLOT
    game.poke(selector, bytes([2]))                                 # Usamune's load read slot 3
    assert keeper.observe(LOADED) is False, "a load wakes no pass: the next watch reads the slot"
    assert loaded_slots(keeper) == []
    told.clear()
    keeper.watch(game)
    assert loaded_slots(keeper) == [3] and told and loaded_slots_of(told[-1]) == [3]
    game.poke(selector, bytes([0]))                                 # he moved the menu's slot, loaded nothing
    keeper.watch(game)
    assert loaded_slots(keeper) == [3]
    keeper.observe(grab(BOB, 5))
    keeper.apply(game)
    assert loaded_slots(keeper) == [], "another context: he has loaded nothing here yet"
    keeper.observe(LOADED)
    game.poke(selector, bytes([0]))
    keeper.watch(game)
    assert loaded_slots(keeper) == [1]
    game.save(0, make_slot(0xE1))                                   # his own save over the slot he loaded
    keeper.watch(game)
    assert sets.slots(BOB, 5)[1] == state_id(make_slot(0xE1)) and loaded_slots(keeper) == []


def loaded_slots_of(view: dict) -> list[int]:
    return [entry["slot"] for entry in view["slots"] if entry["loaded_last"]]


def test_a_state_made_from_a_frame_shows_that_frames_picture(game, library, sets):
    state = keep(library, game, make_slot(0xB1))
    sets.put(library, BOB, None, 1, state.id)
    keeper = stocked(game, library, sets)
    keeper.apply(game)
    assert [entry["picture"] for entry in keeper.view()["slots"]] == [None] * 9
    library.put_picture(state.id, JPEG)
    assert keeper.view()["slots"][0]["picture"] == f"/api/scene-lab/slot-sets/pictures/{state.id}"
    assert library.picture_path(state.id).read_bytes() == JPEG
    with pytest.raises(ValueError):
        library.put_picture(state.id, b"\x89PNG\r\n")
    with pytest.raises(usamune.Refusal):
        library.put_picture("0" * 64, JPEG)


def test_the_open_scenes_picture_of_a_package_frame(tmp_path):
    """SceneLab.frame_picture: the open scene's row whose package frame it is, the recorded camera, a JPEG."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from fastapi import FastAPI

    from sm64_events.server import scene_lab_api

    class Session:
        frames = [10, 11, 12]                     # the package frame of each row

        def __init__(self):
            self.begun = []

        def begin(self, *request):
            self.begun.append(request)
            return "pending"

        def finish(self, pending, share=None):
            return {"encoding": "jpeg"}, JPEG

    config = tmp_path / "lab.json"
    config.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(tmp_path / "packages"),
                                  "map": str(tmp_path / "map.txt"), "lab_root": str(tmp_path / "root")}),
                      encoding="utf-8")
    lab = scene_lab_api.mount_scene_lab(FastAPI(), config)
    session, package = Session(), tmp_path / "packages" / "a.sm64scene"
    lab.worker, lab.delivery = ThreadPoolExecutor(1), ThreadPoolExecutor(1)
    lab.session, lab.open_path, lab.generation = session, package, 4
    try:
        assert asyncio.run(lab.frame_picture(package, 11)) == JPEG
        assert session.begun == [(1, None, "jpeg", True, True)], "row 1 holds package frame 11"
        assert asyncio.run(lab.frame_picture(package, 99)) is None
        assert asyncio.run(lab.frame_picture(tmp_path / "packages" / "b.sm64scene", 11)) is None
        assert len(session.begun) == 1
    finally:
        lab.session = lab.open_path = None
        lab.worker.shutdown()
        lab.delivery.shutdown()


# --- the page's edits ---------------------------------------------------------------------------------------

def test_a_slot_moves_between_the_star_and_the_course_default(game, library, sets):
    star = keep(library, game, make_slot(0xB1))
    sets.put(library, BOB, 3, 1, star.id)
    keeper = stocked(game, library, sets, Target(("star", BOB, 3)))
    keeper.apply(game)
    payload = keeper.move(1, "course")
    assert sets.slots(BOB, None) == {1: star.id} and sets.slots(BOB, 3) == {}
    assert payload["slots"][0]["set"] == "course" and keeper.applied is None
    keeper.move(1, "star")
    assert sets.slots(BOB, 3) == {1: star.id} and sets.slots(BOB, None) == {}
    keeper.remove(1, "star")
    assert sets.courses() == {}
    with pytest.raises(LookupError):
        keeper.remove(1, "star")


def test_moving_to_the_star_needs_a_star(game, library, sets):
    a = keep(library, game, make_slot(0xA1))
    sets.put(library, BOB, None, 1, a.id)
    keeper = stocked(game, library, sets)
    keeper.apply(game)
    with pytest.raises(usamune.Refusal, match="no star"):
        keeper.move(1, "star")


def test_the_routes_answer_the_frozen_shape(game, library, sets, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from sm64_events.server import scene_lab_api
    from sm64_events.server.broadcaster import Broadcaster
    star = keep(library, game, make_slot(0xB1))
    sets.put(library, BOB, 3, 1, star.id)
    config = tmp_path / "lab.json"
    (tmp_path / "packages").mkdir()
    config.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(tmp_path / "packages"),
                                  "map": str(tmp_path / "map.txt"), "lab_root": str(tmp_path / "root")}),
                      encoding="utf-8")
    app = FastAPI()
    lab = scene_lab_api.mount_scene_lab(app, config)
    tracker = SimpleNamespace(broadcaster=Broadcaster(), target=("star", BOB, 3))
    lab.follow_spawns(tracker, game=lambda: game, library=library, log_file=tmp_path / "refit.jsonl")
    client = TestClient(app)
    assert client.get("/api/scene-lab/slot-sets").json()["context"] is None, "no pass has read the game yet"
    lab.refitter.run_once()
    body = client.get("/api/scene-lab/slot-sets").json()
    assert set(body) == {"context", "game", "slots"} and body["game"] == {"build": "us"}
    assert set(body["context"]) == {"course_id", "course", "star_id", "star", "entity_key"}
    assert body["context"]["entity_key"] == "1-3"
    assert set(body["slots"][0]) == {"slot", "set", "state", "translated", "under", "picture", "loaded_last"}
    assert body["slots"][0]["set"] == "star" and body["slots"][0]["translated"] is None   # rom "test": no build
    assert body["slots"][0]["picture"] is None and body["slots"][0]["loaded_last"] is False
    library.put_picture(star.id, JPEG)
    url = client.get("/api/scene-lab/slot-sets").json()["slots"][0]["picture"]
    shown = client.get(url)
    assert shown.status_code == 200 and shown.content == JPEG and shown.headers["content-type"] == "image/jpeg"
    assert client.get("/api/scene-lab/slot-sets/pictures/" + "0" * 64).status_code == 404
    assert client.get("/api/scene-lab/slot-sets/pictures/not-an-id").status_code == 404
    moved = client.post("/api/scene-lab/slot-sets/1/move", json={"to": "course"})
    assert moved.status_code == 200 and moved.json()["slots"][0]["set"] == "course"
    assert client.post("/api/scene-lab/slot-sets/1/move", json={"to": "sideways"}).status_code == 422
    assert client.delete("/api/scene-lab/slot-sets/1?set=course").json()["slots"][0]["set"] is None
    assert client.delete("/api/scene-lab/slot-sets/1?set=course").status_code == 404


# --- the file ------------------------------------------------------------------------------------------------

def test_the_sets_file_is_versioned_and_a_version_1_file_is_read(tmp_path, library, game):
    a, b = keep(library, game, make_slot(0xA1)), keep(library, game, make_slot(0xA2))
    path = tmp_path / "sets.json"
    path.write_text(json.dumps({"version": 1, "sets": [
        {"level": 9, "star": None, "states": [a.id, b.id]}, {"level": 9, "star": "3", "states": [b.id]},
        {"level": 6, "star": None, "states": [a.id]}, {"level": 9, "star": "wild-blue", "states": [a.id]}]}),
        encoding="utf-8")
    sets = CourseSets(path)
    assert sets.resolved(BOB, 3) == {1: (b.id, "star"), 2: (b.id, "course")}
    sets.put(library, BOB, None, 9, a.id)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["version"] == 2 and stored["courses"]["1"]["default"] == {"1": a.id, "2": b.id, "9": a.id}


def test_an_unreadable_sets_file_is_never_overwritten(tmp_path, library, game):
    a = keep(library, game, make_slot(0xA1))
    path = tmp_path / "sets.json"
    path.write_text('{"version": 2, "courses": {"1": {"default": {"10": "x"}, "stars": {}}}}', encoding="utf-8")
    with pytest.raises(Damaged):
        CourseSets(path).put(library, BOB, None, 1, a.id)
    assert "\"10\"" in path.read_text(encoding="utf-8")


def test_a_set_names_only_kept_states_and_real_slots(tmp_path, library):
    sets = CourseSets(tmp_path / "sets.json")
    with pytest.raises(usamune.Refusal):
        sets.put(library, BOB, None, 10, "0" * 64)
    with pytest.raises(usamune.Refusal):
        sets.put(library, BOB, None, 1, "0" * 64)
    assert not sets.path.exists()


def test_the_stand_in_game_is_course_one():
    assert struct.unpack(">h", libfakes.FakeGame().read(usamune.US.curr_level, 2))[0] == 9


def test_a_ghost_lab_write_through_the_route_joins_the_named_star_set(tmp_path):
    """The real moment routes, a stand-in builder, the stand-in game: the written state joins (course, star)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import test_handoff_try as handoff_fakes
    import test_scene_moments as moments_test
    from sm64_events.server import scene_lab_api
    from sm64_events.server.broadcaster import Broadcaster
    game = handoff_fakes.FakeGame()
    library = SlotLibrary(tmp_path / "library")
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / moments_test.PACKAGE).write_bytes(b"not read here")
    config = tmp_path / "lab.json"
    config.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(tmp_path / "packages"),
                                  "map": str(tmp_path / "map.txt"), "lab_root": str(tmp_path / "root")}),
                      encoding="utf-8")
    answer = {**moments_test.ANSWER, "fingerprint": usamune.routine_fingerprint(game.read, usamune.US)}
    app = FastAPI()
    lab = scene_lab_api.mount_scene_lab(app, config, moment_options={
        "builder": lambda _c, _p, _f, register=lambda child: None: (answer, moments_test.slot_bytes()),
        "game": lambda: game, "library": library, "backups": tmp_path / "backups"})
    lab.follow_spawns(SimpleNamespace(broadcaster=Broadcaster(), target=None), game=lambda: game, library=library,
                      log_file=tmp_path / "refit.jsonl")
    lab.refitter.run_once()
    client = TestClient(app)
    asked = []

    async def drawn(path, frame):                 # the Scene viewer has this package open
        asked.append((Path(path).name, frame))
        return JPEG

    lab.frame_picture = drawn
    built = client.post("/api/scene-lab/moment", json={"package": moments_test.PACKAGE, "frame": 7}).json()
    written = client.post(f"/api/scene-lab/moment/{built['id']}/write",
                          json={"slot": 3, "course_id": BOB, "star_id": 4}).json()
    assert written["written"] and written["set"] == "star"
    assert asked == [(moments_test.PACKAGE, 7)]
    assert written["picture"] == f"/api/scene-lab/slot-sets/pictures/{written['kept']}"
    assert library.picture_path(written["kept"]).read_bytes() == JPEG
    sets = CourseSets(library.root / "course-sets.json")
    assert sets.slots(BOB, 4) == {3: written["kept"]}
    assert lab.refitter.watch_once() == [], "the slot worker does not take the Ghost Lab's write for his save"
    assert client.get("/api/scene-lab/slot-sets").json()["context"]["star_id"] is None
