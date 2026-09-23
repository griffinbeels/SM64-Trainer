"""The ghost session resolves when its answer can have moved, not on a timer.

A resolve -- which ghosts exist for the target and whether each can play --
cost 86 ms of CPU on his PB and ran every 4 s whatever happened, switch off
included (2026-09-23: ~21 ms CPU a second for nothing). These pin the new
contract: idle ticks resolve nothing; a moved target, template set, saved-clip
set or database resolves on the very next tick; a PUT resolves only when it
changes which ghosts, which view or the switch; and a backstop resolve runs
every `SAFETY_RESOLVE_S` while the switch is on, so an event nobody signalled
is still picked up.
"""
import asyncio
from types import SimpleNamespace

import pytest

import sm64_events.ghost.session as session_module
from sm64_events.ghost.session import SAFETY_RESOLVE_S

from tests.test_ghost_session import a_session, mark


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(session_module, "monotonic", lambda: now[0])
    return now


def tick(session, clock, seconds=0.5):
    """One watch tick, exactly as `_watch` decides it."""
    clock[0] += seconds
    return asyncio.run(session.refresh(resolve=session._safety_resolve_due()))


def switched(session, on):
    session._load_persisted()
    session._values["enabled"] = on


@pytest.mark.parametrize("on", [True, False])
def test_idle_ticks_resolve_nothing(tmp_path, clock, on):
    session, _made, _tracker, store = a_session(tmp_path)
    mark(store)
    tick(session, clock)                       # the first read resolves once
    switched(session, on)
    viewed = list(session._replay.viewed)
    for _ in range(80):                        # 40 s of ticks
        tick(session, clock)
    assert session._replay.viewed == viewed


def test_the_backstop_resolves_while_on_and_never_while_off(tmp_path, clock):
    session, _made, _tracker, store = a_session(tmp_path)
    mark(store)
    tick(session, clock)
    switched(session, False)
    before = len(session._replay.viewed)
    tick(session, clock, SAFETY_RESOLVE_S * 3)
    assert len(session._replay.viewed) == before
    switched(session, True)
    tick(session, clock, SAFETY_RESOLVE_S)
    assert len(session._replay.viewed) > before


def test_a_change_nobody_signalled_is_found_by_the_backstop(tmp_path, clock):
    """The missed event: a clip saved by a path that tells the session
    nothing (the fake replay has no saved-clip counter). The answer stays
    stale until the backstop, then corrects itself -- the guarantee that no
    missed event can leave the ghost wrong for longer than SAFETY_RESOLVE_S."""
    session, _made, _tracker, store = a_session(tmp_path, saved=())
    mark(store)
    assert tick(session, clock)["source"]["available"] is False
    switched(session, True)
    session._replay._saved.add(7)
    assert tick(session, clock, SAFETY_RESOLVE_S / 2)["source"]["available"] is False
    assert tick(session, clock, SAFETY_RESOLVE_S / 2)["source"]["available"] is True


def test_a_marked_template_resolves_on_the_next_tick(tmp_path, clock):
    session, _made, _tracker, store = a_session(tmp_path)
    assert tick(session, clock)["source"]["available"] is False
    mark(store)                                # the store's generation moves
    assert tick(session, clock)["source"]["available"] is True


def test_a_saved_clip_resolves_on_the_next_tick(tmp_path, clock):
    session, _made, _tracker, store = a_session(tmp_path, saved=())
    session._replay.saved_generation = 0
    mark(store)
    assert tick(session, clock)["source"]["available"] is False
    session._replay._saved.add(7)
    session._replay.saved_generation += 1      # what ReplayService does on a save
    assert tick(session, clock)["source"]["available"] is True


def test_a_target_change_resolves_at_once(tmp_path, clock):
    session, _made, tracker, store = a_session(tmp_path, target=None)
    mark(store)
    assert tick(session, clock)["source"]["available"] is False
    tracker.target = ("star", 24, 1)
    assert tick(session, clock)["source"]["available"] is True


def test_a_slider_put_does_not_resolve_but_a_view_or_the_switch_does(tmp_path, clock):
    session, _made, _tracker, store = a_session(tmp_path)
    mark(store)
    tick(session, clock)
    before = len(session._replay.viewed)
    asyncio.run(session.update(opacity=0.3))
    asyncio.run(session.update(nudge_frames=4))
    assert len(session._replay.viewed) == before
    asyncio.run(session.update(enabled=True))
    assert len(session._replay.viewed) > before


# -- what a resolve asks -------------------------------------------------------

def test_only_mario_asks_the_whole_picture_no_more_than_it_needs(tmp_path):
    session, _made, _tracker, _store = a_session(tmp_path)
    asked = []

    def whole(target, ghost):
        asked.append(ghost)
        return {"available": ghost == "b", "reason_text": None}
    session._resolve_video_source = whole
    session._values["view"] = "whole"
    assert session._row_video() is whole       # every row is shown this way
    session._values["view"] = "mario"
    session._source = {"whole_available": True}
    rows = [session._row_video()(None, name) for name in "abc"]
    assert asked == [] and {row["available"] for row in rows} == {None}
    session._source = {"whole_available": False}
    video_of = session._row_video()
    assert [video_of(None, name)["available"] for name in "abcd"] == [False, True, None, None]
    assert asked == ["a", "b"]


class _Library:
    def pb_for(self, *_):
        return SimpleNamespace(attempt_id=9, frames=300)


@pytest.mark.parametrize("pending,reason", [
    (True, "This PB's video is still being saved."),
    (False, "There is no saved video for this PB."),
])
def test_the_pb_row_says_still_saving_only_while_a_save_runs(tmp_path, pending, reason):
    session, _made, _tracker, _store = a_session(tmp_path)
    session.library = lambda inputs=None: _Library()
    session.video_pending(9, pending)
    row = session._refresh_pb({"kind": "star", "entity_key": "24-1", "strat": "10 coin"},
                              lambda *_: {"available": False, "reason_text": "none", "attempt_id": None})
    assert row["whole_available"] is False and row["whole_reason_text"] == reason
    session.video_pending(9, False)
    assert 9 not in session._videos_pending


def test_the_candidate_cache_never_outlives_a_view_or_a_world_change():
    from sm64_events.ghost.selection import GhostSelection
    ghost = SimpleNamespace(attempt_id=5, frames=300, strat_tag="Standard")
    library = SimpleNamespace(ghosts_for=lambda *_: [ghost])
    target = {"kind": "star", "entity_key": "24-1", "strat": "Standard"}
    selection, asked = GhostSelection(), []

    def video_of(_target, _ghost):
        asked.append(1)
        return {"available": True, "reason_text": None}
    selection.candidates(target, library, video_of, None, variant=("mario", 1))
    selection.candidates(target, library, video_of, None, variant=("mario", 1))
    assert len(asked) == 1                          # the short cache still serves a burst
    selection.candidates(target, library, video_of, None, variant=("whole", 1))
    selection.candidates(target, library, video_of, None, variant=("whole", 2))
    assert len(asked) == 3
