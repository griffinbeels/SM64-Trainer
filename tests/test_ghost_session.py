"""One ghost session for the app: enabled + a target that has a ghost = a
visible overlay showing that target's footage.

Everything here runs against a FAKE window controller. An agent may not put a
window on his screen, so what is proved is the decision — when the overlay
runs, when it is merely hidden, when it is ended, and what it is told to show.
What only a human can see is listed in the report.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.ghost.session import (
    DEFAULT_OPACITY,
    DEFAULT_VIEW,
    HUE_LIMIT_DEGREES,
    MAX_START_ATTEMPTS,
    NUDGE_LIMIT_FRAMES,
    GhostSession,
    follower_url,
)
from sm64_events.inputs.templates import TemplateStore
from sm64_events.server.ghost_clock import GhostClockFeed, clock_message
from sm64_events.storage.db import Database

from tests.ghost_fixture import FakeReplay, a_document, a_view

PORT = 8099


def _a_snapshot() -> GameSnapshot:
    """The shape the poll loop hands the clock feed, so a nudge read back here
    is read off the real wire format."""
    return GameSnapshot(
        wall_time_utc=datetime(2026, 9, 18, tzinfo=timezone.utc),
        global_timer=900, mario_action=0, mario_action_timer=0, num_stars=0,
        last_completed_course=0, last_completed_star=0,
        igt_overall=42, curr_level=24, curr_area=1)


class FakeController:
    """The overlay window's parent-side API, with nothing behind it.

    Mirrors `ghost/window/controller.py`: a value set while stopped is
    remembered and carried into the next start, and `stop()` returns only once
    the child is gone.
    """

    def __init__(self, *, url, opacity=DEFAULT_OPACITY, visible=False, **_kw):
        self.url = url
        self.opacity = opacity
        self.visible = bool(visible)
        #: where the child's own stream is sent; see the diagnostic-log test
        self.on_event = _kw.get("on_event")
        self.alive = False
        self.starts = 0
        self.stops = 0
        self.urls = [url]
        #: set by a test to make the child die the moment it is started
        self.dies_on_start = False

    def start(self):
        self.starts += 1
        self.alive = not self.dies_on_start

    def stop(self):
        self.stops += 1
        self.alive = False

    def is_running(self):
        return self.alive

    def set_url(self, url):
        self.url = url
        self.urls.append(url)

    def set_opacity(self, value):
        self.opacity = float(value)

    def set_visible(self, value):
        self.visible = bool(value)


class FakeTracker:
    """The two doors the session reads the practice target through, plus the
    database the preference is kept in."""

    def __init__(self, db=None, target=None, strat_tag=None):
        self.db = db
        self.target = target
        self.strat_tag = strat_tag


class FakeBroadcaster:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)
        return len(self.events)


def a_store(tmp_path, name="t.db") -> TemplateStore:
    db = Database(tmp_path / name)
    return TemplateStore(db._conn, db._lock)


def mark(store, *, kind="star", entity_key="24-1", strat="10 coin",
         attempt_id=7):
    return store.save(kind=kind, entity_key=entity_key, strat_tag=strat,
                      name="the good one", origin=f"attempt:{attempt_id}",
                      document=a_document())


def a_session(tmp_path, *, target=("star", 24, 1), strat="10 coin",
              saved=(7,), views=None, db=None, store=None, replay=None,
              controllers=None, clock=None):
    """A session over real template storage, a fake replay service and a fake
    window. Returns (session, controllers, tracker, store).

    `clock` is the REAL `GhostClockFeed` where a test cares about the nudge:
    it is the module the value has to reach, and a stand-in for it would prove
    only that the session called something.
    """
    store = a_store(tmp_path) if store is None else store
    if replay is None:
        replay = FakeReplay(saved=saved,
                            views=views or {7: a_view(7)})
    made = [] if controllers is None else controllers
    tracker = FakeTracker(db=db, target=target, strat_tag=strat)

    def factory(**kwargs):
        controller = FakeController(**kwargs)
        made.append(controller)
        return controller

    session = GhostSession(tracker=tracker, inputs=SimpleNamespace(templates=store),
                           replay=replay, broadcaster=FakeBroadcaster(),
                           clock=clock, port=PORT, controller_factory=factory)
    return session, made, tracker, store


def drive(session, *, resolve=True):
    """Run one refresh on a private loop, the way tests/test_ghost_clock.py
    drives the feed."""
    return asyncio.run(session.refresh(resolve=resolve))


# -- what the payload says ---------------------------------------------------

def test_a_session_with_no_target_opens_nothing_and_says_why(tmp_path):
    session, controllers, _tracker, _store = a_session(tmp_path, target=None)
    body = drive(session)
    assert body["target"] is None
    assert body["source"]["available"] is False
    assert body["source"]["reason"] == "no_target"
    assert body["source"]["reason_text"]
    assert body["window"] == {"running": False, "visible": False}
    assert controllers == []


def test_the_payload_carries_exactly_the_frozen_shape(tmp_path):
    session, _controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    body = drive(session)
    assert set(body) == {"enabled", "opacity", "tint", "view", "nudge_frames",
                         "target", "source", "window"}
    assert set(body["tint"]) == {"enabled", "hue_degrees"}
    assert set(body["target"]) == {"kind", "entity_key", "strat"}
    # The I1 answer WITHOUT the two keys only the overlay page needs.
    assert set(body["source"]) == {"available", "reason", "reason_text",
                                   "template_id", "attempt_id", "exactness",
                                   "mario_boxes_available",
                                   "mario_boxes_reason",
                                   "mario_boxes_reason_text"}
    assert set(body["window"]) == {"running", "visible"}
    assert body["target"] == {"kind": "star", "entity_key": "24-1",
                              "strat": "10 coin"}
    assert body["enabled"] is False and body["opacity"] == DEFAULT_OPACITY


def test_a_segment_target_resolves_through_the_same_door(tmp_path):
    """Rule 11 parity: stars and segments alike, even though the prototype
    only plays stars."""
    session, controllers, _tracker, store = a_session(
        tmp_path, target=("segment", 31), strat=None)
    mark(store, kind="segment", entity_key="31", strat=None)
    asyncio.run(session.update(enabled=True))
    body = session.payload()
    assert body["target"] == {"kind": "segment", "entity_key": "31",
                              "strat": None}
    assert body["source"]["available"] is True
    assert body["window"] == {"running": True, "visible": True}
    assert "kind=segment" in controllers[0].url
    assert "entity_key=31" in controllers[0].url


# -- the window ---------------------------------------------------------------

def test_enabling_over_an_available_ghost_shows_the_follower_page(tmp_path):
    session, controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    body = asyncio.run(session.update(enabled=True))
    assert body["enabled"] is True
    assert body["window"] == {"running": True, "visible": True}
    assert len(controllers) == 1
    window = controllers[0]
    assert window.starts == 1 and window.visible is True
    assert window.url == follower_url(body["target"], PORT)
    assert window.url.startswith(f"http://127.0.0.1:{PORT}/ui/ghost/index.html?")


def test_the_url_is_this_servers_own_port(tmp_path, monkeypatch):
    """The overlay is served by the same app it is watching, so the port is
    `core/paths.py::server_port()` and never a guess."""
    monkeypatch.setattr("sm64_events.core.paths.server_port", lambda: 8123)
    session, controllers, _tracker, store = a_session(tmp_path)
    session._port = None                    # resolve it the production way
    mark(store)
    asyncio.run(session.update(enabled=True))
    assert controllers[0].url.startswith("http://127.0.0.1:8123/ui/ghost/")


def test_a_target_with_no_ghost_hides_the_window_and_keeps_the_child(tmp_path):
    """An ordinary flip HIDES. Killing the child on every target change would
    make coming back cost a process start and a page load."""
    session, controllers, tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True))
    tracker.target = ("star", 15, 3)        # nothing marked for this one
    body = drive(session)
    assert body["source"]["reason"] == "no_template"
    assert body["source"]["reason_text"]
    assert body["window"] == {"running": True, "visible": False}
    assert len(controllers) == 1 and controllers[0].stops == 0


def test_a_retired_target_parks_the_window_rather_than_leaving_it_up(tmp_path):
    """ROUND 3, ITEM 5's other half. The tracker retires the active star when
    he walks into another course (`tracking/projection.py`), and the answer has
    to be the same as any other "nothing to show": the window PARKS.

    It must not keep the last target's footage on screen -- that is the shape
    of his report, LLL footage still playing in the castle -- and it must not
    kill the child either, because he walks back in a moment later.
    """
    session, controllers, tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True, nudge_frames=12))
    assert controllers[0].visible is True
    tracker.target = None
    body = drive(session)
    assert body["target"] is None
    assert body["source"]["reason"] == "no_target"
    assert body["window"] == {"running": True, "visible": False}
    assert controllers[0].stops == 0
    # The offset belonged to the star that is gone.
    assert body["nudge_frames"] == 0


def test_the_window_comes_back_when_the_target_has_a_ghost_again(tmp_path):
    session, controllers, tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True))
    tracker.target = ("star", 15, 3)
    drive(session)
    tracker.target = ("star", 24, 1)
    body = drive(session)
    assert body["window"] == {"running": True, "visible": True}
    assert controllers[0].starts == 1       # the same child, shown again


def test_disabling_ends_the_child_process(tmp_path):
    """Off means off: he asked for no ghost, so nothing of it is left
    running."""
    session, controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True))
    body = asyncio.run(session.update(enabled=False))
    assert body["window"] == {"running": False, "visible": False}
    assert controllers[0].stops == 1
    # and enabling again starts a fresh one rather than reviving the old
    asyncio.run(session.update(enabled=True))
    assert len(controllers) == 2 and controllers[1].is_running()


def test_stopping_the_session_leaves_no_child_behind(tmp_path):
    session, controllers, _tracker, store = a_session(tmp_path)
    mark(store)

    async def run():
        await session.start()
        await session.update(enabled=True)
        await session.stop()

    asyncio.run(run())
    assert controllers[0].stops == 1 and not controllers[0].is_running()
    assert session.payload()["window"] == {"running": False, "visible": False}


def test_the_server_lifespan_starts_and_stops_the_session():
    """The session's own `stop()` is unreachable unless the app calls it, and
    every unit below here passes unwired — the same shape as the poller's frame
    heartbeat. So this drives the REAL lifespan."""
    from fastapi.testclient import TestClient

    from sm64_events.detectors.star_grab import StarGrabDetector
    from sm64_events.server.app import create_app
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.server.poller import Poller

    class Recording:
        def __init__(self):
            self.calls = []

        async def start(self):
            self.calls.append("start")

        async def stop(self):
            self.calls.append("stop")

    class Offline:
        attached = False

        def attach(self):
            return False

        def detach(self):
            pass

    session = Recording()
    broadcaster = Broadcaster()
    poller = Poller(Offline(), [StarGrabDetector()], broadcaster)
    with TestClient(create_app(poller, broadcaster, ghost=session)):
        assert session.calls == ["start"]
    assert session.calls == ["start", "stop"]


def test_a_crashed_child_is_reported_and_started_again(tmp_path):
    session, controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True))
    controllers[0].alive = False             # the child died on its own
    body = drive(session, resolve=False)
    assert body["window"] == {"running": True, "visible": True}
    assert controllers[0].starts == 2        # restarted, same controller


def test_a_child_that_will_not_stay_up_stops_being_restarted(tmp_path):
    """A crash loop must not spawn a process twice a second forever."""
    session, controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True))
    controllers[0].dies_on_start = True
    controllers[0].alive = False
    for _ in range(10):
        drive(session, resolve=False)
    assert controllers[0].starts == MAX_START_ATTEMPTS
    assert session.payload()["window"] == {"running": False, "visible": False}


def test_the_opacity_reaches_the_running_window(tmp_path):
    session, controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True))
    asyncio.run(session.update(opacity=0.25))
    assert controllers[0].opacity == 0.25


# -- the nudge ----------------------------------------------------------------

def test_the_nudge_reaches_the_overlay_on_the_clock(tmp_path):
    """The overlay follows the live clock, and the nudge is part of the same
    rule, so the session publishes it on that feed. The real feed, because the
    point of this test is that the value arrives where the page reads it."""
    clock = GhostClockFeed()
    session, _controllers, _tracker, store = a_session(tmp_path, clock=clock)
    mark(store)
    asyncio.run(session.update(enabled=True))
    asyncio.run(session.update(nudge_frames=-12))
    assert session.payload()["nudge_frames"] == -12
    assert clock.nudge_frames == -12
    assert clock_message(_a_snapshot(), clock.nudge_frames)["nudge_frames"] == -12


def test_changing_the_nudge_never_re_points_the_window(tmp_path):
    """THE DEFECT THIS CLOSES. The nudge used to ride the page's URL, so every
    press made the session call `set_url` — which is how the window is told to
    LOAD ANOTHER PAGE. The ghost blinked off and re-seeked on every press, and
    a held key fired a stream of them."""
    session, controllers, _tracker, store = a_session(tmp_path,
                                                      clock=GhostClockFeed())
    mark(store)
    asyncio.run(session.update(enabled=True))
    window = controllers[0]
    opened = list(window.urls)
    for frames in (1, 2, 3, 4, 5, -20):
        asyncio.run(session.update(nudge_frames=frames))
    assert window.urls == opened, "the overlay page was navigated for a nudge"
    assert window.starts == 1 and window.stops == 0
    assert session.payload()["nudge_frames"] == -20


def test_a_target_change_resets_the_nudge_and_swaps_the_url(tmp_path):
    """His timing offset is about ONE thing he is practising. Carrying it onto
    the next star would put the next ghost silently out of step. The target IS
    another page, so this one does re-point the window."""
    clock = GhostClockFeed()
    session, controllers, tracker, store = a_session(tmp_path, clock=clock)
    mark(store)
    mark(store, entity_key="15-3", strat=None, attempt_id=7)
    asyncio.run(session.update(enabled=True))
    asyncio.run(session.update(nudge_frames=30))
    tracker.target, tracker.strat_tag = ("star", 15, 3), None
    body = drive(session)
    assert body["nudge_frames"] == 0
    assert "entity_key=15-3" in controllers[0].url
    assert clock.nudge_frames == 0


def test_a_strategy_change_alone_counts_as_a_new_target(tmp_path):
    session, _controllers, tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True, nudge_frames=9))
    tracker.strat_tag = "backflip"
    assert drive(session)["nudge_frames"] == 0


def test_a_clock_attached_after_the_fact_is_told_the_current_nudge(tmp_path):
    """`mount_ghost_routes` makes the join, and the app factory runs after the
    session is built. A page that opens later must not have to wait for the
    next press to learn where the ghost belongs."""
    session, _controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True, nudge_frames=7))
    clock = GhostClockFeed()
    session.attach_clock(clock)
    assert clock.nudge_frames == 7


def test_a_session_with_no_clock_still_works(tmp_path):
    """A window with no overlay feed (the db-less boot, a test app) must not
    lose the switch because the nudge has nowhere to go."""
    session, controllers, _tracker, store = a_session(tmp_path, clock=None)
    mark(store)
    asyncio.run(session.update(enabled=True, nudge_frames=4))
    assert session.payload()["nudge_frames"] == 4
    assert controllers[0].visible is True


# -- the colour treatment -----------------------------------------------------

def test_the_ghost_is_coloured_by_default_so_it_cannot_be_mistaken(tmp_path):
    """His report: *"It feels really hard to understand which one is the ACTUAL
    LIVE GAMEPLAY and which one is the GHOST gameplay."* So the treatment is on
    the first time the overlay is ever used, and at a shift big enough to see.

    The exact angle is a shipped preference and is deliberately NOT pinned
    (domain contract 7) -- what is pinned is that it is on, and that it is a
    real shift rather than zero, which would be the same picture with a
    preference switched on to say otherwise.
    """
    session, _controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    tint = drive(session)["tint"]
    assert tint["enabled"] is True
    assert 0 < tint["hue_degrees"] <= HUE_LIMIT_DEGREES


def test_the_colour_reaches_the_overlay_on_the_clock(tmp_path):
    """It travels the way the nudge does: the page is already reading this
    socket, so a change lands within one game frame and the window is never
    re-pointed at a URL. Dragging a colour slider through a page load per
    event is the failure this shape exists to avoid."""
    clock = GhostClockFeed()
    session, controllers, _tracker, store = a_session(tmp_path, clock=clock)
    mark(store)
    asyncio.run(session.update(enabled=True))
    opened = list(controllers[0].urls)
    asyncio.run(session.update(tint={"hue_degrees": 240}))
    assert session.payload()["tint"]["hue_degrees"] == 240
    assert clock.tint == {"enabled": True, "hue_degrees": 240}
    assert clock_message(_a_snapshot(), 0, clock.tint)["tint"]["hue_degrees"] == 240
    assert controllers[0].urls == opened, "the overlay page was navigated"


def test_each_half_of_the_colour_can_be_set_without_losing_the_other(tmp_path):
    """The control sends the half he touched: the switch on its own gesture,
    the angle on a trailing edge. Replacing the whole object here would throw
    away the angle he chose the moment he switched the treatment off and on."""
    session, _controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(tint={"hue_degrees": 300}))
    asyncio.run(session.update(tint={"enabled": False}))
    assert session.payload()["tint"] == {"enabled": False, "hue_degrees": 300}
    asyncio.run(session.update(tint={"enabled": True}))
    assert session.payload()["tint"] == {"enabled": True, "hue_degrees": 300}


def test_the_colour_survives_a_restart(tmp_path):
    """Persisted the way the opacity is: it is a look he chose once, and
    picking it again on every launch is the preference not working."""
    db = FakeStateDb()
    store = a_store(tmp_path)
    mark(store)
    session, _c, _t, _s = a_session(tmp_path, db=db, store=store)
    asyncio.run(session.update(tint={"enabled": False, "hue_degrees": 45}))
    again, _c2, _t2, _s2 = a_session(tmp_path, db=db, store=store)
    assert drive(again)["tint"] == {"enabled": False, "hue_degrees": 45}


def test_a_nonsense_stored_colour_never_breaks_the_session(tmp_path):
    db = FakeStateDb({"ghost_tint_enabled": "sure", "ghost_tint_hue": "blue"})
    store = a_store(tmp_path)
    mark(store)
    session, _c, _t, _s = a_session(tmp_path, db=db, store=store)
    tint = drive(session)["tint"]
    assert tint["enabled"] is True
    assert 0 <= tint["hue_degrees"] <= HUE_LIMIT_DEGREES


# -- what a bad value does ----------------------------------------------------

@pytest.mark.parametrize("changes", [
    {"opacity": 1.5}, {"opacity": -0.1}, {"opacity": "half"},
    {"nudge_frames": NUDGE_LIMIT_FRAMES + 1},
    {"nudge_frames": -NUDGE_LIMIT_FRAMES - 1},
    {"nudge_frames": 1.5}, {"enabled": "yes"},
    {"tint": {"hue_degrees": HUE_LIMIT_DEGREES + 1}},
    {"tint": {"hue_degrees": -1}}, {"tint": {"hue_degrees": 12.5}},
    {"tint": {"enabled": "on"}}, {"tint": {"shade": 3}}, {"tint": 120},
    {"view": "rotoscope"}, {"view": None}, {"view": "Mario"},
])
def test_an_out_of_range_value_is_refused_with_a_readable_sentence(tmp_path, changes):
    session, _controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    with pytest.raises(ValueError) as refusal:
        asyncio.run(session.update(**changes))
    sentence = str(refusal.value)
    assert sentence.endswith(".") and sentence[0].isupper()
    assert "Traceback" not in sentence


def test_a_refused_value_changes_nothing(tmp_path):
    session, controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True, opacity=0.4))
    with pytest.raises(ValueError):
        asyncio.run(session.update(opacity=9.0))
    assert session.payload()["opacity"] == 0.4
    assert controllers[0].opacity == 0.4


# -- persistence --------------------------------------------------------------

class FakeStateDb:
    def __init__(self, state=None):
        self.state = dict(state or {})

    def get_state(self, key, default):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value


def test_enabled_and_opacity_survive_a_restart(tmp_path):
    db = FakeStateDb()
    store = a_store(tmp_path)
    mark(store)
    session, _controllers, _tracker, _store = a_session(tmp_path, db=db, store=store)
    asyncio.run(session.update(enabled=True, opacity=0.8))
    assert db.state == {"ghost_enabled": True, "ghost_opacity": 0.8}

    again, controllers, _tracker, _store = a_session(tmp_path, db=db, store=store)
    body = drive(again)
    assert body["enabled"] is True and body["opacity"] == 0.8
    assert controllers[0].opacity == 0.8 and controllers[0].visible is True


def test_the_nudge_is_not_persisted(tmp_path):
    db = FakeStateDb()
    store = a_store(tmp_path)
    mark(store)
    session, _c, _t, _s = a_session(tmp_path, db=db, store=store)
    asyncio.run(session.update(enabled=True, nudge_frames=44))
    assert "ghost_nudge_frames" not in db.state
    again, _c2, _t2, _s2 = a_session(tmp_path, db=db, store=store)
    assert drive(again)["nudge_frames"] == 0


def test_a_nonsense_stored_preference_never_breaks_the_session(tmp_path):
    db = FakeStateDb({"ghost_enabled": "sure", "ghost_opacity": 12})
    store = a_store(tmp_path)
    mark(store)
    session, _controllers, _tracker, _store = a_session(tmp_path, db=db, store=store)
    body = drive(session)
    assert body["enabled"] is True          # truthy, coerced
    assert 0.0 <= body["opacity"] <= 1.0


def test_a_boot_with_no_database_still_answers(tmp_path):
    """A db-less boot (the instance-lock race) must not make the practice
    card's switch unreachable — it says why instead."""
    session, controllers, _tracker, _store = a_session(tmp_path, db=None)
    body = drive(session)
    assert body["enabled"] is False
    assert body["source"]["available"] is False
    assert controllers == []


