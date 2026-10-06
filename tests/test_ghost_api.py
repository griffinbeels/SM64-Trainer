"""The native session REST routes and readable validation failures."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from sm64_events.server.ghost_api import create_ghost_router

class FakeSession:
    """The two calls the routes make, and nothing else."""

    def __init__(self):
        self.state = {"enabled": False, "opacity": 0.5, "nudge_frames": 0,
                      "tint": {"enabled": True, "hue_degrees": 120},
                      "target": None, "source": {"available": False},
                      "window": {"running": False, "visible": False}}
        self.refreshes = []

    async def refresh(self, *, resolve=True):
        self.refreshes.append(resolve)
        return dict(self.state)

    async def update(self, changes):
        from sm64_events.ghost.session import validate_changes
        self.state.update(validate_changes(changes))
        return dict(self.state)


def session_client(session) -> TestClient:
    app = FastAPI()
    app.include_router(create_ghost_router(session=session))
    return TestClient(app)


def test_reading_the_ghost_answers_the_whole_shape_without_forcing_a_resolve():
    """Every page load reads this, and a forced resolve cost 86 ms of CPU a
    read (2026-09-23). The session re-resolves on its own whenever what the
    answer reads has moved (tests/test_ghost_session.py), so the read only
    reconciles."""
    session = FakeSession()
    body = session_client(session).get("/api/ghost").json()
    assert set(body) == {"enabled", "opacity", "tint", "nudge_frames",
                         "target", "source", "window"}
    assert session.refreshes == [False]


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
    ({"tint": {"hue_degrees": 400}}, "colour wheel"),
    ({"tint": {"shade": "blue"}}, "no setting called"),
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
    # Unknown rather than a guess, the same rule the opacity follows: the
    # shipped colour is ON, so answering "off" here would show him a plain
    # ghost and claim it was something he had already turned off.
    assert state["tint"] is None
    assert state["target"] is None
    assert state["window"] == {"running": False, "visible": False}
    assert state["source"]["available"] is False
    assert state["source"]["reason"] == "no_session"
    assert "this window" in state["source"]["reason_text"]
    assert unmanned.put("/api/ghost", json={"enabled": True}).status_code == 503


def test_retired_video_source_route_is_not_mounted():
    assert session_client(None).get("/api/ghost/source").status_code == 404
