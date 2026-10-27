"""The Scene viewer's camera presets (M4 round 6, item 2): the store (tools/scene_lab/camera_presets.py)
and its two routes, GET and PUT /api/scene-lab/camera-presets?level=&area= (interface F).

A preset set belongs to one level and area; the file lives in the lab folder and survives a new
store (a restart); a write replaces the file whole or not at all; a file the store cannot read
answers empty with one sentence and is kept aside by the next write. No ROM, GPU or emulator: the
lab config only names a lab folder under tmp_path.
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import camera_presets  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402

WIDE = {"focus": [100.0, 200.0, -300.0], "position": [100.0, 900.0, 1700.0], "roll": 0, "fov": 45.0}
LOW = {"focus": [-50.0, 0.0, 10.0], "position": [400.0, 20.0, 10.0], "roll": -1200, "fov": 30.5}


def slot(number, name, camera=WIDE):
    return {"slot": number, "name": name, "camera": camera}


@pytest.fixture
def store(tmp_path):
    return camera_presets.CameraPresets(tmp_path / camera_presets.FILE_NAME)


def test_each_level_and_area_keeps_its_own_slots_across_a_restart(store, tmp_path):
    store.replace(9, 1, [slot(3, "Wide hall", WIDE), slot(1, "Low run", LOW)])
    store.replace(9, 2, [slot(1, "Inside the cannon", LOW)])
    store.replace(24, 1, [slot(9, "Tower", WIDE)])
    again = camera_presets.CameraPresets(tmp_path / camera_presets.FILE_NAME)    # the next server
    assert again.slots(9, 1) == {"slots": [slot(1, "Low run", LOW), slot(3, "Wide hall", WIDE)], "problem": None}
    assert again.slots(9, 2)["slots"] == [slot(1, "Inside the cannon", LOW)]
    assert again.slots(24, 1)["slots"] == [slot(9, "Tower", WIDE)]
    assert again.slots(24, 2) == {"slots": [], "problem": None}
    document = json.loads((tmp_path / camera_presets.FILE_NAME).read_text(encoding="utf-8"))
    assert document["version"] == camera_presets.VERSION and sorted(document["areas"]) == ["24:1", "9:1", "9:2"]
    again.replace(9, 2, [])                            # an empty set clears that area only
    assert again.slots(9, 2)["slots"] == [] and len(again.slots(9, 1)["slots"]) == 2
    assert "9:2" not in json.loads((tmp_path / camera_presets.FILE_NAME).read_text(encoding="utf-8"))["areas"]


def test_a_write_that_fails_leaves_the_last_file_whole(store, tmp_path, monkeypatch):
    store.replace(9, 1, [slot(1, "Kept")])
    before = (tmp_path / camera_presets.FILE_NAME).read_bytes()

    def dies(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(json, "dump", dies)            # the new file never finishes
    with pytest.raises(OSError):
        store.replace(9, 1, [slot(1, "Lost"), slot(2, "Lost too")])
    assert (tmp_path / camera_presets.FILE_NAME).read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == [camera_presets.FILE_NAME]   # no temporary left


@pytest.mark.parametrize("damage", [
    b"{not json",
    b"\xff\xfe garbage",
    json.dumps({"version": 2, "areas": {}}).encode(),
    json.dumps({"version": 1, "areas": {"castle": [slot(1, "A")]}}).encode(),
    json.dumps({"version": 1, "areas": {"9:1": [slot(1, "A", {"focus": [0, 0, 0], "position": [0, 0, 0]})]}}).encode(),
    json.dumps({"version": 1, "areas": {"9:1": [slot(10, "A")]}}).encode(),
    json.dumps([1, 2, 3]).encode(),
])
def test_a_file_it_cannot_read_answers_empty_with_a_sentence_and_is_kept_aside(store, tmp_path, damage):
    (tmp_path / camera_presets.FILE_NAME).write_bytes(damage)
    answer = store.slots(9, 1)
    assert answer["slots"] == [] and answer["problem"] == camera_presets.UNREADABLE
    assert (tmp_path / camera_presets.FILE_NAME).read_bytes() == damage        # reading never writes
    store.replace(9, 1, [slot(2, "Fresh")])
    assert (tmp_path / f"camera-presets{camera_presets.BAD_SUFFIX}").read_bytes() == damage
    assert store.slots(9, 1) == {"slots": [slot(2, "Fresh")], "problem": None}


def test_an_oversized_file_is_refused_without_reading_it_whole(store, tmp_path, monkeypatch):
    monkeypatch.setattr(camera_presets, "MAX_FILE_BYTES", 64)
    store.replace(9, 1, [slot(1, "A long enough name to pass sixty four bytes")])
    assert store.slots(9, 1)["problem"] == camera_presets.UNREADABLE


@pytest.mark.parametrize(("slots", "words"), [
    ([slot(0, "A")], "1 to 9"),
    ([slot(10, "A")], "1 to 9"),
    ([slot(True, "A")], "1 to 9"),
    ([slot(2, "A"), slot(2, "B")], "twice"),
    ([slot(1, "   ")], "name"),
    ([slot(1, "x" * 49)], "48"),
    ([{**slot(1, "A"), "extra": 1}], "{slot, name, camera}"),
    ([slot(1, "A", {"focus": [0, 0, 0], "position": [0, 0, 0.5]})], "own focus"),
    ([slot(1, "A", {**WIDE, "fov": 171})], "fov"),
    ([slot(1, "A", {**WIDE, "roll": 1.5})], "roll"),
    ([slot(1, "A", {**WIDE, "focus": [0, "up", 0]})], "focus"),
    ([slot(n, "A") for n in range(1, 11)], "nine"),
    ("not a list", "list"),
])
def test_a_set_it_refuses_names_why_and_changes_nothing(store, tmp_path, slots, words):
    store.replace(9, 1, [slot(1, "Kept")])
    with pytest.raises(camera_presets.PresetError, match=None) as refused:
        store.replace(9, 1, slots)
    assert words in str(refused.value)
    assert store.slots(9, 1)["slots"] == [slot(1, "Kept")]


def test_names_keep_single_spaces_and_cameras_keep_what_the_free_camera_draws(store):
    answer = store.replace(9, 1, [slot(4, "  Wide   hall\t "), slot(5, "No fov", {"focus": [1, 2, 3], "position": [1, 2, 300]})])
    assert [entry["name"] for entry in answer["slots"]] == ["Wide hall", "No fov"]
    assert answer["slots"][1]["camera"] == {"focus": [1.0, 2.0, 3.0], "position": [1.0, 2.0, 300.0], "roll": None,
                                            "fov": None}


# ------------------------------------------------------------------ the routes (interface F)

@pytest.fixture
def client(tmp_path):
    lab_root = tmp_path / "lab-root"
    config = tmp_path / "lab.json"
    config.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(tmp_path / "packages"),
                                  "lab_root": str(lab_root)}), encoding="utf-8")
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, config)
    with TestClient(app) as test_client:
        yield test_client, lab_root


def test_the_routes_put_and_get_one_areas_slots_in_the_lab_folder(client):
    http, lab_root = client
    assert http.get("/api/scene-lab/camera-presets?level=9&area=1").json() == {"slots": [], "problem": None}
    put = http.put("/api/scene-lab/camera-presets?level=9&area=1", json={"slots": [slot(2, "Wide hall")]})
    assert put.status_code == 200 and put.json() == {"slots": [slot(2, "Wide hall")], "problem": None}
    assert http.get("/api/scene-lab/camera-presets?level=9&area=1").json()["slots"] == [slot(2, "Wide hall")]
    assert http.get("/api/scene-lab/camera-presets?level=9&area=2").json()["slots"] == []
    saved = json.loads((lab_root / camera_presets.FILE_NAME).read_text(encoding="utf-8"))
    assert saved == {"version": 1, "areas": {"9:1": [slot(2, "Wide hall")]}}


@pytest.mark.parametrize(("query", "body"), [
    ("level=9&area=1", {"slots": [slot(1, "A", {"focus": [0, 0, 0], "position": [0, 0, 0]})]}),
    ("level=9&area=1", {"slots": [slot(n, "A") for n in range(1, 11)]}),
    ("level=9&area=1", {"cameras": []}),
    ("level=9", {"slots": []}),
    ("level=-1&area=1", {"slots": []}),
    ("level=nine&area=1", {"slots": []}),
])
def test_a_refused_put_is_422_and_writes_nothing(client, query, body):
    http, lab_root = client
    answer = http.put(f"/api/scene-lab/camera-presets?{query}", json=body)
    assert answer.status_code == 422, answer.text
    assert not (lab_root / camera_presets.FILE_NAME).exists()


def test_a_bad_file_never_stops_the_route_and_its_sentence_reaches_the_page(client):
    http, lab_root = client
    lab_root.mkdir()
    (lab_root / camera_presets.FILE_NAME).write_text("{oops", encoding="utf-8")
    answer = http.get("/api/scene-lab/camera-presets?level=9&area=1")
    assert answer.status_code == 200 and answer.json() == {"slots": [], "problem": camera_presets.UNREADABLE}