# -- the broadcast ------------------------------------------------------------

def test_every_change_broadcasts_the_whole_shape_once(tmp_path):
    session, _controllers, tracker, store = a_session(tmp_path)
    mark(store)
    bus = session._broadcaster

    async def run():
        await session.start()
        assert bus.events == []             # the first read is not a change
        await session.update(enabled=True)
        assert [e.type for e in bus.events] == ["ghost_changed"]
        assert bus.events[0].payload == session.payload()
        await session.refresh()             # nothing moved
        assert len(bus.events) == 1
        tracker.target = ("star", 15, 3)    # a target change is a change
        await session.refresh()
        assert len(bus.events) == 2
        await session.stop()

    asyncio.run(run())


def test_the_session_publishes_where_nothing_is_journaled(tmp_path):
    """`ghost_changed` is broadcast-only. It goes out on the Broadcaster, which
    has no journal behind it, rather than through `TrackerService.publish`,
    which WRITES every type not named in its BROADCAST_ONLY set."""
    session, _controllers, tracker, store = a_session(tmp_path)
    mark(store)

    def journal(*_args, **_kwargs):
        raise AssertionError("ghost_changed must never reach the journal")

    tracker.publish = journal
    asyncio.run(session.update(enabled=True))
    assert [e.type for e in session._broadcaster.events] == ["ghost_changed"]


