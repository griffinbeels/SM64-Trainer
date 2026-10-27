"""The Scene viewer's stage and ghost catalog (server/scene_ghosts.py), through the lab's real router.

GET /api/scene-lab/stage and /ghosts, the open answer's "title" and "target", and load_ghost (the
renderer's recordings, interface C). The journal and the replay service's two pose doors
(find_native, native_with_inputs) are doubles answering as the real ones do; the rank standards,
the rival race (`Rivals`), the population (`LocalPopulation`, rows written straight into its index)
and the imported collection (`GhostSharing`) are the real ones; every clip is a real native clip
(ghost_native_fixture), so load_ghost reads, hashes and decodes real bytes. No live data, ROM,
GPU or emulator; the attempt scenes are attempt_scenes.AttemptScenes fed by a cutter double.
"""
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import fake_viewer_host  # noqa: E402
import gpu_gate  # noqa: E402
import package  # noqa: E402
import scene_attempt_fixture as fixture  # noqa: E402
import scene_session  # noqa: E402
from ghost_native_fixture import frame, producer  # noqa: E402
from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders  # noqa: E402
from sm64_events.ghost.clip import INCOMPLETE_INPUTS, inputs_complete, read_clip, write_clip  # noqa: E402
from sm64_events.ghost.native_layout import STAR_CLOCK  # noqa: E402
from sm64_events.ghost.native_session import NativeGhost  # noqa: E402
from sm64_events.ghost.pb import AttemptGhost  # noqa: E402
from sm64_events.ghost.population import LocalPopulation  # noqa: E402
from sm64_events.ghost.portable import make_entry  # noqa: E402
from sm64_events.ghost.rivals import Rivals  # noqa: E402
from sm64_events.ghost.sharing_targets import canonical, describe  # noqa: E402
from sm64_events.ranks.standards import RankStandards  # noqa: E402
from sm64_events.server import scene_ghosts, scene_lab_api  # noqa: E402

T0 = datetime(2026, 10, 27, 1, 0, tzinfo=timezone.utc)
ME = "player-me"


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def run(attempt_id, *, igt=331, strat="Standard", outcome="success", minutes=0, course=2, star=2, segment=None,
        **changes):
    """A projected Attempt as the journal holds it (only the fields the catalog and the scenes read)."""
    started = T0 + timedelta(minutes=minutes)
    finished = outcome == "success" and igt is not None
    return SimpleNamespace(**{
        "id": attempt_id, "session_id": 3, "course_id": None if segment else course, "star_id": None if segment else star,
        "segment_id": segment, "strat_tag": strat, "outcome": outcome, "cleared": False, "valid": True,
        "anchor_type": "practice_reset", "anchor_frame": 5020, "closed_frame": 5150, "state_loads": 0,
        "invalid_reason": None, "timed_at": "xcam", "igt_frames": igt if finished else None,
        "rta_frames": igt + 9 if finished else None, "started_utc": _iso(started),
        "ended_utc": _iso(started + timedelta(seconds=15)), "native_clock_frame": 5020,
        "native_clock_utc": _iso(started), "native_clock_kind": "star", "game_version": "us", **changes})


class Journal:
    """The tracker database's reads the catalog makes."""

    def __init__(self, attempts=(), definitions=(), state=None):
        self.rows = {attempt.id: attempt for attempt in attempts}
        self.definitions = list(definitions)
        self.state = {"ghost_player_id": ME, **(state or {})}

    def attempt(self, attempt_id):
        return self.rows.get(attempt_id)

    def attempts(self):
        return list(self.rows.values())

    def target_attempts(self, *, course_id=None, star_id=None, segment_id=None):
        if segment_id is not None:
            return [row for row in self.rows.values() if row.segment_id == segment_id]
        return [row for row in self.rows.values()
                if row.segment_id is None and (row.course_id, row.star_id) == (course_id, star_id)]

    def segment_defs(self):
        return list(self.definitions)

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def current_pb(self, *, timer_mode, strat_tag, course_id=None, star_id=None, segment_id=None, **_slot):
        pb = self.state.get("pb")
        if pb and timer_mode == "igt" and strat_tag == pb["strat"] and (course_id, star_id) == pb["star"]:
            return {"id": 1, "attempt_id": pb["attempt"], "strat_tag": strat_tag, "frames": pb["frames"]}
        return None


