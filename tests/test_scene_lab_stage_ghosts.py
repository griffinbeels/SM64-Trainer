"""The scene viewer's ghosts (M4 round 4, lab only) through the session and the real routes.

The renderer host is fake_viewer_host.py (a child speaking viewer_host.c's protocol: its picture is
the sha256 of everything a request carried, ghosts block included), the package is
scene_attempt_fixture's synthetic recording with scene state, and clips are byte strings the loader
hands over. What the renderer itself draws, and its own refusals, are the native witness's
(test_ghost_viewer_native.py) and the GPU run's.
"""
import json
import struct
import sys
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import fake_viewer_host  # noqa: E402
import stage_ghosts  # noqa: E402
import gpu_gate  # noqa: E402
import scene_attempt_fixture as fixture  # noqa: E402
import scene_session  # noqa: E402
import viewer  # noqa: E402
from sm64_events.ghost.native_session import ghost_hue  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402

# Game frames of the recording's pictures: two lag frames (1003 follows 1001) and a held one.
TIMERS = [1000, 1001, 1003, 1004, 1004, 1005, 1006, 1007]
CLIP = b"SM64GHO7" + bytes(200)


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    folder = tmp_path_factory.mktemp("scene-ghosts")
    rom = bytearray(fixture.rom())
    rom[0x3E] = ord("E")                                 # a US cartridge, as the practice ROM is
    rom = bytes(rom)
    session = fixture.write_session(folder, rom, [(timer, 24, 1) for timer in TIMERS])
    out = folder / "scene.sm64scene"
    fixture.pk.write_package(fixture.pk.tap_source(session, "next", states=True), rom, out, fixture.pk.ZLIB)
    (folder / "rom.z64").write_bytes(rom)
    return {"folder": folder, "rom": folder / "rom.z64", "package": out}