# -- the watcher --------------------------------------------------------------

def test_the_watcher_follows_the_target_without_anybody_asking(tmp_path):
    """The overlay has to follow what he is practising whether or not a
    browser tab is open, so the session runs its own slow watch."""
    session, controllers, tracker, store = a_session(tmp_path)
    session._period_s = 0.01
    mark(store)
    mark(store, entity_key="15-3", strat=None)

    async def run():
        await session.start()
        await session.update(enabled=True)
        tracker.target, tracker.strat_tag = ("star", 15, 3), None
        for _ in range(50):
            await asyncio.sleep(0.01)
            if "entity_key=15-3" in controllers[0].url:
                break
        await session.stop()
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(run())
    assert "entity_key=15-3" in controllers[0].url
    assert controllers[0].stops == 1


def test_the_watcher_survives_a_failing_cycle(tmp_path):
    session, _controllers, tracker, store = a_session(tmp_path)
    session._period_s = 0.01
    mark(store)

    class Exploding:
        @property
        def target(self):
            raise RuntimeError("the projector is mid-recovery")

        strat_tag = None
        db = None

    async def run():
        await session.start()
        session._tracker = Exploding()
        await asyncio.sleep(0.05)
        running = session._task is not None and not session._task.done()
        session._tracker = tracker
        await session.stop()
        return running

    assert asyncio.run(run()) is True