class Replay:
    """ReplayService's pose doors over real clip files: find_native answers a committed clip
    (None without one), native_with_inputs restates whether every record carries a pad."""

    def __init__(self, root: Path, journal: Journal):
        self.cfg = SimpleNamespace(save_root=root)
        self.journal = journal

    def commit(self, attempt_id: int, *, records: int = 40, pads: bool = True) -> Path:
        path = self.cfg.save_root / "native" / f"attempt_{attempt_id}.ghost"
        manifest = write_clip(path, [frame(n, elapsed=n - 1, pad=(0, 0, 0) if pads else None)
                                     for n in range(1, records + 1)], producer(), clock_kind=STAR_CLOCK)
        path.with_suffix(".json").write_text(json.dumps(manifest), encoding="utf-8")
        return path

    def attempt(self, attempt_id):
        found = self.journal.attempt(attempt_id)
        if found is None:
            raise LookupError(f"no attempt {attempt_id}")
        return found

    def find_native(self, attempt_id):
        path = self.cfg.save_root / "native" / f"attempt_{attempt_id}.ghost"
        if not path.is_file():
            return None
        return path, json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))

    def native_with_inputs(self, attempt_id, found):
        path, manifest = found
        return path, {**manifest, "inputs_complete": inputs_complete(read_clip(path, manifest["sha256"]))}

    def native_identity(self, attempt_id):
        return {"attempt_id": attempt_id}


@pytest.fixture(scope="module")
def ranks(tmp_path_factory):
    standards = RankStandards(tmp_path_factory.mktemp("ranks") / "rank_standards.json", bundled_rank_standards(),
                              bundled_sheet_ladders())
    standards.load()
    return standards


def publish(population: LocalPopulation, *, player_id, player_name, frames, strat="Standard", minutes=0,
            source=None, records=40) -> str:
    """A recording in the population's own index and clip folder (what admission leaves behind)."""
    staging = population.root / "staging" / f"{player_id}-{frames}-{minutes}.ghost"
    write_clip(staging, [frame(n, elapsed=n - 1) for n in range(1, records + 1)], producer(), clock_kind=STAR_CLOCK)
    data = staging.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    (population.root / "clips").mkdir(exist_ok=True)
    (population.root / "clips" / f"{digest}.ghost").write_bytes(data)
    target = describe("star", "2-2", strat, ())
    document = {"player_id": player_id, "player_name": player_name, "target": target, "frames": frames,
                "clock": "igt", "game_version": "us", "sha256": digest,
                "recorded_utc": _iso(T0 + timedelta(minutes=minutes)), "source": source,
                "certified": True, "valid": True, "full_start": True}
    document["id"] = hashlib.sha256(canonical(document)).hexdigest()
    with sqlite3.connect(population.root / "population.sqlite3") as index:
        index.execute("INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (document["id"], player_id, player_name.casefold(), "star:2-2", "star", "igt", "us", frames,
                       digest, len(data), canonical(document).decode()))
    return document["id"]


def import_ghost(save_root: Path, *, author, frames, strat="Standard", pads=True) -> dict:
    """One imported ghost in the collection index GhostSharing reads."""
    folder = save_root / "shared-ghosts"
    staging = save_root / "staging" / f"{author}-{frames}.ghost"
    write_clip(staging, [frame(n, elapsed=n - 1, pad=(0, 0, 0) if pads else None) for n in range(1, 41)],
               producer(), clock_kind=STAR_CLOCK)
    entry = make_entry(staging, author=author, target=describe("star", "2-2", strat, ()), frames=frames,
                       clock="igt", recorded_utc=_iso(T0), practice=False, outcome="success")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{entry['sha256']}.ghost").write_bytes(staging.read_bytes())
    index = folder / "collection.json"
    rows = json.loads(index.read_text(encoding="utf-8"))["ghosts"] if index.is_file() else []
    index.write_text(json.dumps({"version": 1, "ghosts": [*rows, entry]}), encoding="utf-8")
    return entry