@pytest.fixture
def config(recording, tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    (tmp_path / "Plugin").mkdir()
    return type("Config", (), {"rom": recording["rom"], "pj64_plugin": tmp_path / "Plugin", "renderer": "trainer",
                               "resolution": None, "map": None})()


class Loader:
    """Interface C's shape over a dict of refs ({ref: (clip, label[, time])}), counting what it was
    asked; a ref in `slow` waits for `release` first, as a community download does."""

    def __init__(self, clips: dict, slow: tuple = ()):
        self.clips, self.asked, self.slow, self.release = clips, [], set(slow), threading.Event()
        self.threads: set[str] = set()

    def __call__(self, ref):
        self.asked.append(ref)
        self.threads.add(threading.current_thread().name)
        if ref in self.slow:
            assert self.release.wait(10)
        if ref not in self.clips:
            raise LookupError("This ghost is not in the catalog any more.")
        clip, label, *time = self.clips[ref]
        return {"clip": clip, "player": label, "frames": 300, "label": label, "time": time[0] if time else None}


def _open(config, recording, **options):
    host = scene_session.command_host(fake_viewer_host.command(**options))
    return scene_session.SceneSession.open(config, recording["package"], host_factory=host)


def _decoded(block: bytes) -> tuple[tuple, list[tuple]]:
    count = struct.unpack_from("<I", block)[0]
    look = viewer.GHOST_LOOK.unpack_from(block, 4)
    entries = [viewer.GHOST_ENTRY.unpack_from(block, 4 + viewer.GHOST_LOOK.size + viewer.GHOST_ENTRY.size * at)
               for at in range(count)]
    return look, entries


def test_each_ghost_frame_counts_game_frames_from_the_stage_first_frame(config, recording):
    """Lag frames skip ghost rows and a held picture repeats one: the ghost stays in step with the
    stage's game clock, not with its pictures; an origin moves every ghost's first frame there."""
    session = _open(config, recording)
    try:
        loader = Loader({"a": (CLIP, "YOUR PB"), "b": (CLIP, "Alice")})
        assert session.set_ghosts([{"ref": "a", "label": "YOUR PB"}, {"ref": "b", "label": "Alice"}], loader) == {
            "shown": ["a", "b"], "refused": []}
        assert [stage_ghosts.ghost_frame(timer, TIMERS[0]) for timer in TIMERS] == [0, 1, 3, 4, 4, 5, 6, 7]
        for position, index in enumerate(session.frames):
            look, entries = _decoded(session._ghost_block(index))
            assert [frame for _slot, frame, _hue, _ in entries] == [TIMERS[position] - TIMERS[0]] * 2
            # Each ghost keeps its colour by its place in the set, as the game's set does.
            assert [hue for _slot, _frame, hue, _ in entries] == [ghost_hue(250, 0), ghost_hue(250, 1)]
        session.set_ghosts([{"ref": "a", "label": "x"}, {"ref": "b", "label": "x"}], loader, origin=2)
        _look, entries = _decoded(session._ghost_block(session.frames[0]))
        assert [frame for _slot, frame, _hue, _ in entries] == [-3, -3]     # before the new first frame
        assert loader.asked == ["a", "b"]                                   # nothing reloaded
    finally:
        session.close()


def test_a_scene_whose_first_task_was_never_shown_still_takes_ghosts(config, recording):
    """An attempt scene starts at its anchor's task, which no present may have shown: it is still
    every ghost's first frame, and the renderer's set-up pictures use the first one shown."""
    session = _open(config, recording)
    try:
        session.outlines[0] = {**session.outlines[0], "vi": None}       # as a task no present showed
        session.frames = session.frames[1:]
        answer = session.set_ghosts([{"ref": "a", "label": "x"}], Loader({"a": (CLIP, "x")}))
        assert answer == {"shown": ["a"], "refused": []}
        _look, entries = _decoded(session._ghost_block(session.frames[0]))
        assert entries[0][1] == TIMERS[1] - TIMERS[0]                     # still counted from task 0
    finally:
        session.close()


def test_pictures_carry_the_set_and_drop_it_exactly(config, recording):
    """A picture with ghosts names them to the host and says what it drew; one after the set is
    emptied is the very picture drawn before any ghost was added."""
    session = _open(config, recording)
    try:
        before = session.picture(3, None, "raw")
        assert "ghosts" not in before[0]
        session.set_ghosts([{"ref": "a", "label": "YOUR RUN"}], Loader({"a": (CLIP, "YOUR RUN")}))
        header, payload = session.picture(3, None, "raw")
        assert header["ghosts"] == {"asked": 1, "chosen": 1, "drawn": 1, "displayed": True, "pending": False,
                                    "refused": False}
        assert payload != before[1]
        assert session.set_ghosts([], None) == {"shown": [], "refused": []}
        after = session.picture(3, None, "raw")
        assert after[1] == before[1] and "ghosts" not in after[0]
    finally:
        session.close()


def test_a_ghost_that_cannot_be_drawn_is_refused_with_its_reason(config, recording):
    session = _open(config, recording)
    try:
        clips = {f"g{n}": (CLIP, f"P{n}") for n in range(stage_ghosts.MAX_GHOSTS + 1)}
        clips["broken"] = (b"not a ghost", "Bob")
        loader = Loader(clips)
        answer = session.set_ghosts([{"ref": "gone", "label": "x"}, {"ref": "broken", "label": "x"}]
                                    + [{"ref": f"g{n}", "label": "x"} for n in range(stage_ghosts.MAX_GHOSTS + 1)],
                                    loader)
        reasons = {item["ref"]: item["reason"] for item in answer["refused"]}
        assert reasons == {"gone": "This ghost is not in the catalog any more.", "broken": stage_ghosts.NOT_PLAYABLE,
                           f"g{stage_ghosts.MAX_GHOSTS}": stage_ghosts.TOO_MANY}
        assert len(answer["shown"]) == stage_ghosts.MAX_GHOSTS
        for bad in ("not a list", [{"label": "no ref"}], [{"ref": 5}]):
            with pytest.raises(ValueError):
                session.set_ghosts(bad, loader)
        session.package.states = None                    # a recording without scene state
        answer = session.set_ghosts([{"ref": "g0", "label": "x"}], loader)
        assert answer == {"shown": [], "refused": [{"ref": "g0", "reason": stage_ghosts.NO_SCENE_STATE}]}
    finally:
        session.close()
    session = _open(config, recording, ghosts=False)    # a renderer without the ghost table
    try:
        answer = session.set_ghosts([{"ref": "a", "label": "x"}], Loader({"a": (CLIP, "x")}))
        assert answer == {"shown": [], "refused": [{"ref": "a", "reason": stage_ghosts.NO_RENDERER}]}
    finally:
        session.close()


class _Slots:
    """A host double that keeps what each slot holds."""

    def __init__(self):
        self.slots, self.calls = {}, []

    def ghost_load(self, slot, clip, label):
        self.calls.append((slot, bool(clip), label))
        if clip:
            self.slots[slot] = label
        else:
            self.slots.pop(slot, None)
        return 0


def test_a_new_set_keeps_shown_ghosts_in_their_slots_and_empties_the_rest():
    host, ghosts = _Slots(), stage_ghosts.StageGhosts()
    loader = Loader({"a": (CLIP, "YOUR PB"), "b": (CLIP, "Alice"), "c": (CLIP, "Bob"), "bad": (CLIP, "bad#name")})
    ghosts.replace(host, [{"ref": "a"}, {"ref": "b"}], loader)
    slot_b = ghosts.shown[1].slot
    assert ghosts.replace(host, [{"ref": "b"}, {"ref": "c"}, {"ref": "bad"}], loader)["shown"] == ["b", "c", "bad"]
    assert [ghost.ref for ghost in ghosts.shown] == ["b", "c", "bad"] and ghosts.shown[0].slot == slot_b
    assert sorted(host.slots.values()) == ["Alice", "Bob", "GHOST 3"]    # the game's label rule
    assert loader.asked == ["a", "b", "c", "bad"]                       # b was not loaded again
    look = stage_ghosts.Look.of({"tint": {"enabled": False, "hue_degrees": 10}, "name_background": "white",
                              "inputs_enabled": True, "input_mode": "values", "opacity": 0.5})
    packed_look, entries = _decoded(ghosts.block(look, 12))
    assert packed_look[0] == 0.5 and packed_look[2] == (viewer.GHOST_FLAGS["shadow"] | viewer.GHOST_FLAGS["names"]
                                                        | viewer.GHOST_FLAGS["inputs"] | viewer.GHOST_FLAGS["values"]
                                                        | viewer.GHOST_FLAGS["white_name"])
    assert [(slot, frame, hue) for slot, frame, hue, _ in entries] == [
        (ghost.slot, 12, ghost_hue(10, at)) for at, ghost in enumerate(ghosts.shown)]
    ghosts.clear(host)
    assert host.slots == {} and ghosts.block(look, 0) is None


def _lab(config, recording, tmp_path, loader):
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / "scene.sm64scene").write_bytes(recording["package"].read_bytes())
    path = tmp_path / "lab.json"
    path.write_text(json.dumps({"rom": str(recording["rom"]), "packages": str(tmp_path / "packages"),
                                "pj64_plugin": str(config.pj64_plugin), "map": str(tmp_path / "MappingUS.map")}),
                    encoding="utf-8")
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, path, watch_seconds=0.05, ghost_loader=loader, session_options={
        "host_factory": scene_session.command_host(fake_viewer_host.command())})
    return app


