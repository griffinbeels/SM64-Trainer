"""The Scene viewer's community ghosts cost as few service calls as possible (scene_community.py).

Griffin, 2026-10-27: "We should also consider how caching / cloudflare database interactions work
here, to make sure we're being as cost effective as possible (and, if someone loads a ghost here,
could we simply reuse that ghost locally if they go to actually challenge it later, for example)."

The real community client (`CommunitySync`), its cache (`CachedPopulation` over a real
`LocalPopulation`), the rival race (`Rivals`) and the rank standards run behind the lab's real
router; only the network is a double (`Service`), which counts every call. Each test asserts the
calls a viewer action made: the store a ghost lands in is the race's own
(`data/community_cache/<id>.ghost`), so a ghost loaded here races with no second download and a
ghost a race downloaded loads here with no call.
"""
import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import gpu_gate  # noqa: E402
import scene_attempt_fixture as fixture  # noqa: E402
from test_community_account import UID  # noqa: E402
from test_community_codec import native  # noqa: E402
from sm64_events.community import CachedPopulation, CommunityError, CommunitySync, codec  # noqa: E402
from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders  # noqa: E402
from sm64_events.ghost.population import LocalPopulation  # noqa: E402
from sm64_events.ghost.rivals import Rivals  # noqa: E402
from sm64_events.ranks.standards import RankStandards  # noqa: E402
from sm64_events.server import scene_community, scene_lab_api  # noqa: E402

TARGET = {"kind": "star", "entity_key": "2-2", "strat": "Standard"}
STAR = {"course": 2, "star": 2}


def recording(number: int, *, frames: int, player: str | None = None, player_id: str | None = None) -> dict:
    """A published recording's metadata, its bytes bearing out its own time (community/claims.py)."""
    raw = native(frames)
    encoded = codec.encode(raw)
    return {"id": f"{number:064x}", "player_id": player_id or f"{number:08x}-0000-4000-8000-000000000000",
            "player_name": player or f"Runner{number}", "target": dict(TARGET), "strategy": "Standard",
            "clock": "igt", "game_version": "us", "frames": frames, "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "raw_bytes": len(raw), "encoded_sha256": hashlib.sha256(encoded).hexdigest(),
            "encoded_bytes": len(encoded), "codec": "ghc1", "generation": "g1", "step": 2,
            "provenance": "community-unverified", "structurally_valid": True, "full_start": True}