@pytest.fixture
def world(tmp_path, ranks):
    """A lab whose catalog reads a journal double, real rank standards, a real population and rivals."""
    journal = Journal(definitions=[{"id": 41, "name": "Lobby to WF", "enabled": True}])
    tracker = SimpleNamespace(db=journal, ranks=ranks, strat_by_star={(2, 2): "Standard"}, strat_by_segment={},
                              effective_game_version=lambda: "us", segment_defs=[])
    replay = Replay(tmp_path / "replays", journal)
    population = LocalPopulation(tmp_path / "population", definitions=())
    rivals = Rivals(tracker, population, SimpleNamespace(), lambda: "us")
    return SimpleNamespace(journal=journal, tracker=tracker, replay=replay, population=population, rivals=rivals,
                           sources={"tracker": tracker, "replay": replay, "ghost": SimpleNamespace(rivals=rivals)},
                           tmp=tmp_path)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    for folder in ("packages", "Plugin", "lab"):
        (tmp_path / folder).mkdir()
    rom = fixture.rom()
    (tmp_path / "rom.z64").write_bytes(rom)
    config = tmp_path / "lab.json"
    config.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(tmp_path / "packages"),
                                  "pj64_plugin": str(tmp_path / "Plugin"), "lab_root": str(tmp_path / "lab")}),
                      encoding="utf-8")
    return SimpleNamespace(config=config, rom=rom, root=tmp_path)


class Lease:
    def __init__(self, lab_root):
        pass

    def start(self):
        pass

    def stop(self):
        pass


class Cutter:
    def __init__(self, folder, on_event):
        self.folder, self.on_event = folder, on_event

    def send(self, note):
        pass

    def close(self):
        pass

    def ready(self, attempt_id: int, data: bytes = b"scene") -> None:
        path = self.folder / f"{attempt_id}.sm64scene"
        path.write_bytes(data)
        self.on_event({"id": attempt_id, "state": "ready", "seconds": 9.7, "path": str(path)})


def _app(lab, world):
    app, cutters = FastAPI(), []

    def cutter(folder, on_event):
        cutters.append(Cutter(folder, on_event))
        return cutters[-1]
    host = scene_session.command_host(fake_viewer_host.command())
    server = scene_lab_api.mount_scene_lab(
        app, lab.config, live=True, lease_factory=Lease, cutter_factory=cutter, ghost_sources=world.sources,
        session_options={"host_factory": host,
                         "evaluator_factory": lambda scene, rom: fake_viewer_host.FakeEvaluator()})
    return app, server, cutters


def _scenes(server, cutters, *attempts, data=b"scene"):
    server.scenes.observe_completed(list(attempts))
    for attempt in attempts:
        cutters[0].ready(attempt.id, data)


def _ghosts(client, **params):
    answer = client.get("/api/scene-lab/ghosts", params={"course": 2, "star": 2, **params})
    assert answer.status_code == 200, answer.text
    return answer.json()


# ---------------------------------------------------------------- the stage


def test_the_stage_is_his_fastest_finished_attempt_of_the_strategy_with_a_scene(lab, world):
    fastest_unrecorded = run(10, igt=300, minutes=0)
    slow, fast, reset = run(11, igt=360, minutes=2), run(12, igt=331, minutes=1), run(13, outcome="reset", minutes=3)
    other = run(14, igt=310, strat="Single Jump", minutes=4)
    world.journal.rows.update({row.id: row for row in (fastest_unrecorded, slow, fast, reset, other)})
    app, server, cutters = _app(lab, world)
    with TestClient(app) as client:
        _scenes(server, cutters, slow, fast, reset, other)
        stage = client.get("/api/scene-lab/stage", params={"course": 2, "star": 2}).json()
        assert stage["target"] == {"course_id": 2, "star_id": 2, "segment_id": None, "strat": "Standard",
                                   "course_name": "Whomp's Fortress", "star_name": "Shoot into the Wild Blue",
                                   "strat_name": "Standard"}
        assert (stage["attempt"], stage["time"], stage["reason"]) == (12, '11"03', None)
        assert [(row["attempt"], row["time"]) for row in stage["scenes"]] == [(12, '11"03'), (11, '12"00'), (13, None)]
        assert stage["scenes"][0]["recorded_utc"] == fast.started_utc
        picked = client.get("/api/scene-lab/stage", params={"course": 2, "star": 2, "strat": "Single Jump"}).json()
        assert (picked["attempt"], picked["target"]["strat_name"]) == (14, "Single Jump")
        nothing = client.get("/api/scene-lab/stage", params={"course": 2, "star": 3}).json()
        assert (nothing["attempt"], nothing["time"], nothing["scenes"]) == (None, None, [])
        assert nothing["reason"] == "No saved scene for this strategy yet: play it with the scene lab on."


