"""Actual HTTP reads keep one calibration and independent personal cutoffs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.library.adoptions import Adoptions
from sm64_events.library.build import SCHEMA_VERSION
from sm64_events.library.ladders import fit_payload
from sm64_events.library.store import LibraryStore
from sm64_events.ranks import curves
from sm64_events.ranks.classify import display_cs
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.rank_reading import install_calibration_reads
from sm64_events.server.ranks_api import create_ranks_router, absorb_after_regrade
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

ENTITY = "star:2:4"


def observations(offset=0):
    row = {"name": "Standard", "ids": ["3"], "matched_strategy": "Standard",
           "entries": [{"runner": f"Runner {i}", "time_cs": 1300 + i * 20 + offset,
                        "version": version} for version in ("us", "jp") for i in range(20)]}
    return fit_payload({"schema_version": SCHEMA_VERSION, "sheet_revision": "2026-09-08T00:00:00",
                        "runners": [], "targets": [{"entity_key": ENTITY, "label": "Caged",
                        "group": "Whomp's Fortress", "section": "", "version": None,
                        "approaches": [row], "subsections": []}]})


@pytest.fixture
def app_state(tmp_path):
    ranks = RankStandards(tmp_path / "ranks.json")
    ranks.load()
    store = LibraryStore(path=tmp_path / "sheet.json.gz")
    adoptions = Adoptions(tmp_path / "links.json", store, ranks)
    store.absorb(observations())
    db = Database(tmp_path / "test.db")
    service = TrackerService(db, Broadcaster(), ranks=ranks)
    app = FastAPI()
    install_calibration_reads(app, ranks, store)
    app.include_router(create_ranks_router(service, store, adoptions))
    yield app, service, store
    db.close()


def standards(client, version="us"):
    response = client.get("/api/ranks/standards", params={"entity": ENTITY, "version": version})
    assert response.status_code == 200, response.text
    assert response.headers["X-Rank-Calibration"] == response.json()["calibration_revision"]
    return response.json()


def test_overall_edit_reset_and_rejection_are_separate_from_strategy_and_region(app_state):
    app, service, _store = app_state
    with TestClient(app) as client:
        before, jp = standards(client), standards(client, "jp")
        goal = before["overall"]["Gold"]
        pinned = round(goal - .1, 2)
        # Choose an actual frame; arbitrary decimal cutoffs are refused.
        pinned = display_cs(round(pinned * 30)) / 100
        response = client.put(f"/api/ranks/overall/{ENTITY}/Gold", json={"seconds": pinned})
        assert response.status_code == 200, response.text
        edited = standards(client)
        assert edited["overall_overrides"] == {"Gold": pinned}
        assert edited["overall"]["Gold"] == pinned
        assert edited["strategies"] == before["strategies"]
        assert standards(client, "jp")["overall_curve"] == jp["overall_curve"]
        saved, revision = service.ranks.path.read_bytes(), edited["calibration_revision"]
        bad = client.put(f"/api/ranks/overall/{ENTITY}/Mario", json={"seconds": 999})
        assert bad.status_code == 409
        assert service.ranks.path.read_bytes() == saved
        assert standards(client)["calibration_revision"] == revision
        assert client.delete(f"/api/ranks/overall/{ENTITY}?version=us").status_code == 200
        reset = standards(client)
        assert reset["overall_overrides"] == {}
        assert reset["overall_curve"] == before["overall_curve"]


def test_history_card_and_pb_identity_follow_refresh_without_earned_celebration(app_state):
    app, service, store = app_state
    service.db.insert_pb(2, 4, "Standard", "igt", 450, None, "2026-09-07T01:00:00Z",
                         imported_from="manual", game_version="us")
    service.db.insert_pb(2, 4, "Standard", "igt", 460, None, "2026-09-07T02:00:00Z",
                         imported_from="manual", game_version="jp")
    saved = deepcopy(service.db.pbs())
    with TestClient(app) as client:
        before = client.get("/api/marelo").json()
        assert store.absorb(observations(100))["applied"]
        absorb_after_regrade(service)
        current = client.get("/api/marelo").json()
        history = client.get("/api/marelo/history").json()
        assert current["calibration_revision"] != before["calibration_revision"]
        assert current["marelo"] > before["marelo"]
        assert history["points"][-1]["marelo"] == pytest.approx(current["marelo"])
        assert history["calibration_revision"] == current["calibration_revision"]
        assert current["celebration"] is None
        assert service.db.pbs() == saved


def test_a_get_cannot_mix_a_new_snapshot_with_its_old_curve_or_response_header(app_state):
    app, service, store = app_state
    entered, resume = Event(), Event()

    @app.get("/api/calibration-probe")
    def probe():
        before = service.ranks.calibration_revision
        entered.set()
        assert resume.wait(15), "refresh did not release the controlled reader"
        return {"revision": before, "after": service.ranks.calibration_revision,
                "source": store.payload["targets"][0]["approaches"][0]["entries"][0]["time_cs"],
                "score": curves.score_for(service.ranks.overall_curve(ENTITY), 1500)}

    old = service.ranks.calibration_revision
    score = curves.score_for(service.ranks.overall_curve(ENTITY), 1500)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(client.get, "/api/calibration-probe")
        try:
            assert entered.wait(15), "controlled GET did not enter its reader"
            store.absorb(observations(100))
        finally:
            resume.set()
        response = pending.result(timeout=15)
        assert response.json() == {"revision": old, "after": old, "source": 1300, "score": score}
        assert response.headers["X-Rank-Calibration"] == old
        assert standards(client)["calibration_revision"] != old