def test_a_first_ever_switch_on_is_see_through(tmp_path):
    """His first try was fully solid, so the ghost was indistinguishable from
    the emulator having vanished behind a black rectangle.

    The exact number is a shipped preference and his to move, so nothing here
    pins it (domain contract 7); what is pinned is that a player who has never
    chosen one can still see the game through the ghost.
    """
    assert 0.15 <= DEFAULT_OPACITY <= 0.85, (
        "a first switch-on must be see-through, not solid and not invisible")
    session, controllers, _tracker, store = a_session(tmp_path, db=None)
    mark(store)
    asyncio.run(session.update(enabled=True))
    assert session.payload()["opacity"] == DEFAULT_OPACITY
    assert controllers[0].opacity == DEFAULT_OPACITY


def test_the_child_is_given_somewhere_to_report(tmp_path, monkeypatch):
    """Whatever the overlay child prints goes into the same file it writes its
    own rectangles to. That stream is all that survives when the child dies
    before it can write anything itself -- a WebView2 that will not start."""
    from sm64_events.ghost.window import diag

    log = tmp_path / "data" / "ghost_window.jsonl"
    monkeypatch.setenv(diag.PATH_ENV, str(log))
    session, controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True))

    forward = controllers[0].on_event
    assert callable(forward), "the controller was not told where to report"
    forward({"event": "error", "detail": "WebView2Error: runtime not found"})

    written = diag.read(log)
    assert any(entry["event"] == "window_starting" for entry in written)
    said = next(entry for entry in written if entry["event"] == "child_said")
    assert said["level"] == "error"
    assert said["said"]["detail"] == "WebView2Error: runtime not found"