def _picture_header(socket) -> dict:
    data = socket.receive()["bytes"]
    return json.loads(data[4:4 + struct.unpack_from("<I", data)[0]])


def test_the_socket_sets_the_ghosts_and_later_pictures_draw_them(config, recording, tmp_path):
    """Interface B over the real routes: the "ghosts" message's answer names what was shown and
    refused, and a picture asked for after it draws the set."""
    loader = Loader({"a": (CLIP, "YOUR PB")})
    with TestClient(_lab(config, recording, tmp_path, loader)) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            socket.send_json({"id": 1, "session": opened["session"], "kind": "ghosts",
                              "set": [{"ref": "a", "label": "YOUR PB"}, {"ref": "z", "label": "Zed"}]})
            reply = json.loads(socket.receive()["text"])
            assert reply == {"id": 1, "kind": "ghosts", "shown": ["a"],
                             "refused": [{"ref": "z", "reason": "This ghost is not in the catalog any more."}]}
            socket.send_json({"id": 2, "session": opened["session"], "frame": 1, "camera": None, "encoding": "raw"})
            header = _picture_header(socket)
            assert header["id"] == 2 and header["ghosts"]["drawn"] == 1
            socket.send_json({"id": 3, "session": opened["session"], "kind": "ghosts", "set": "nope"})
            assert "error" in json.loads(socket.receive()["text"])
    assert loader.threads and not any(name.startswith("scene-lab") for name in loader.threads)


