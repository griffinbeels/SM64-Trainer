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

from tests.ghost_fixture import (
    FakeAttempt, FakeReplay, a_document, a_real_clip_view, a_view)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "t.db")


def a_service(db, *attempts) -> InputsService:
    """The captured-input service the route reads BOTH doors of: the template
    store, and `attempt` for the frames the span is clamped to."""
    return InputsService(db.inputs, db.input_templates, lambda: list(attempts))


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


def test_the_answer_carries_the_span_the_ghost_is_clamped_to(db):
    """His ruling, over his own clip: the ghost is exactly the attempt, so the
    45 pictures of the previous run and the fifteen after the star are out."""
    mark(db, origin="attempt:9")
    replay = FakeReplay(saved=[9], views={9: a_real_clip_view(9)})
    row = FakeAttempt(id=9, anchor_frame=308677, rta_frames=584,
                      igt_frames=584, timed_at="xcam")
    body = ask(client(a_service(db, row), replay)).json()
    assert body["span"] == {"first_picture": 45, "last_picture": 626,
                            "start_frame": 308677, "end_frame": 309261,
                            "grab_frame": None, "basis": "attempt_frames"}
    assert len(body["view"]["frame_map"]) == 642


def test_a_ghost_whose_run_left_the_history_is_served_unclamped(db):
    """`span: null` is an answer the page can act on; refusing the footage
    would hide a run he can still watch."""
    mark(db)
    replay = FakeReplay(saved=[7], views={7: a_view(7)})
    body = ask(client(a_service(db), replay)).json()
    assert body["available"] is True and body["span"] is None


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


# -- the session doors: GET / PUT /api/ghost ---------------------------------

class FakeSession:
    """The two calls the routes make, and nothing else."""

    def __init__(self):
        self.state = {"enabled": False, "opacity": 0.5, "nudge_frames": 0,
                      "target": None, "source": {"available": False},
                      "window": {"running": False, "visible": False}}
        self.refreshes = 0

    async def refresh(self):
        self.refreshes += 1
        return dict(self.state)

    async def update(self, changes):
        from sm64_events.ghost.session import validate_changes
        self.state.update(validate_changes(changes))
        return dict(self.state)


def session_client(session) -> TestClient:
    app = FastAPI()
    app.include_router(create_ghost_router(None, None, session=session))
    return TestClient(app)


def test_reading_the_ghost_answers_the_whole_shape_freshly_resolved():
    """A cached `available` is what would leave the switch enabled over a
    template he has just deleted, so the read re-resolves."""
    session = FakeSession()
    body = session_client(session).get("/api/ghost").json()
    assert set(body) == {"enabled", "opacity", "nudge_frames", "target",
                         "source", "window"}
    assert session.refreshes == 1


def test_setting_the_ghost_answers_the_same_shape():
    body = session_client(FakeSession()).put(
        "/api/ghost", json={"enabled": True, "opacity": 0.25}).json()
    assert body["enabled"] is True and body["opacity"] == 0.25
    assert set(body) >= {"target", "source", "window"}


@pytest.mark.parametrize("body,word", [
    ({"opacity": 4}, "see-through"),
    ({"nudge_frames": 10_000}, "nudged"),
    ({"enabled": "on"}, "on or off"),
    ({"opactiy": 0.5}, "no setting called"),
])
def test_a_value_it_cannot_take_is_422_with_one_readable_sentence(body, word):
    response = session_client(FakeSession()).put("/api/ghost", json=body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str) and word in detail


def test_without_a_session_the_read_says_so_and_the_write_is_refused():
    """The practice card reads /api/ghost on every page that mounts it, so a
    window with no session (a broadcast-only second server, every UI fixture)
    must get an ANSWER, not an error: the full gate on 91041033 went red on 40
    browser tests whose only fault was "HTTP 503 /api/ghost" in the page's
    problem list. The read is 200 in the agreed shape with the reason as a
    sentence; only the write, which genuinely cannot happen here, is a 503."""
    unmanned = session_client(None)
    read = unmanned.get("/api/ghost")
    assert read.status_code == 200
    state = read.json()
    assert state["enabled"] is False and state["opacity"] is None
    assert state["target"] is None
    assert state["window"] == {"running": False, "visible": False}
    assert state["source"]["available"] is False
    assert state["source"]["reason"] == "no_session"
    assert "this window" in state["source"]["reason_text"]
    assert unmanned.put("/api/ghost", json={"enabled": True}).status_code == 503