def test_the_diagnostic_log_never_stops_the_window(tmp_path, monkeypatch):
    from sm64_events.ghost.window import diag

    def explode(*_args, **_kwargs):
        raise RuntimeError("the disk is gone")

    monkeypatch.setattr(diag, "record", explode)
    session, controllers, _tracker, store = a_session(tmp_path)
    mark(store)
    asyncio.run(session.update(enabled=True))
    assert controllers[0].is_running() is True


# -- what the ghost shows ----------------------------------------------------

def test_the_ghost_shows_the_whole_picture_until_he_asks_otherwise(tmp_path):
    """The ghost he already knows. Most clips have no Mario boxes, so a
    default of "mario" would ship a ghost that silently fell back."""
    session, _c, _t, store = a_session(tmp_path)
    mark(store)
    assert drive(session)["view"] == DEFAULT_VIEW == "whole"


def test_the_view_reaches_the_overlay_on_the_clock(tmp_path):
    """Same wire as the nudge and the colour, and the same reason: what the
    ghost SHOWS is something the running page can take, so changing it must
    never re-point the window at a URL."""
    clock = GhostClockFeed()
    session, controllers, _t, store = a_session(tmp_path, clock=clock)
    mark(store)
    asyncio.run(session.update(enabled=True))
    opened = list(controllers[0].urls)
    asyncio.run(session.update(view="mario"))
    assert session.payload()["view"] == "mario"
    assert clock.view == "mario"
    assert clock_message(_a_snapshot(), 0, None, clock.view)["view"] == "mario"
    assert controllers[0].urls == opened, "the overlay page was navigated"