class Service:
    """The community Worker as the signed-in account reaches it; counts every call."""

    def __init__(self, rows=()):
        self.rows = {row["id"]: row for row in rows}
        self.calls: list[tuple[str, str, dict]] = []
        self.library: list = []          # the /v1/library/recordings pages, in order
        self.searches: dict = {}         # prefix -> its /v1/search rows
        self.pool: list = []
        self.refuse = None
        self.state = {"authenticated": True, "status": "active", "uid": UID, "sharing": True, "name": "Me"}

    def snapshot(self):
        return dict(self.state)

    def count(self, prefix: str) -> int:
        return sum(1 for _method, path, _kwargs in self.calls if path.startswith(prefix))

    async def request_json(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.refuse is not None and path != "/v1/catalog":
            raise self.refuse
        if path == "/v1/catalog":
            cutoffs = [{"step": 0, "frames": 1000}, {"step": 1, "frames": 500}, {"step": 2, "frames": 200}]
            return {"v": 1, "generation": "g1", "entries": [{
                "target": dict(TARGET), "clock": "igt", "game_version": "us",
                "strategies": [{"name": "Standard", "cutoffs": cutoffs}], "overall_cutoffs": cutoffs}]}
        if path == "/v1/library/recordings":
            rows, cursor = self.library.pop(0)
            return {"v": 1, "generation": "g1", "recordings": rows, "next_cursor": cursor, "complete": cursor is None}
        if path in ("/v1/search", "/v1/pool"):
            rows = self.searches.get(kwargs["params"].get("prefix"), []) if path == "/v1/search" else self.pool
            return {"v": 1, "recordings": rows, "next_cursor": None, "coverage": {
                "complete": True, "min_step": 0, "max_step": 2, "base_step": 0, "generation": "g1"}}
        if path.startswith("/v1/recordings/"):
            return {"v": 1, **self.rows[path.split("/")[-1]]}
        raise AssertionError(path)

    async def request_bytes(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return codec.encode(native(self.rows[path.split("/")[-2]]["frames"]))


@pytest.fixture(scope="module")
def ranks(tmp_path_factory):
    standards = RankStandards(tmp_path_factory.mktemp("ranks") / "rank_standards.json", bundled_rank_standards(),
                              bundled_sheet_ladders())
    standards.load()
    return standards


@pytest.fixture
def world(tmp_path, ranks, monkeypatch):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    for folder in ("packages", "Plugin", "lab"):
        (tmp_path / folder).mkdir()
    (tmp_path / "rom.z64").write_bytes(fixture.rom())
    config = tmp_path / "lab.json"
    config.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(tmp_path / "packages"),
                                  "pj64_plugin": str(tmp_path / "Plugin"), "lab_root": str(tmp_path / "lab")}),
                      encoding="utf-8")
    rows = [recording(number, frames=10 + number) for number in range(1, 26)]
    rows.append(recording(90, frames=60, player="Me", player_id=UID))
    service = Service(rows)
    journal = SimpleNamespace(attempt=lambda _id: None, target_attempts=lambda **_where: [], segment_defs=lambda: [],
                              get_state=lambda key, default=None: default)
    tracker = SimpleNamespace(db=journal, ranks=ranks, strat_by_star={(2, 2): "Standard"}, strat_by_segment={},
                              effective_game_version=lambda: "us")
    population = CachedPopulation(LocalPopulation(tmp_path / "population", definitions=()),
                                  root=tmp_path / "community_cache", account=service, definitions=())
    sync = CommunitySync(service, population)
    rivals = Rivals(tracker, population, SimpleNamespace(), lambda: "us", community=sync)
    app = FastAPI()
    lab = scene_lab_api.mount_scene_lab(app, config, ghost_sources={
        "tracker": tracker, "ghost": SimpleNamespace(rivals=rivals), "community": sync})
    identity = rivals.identity_for(TARGET, version="us", scope="strategy")
    with TestClient(app) as client:
        client.portal.call(sync.start)
        yield SimpleNamespace(client=client, lab=lab, service=service, rows=service.rows, sync=sync,
                              population=population, rivals=rivals, identity=identity, cache=tmp_path / "community_cache")
        client.portal.call(sync.stop)


def page(world, **params) -> dict:
    answer = world.client.get("/api/scene-lab/ghosts", params={**STAR, **params})
    assert answer.status_code == 200, answer.text
    return answer.json()


def ordered(world, *numbers):
    return [world.rows[f"{number:064x}"] for number in numbers]


def test_one_library_page_serves_two_viewer_pages_and_a_reopen_inside_the_lease_costs_nothing(world):
    world.service.library = [(ordered(world, *range(1, 21)), "next-page"), (ordered(world, *range(21, 26), 90), None)]
    first = page(world, section="top")
    assert [row["player"] for row in first["items"][:2]] == ["Runner1", "Runner2"]
    assert (world.service.count("/v1/library"), first["pages"]) == (1, 3)    # 2 held + "there is more"
    assert page(world, section="top", page=2)["items"][-1]["player"] == "Runner20"
    assert world.service.count("/v1/library") == 1
    third = page(world, section="top", page=3)
    assert world.service.count("/v1/library") == 2
    assert world.service.calls[-1][2]["params"]["cursor"] == "next-page"
    assert [row["player"] for row in third["items"]] == ["Runner21", "Runner22", "Runner23", "Runner24",
                                                          "Runner25", "You"]
    assert third["items"][-1]["yours"] and third["pages"] == 3
    for number in (1, 2, 3):
        page(world, section="top", page=number)
    assert world.service.count("/v1/library") == 2
    assert world.service.count("/v1/recordings") == 0                         # listing never downloads
    community = world.lab.ghosts.community
    started = community.clock()
    world.service.library = [(ordered(world, *range(1, 21)), "next-page")]
    community.clock = lambda: started + scene_community.TTL + 1                # the publication lease lapsed
    page(world, section="top")
    assert world.service.count("/v1/library") == 3


def test_search_asks_only_for_what_the_held_lists_cannot_answer(world):
    world.service.library = [(ordered(world, *range(1, 21)), "next-page")]
    page(world, section="top")
    world.service.searches = {"runner2": ordered(world, 2, 20, 21, 22)}
    found = page(world, section="search", q="Runner2")
    assert [row["player"] for row in found["items"]] == ["Runner2", "Runner20", "Runner21", "Runner22"]
    assert world.service.count("/v1/search") == 1
    page(world, section="search", q="runner2")                                 # the same name: held
    page(world, section="search", q="Runner21")                                # a longer one: answered
    assert [row["player"] for row in page(world, section="player", player="Runner22")["items"]] == ["Runner22"]
    assert world.service.count("/v1/search") == 1
    assert page(world, section="search", q="  ")["reason"] and world.service.count("/v1/search") == 1