def test_a_stage_with_no_finished_attempt_is_the_newest_scene_and_segments_name_themselves(lab, world):
    older, newer = run(20, outcome="reset", minutes=0), run(21, outcome="death", minutes=5)
    piece = run(22, segment=41, strat="Standard", igt=None, minutes=6, rta_frames=1900)
    world.journal.rows.update({row.id: row for row in (older, newer, piece)})
    world.tracker.strat_by_segment = {41: "Standard"}
    app, server, cutters = _app(lab, world)
    with TestClient(app) as client:
        _scenes(server, cutters, older, newer, piece)
        stage = client.get("/api/scene-lab/stage", params={"course": 2, "star": 2}).json()
        assert (stage["attempt"], stage["time"]) == (21, None)
        segment = client.get("/api/scene-lab/stage", params={"segment": 41}).json()
        assert segment["target"]["segment_name"] == "Lobby to WF" and segment["target"]["course_name"] is None
        assert (segment["attempt"], segment["time"]) == (22, '1\'03"33')


def test_the_routes_refuse_a_target_that_does_not_exist(lab, world):
    app, _server, _cutters = _app(lab, world)
    with TestClient(app) as client:
        for params in ({}, {"course": 2}, {"course": 2, "star": 7}, {"course": 30, "star": 0}, {"segment": 99},
                       {"course": 2, "star": 2, "segment": 41}):
            for route in ("/api/scene-lab/stage", "/api/scene-lab/ghosts"):
                assert client.get(route, params=params).status_code == 422, (route, params)
        assert client.get("/api/scene-lab/ghosts", params={"course": 2, "star": 2, "section": "best"}).status_code == 422
        assert client.get("/api/scene-lab/ghosts", params={"course": 2, "star": 2, "sort": "slowest"}).status_code == 422


def test_an_opened_scene_says_what_it_is(lab, world, tmp_path):
    places = [(5000 + index, 9 if index < 300 else 6, 1) for index in range(400)]
    session = fixture.write_session(tmp_path / "recording", lab.rom, places)
    scene = tmp_path / "scene.sm64scene"
    package.write_package(package.tap_source(session, "next", states=True), lab.rom, scene, package.ZLIB)
    (lab.root / "packages" / "take.sm64scene").write_bytes(scene.read_bytes())
    attempt = run(12, igt=331, strat="Single Jump")
    world.journal.rows[12] = attempt
    app, server, cutters = _app(lab, world)
    with TestClient(app) as client:
        _scenes(server, cutters, attempt, data=scene.read_bytes())
        opened = client.post("/api/scene-lab/open", json={"attempt": 12}).json()
        assert opened["title"] == 'Whomp\'s Fortress · Shoot into the Wild Blue · Single Jump · 11"03'
        assert opened["target"]["star_name"] == "Shoot into the Wild Blue" and opened["target"]["strat"] == "Single Jump"
        assert client.get("/api/scene-lab/status").json()["session"]["title"] == opened["title"]
        lab_package = client.post("/api/scene-lab/open", json={"package": "take.sm64scene"}).json()
        assert (lab_package["title"], lab_package["target"]) == ("Lab recording", None)
        client.post("/api/scene-lab/close")


# ---------------------------------------------------------------- his ghosts


def test_yours_lists_only_his_perfect_recordings_fastest_or_newest(lab, world):
    rows = [run(30, igt=340, minutes=0), run(31, igt=331, minutes=1), run(32, igt=350, minutes=2),
            run(33, igt=320, minutes=3),                                   # inputs not all known
            run(34, igt=300, minutes=4),                                   # no committed poses
            run(35, igt=290, minutes=5, anchor_type="state_loaded"),       # a save-state run is not a best
            run(36, outcome="reset", minutes=6), run(37, igt=280, strat="Single Jump", minutes=7)]
    world.journal.rows.update({row.id: row for row in rows})
    for attempt_id in (30, 31, 32, 35, 36, 37):
        world.replay.commit(attempt_id)
    world.replay.commit(33, pads=False)
    app, server, _cutters = _app(lab, world)
    with TestClient(app) as client:
        fastest = _ghosts(client, section="yours")
        assert [row["time"] for row in fastest["items"]] == ['11"03', '11"33', '11"66']
        assert all(row["yours"] and row["player"] == "You" and row["more"] == 0 for row in fastest["items"])
        assert (fastest["page"], fastest["pages"], fastest["reason"]) == (1, 1, None)
        newest = _ghosts(client, section="yours", sort="newest")
        assert [row["frames"] for row in newest["items"]] == [350, 331, 340]
        assert newest["items"][0]["recorded_utc"] == rows[2].started_utc
        assert server.ghosts.exclusions()["2-2 Standard"] == {"refused": 1, "unreadable": 0}
        other = _ghosts(client, section="yours", strat="Single Jump")
        assert [row["time"] for row in other["items"]] == ['09"33']       # timefmt.format_igt_short's own form
        assert _ghosts(client, section="yours", star=3, strat="Standard")["reason"] == scene_ghosts.NO_YOURS


