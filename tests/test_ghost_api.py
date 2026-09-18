"""The ghost-source route is a skin over `ghost/source.py`: one read, the
agreed JSON, and this project's exception taxonomy (404 / 409 / 503).

It answers 200 with `available: false` for every ordinary "no ghost here"
case, because "this star has no footage yet" is an answer, not an error — a
switch that has to read an error body to know it should be disabled would
also have to guess at a network failure.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.inputs.service import InputsService
from sm64_events.server.ghost_api import create_ghost_router
from sm64_events.storage.db import Database

from tests.ghost_fixture import FakeReplay, a_document, a_view


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


def a_service(db) -> InputsService:
    return InputsService(db.inputs, db.input_templates, lambda: [])


def mark(db, *, kind="star", entity_key="24-1", strat_tag="10 coin",
         origin="attempt:7", target="star 24 1"):
    return db.input_templates.save(kind=kind, entity_key=entity_key,
                                   strat_tag=strat_tag, name="the good one",
                                   origin=origin, document=a_document(target))


def client(inputs, replay) -> TestClient:
    app = FastAPI()
    app.include_router(create_ghost_router(inputs, replay))
    return TestClient(app)


def ask(client, kind="star", entity_key="24-1", strat="10 coin"):
    params = {"kind": kind, "entity_key": entity_key}
    if strat is not None:
        params["strat"] = strat
    return client.get("/api/ghost/source", params=params)


def test_a_ready_ghost_answers_with_its_footage_and_view(db):
    template = mark(db)
    replay = FakeReplay(saved=[7], views={7: a_view(7)})
    body = ask(client(a_service(db), replay)).json()
    assert body["available"] is True
    assert body["reason"] is None and body["reason_text"] is None
    assert body["template_id"] == template.id
    assert body["attempt_id"] == 7
    assert body["media_url"] == "/api/replay/saved/7"
    assert body["exactness"] == "frame_map"
    assert body["view"]["frame_map"] == [11, 11, 12, 13]
    assert body["view"]["picture_igt"] == [100, 100, 101, 102]


def test_no_ghost_is_a_200_carrying_the_reason_to_show(db):
    response = ask(client(a_service(db), FakeReplay()))
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["reason"] == "no_template"
    assert body["reason_text"].endswith(".")


def test_the_strategy_is_optional(db):
    mark(db, strat_tag=None)
    replay = FakeReplay(saved=[7], views={7: a_view(7)})
    assert ask(client(a_service(db), replay), strat=None).json()["available"]


def test_a_segment_is_asked_for_the_same_way(db):
    mark(db, kind="segment", entity_key="12", strat_tag=None,
         origin="attempt:9", target="segment 12")
    replay = FakeReplay(saved=[9], views={9: a_view(9)})
    body = ask(client(a_service(db), replay), kind="segment",
               entity_key="12", strat=None).json()
    assert (body["available"], body["attempt_id"]) == (True, 9)


def test_an_unknown_kind_is_refused(db):
    response = ask(client(a_service(db), FakeReplay()), kind="course")
    assert response.status_code == 409


def test_a_request_that_names_no_target_is_rejected_by_the_route(db):
    assert client(a_service(db), FakeReplay()).get(
        "/api/ghost/source").status_code == 422


def test_without_recordings_the_answer_is_unavailable_not_a_wrong_no(db):
    """Replay off is a 503: the truthful answer is "cannot tell", and a
    `no_template` here would disable his switch for a reason that is false."""
    mark(db)
    assert ask(client(a_service(db), None)).status_code == 503


def test_a_boot_with_no_database_yet_answers_503(db):
    def not_ready():
        raise RuntimeError("database unavailable")

    replay = FakeReplay(saved=[7], views={7: a_view(7)})
    assert ask(client(not_ready, replay)).status_code == 503


def test_the_service_is_resolved_per_request(db):
    """Same reason `create_inputs_router` takes a callable: the database can
    arrive after boot, and the router is built once."""
    ready = {"service": None}
    replay = FakeReplay(saved=[7], views={7: a_view(7)})
    session = client(lambda: ready["service"] or _raise(), replay)
    assert ask(session).status_code == 503
    mark(db)
    ready["service"] = a_service(db)
    assert ask(session).json()["available"] is True


def _raise():
    raise RuntimeError("database unavailable")