def test_a_held_complete_list_or_the_races_own_list_answers_without_asking(world):
    world.service.library = [(ordered(world, 1, 2, 3), None)]
    page(world, section="top")
    page(world, section="search", q="nobody")
    page(world, section="player", player="Runner3")
    assert (world.service.count("/v1/library"), world.service.count("/v1/search")) == (1, 0)


def test_the_races_complete_list_serves_the_viewer_with_no_call_of_its_own(world):
    world.service.pool = ordered(world, 4, 5, 6)
    assert world.sync.request_pool(world.identity, frame_windows=[(1, 1000)])
    for _ in range(200):
        if world.population.coverage(world.identity)["complete"]:
            break
        time.sleep(0.01)
    before = len(world.service.calls)
    top = page(world, section="top")
    assert [row["player"] for row in top["items"]] == ["Runner4", "Runner5", "Runner6"]
    page(world, section="search", q="Runner5")
    assert len(world.service.calls) == before


def test_a_ghost_loaded_in_the_viewer_races_later_with_no_second_download(world):
    world.service.library = [(ordered(world, *range(1, 21)), "next-page")]
    ref = page(world, section="top")["items"][0]["ref"]
    identifier = f"{1:064x}"
    loaded = world.lab.ghosts.load_ghost(ref)
    assert (world.service.count(f"/v1/recordings/{identifier}"), world.service.count("/v1/recordings/")) == (2, 2)
    assert (world.cache / f"{identifier}.ghost").read_bytes() == loaded["clip"]
    assert (loaded["player"], loaded["label"], loaded["frames"]) == ("Runner1", "Runner1", 11)
    before = len(world.service.calls)
    # The race's own door for a chosen rival: ready, so nothing is pending and nothing is asked.
    staged = world.rivals.community.stage(({**world.population.get(identifier), "slot": 0},))
    assert [row["pending"] for row in staged] == [False]
    assert world.sync.request_assets([identifier])
    path, _manifest = world.population.resolve(world.population.get(identifier)["ghost"], TARGET)
    assert path == world.cache / f"{identifier}.ghost"
    assert world.lab.ghosts.load_ghost(ref)["clip"] == loaded["clip"]
    time.sleep(0.2)                                                            # the download worker is idle
    assert len(world.service.calls) == before


def test_a_ghost_a_race_downloaded_loads_in_the_viewer_with_no_call(world):
    world.service.pool = ordered(world, 7, 8)
    identifier = f"{7:064x}"
    assert world.sync.request_pool(world.identity, frame_windows=[(1, 1000)])
    for _ in range(200):
        if world.population.coverage(world.identity)["complete"]:
            break
        time.sleep(0.01)
    world.sync.request_assets([identifier])                                    # the race seats it
    for _ in range(300):
        if world.population.is_ready(identifier):
            break
        time.sleep(0.01)
    before = len(world.service.calls)
    ref = next(row["ref"] for row in page(world, section="top")["items"] if row["player"] == "Runner7")
    assert world.lab.ghosts.load_ghost(ref)["clip"] == (world.cache / f"{identifier}.ghost").read_bytes()
    assert len(world.service.calls) == before


def test_a_refusal_is_held_and_the_lists_answer_from_this_trainer(world):
    world.service.refuse = CommunityError("sign_in_required", status=401)
    first = page(world, section="top")
    asked = len(world.service.calls)
    second = page(world, section="top")
    assert len(world.service.calls) == asked                                   # held for FAILURE_S
    assert first["items"] == second["items"] == []
    assert second["reason"] == second["note"] == scene_community.SIGN_IN
    world.service.state = {**world.service.state, "status": "signed_out"}
    assert page(world, section="search", q="Runner")["note"] == scene_community.SIGN_IN
    assert len(world.service.calls) == asked
    assert page(world, section="yours")["note"] is None


def test_his_own_sections_never_reach_the_network(world):
    for params in ({"section": "yours"}, {"section": "yours", "sort": "newest"}):
        page(world, **params)
    client = world.client
    assert client.get("/api/scene-lab/stage", params=STAR).status_code == 200
    assert world.service.calls == []