def test_the_catalog_refuses_exactly_the_runs_the_in_game_race_refuses(lab, world):
    rows = [run(40, igt=331), run(41, igt=332, minutes=1), run(42, igt=333, minutes=2)]
    world.journal.rows.update({row.id: row for row in rows})
    world.replay.commit(40)
    world.replay.commit(41, pads=False)
    native = NativeGhost(world.replay)
    target = {"kind": "star", "entity_key": "2-2", "strat": "Standard"}
    race = {row.id: native.probe(target, AttemptGhost(row.id, "star", "2-2", "Standard"))["available"]
            for row in rows}
    app, server, _cutters = _app(lab, world)
    with TestClient(app) as client:
        listed = {row["frames"] for row in _ghosts(client, section="yours")["items"]}
    assert race == {40: True, 41: False, 42: False}
    assert {row.id: row.igt_frames in listed for row in rows} == race
    assert server.ghosts.run_clip(rows[1]) == INCOMPLETE_INPUTS


# ---------------------------------------------------------------- everyone's ghosts


def test_top_is_each_players_best_fastest_first_with_rank_and_how_many_more(lab, world):
    world.journal.rows[50] = run(50, igt=331)
    world.replay.commit(50)
    publish(world.population, player_id="kim", player_name="Kim", frames=320, minutes=1)
    publish(world.population, player_id="kim", player_name="Kim", frames=340, minutes=2)
    publish(world.population, player_id="kim", player_name="Kim", frames=345, minutes=3)
    publish(world.population, player_id="ana", player_name="Ana", frames=331, minutes=4)
    publish(world.population, player_id="bo", player_name="Bo", frames=400, minutes=5)
    publish(world.population, player_id=ME, player_name="Me", frames=360, minutes=6)   # his own certified run
    publish(world.population, player_id=ME, player_name="Me", frames=331, minutes=7,    # run 50's certified copy
            source={"session_id": 3, "attempt_id": 50, "started_utc": world.journal.rows[50].started_utc})
    app, _server, _cutters = _app(lab, world)
    with TestClient(app) as client:
        top = _ghosts(client, section="top")
        assert [(row["player"], row["time"], row["rank"], row["more"], row["yours"]) for row in top["items"]] == [
            ("Kim", '10"66', 1, 2, False), ("You", '11"03', 2, 1, True), ("Ana", '11"03', 2, 0, False),
            ("Bo", '13"33', 4, 0, False)]
        assert top["items"][0]["tier"] is not None and top["items"][0]["division"] is not None
        kim = _ghosts(client, section="player", player="kim")
        # A slower ghost holds the place its time would: behind You and Ana (331), ahead of Bo.
        assert [(row["frames"], row["rank"], row["more"]) for row in kim["items"]] == [(320, 1, 0), (340, 3, 0),
                                                                                      (345, 3, 0)]
        mine = _ghosts(client, section="yours")
        assert [(row["frames"], row["yours"], row["attempt"]) for row in mine["items"]] == [(331, True, 50),
                                                                                             (360, True, None)]
        assert {row["attempt"] for row in top["items"] if not row["yours"]} == {None}
        found = _ghosts(client, section="search", q="A")
        assert [row["player"] for row in found["items"]] == ["Ana"]
        assert _ghosts(client, section="search", q="  ")["reason"] == scene_ghosts.NO_QUERY
        assert _ghosts(client, section="search", q="zed")["reason"] == scene_ghosts.NO_MATCH
        assert _ghosts(client, section="player", player="zed")["reason"] == scene_ghosts.NO_PLAYER


def test_pages_hold_ten_and_imported_players_join_the_list(lab, world):
    for number in range(12):
        publish(world.population, player_id=f"p{number:02d}", player_name=f"Runner {number:02d}",
                frames=330 + number, minutes=number)
    import_ghost(world.replay.cfg.save_root, author="Visitor", frames=300)
    import_ghost(world.replay.cfg.save_root, author="Broken", frames=299, pads=False)
    app, server, _cutters = _app(lab, world)
    with TestClient(app) as client:
        first, second = _ghosts(client, section="top"), _ghosts(client, section="top", page=2)
        assert (first["pages"], len(first["items"]), second["page"], len(second["items"])) == (2, 10, 2, 3)
        assert first["items"][0]["player"] == "Visitor" and first["items"][0]["rank"] == 1
        assert [row["rank"] for row in second["items"]] == [11, 12, 13]
        assert _ghosts(client, section="top", page=9)["page"] == 2
        assert "Broken" not in {row["player"] for page in (first, second) for row in page["items"]}
        assert server.ghosts.exclusions()["2-2 Standard"]["refused"] == 1