def test_a_ghost_still_downloading_never_holds_up_the_pictures(config, recording, tmp_path):
    """A clip that takes its time (a community download) loads off the session's worker: pictures
    go on meanwhile, and a later set waits its turn, so the last set sent is the one shown."""
    loader = Loader({"slow": (CLIP, "Bob"), "fast": (CLIP, "Alice")}, slow=("slow",))
    with TestClient(_lab(config, recording, tmp_path, loader)) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            socket.send_json({"id": 1, "session": opened["session"], "kind": "ghosts", "set": [{"ref": "slow"}]})
            socket.send_json({"id": 2, "session": opened["session"], "kind": "ghosts", "set": [{"ref": "fast"}]})
            socket.send_json({"id": 3, "session": opened["session"], "frame": 1, "camera": None, "encoding": "raw"})
            header = _picture_header(socket)                        # while "slow" still loads
            assert header["id"] == 3 and "ghosts" not in header
            loader.release.set()
            replies = [json.loads(socket.receive()["text"]) for _ in range(2)]
            assert [reply["id"] for reply in replies] == [1, 2]
            assert replies[1]["shown"] == ["fast"]
            socket.send_json({"id": 4, "session": opened["session"], "frame": 1, "camera": None, "encoding": "raw"})
            assert _picture_header(socket)["ghosts"]["drawn"] == 1


def test_ghosts_that_would_share_a_name_add_their_times():
    """His goal: tell ghosts apart. Two of his runs both read YOUR RUN in the game; here each adds
    its time, while a name no other ghost shares stays exactly the game's."""
    host, ghosts = _Slots(), stage_ghosts.StageGhosts()
    loader = Loader({"a": (CLIP, "YOUR RUN", '11"03'), "b": (CLIP, "YOUR RUN", '11"20'), "c": (CLIP, "Alice", '10"90'),
                     "d": (CLIP, "YOUR PB", '1\'02"16'), "e": (CLIP, "YOUR PB")})
    ghosts.replace(host, [{"ref": "a"}, {"ref": "c"}], loader)
    assert [ghost.label for ghost in ghosts.shown] == ["YOUR RUN", "Alice"]
    ghosts.replace(host, [{"ref": "a"}, {"ref": "c"}, {"ref": "b"}], loader)
    assert [ghost.label for ghost in ghosts.shown] == ["YOUR RUN 11''03", "Alice", "YOUR RUN 11''20"]
    assert host.slots[ghosts.shown[0].slot] == "YOUR RUN 11''03"            # loaded again under it
    ghosts.replace(host, [{"ref": "c"}, {"ref": "b"}], loader)
    assert [ghost.label for ghost in ghosts.shown] == ["Alice", "YOUR RUN"]   # unique again: the game's
    assert host.slots[ghosts.shown[1].slot] == "YOUR RUN"
    ghosts.replace(host, [{"ref": "d"}, {"ref": "e", "time": '9"99'}], loader)  # the page's time as fallback
    assert [ghost.label for ghost in ghosts.shown] == ["YOUR PB 1'02''16", "YOUR PB 9''99"]
    long = stage_ghosts.with_time("A" * 40, '1\'02"16')
    assert long.endswith(" 1'02''16") and len(long) <= stage_ghosts.LABEL_FIT
    assert stage_ghosts.distinct_labels([("X", None), ("X", '1"00')]) == ["X", "X 1''00"]
    for label in host.slots.values():
        assert stage_ghosts.label_text(label, 1) == label                    # the game's font writes it