def test_asking_for_mario_on_a_ghost_with_no_boxes_is_not_an_error(tmp_path):
    """The setting is app-wide and he walks between stars. Refusing it would
    jam the control on the first ghost without boxes; the page falls back to
    the whole picture and the card says why."""
    session, _c, _t, store = a_session(tmp_path)
    mark(store)
    body = asyncio.run(session.update(view="mario"))
    assert body["view"] == "mario"
    assert body["source"]["mario_boxes_available"] is False


def test_the_view_survives_a_restart(tmp_path):
    db = FakeStateDb()
    store = a_store(tmp_path)
    mark(store)
    session, _c, _t, _s = a_session(tmp_path, db=db, store=store)
    asyncio.run(session.update(view="mario"))
    again, _c2, _t2, _s2 = a_session(tmp_path, db=db, store=store)
    assert drive(again)["view"] == "mario"


def test_a_stored_view_this_build_cannot_draw_falls_back(tmp_path):
    db = FakeStateDb({"ghost_view": "rotoscope"})
    store = a_store(tmp_path)
    mark(store)
    session, _c, _t, _s = a_session(tmp_path, db=db, store=store)
    assert drive(session)["view"] == DEFAULT_VIEW


def test_the_card_is_told_whether_this_ghost_can_be_drawn_as_mario(tmp_path):
    """One bit and one sentence, not the boxes themselves: a list is one
    rectangle per picture, and `ghost_changed` broadcasts this shape every
    time anything moves."""
    session, _c, _t, store = a_session(tmp_path)
    mark(store)
    source = drive(session)["source"]
    assert source["mario_boxes_available"] is False
    assert source["mario_boxes_reason"] == "no_box_list"
    assert "Mario" in source["mario_boxes_reason_text"]
    assert "mario_boxes" not in source, "the whole list rode the broadcast"