def test_every_section_says_why_it_is_empty(lab, world):
    world.tracker.strat_by_star = {}
    app, _server, _cutters = _app(lab, world)
    with TestClient(app) as client:
        for section in ("yours", "top", "search", "player"):
            answer = _ghosts(client, section=section)
            assert (answer["items"], answer["reason"]) == ([], scene_ghosts.NO_STRATEGY)
        assert _ghosts(client, section="top", strat="Standard")["reason"] == scene_ghosts.NO_GHOSTS
    world.sources = {"tracker": world.tracker}
    app, _server, _cutters = _app(lab, world)
    with TestClient(app) as client:
        assert _ghosts(client, section="top", strat="Standard")["reason"] == scene_ghosts.NO_RECORDINGS


# ---------------------------------------------------------------- the renderer's recordings


def test_load_ghost_hands_the_renderer_the_race_bytes_and_the_in_game_name_box(lab, world):
    pb, other = run(60, igt=331), run(61, igt=340, minutes=1)
    world.journal.rows.update({60: pb, 61: other})
    world.journal.state["pb"] = {"attempt": 60, "strat": "Standard", "star": (2, 2), "frames": 331}
    clip = world.replay.commit(60, records=37)
    world.replay.commit(61)
    publish(world.population, player_id="kim", player_name="Kim", frames=320, records=33)
    import_ghost(world.replay.cfg.save_root, author="Visitor", frames=300)
    app, server, _cutters = _app(lab, world)
    with TestClient(app) as client:
        rows = {row["player"] + row["time"]: row for row in _ghosts(client, section="top")["items"]}
        mine = {row["frames"]: row["ref"] for row in _ghosts(client, section="yours")["items"]}
    loaded = server.ghosts.load_ghost(mine[331])
    assert loaded == {"clip": clip.read_bytes(), "player": "You", "frames": 37, "label": "YOUR PB", "time": '11"03'}
    assert server.ghosts.load_ghost(mine[340])["label"] == "YOUR RUN"
    kim = server.ghosts.load_ghost(rows['Kim10"66']["ref"])
    assert (kim["player"], kim["label"], kim["frames"]) == ("Kim", "Kim", 33)
    assert server.ghosts.load_ghost(rows['Visitor10"00']["ref"])["label"] == "Visitor"
    with pytest.raises(LookupError, match="not in the list"):
        server.ghosts.load_ghost("g" + "0" * 20)
    with pytest.raises(LookupError, match="not in the list"):
        server.ghosts.load_ghost(str(clip))


def test_a_recording_that_changed_after_listing_is_refused_and_leaves_the_list(lab, world):
    world.journal.rows[70] = run(70, igt=331)
    world.replay.commit(70)
    identifier = publish(world.population, player_id="kim", player_name="Kim", frames=320)
    app, server, _cutters = _app(lab, world)
    with TestClient(app) as client:
        ref = next(row["ref"] for row in _ghosts(client, section="top")["items"] if row["player"] == "Kim")
        digest = world.population.get(identifier)["sha256"]
        (world.population.root / "clips" / f"{digest}.ghost").write_bytes(b"not a clip")
        with pytest.raises(LookupError):
            server.ghosts.load_ghost(ref)
        assert [row["player"] for row in _ghosts(client, section="top")["items"]] == ["You"]
        assert server.ghosts.exclusions()["2-2 Standard"] == {"refused": 0, "unreadable": 1}


def test_refs_are_opaque_stable_and_belong_to_one_server(lab, world):
    world.journal.rows[80] = run(80, igt=331)
    world.replay.commit(80)
    app, server, _cutters = _app(lab, world)
    with TestClient(app) as client:
        first = _ghosts(client, section="yours")["items"][0]["ref"]
        assert _ghosts(client, section="yours", sort="newest")["items"][0]["ref"] == first
    assert re.fullmatch(r"g[0-9a-f]{20}", first)
    _app2, other, _cutters2 = _app(lab, world)
    assert other.ghosts._ref(("attempt", 80)) != first
    with pytest.raises(LookupError):
        other.ghosts.load_ghost(first)
