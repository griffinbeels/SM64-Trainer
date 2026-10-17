"""Native playback controls, ownership and refusal through the ghost session."""
import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from sm64_events.ghost import settings
from sm64_events.ghost.native_session import LATE_JOIN_FRAMES, NATIVE_BUDGET_REASON, NativeGhost, budget_reason
from sm64_events.ghost.native_layout import GP_WRONG_SCENE
from tests.test_ghost_session import a_session, mark
from tests.test_ui_ghost_session import controls, run_node


def default_ghosts(ghost, target, store):
    """Resolve the target's default choice (PB box ticked, nothing else) the
    way the session does, over a store with no PBs."""
    from sm64_events.ghost.pb import GhostLibrary
    from sm64_events.ghost.selection import DEFAULT_CHOICE, chosen_ghosts
    return ghost.resolve(target, chosen_ghosts(DEFAULT_CHOICE, GhostLibrary(store, None), target)[0])


class Capture:
    def __init__(self):
        self.commands = []
        self.displayed = False

    def set_playback(self, *, blocking=True, **command):
        self.commands.append(command)
        return True

    def snapshot_status(self, *, blocking=True):
        return {"available": True, "state": 4, "playback_reason": 2,
                "producer": SimpleNamespace(configuration=2, epoch=3),
                "displayed": self.displayed}


def native_session(tmp_path, *, asset=True):
    session, _, tracker, store = a_session(tmp_path)
    source = Capture()
    manifest = {"clip_id": "ab" * 16, "sha256": "cd" * 32, "blink_frames": 0, "inputs_complete": True}
    replay = SimpleNamespace(recorder=SimpleNamespace(ghost_capture=source),
                             native_identity=lambda _: {"native_clock_frame": 100},
                             find_native=lambda _: (tmp_path / "run.ghost", manifest) if asset else None)
    session._native = NativeGhost(replay, origin_of=lambda *_: {
        "kind": "star", "session_id": 1, "arm_id": 9,
        "frame": 100, "utc": "2026-09-21T08:00:00+00:00"})
    mark(store)
    return session, source, tracker


def test_adaptive_preparation_is_ready_before_a_certified_start_and_never_late(tmp_path, monkeypatch):
    session, source, _ = native_session(tmp_path)
    native = session._native
    target = {"kind": "star", "entity_key": "24-1", "strat": "10 coin"}
    ghost = SimpleNamespace(id=17, origin="attempt:7", frames=30, pb=True, hue_degrees=48)
    controls = settings.native_controls(settings.defaults())
    origin = {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
              "utc": "2026-09-21T08:00:00+00:00", "full_start_token": ("star", 1, 9),
              "full_start_now": True}
    native._origin_of = lambda *_: origin
    served = source.snapshot_status
    generations = {"prepared_generation": 0, "active_generation": 0}
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), **generations}
    answer = native.prepare(target, [ghost], controls, 7)
    assert answer["available"] and answer["generation"] == 7
    prepared = source.commands[-1]
    assert not prepared["enabled"] and "origin" not in prepared
    assert prepared["prepare_generation"] == 7 and prepared["activate_generation"] == 0
    assert prepared["clips"][0]["hue_degrees"] == 48
    native.reconcile(enabled=True, target=target, controls={**controls, "opacity": .7})
    assert source.commands[-1]["prepare_generation"] == 7
    assert source.commands[-1]["opacity"] == .7
    # The poll entry point must not consult a clip, a template or the disk.
    monkeypatch.setattr(native, "resolve", lambda *_: pytest.fail("poll resolved assets"))
    monkeypatch.setattr(native, "_fingerprint", lambda *_: pytest.fail("poll inspected files"))
    native.note()
    assert source.commands[-1]["activate_generation"] == 0
    generations["prepared_generation"] = 7
    native.note()  # readiness arrived after this start was already consumed; no elapsed count, so no late join
    assert source.commands[-1]["activate_generation"] == 0
    # A load/rebase is never a start, even with a different clock and arm.
    origin = {**origin, "frame": 120, "arm_id": 10, "full_start_now": False,
              "full_start_token": ("star", 1, 10)}
    native.note()
    assert source.commands[-1]["activate_generation"] == 0
    origin = {**origin, "arm_id": 11, "full_start_now": True, "full_start_token": ("star", 1, 11)}
    native.note()
    assert source.commands[-1]["activate_generation"] == 7
    assert source.commands[-1]["activate_start"] == 2
    assert native.state()["prepared_generation"] == 7
    assert native.state()["active_generation"] == 0  # native adoption is its own acknowledgement
    native.stop()


@pytest.mark.parametrize("scope", [None, "strategy", "overall"])
def test_new_preparation_and_switch_off_preserve_the_certified_start_gate(tmp_path, scope):
    session, source, _ = native_session(tmp_path)
    native = session._native
    target = {"kind": "star", "entity_key": "24-1", "strat": "10 coin"}
    if scope is not None:
        target["scope"] = scope
    ghost = SimpleNamespace(id=17, origin="attempt:7", frames=30, pb=True)
    controls = settings.native_controls(settings.defaults())
    origin = {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
              "utc": "2026-09-21T08:00:00+00:00", "full_start_token": (1, 9), "full_start_now": True}
    native._origin_of = lambda *_: origin
    served = source.snapshot_status
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), "prepared_generation": 1}
    native.prepare(target, [ghost], controls, 1)
    native.note()
    assert source.commands[-1]["activate_generation"] == 1
    selected = native._selected_asset
    # Missing scope and explicit Strategy have the same native owner.
    target = {**target, "scope": scope or "strategy"}
    native.prepare(target, [ghost], controls, 2)
    native.reconcile(enabled=True, target=target, controls={**controls, "opacity": .7})
    assert source.commands[-1]["prepare_generation"] == 2 and source.commands[-1]["activate_generation"] == 0
    assert source.commands[-1]["enabled"] and source.commands[-1]["opacity"] == .7
    assert native._selected_asset is selected
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), "prepared_generation": 2}
    native.note()
    assert source.commands[-1]["activate_generation"] == 0
    native.reconcile(enabled=False, target=target, controls=controls)
    assert source.commands[-1] == {"enabled": False}
    native.reconcile(enabled=True, target=target, controls=controls)
    assert source.commands[-1]["prepare_generation"] == 2 and source.commands[-1]["activate_generation"] == 0
    native.stop()


@pytest.mark.parametrize("reconcile_first", [False, True])
@pytest.mark.parametrize(("ownership_field", "other_value"), [
    ("strat", "Single jump ending"), ("scope", "overall"),
])
def test_strategy_or_scope_swap_revokes_the_active_roster_and_return_waits_for_a_new_start(
        tmp_path, monkeypatch, reconcile_first, ownership_field, other_value):
    session, source, _ = native_session(tmp_path)
    native = session._native
    standard = {"kind": "star", "entity_key": "2-2", "strat": "Standard"}
    alternate = {**standard, ownership_field: other_value}
    controls = settings.native_controls(settings.defaults())
    ghosts = [SimpleNamespace(id=index, origin=f"attempt:{index}", frames=30, pb=True)
              for index in range(1, 3)]
    manifests = {index: {"clip_id": f"{index:02x}" * 16, "sha256": "cd" * 32, "blink_frames": 0, "inputs_complete": True}
                 for index in range(1, 3)}
    native._replay.find_native = lambda attempt: (tmp_path / f"{attempt}.ghost", manifests[attempt])
    origin = {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
              "utc": "2026-09-21T08:00:00+00:00", "full_start_token": (1, 9), "full_start_now": True}

    native._origin_of = lambda kind, entity_key: origin
    served = source.snapshot_status
    generations = {"prepared_generation": 1, "active_generation": 0}
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), **generations}
    native.prepare(standard, [ghosts[0]], controls, 1)
    native.reconcile(enabled=True, target=standard, controls=controls)
    native.note()
    assert source.commands[-1]["activate_generation"] == 1 and source.commands[-1]["enabled"]
    generations["active_generation"] = 1

    def switch(target, ghost, generation):
        if reconcile_first:
            native.reconcile(enabled=True, target=target, controls=controls)
            assert source.commands[-1] == {"enabled": False}
        resolve = native.resolve

        def resolve_after_revocation(*args):
            assert native._selected_asset is native._asset is None
            assert source.commands[-1] == {"enabled": False}
            return resolve(*args)

        with monkeypatch.context() as patch:
            patch.setattr(native, "resolve", resolve_after_revocation)
            native.prepare(target, [ghost], controls, generation)
        native.reconcile(enabled=True, target=target, controls=controls)
        generations["prepared_generation"] = generation
        native.note()
        # The renderer can observe only the last command, missing the OFF.
        # Even with its previous active acknowledgement, that command stays off.
        assert not source.commands[-1]["enabled"]
        assert source.commands[-1]["activate_generation"] == 0
        assert native._selected_asset is None
        assert native.state()["active_generation"] == 0

    switch(alternate, ghosts[1], 3)
    origin = {**origin, "arm_id": 10, "full_start_token": (1, 10)}
    native.note()
    assert source.commands[-1]["activate_generation"] == 3 and source.commands[-1]["enabled"]
    assert native._selected_asset[0][1]["clip_id"] == manifests[2]["clip_id"]
    generations["active_generation"] = 3
    switch(standard, ghosts[0], 4)
    origin = {**origin, "arm_id": 11, "full_start_token": (1, 11)}
    native.note()
    assert source.commands[-1]["activate_generation"] == 4 and source.commands[-1]["enabled"]
    assert source.commands[-1]["activate_start"] == 3
    assert native._selected_asset[0][1]["clip_id"] == manifests[1]["clip_id"]
    native.stop()


@pytest.mark.parametrize("reconcile_first", [False, True])
def test_scope_round_trip_with_identical_clips_never_reuses_the_same_start(tmp_path, reconcile_first):
    session, source, _ = native_session(tmp_path)
    native = session._native
    target = {"kind": "star", "entity_key": "2-2", "strat": "Standard"}
    ghost = SimpleNamespace(id=17, origin="attempt:7", frames=30, pb=True)
    controls = settings.native_controls(settings.defaults())
    origin = {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
              "utc": "2026-09-21T08:00:00+00:00", "full_start_token": (1, 9), "full_start_now": True}
    native._origin_of = lambda kind, entity_key: origin
    served = source.snapshot_status
    generations = {"prepared_generation": 1, "active_generation": 1}
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), **generations}
    native.prepare(target, [ghost], controls, 1)
    native.note()
    assert source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 1
    for generation, scope in enumerate(("overall", "strategy"), 2):
        target = {**target, "scope": scope}
        if reconcile_first:
            native.reconcile(enabled=True, target=target, controls=controls)
            assert source.commands[-1] == {"enabled": False}
        native.prepare(target, [ghost], controls, generation)
        native.reconcile(enabled=True, target=target, controls=controls)
        generations["prepared_generation"] = generation
        native.note()
        assert not source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 0
        assert native._selected_asset is None
    origin = {**origin, "arm_id": 10, "full_start_token": (1, 10)}
    native.note()
    assert source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 3
    assert source.commands[-1]["activate_start"] == 2
    native.stop()


def test_scope_switch_revokes_native_owner_before_population_failure(tmp_path):
    from sm64_events.ghost.rivals import Rivals
    from sm64_events.storage.db import Database
    from tests.test_ghost_adaptive import Standards, attempt, save

    session, source, tracker = native_session(tmp_path)
    native = session._native
    tracker.db, tracker.ranks = Database(tmp_path / "scope-failure.db"), Standards()
    row = attempt(7, 30, course_id=24, star_id=1, strat_tag="10 coin",
                  native_clock_frame=100, closed_frame=130)
    tracker.db.upsert_attempt(row)
    save(tracker.db, row)
    target = session._read_target()
    origin = {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
              "utc": "2026-09-21T08:00:00+00:00", "full_start_token": (1, 9), "full_start_now": True}
    native._origin_of = lambda kind, entity_key: origin
    served = source.snapshot_status
    generations = {"prepared_generation": 1, "active_generation": 1}
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), **generations}

    def metadata_snapshot(_kind, _entity, *, clock, game_version, strategy=None):
        if strategy is None:
            assert source.commands[-1] == {"enabled": False}
            assert native._asset is native._selected_asset is None
            raise OSError("Overall population unavailable")
        return ()

    population = SimpleNamespace(generation=1, metadata_snapshot=metadata_snapshot)
    session._rivals = Rivals(tracker, population, native, lambda: "us")

    async def check():
        await session.update(enabled=True)
        native.note()
        assert source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 1
        with pytest.raises(OSError, match="Overall population unavailable"):
            await session.update({"target": target, "rivals": {"scope": "overall"}})
        assert session.payload()["rivals"]["scope"] == "strategy"
        assert session._pending == {}
        native.note()
        assert source.commands[-1] == {"enabled": False}
        assert native._selected_asset is None
        await session.update({"target": target, "rivals": {"scope": "strategy"}})
        generations["prepared_generation"] = session._rivals.generation
        native.note()
        assert not source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 0
        assert native._selected_asset is None
        await session.stop()

    try:
        asyncio.run(check())
    finally:
        tracker.db.close()


def test_cancel_preparation_invalidates_obsolete_readiness_but_retains_the_active_race(tmp_path):
    session, source, _ = native_session(tmp_path)
    native = session._native
    target = {"kind": "star", "entity_key": "24-1", "strat": "10 coin"}
    ghost = SimpleNamespace(id=17, origin="attempt:7", frames=30, pb=True)
    controls = settings.native_controls(settings.defaults())
    origin = {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
              "utc": "2026-09-21T08:00:00+00:00", "full_start_token": (1, 9), "full_start_now": True}
    native._origin_of = lambda *_: origin
    served = source.snapshot_status
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), "prepared_generation": 1, "active_generation": 1}
    native.prepare(target, [ghost], controls, 1)
    native.note()
    native.prepare(target, [ghost], controls, 2)
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), "prepared_generation": 2, "active_generation": 1}
    native.cancel_preparation()
    cancel = source.commands[-1]
    assert cancel["enabled"] and cancel["cancel_preparation"]
    assert cancel["prepare_generation"] == 2 and cancel["activate_generation"] == 0
    assert cancel["clips"] == []
    assert native.state()["prepared_generation"] == 0 and native.state()["active_generation"] == 1
    origin = {**origin, "full_start_token": (1, 10), "arm_id": 10}
    native.note()
    assert source.commands[-1]["cancel_preparation"] and source.commands[-1]["activate_generation"] == 0
    native.prepare(target, [ghost], controls, 3)
    assert "cancel_preparation" not in source.commands[-1]
    assert source.commands[-1]["prepare_generation"] == 3 and source.commands[-1]["activate_generation"] == 0
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), "prepared_generation": 3, "active_generation": 1}
    native.note()
    assert source.commands[-1]["activate_generation"] == 0  # frozen canceled start stays consumed
    origin = {**origin, "full_start_token": (1, 11), "arm_id": 11}
    native.note()
    assert source.commands[-1]["activate_generation"] == 3
    native.stop()


def test_target_change_during_canceled_preparation_never_plays_the_previous_targets_asset(tmp_path):
    session, source, _ = native_session(tmp_path)
    native = session._native
    target = {"kind": "star", "entity_key": "24-1", "strat": "10 coin"}
    ghost = SimpleNamespace(id=17, origin="attempt:7", frames=30, pb=True)
    controls = settings.native_controls(settings.defaults())
    served = source.snapshot_status
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), "prepared_generation": 1}
    native._origin_of = lambda *_: {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
        "utc": "2026-09-21T08:00:00+00:00", "full_start_token": (1, 9), "full_start_now": True}
    native.prepare(target, [ghost], controls, 1)
    native.note()
    assert source.commands[-1]["enabled"] and native._selected_asset
    native.cancel_preparation()
    other = {**target, "entity_key": "27-1"}
    native.reconcile(enabled=True, target=other, controls=controls)
    count = len(source.commands)
    native.note()
    native.reconcile(enabled=True, target=other, controls=controls)
    native.note()
    assert source.commands[-1] == {"enabled": False}
    assert len(source.commands) == count
    assert native._asset is native._selected_asset is None
    native.stop()


def test_certificate_selects_a_ready_roster_before_the_move_clock_exists(tmp_path, monkeypatch):
    session, source, _ = native_session(tmp_path)
    native = session._native
    target = {"kind": "segment", "entity_key": "move-clock", "strat": "10 coin"}
    ghost = SimpleNamespace(id=17, origin="attempt:7", frames=30, pb=True)
    controls = settings.native_controls(settings.defaults())
    certificate = {"clock_ready": False, "full_start_token": ("segment", 1, 9),
                   "full_start_now": True, "session_id": 1, "start_frame": 100}
    origin = certificate
    native._origin_of = lambda *_: origin
    served = source.snapshot_status
    generations = {"prepared_generation": 1, "active_generation": 0}
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), **generations}
    native.prepare(target, [ghost], controls, 1)
    native.note()
    selected = source.commands[-1]
    assert not selected["enabled"] and "origin" not in selected
    assert selected["prepare_generation"] == selected["activate_generation"] == 1
    assert selected["activate_start"] == 1 and native._selected_asset[0][1]["clip_id"] == "ab" * 16
    # Another roster can become ready during the clock's 90-frame fallback.
    # The already certified start remains bound to its original choice.
    native._replay.find_native = lambda _: (tmp_path / "later.ghost", {
        "clip_id": "ef" * 16, "sha256": "cd" * 32, "blink_frames": 0, "inputs_complete": True})
    later = SimpleNamespace(id=18, origin="attempt:8", frames=60, pb=True)
    native.prepare(target, [later], controls, 2)
    generations["prepared_generation"] = 2
    monkeypatch.setattr(native, "resolve", lambda *_: pytest.fail("poll resolved assets"))
    monkeypatch.setattr(native, "_fingerprint", lambda *_: pytest.fail("poll inspected files"))
    origin = {**certificate, "full_start_now": False}
    native.note()
    assert not source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 0
    origin = {**certificate, "clock_ready": True, "full_start_now": False,
              "kind": "segment", "arm_id": 9, "frame": 190, "utc": "2026-09-21T08:00:03+00:00"}
    native.note()
    playing = source.commands[-1]
    assert playing["enabled"] and playing["origin"]["global_timer"] == 190
    assert playing["prepare_generation"] == 2 and playing["activate_generation"] == 0
    assert native._selected_asset[0][1]["clip_id"] == "ab" * 16
    native.stop()


def test_late_readiness_does_not_adopt_when_an_uncertified_move_clock_resolves(tmp_path):
    session, source, _ = native_session(tmp_path)
    native = session._native
    target = {"kind": "segment", "entity_key": "move-clock", "strat": "10 coin"}
    ghost = SimpleNamespace(id=17, origin="attempt:7", frames=30, pb=True)
    controls = settings.native_controls(settings.defaults())
    origin = {"clock_ready": False, "full_start_token": (1, 9), "full_start_now": True}
    native._origin_of = lambda *_: origin
    served = source.snapshot_status
    native.prepare(target, [ghost], controls, 1)
    native.note()  # not prepared at the actual arm boundary
    assert source.commands[-1]["activate_generation"] == 0
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), "prepared_generation": 1}
    origin = {**origin, "clock_ready": True, "full_start_now": False,
              "kind": "segment", "session_id": 1, "arm_id": 9, "frame": 190,
              "utc": "2026-09-21T08:00:03+00:00"}
    native.note()
    assert source.commands[-1]["activate_generation"] == 0
    native.stop()


def late_join_session(tmp_path, monkeypatch):
    """A start consumed while the roster is still unready, the shape of his
    2026-10-13 PB: the pose export finished a second after the next spawn."""
    session, source, _ = native_session(tmp_path)
    native = session._native
    target = {"kind": "star", "entity_key": "1-5", "strat": "Bomb Clip"}
    ghost = SimpleNamespace(id=17, origin="attempt:7", frames=30, pb=True)
    controls = settings.native_controls(settings.defaults())
    origin = {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
              "utc": "2026-09-21T08:00:00+00:00", "full_start_token": ("star", 1, 9),
              "full_start_now": True, "frames_since_start": 0}
    state = {"origin": origin}
    native._origin_of = lambda *_: state["origin"]
    served = source.snapshot_status
    generations = {"prepared_generation": 0, "active_generation": 0}
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), **generations}
    native.prepare(target, [ghost], controls, 7)
    native.reconcile(enabled=True, target=target, controls=controls)
    native.note()  # the start is consumed while the roster is still unready
    assert source.commands[-1]["activate_generation"] == 0
    monkeypatch.setattr(native, "resolve", lambda *_: pytest.fail("poll resolved assets"))
    monkeypatch.setattr(native, "_fingerprint", lambda *_: pytest.fail("poll inspected files"))
    return native, source, state, generations


@pytest.mark.parametrize("late", [30, LATE_JOIN_FRAMES])
def test_a_roster_ready_during_the_spawn_fall_joins_the_run_it_missed(tmp_path, monkeypatch, late):
    native, source, state, generations = late_join_session(tmp_path, monkeypatch)
    generations["prepared_generation"] = 7
    state["origin"] = {**state["origin"], "full_start_now": False, "frames_since_start": late}
    native.note()
    joined = source.commands[-1]
    assert joined["enabled"] and joined["activate_generation"] == 7
    assert joined["activate_start"] == 1  # the same start, not a second one
    assert native._selected_asset[0][1]["clip_id"] == "ab" * 16
    native.stop()


def test_a_roster_ready_after_the_spawn_fall_waits_for_the_next_full_start(tmp_path, monkeypatch):
    native, source, state, generations = late_join_session(tmp_path, monkeypatch)
    generations["prepared_generation"] = 7
    state["origin"] = {**state["origin"], "full_start_now": False,
                       "frames_since_start": LATE_JOIN_FRAMES + 1}
    native.note()
    assert not source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 0
    assert native._selected_asset is None
    state["origin"] = {**state["origin"], "arm_id": 10, "full_start_token": ("star", 1, 10),
                       "full_start_now": True, "frames_since_start": 0}
    native.note()
    assert source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 7
    assert source.commands[-1]["activate_start"] == 2
    native.stop()


@pytest.mark.parametrize("consumed_first", [False, True])
def test_a_different_runs_token_never_joins_late(tmp_path, monkeypatch, consumed_first):
    """Only the start this session consumed may be joined late. A token it
    never saw at its start frame (or before any start) waits for its own
    certified full start."""
    if consumed_first:
        native, source, state, generations = late_join_session(tmp_path, monkeypatch)
    else:
        session, source, _ = native_session(tmp_path)
        native = session._native
        target = {"kind": "star", "entity_key": "1-5", "strat": "Bomb Clip"}
        ghost = SimpleNamespace(id=17, origin="attempt:7", frames=30, pb=True)
        controls = settings.native_controls(settings.defaults())
        state = {"origin": {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
                            "utc": "2026-09-21T08:00:00+00:00", "full_start_token": ("star", 1, 9),
                            "full_start_now": True, "frames_since_start": 0}}
        native._origin_of = lambda *_: state["origin"]
        served = source.snapshot_status
        generations = {"prepared_generation": 0, "active_generation": 0}
        source.snapshot_status = lambda **kwargs: {**served(**kwargs), **generations}
        native.prepare(target, [ghost], controls, 7)
        native.reconcile(enabled=True, target=target, controls=controls)
    generations["prepared_generation"] = 7
    state["origin"] = {**state["origin"], "arm_id": 10, "full_start_token": ("star", 1, 10),
                       "full_start_now": False, "frames_since_start": 10}
    native.note()
    assert not source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 0
    assert native._selected_asset is None
    state["origin"] = {**state["origin"], "full_start_now": True, "frames_since_start": 0}
    native.note()
    assert source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 7
    native.stop()


#: His report's moment, 2026-10-17: 0'04"43 into a run, at 30 fps.
MID_RUN_FRAMES = 133


def switch_session(tmp_path, monkeypatch, *, prepared_before_start=True):
    """A star run with its certified start, and the overlay switch as the
    session drives it (`GhostSession._reconcile`). Returns the native owner,
    its fake renderer, the run's live origin and the renderer's generations."""
    session, source, _ = native_session(tmp_path)
    native = session._native
    target = {"kind": "star", "entity_key": "1-5", "strat": "Bomb Clip"}
    ghost = SimpleNamespace(id=17, origin="attempt:7", frames=30, pb=True)
    controls = settings.native_controls(settings.defaults())
    state = {"origin": {"kind": "star", "session_id": 1, "arm_id": 9, "frame": 100,
                        "utc": "2026-09-21T08:00:00+00:00", "full_start_token": ("star", 1, 9),
                        "full_start_now": True, "frames_since_start": 0}}
    native._origin_of = lambda *_: state["origin"]
    served = source.snapshot_status
    generations = {"prepared_generation": 7 if prepared_before_start else 0, "active_generation": 0}
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), **generations}

    def switch(on):
        native.reconcile(enabled=True, hidden=not on, target=target, controls=controls)

    def prepare():
        native.prepare(target, [ghost], controls, 7)

    return native, source, state, generations, switch, prepare


def mid_run(state, frames=MID_RUN_FRAMES):
    state["origin"] = {**state["origin"], "full_start_now": False, "frames_since_start": frames}


def without(command, *keys):
    return {key: value for key, value in command.items() if key not in keys}


def test_switching_off_mid_run_hides_the_race_and_switching_on_resumes_it_on_the_next_command(
        tmp_path, monkeypatch):
    native, source, state, generations, switch, prepare = switch_session(tmp_path, monkeypatch)
    prepare()
    switch(True)
    native.note()  # the run's certified start adopts the ready roster
    racing = source.commands[-1]
    assert racing["enabled"] and not racing.get("hidden") and racing["activate_generation"] == 7
    generations["active_generation"] = 7
    mid_run(state)
    native.note()
    assert source.commands[-1] == racing
    # Neither flip may touch a clip, a template or the disk.
    monkeypatch.setattr(native, "resolve", lambda *_: pytest.fail("the switch resolved assets"))
    monkeypatch.setattr(native, "_fingerprint", lambda *_: pytest.fail("the switch inspected files"))
    sent = len(source.commands)
    switch(False)
    hidden = source.commands[-1]
    assert len(source.commands) == sent + 1, "switching off sends exactly one command"
    assert hidden["enabled"] and hidden.get("hidden")
    # The binding is kept: the same clips, origin, generation and activation.
    assert without(hidden, "hidden") == without(racing, "hidden")
    assert native.state() == {"running": False, "visible": False, "backend": "native", "reason": None,
                              "prepared_generation": 7, "active_generation": 7}
    mid_run(state, MID_RUN_FRAMES + 30)  # one second later, still following the run
    native.note()
    assert source.commands[-1] == hidden
    switch(True)
    shown = source.commands[-1]
    assert len(source.commands) == sent + 2, "switching on shows it on the very next command"
    assert shown == racing
    assert native.state()["running"] and native.state()["reason"] is None
    native.stop()


def test_switching_on_mid_run_with_nobody_racing_joins_this_run_once_its_roster_is_ready(
        tmp_path, monkeypatch):
    """Off at the run's start (nothing prepared then), on 0'04"43 in: the
    roster prepares now and joins THIS run, aligned to its elapsed clock, not
    the next one. A switch-on is not the spawn fall's late join."""
    native, source, state, generations, switch, prepare = switch_session(
        tmp_path, monkeypatch, prepared_before_start=False)
    switch(False)
    native.note()  # the run starts while the overlay is off
    assert source.commands[-1] == {"enabled": False}
    mid_run(state)
    switch(True)   # the session's switch reaches the native owner first,
    prepare()      # then the resolve prepares the roster
    native.note()
    assert not source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 0
    generations["prepared_generation"] = 7
    native.note()
    joined = source.commands[-1]
    assert joined["enabled"] and not joined.get("hidden")
    assert joined["activate_generation"] == 7 and joined["activate_start"] == 1
    assert joined["origin"]["global_timer"] == 100, "aligned to this run's own start"
    assert native._selected_asset[0][1]["clip_id"] == "ab" * 16
    native.note()
    assert source.commands[-1] == joined, "the join is consumed once"
    native.stop()


def test_a_switch_on_with_nobody_racing_joins_as_soon_as_the_roster_is_ready_even_late(
        tmp_path, monkeypatch):
    native, source, state, generations, switch, prepare = switch_session(
        tmp_path, monkeypatch, prepared_before_start=False)
    switch(False)
    native.note()
    mid_run(state, 3000)  # well past the spawn fall's late join
    switch(True)
    prepare()
    generations["prepared_generation"] = 7
    native.note()
    assert source.commands[-1]["enabled"] and source.commands[-1]["activate_generation"] == 7
    native.stop()


def test_a_full_start_while_switched_off_races_hidden_and_switching_on_shows_it_at_once(
        tmp_path, monkeypatch):
    native, source, state, generations, switch, prepare = switch_session(tmp_path, monkeypatch)
    prepare()
    switch(True)
    native.note()
    generations["active_generation"] = 7
    switch(False)
    # His reset while the overlay is off: the next run adopts the roster, hidden.
    state["origin"] = {**state["origin"], "arm_id": 10, "frame": 900, "full_start_token": ("star", 1, 10),
                       "full_start_now": True, "frames_since_start": 0}
    native.note()
    hidden = source.commands[-1]
    assert hidden["enabled"] and hidden.get("hidden") and hidden["activate_start"] == 2
    assert hidden["origin"]["global_timer"] == 900
    mid_run(state)
    switch(True)
    assert source.commands[-1] == {**hidden, "hidden": False}
    native.stop()


@pytest.mark.parametrize(("change", "value"), [("strat", "Single jump ending"), ("scope", "overall"),
                                               ("entity_key", "1-6")])
def test_a_target_strategy_or_scope_change_while_switched_off_still_revokes(tmp_path, monkeypatch, change, value):
    native, source, state, generations, switch, prepare = switch_session(tmp_path, monkeypatch)
    prepare()
    switch(True)
    native.note()
    switch(False)
    controls = settings.native_controls(settings.defaults())
    other = {"kind": "star", "entity_key": "1-5", "strat": "Bomb Clip", change: value}
    native.reconcile(enabled=True, hidden=True, target=other, controls=controls)
    assert source.commands[-1] == {"enabled": False}
    assert native._selected_asset is native._asset is None
    native.reconcile(enabled=True, hidden=False, target=other, controls=controls)
    native.note()
    assert source.commands[-1] == {"enabled": False}
    native.stop()


def test_the_card_toggle_and_dpad_up_hide_and_resume_the_lineups_race_without_preparing_again(tmp_path):
    """Both switches reach `GhostSession.update(enabled=...)`. The flip reaches
    the renderer before the setting is saved or the roster re-resolves, and
    switching back on keeps the same generation and activation, so the card
    never says the lineup "Updates on your next attempt"."""
    from sm64_events.ghost.dpad_toggle import OverlayFlipper
    from sm64_events.ghost.rivals import Rivals
    from sm64_events.storage.db import Database
    from tests.test_ghost_adaptive import Standards, attempt, save

    session, source, tracker = native_session(tmp_path)
    native = session._native
    tracker.db, tracker.ranks = Database(tmp_path / "switch.db"), Standards()
    row = attempt(7, 30, course_id=24, star_id=1, strat_tag="10 coin",
                  native_clock_frame=100, closed_frame=130)
    tracker.db.upsert_attempt(row)
    save(tracker.db, row)
    native._origin_of = lambda kind, entity_key: {
        "kind": "star", "session_id": 1, "arm_id": 9, "frame": 100, "utc": "2026-09-21T08:00:00+00:00",
        "full_start_token": (1, 9), "full_start_now": True}
    served = source.snapshot_status
    source.snapshot_status = lambda **kwargs: {**served(**kwargs), "prepared_generation": 1, "active_generation": 1}
    population = SimpleNamespace(generation=1, metadata_snapshot=lambda *_args, **_kwargs: ())
    session._rivals = Rivals(tracker, population, native, lambda: "us")
    refresh, resolved = session._rivals.refresh, []

    def observed(*args, **kwargs):
        if kwargs.get("force"):
            resolved.append(source.commands[-1].get("hidden") if source.commands else None)
        return refresh(*args, **kwargs)

    session._rivals.refresh = observed

    async def check():
        await session.update(enabled=True)
        native.note()
        racing = source.commands[-1]
        assert racing["enabled"] and racing["activate_generation"] == 1 and not racing.get("hidden")
        generation = session._rivals.generation
        resolved.clear()
        await session.update(enabled=False)
        assert source.commands[-1] == {**racing, "hidden": True}
        await OverlayFlipper(session).flip()  # D-pad Up
        assert source.commands[-1] == racing
        assert resolved == [True, False], "each flip reached the renderer before the roster re-resolved"
        assert session._rivals.generation == generation
        assert session.payload()["rivals"]["status"] is None
        await session.stop()

    try:
        asyncio.run(check())
    finally:
        tracker.db.close()


@pytest.mark.parametrize("hidden", [False, True])
def test_recording_off_still_blocks_a_hidden_or_shown_race(tmp_path, monkeypatch, hidden):
    native, source, state, generations, switch, prepare = switch_session(tmp_path, monkeypatch)
    prepare()
    switch(True)
    native.note()
    native._replay.recorder.recording_enabled = False
    switch(not hidden)
    assert source.commands[-1] == {"enabled": False}
    native.stop()


def test_native_switch_uses_saved_poses_and_preserves_controls(tmp_path):
    session, source, _ = native_session(tmp_path)

    async def check():
        await session.update({"enabled": True, "opacity": 1,
                           "tint": {"enabled": True, "hue_degrees": 120}, "nudge_frames": 9})
        session._native.note()
        assert session.payload()["source"]["exactness"] == "native_pose"
        command = source.commands[-1]
        assert command["enabled"] and command["opacity"] == 1 and command["nudge"] == 9
        assert command["tint_enabled"] and [clip["hue_degrees"] for clip in command["clips"]] == [120]
        assert command["proximity_enabled"] and command["proximity_distance"] == settings.DEFAULT_PROXIMITY["extra_distance"]
        assert command["stop_at_star"] and command["rom_path"] == ""
        assert command["origin"]["global_timer"] == 100
        assert command["origin"]["configuration"] == 2 and command["origin"]["epoch"] == 3
        count = len(source.commands)
        await session.refresh(resolve=False)
        assert len(source.commands) == count
        await session.update({"proximity": {"extra_distance": 120}})
        assert source.commands[-1]["proximity_distance"] == 120
        await session.update({"proximity": {"enabled": False}})
        assert not source.commands[-1]["proximity_enabled"]
        assert source.commands[-1]["proximity_distance"] == 120
        await session.update({"enabled": False})
        assert not source.commands[-1]["enabled"]
        await session.stop()

    asyncio.run(check())


@pytest.mark.parametrize("clock,stops", [("star", True), ("segment", False)])
def test_a_star_timed_by_its_exit_segment_is_not_over_at_the_first_grab(tmp_path, clock, stops):
    """The 100-coin star's live clock is its exit segment's: his 100-coin grab
    (or the ghost's) must not end the run; the exit star's close does."""
    session, source, _ = native_session(tmp_path)
    session._native._origin_of = lambda *_: {"kind": clock, "session_id": 1, "arm_id": 9,
                                             "frame": 100, "utc": "2026-09-21T08:00:00+00:00"}

    async def check():
        await session.update({"enabled": True})
        session._native.note()
        assert source.commands[-1]["enabled"] and source.commands[-1]["stop_at_star"] is stops
        await session.stop()

    asyncio.run(check())


def test_missing_pose_disables_native_playback(tmp_path):
    session, source, _ = native_session(tmp_path, asset=False)

    async def check():
        await session.update({"enabled": True})
        assert not session.payload()["source"]["available"]
        assert session.payload()["source"]["reason"] == "no_native_poses"
        assert not source.commands[-1]["enabled"]
        await session.stop()

    asyncio.run(check())


def test_a_set_that_does_not_fit_says_so_on_the_ghost_card(tmp_path):
    """The renderer refusing a set for room (GIPC_BUDGET) reaches the ghost card
    as a sentence he can act on, not the bad-file "could not be played"."""
    session, source, _ = native_session(tmp_path)

    async def check():
        await session.update({"enabled": True})
        session._native.note()
        source.snapshot_status = lambda **_: {
            "available": True, "state": 5, "reason": NATIVE_BUDGET_REASON,
            "producer": SimpleNamespace(configuration=2, epoch=3)}
        body = session.payload()
        # One ghost alone always fits, so its refusal only waits for room.
        assert body["window"]["reason"] == budget_reason(1) == "Making room for this ghost..."
        key = run_node("ghostTargetKey", f"ghostTargetKey({json.dumps(body['target'])})")
        assert controls(body, key)["reasonText"] == "Making room for this ghost..."
        await session.stop()

    asyncio.run(check())
    assert budget_reason(2) == budget_reason(16) == "These ghosts are too long to show together; untick one."


def test_pose_ready_is_not_display_evidence(tmp_path):
    session, source, _ = native_session(tmp_path)

    async def check():
        await session.update({"enabled": True})
        session._native.note()
        assert session._native.state()["running"]
        assert not session._native.state()["visible"]
        source.displayed = True
        assert session._native.state()["visible"]
        source.displayed = False
        assert not session._native.state()["visible"]
        await session.stop()

    asyncio.run(check())


@pytest.mark.parametrize("displayed,playback_reason,reason", [
    (False, GP_WRONG_SCENE, "Ghost is in another area."),
    (True, GP_WRONG_SCENE, None),  # Another selected ghost may still be visible.
    (False, 2, None),
    (False, 1, None),  # A new command has not received its own acknowledgement.
    (False, 999, None),
    (False, None, None),
])
def test_area_mismatch_is_explained_only_when_ready_and_hidden(tmp_path, displayed, playback_reason, reason):
    session, source, _ = native_session(tmp_path)
    served = source.snapshot_status

    async def check():
        await session.update({"enabled": True})
        session._native.note()
        source.snapshot_status = lambda **_: {**served(), "displayed": displayed,
                                               "playback_reason": playback_reason}
        body = session.payload()
        assert body["window"] == {"running": True, "visible": displayed,
                                  "backend": "native", "reason": reason}
        assert body["source"]["blinks"] == 0
        key = run_node("ghostTargetKey", f"ghostTargetKey({json.dumps(body['target'])})")
        answer = controls(body, key)
        assert answer["canToggle"] and answer["reasonText"] == reason
        await session.stop()

    asyncio.run(check())


def test_area_reason_does_not_replace_waiting_for_an_attempt(tmp_path):
    session, source, _ = native_session(tmp_path)
    served = source.snapshot_status
    source.snapshot_status = lambda **_: {**served(), "playback_reason": GP_WRONG_SCENE}

    async def check():
        await session.update({"enabled": True})
        assert session._native.state()["reason"] == "Waiting for the next attempt."
        session._native.note()
        assert session._native.state()["reason"] == "Ghost is in another area."
        session._native._origin_of = lambda *_: None
        session._native.note()
        assert session._native.state() == {
            "running": False, "visible": False, "backend": "native",
            "reason": "Waiting for the next attempt or a saved state."}
        await session.stop()

    asyncio.run(check())


@pytest.mark.parametrize("status,reason", [
    ({"available": True, "state": 3}, "Preparing Mario..."),
    ({"available": False, "state": 4}, "Start the game with the trainer's GLideN64 renderer."),
])
def test_unready_renderer_does_not_publish_a_stale_area_reason(tmp_path, status, reason):
    session, source, _ = native_session(tmp_path)
    served = source.snapshot_status

    async def check():
        await session.update({"enabled": True})
        session._native.note()
        source.snapshot_status = lambda **_: {**served(), **status,
                                               "playback_reason": GP_WRONG_SCENE}
        assert session._native.state() == {
            "running": False, "visible": False, "backend": "native", "reason": reason}
        await session.stop()

    asyncio.run(check())


@pytest.mark.parametrize("suspended", [False, True])
def test_failed_roster_preparation_is_visible_before_the_first_attempt(tmp_path, suspended):
    session, source, _ = native_session(tmp_path)
    session._native._source = source
    session._native._enabled = False
    session._native._requested = True
    session._native._owner_suspended = suspended
    source.snapshot_status = lambda **_: {"available": True, "state": 5, "reason": 6}
    assert session._native.state()["reason"] == "The saved Mario poses could not be loaded."
    source.snapshot_status = lambda **_: {"available": True, "state": 3}
    assert session._native.state()["reason"] == (
        "Waiting for the next attempt or a saved state." if suspended else "Preparing Mario...")


def test_poll_markers_rebase_and_disarm_without_frame_pacing_or_file_reads(tmp_path):
    session, source, _ = native_session(tmp_path)
    origin = {"kind": "segment", "session_id": 1, "arm_id": 99,
              "frame": 120, "utc": "2026-09-21T08:00:00+00:00"}
    session._native._origin_of = lambda *_: origin

    async def check():
        nonlocal origin
        await session.update({"enabled": True})
        assert not source.commands[-1]["enabled"]
        assert session._native.state()["reason"] == "Waiting for the next attempt."
        session._native.note()
        first = source.commands[-1]
        assert first["origin"]["kind"] == 2 and first["origin"]["global_timer"] == 120
        count = len(source.commands)
        for _ in range(90):
            session._native.note()
        assert len(source.commands) == count
        origin = {**origin, "frame": 145, "utc": "2026-09-21T08:00:00.8+00:00"}
        session._native.note()
        assert source.commands[-1]["origin"]["global_timer"] == 145
        assert source.commands[-1]["origin"]["arm_id"] == 99
        origin = None
        session._native.note()
        assert source.commands[-1]["enabled"] and source.commands[-1]["owner_suspended"]
        assert source.commands[-1]["origin"]["arm_id"] == 99
        assert not session._native.state()["visible"]
        origin = {"kind": "segment", "session_id": 1, "arm_id": 100,
                  "frame": 130, "utc": "2026-09-21T08:00:02+00:00"}
        session._native.note()
        assert source.commands[-1]["enabled"] and not source.commands[-1]["owner_suspended"]
        assert source.commands[-1]["origin"]["arm_id"] == 100
        await session.stop()
        session._native.note()
        assert source.commands[-1] == {"enabled": False}

    asyncio.run(check())


def test_rom_reopen_keeps_binding_until_new_producer_identity_arrives(tmp_path):
    session, source, _ = native_session(tmp_path)
    original_status = source.snapshot_status

    async def check():
        await session.update({"enabled": True})
        session._native.note()
        before = len(source.commands)
        source.snapshot_status = lambda **_: {"available": False}
        session._native.note()
        assert len(source.commands) == before and source.commands[-1]["enabled"]
        source.snapshot_status = lambda **_: {**original_status(),
            "producer": SimpleNamespace(configuration=5, epoch=8)}
        session._native.note()
        assert source.commands[-1]["origin"]["configuration"] == 5
        assert source.commands[-1]["origin"]["epoch"] == 8
        source.snapshot_status = lambda **_: {"available": False}
        await session.update({"enabled": False})
        assert source.commands[-1] == {"enabled": False}
        await session.stop()

    asyncio.run(check())


def test_a_capture_reconnect_says_reconnecting_not_start_the_game(tmp_path):
    """The ghost card's sentence while the renderer is out of reach. Live
    2026-09-25: 9 of 1,293 ghost_changed told him to start the game he was
    playing, each right after a GPU capture reconnect replaced the recorder's
    pose source. Poses served earlier by a game that still runs mean the
    renderer is reconnecting; a game that never served, or has exited, is
    still one to start."""
    from sm64_events.ghost.native_session import RECONNECTING_REASON, START_GAME_REASON
    session, source, _ = native_session(tmp_path)
    game = SimpleNamespace(pid=4242, created=77, configuration=2, epoch=3)
    alive = {game.pid: game.created}
    session._native._running = lambda pid, created: alive.get(pid) == created
    recorder = session._native._replay.recorder
    served = source.snapshot_status

    async def card():
        await session.refresh(resolve=False)
        return session.payload()["window"]["reason"]

    async def check():
        source.snapshot_status = lambda **_: {"available": False, "reason": "not_started"}
        await session.update({"enabled": True})
        assert await card() == START_GAME_REASON          # never served
        source.snapshot_status = lambda **_: {**served(), "producer": game}
        session._native.note()
        assert await card() is None                        # drawing
        recorder.ghost_capture = None                      # capture torn down...
        assert await card() == RECONNECTING_REASON
        fresh = Capture()                                  # ...and a new source not yet through
        fresh.snapshot_status = lambda **_: {"available": False, "reason": "not_started"}
        recorder.ghost_capture = fresh
        assert await card() == RECONNECTING_REASON
        fresh.snapshot_status = lambda **_: {**served(), "producer": game}
        session._native.note()
        assert await card() is None
        fresh.snapshot_status = lambda **_: {"available": False, "reason": "no Project64"}
        del alive[game.pid]                                # the game exited
        assert await card() == START_GAME_REASON
        alive[game.pid] = game.created                     # and nothing it served is remembered
        assert await card() == START_GAME_REASON
        await session.stop()

    asyncio.run(check())


def test_the_game_is_running_only_while_its_own_process_is():
    """A reused pid is a new game: identity is the pid AND its birth stamp."""
    import os
    from sm64_events.ghost.native_session import emulator_running
    from sm64_events.replay.capturecontrol import _kernel, _process_creation
    low, high = _process_creation(_kernel(), os.getpid())
    birth = low | (high << 32)
    assert emulator_running(os.getpid(), birth)
    assert not emulator_running(os.getpid(), birth + 1)
    assert not emulator_running(0, birth)


def test_target_change_revokes_old_pose_and_resets_nudge(tmp_path):
    session, source, tracker = native_session(tmp_path)

    async def check():
        await session.update({"enabled": True, "nudge_frames": 8})
        tracker.target = ("segment", 55)
        await session.refresh()
        assert not source.commands[-1]["enabled"]
        assert session.payload()["nudge_frames"] == 0
        await session.stop()

    asyncio.run(check())


def test_stop_joins_uncancelled_selection_before_final_revoke(tmp_path):
    session, source, _ = native_session(tmp_path)
    entered, release = threading.Event(), threading.Event()
    resolve = session._native.resolve

    def blocked(*args):
        answer = resolve(*args)
        entered.set()
        assert release.wait(5)
        return answer

    session._native.resolve = blocked

    async def check():
        selection = asyncio.create_task(session.update({"enabled": True}))
        assert await asyncio.to_thread(entered.wait, 3)
        stopping = asyncio.create_task(session.stop())
        try:
            await asyncio.sleep(0.03)
            assert not stopping.done()
        finally:
            release.set()
        await selection
        await stopping
        assert not source.commands[-1]["enabled"]
        count = len(source.commands)
        await session.refresh()
        assert len(source.commands) == count

    asyncio.run(check())


def test_unchanged_clip_is_validated_once_until_asset_changes(tmp_path):
    session, _, _ = native_session(tmp_path)
    path = tmp_path / "run.ghost"
    path.write_bytes(b"asset")
    path.with_suffix(".json").write_text("{}")
    calls = []
    find = session._native._replay.find_native
    session._native._replay.find_native = lambda number: calls.append(number) or find(number)

    async def check():
        await session.refresh()
        await session.refresh()
        assert calls == [7]
        path.write_bytes(b"changed asset")
        await session.refresh()
        assert calls == [7, 7]
        await session.stop()

    asyncio.run(check())


def test_reprojected_attempt_invalidates_unchanged_cached_asset(tmp_path):
    session, _, _ = native_session(tmp_path)
    path = tmp_path / "run.ghost"
    path.write_bytes(b"asset")
    path.with_suffix(".json").write_text("{}")
    calls = []
    replay = session._native._replay
    find = replay.find_native
    replay.find_native = lambda number: calls.append(number) or find(number)

    async def check():
        await session.refresh()
        await session.refresh()
        assert session._native._asset is not None
        replay.native_identity = lambda _: {"native_clock_frame": 145}
        replay.find_native = lambda number: calls.append(number)
        await session.refresh()
        assert calls == [7, 7] and session._native._asset is None
        assert not session.payload()["source"]["available"]
        await session.stop()

    asyncio.run(check())


def test_poll_tick_skips_capture_lock_and_retries_unpublished_command(tmp_path):
    from sm64_events.ghost.capture import NativeCapture
    from sm64_events.memory.layout import US

    session, fake, _ = native_session(tmp_path)
    source = NativeCapture(US, lambda *_: None)
    source._report(**fake.snapshot_status())
    session._native._replay.recorder.ghost_capture = source

    async def check():
        await session.update({"enabled": True})
        completed = threading.Event()
        thread = threading.Thread(target=lambda: (session._native.note(), completed.set()))
        source._lock.acquire()
        try:
            thread.start()
            assert completed.wait(1), "poll tick waited on the capture worker"
            assert source.set_playback(blocking=False, enabled=True) is False
            assert session._native._sent == {"enabled": False}
        finally:
            source._lock.release()
            thread.join(2)
        session._native.note()
        assert source._playback["enabled"] and session._native._sent == source._playback
        await session.stop()

    asyncio.run(check())


def refused_recording(tmp_path):
    from ghost_native_fixture import frame
    from test_ghost_recording import native_service
    from tests.test_ghost_session import a_store

    service, row, archive = native_service(tmp_path, rows=tuple(frame(n, valid=29) for n in (1, 2, 3)))
    store = a_store(tmp_path)
    target = {"kind": "star", "entity_key": f"{row.course_id}-{row.star_id}", "strat": row.strat_tag}
    return service, row, archive, store, target


def test_real_export_refusal_reaches_saved_template_without_retrying_archive(tmp_path, monkeypatch, caplog):
    from dataclasses import replace
    from sm64_events.inputs.frame import InputFrame
    from sm64_events.inputs.service import InputsService
    from sm64_events.storage.db import Database

    service, row, archive, _, target = refused_recording(tmp_path)
    db = Database(tmp_path / "inputs.sqlite")
    row = replace(row, session_id=db.insert_session(row.started_utc))
    service.tracker.db._attempts = [row]
    db.inputs.append(row.session_id, [(n, InputFrame(0, 0, 0, 0)) for n in (101, 102, 103)],
                     row.started_utc, row.ended_utc)
    results = []

    def preserve(attempt_id):
        result = service.preserve_native(attempt_id)
        results.append(result)
        return result

    store = db.input_templates
    inputs = InputsService(db.inputs, store, lambda: [row], preserve_native=preserve)
    try:
        saved = inputs.mark_template(row.id)
        assert len(results) == 1
        result = results[0]
        assert result["native_ghost_reason"] == "native ghost has no supported recorded poses"
        assert result["native_ghost_reason"] in caplog.text
        monkeypatch.setattr(archive, "export_attempt", lambda *a, **k: pytest.fail("resolve retried export"))
        ghost = NativeGhost(service)
        for _ in range(3):
            answer = default_ghosts(ghost, target, store)
            assert not answer["available"] and answer["reason"] == "native_preservation_failed"
            assert answer["template_id"] == saved.id and answer["attempt_id"] == row.id
            assert answer["reason_text"] == (
                "The template was saved, but this recording has no Mario poses supported by the renderer.")
        assert store.active_for(target["kind"], target["entity_key"], row.strat_tag).id == saved.id
        assert not service.extractor.calls
        assert not list(service.cfg.save_root.rglob("*.ghost"))
    finally:
        archive.close()
        db.close()


@pytest.mark.parametrize("changes", [
    {"session_id": 99}, {"strat_tag": "reprojected"}, {"native_clock_frame": 102},
    {"native_clock_utc": "2027-01-15T08:00:01+00:00"}, {"native_clock_kind": "segment"},
    {"closed_frame": 104}, {"ended_utc": "2027-01-15T08:00:02+00:00"},
])
def test_preservation_refusal_does_not_survive_changed_attempt_identity(tmp_path, changes):
    from dataclasses import replace

    service, row, archive, _, _ = refused_recording(tmp_path)
    try:
        service.preserve_native(row.id)
        assert service.native_preservation_reason(row.id)
        service.tracker.db._attempts = [replace(row, **changes)]
        assert service.native_preservation_reason(row.id) is None
    finally:
        archive.close()


def test_successful_pose_commit_clears_the_prior_refusal(tmp_path):
    from ghost_native_fixture import frame, producer
    from sm64_events.ghost.archive import PoseArchive

    service, row, archive, store, target = refused_recording(tmp_path)
    good = PoseArchive(tmp_path / "supported.sqlite")
    try:
        service.preserve_native(row.id)
        mark(store, entity_key=target["entity_key"], strat=row.strat_tag, attempt_id=row.id)
        ghost = NativeGhost(service)
        assert default_ghosts(ghost, target, store)["reason"] == "native_preservation_failed"
        good.ingest(producer(), tuple(frame(n) for n in (1, 2, 3)))
        service.recorder.pose_archive = good
        assert service.preserve_native(row.id)["native_ghost"] is not None
        assert service.native_preservation_reason(row.id) is None
        assert default_ghosts(ghost, target, store)["available"]
        path, _ = service.find_native(row.id)
        path.with_suffix(".json").unlink()
        assert default_ghosts(ghost, target, store)["reason"] == "no_native_poses"
    finally:
        archive.close()
        good.close()


def test_refusal_lookup_does_not_wait_for_an_in_progress_export(tmp_path, monkeypatch):
    service, row, archive, store, target = refused_recording(tmp_path)
    entered, release = threading.Event(), threading.Event()
    export, results = archive.export_attempt, []

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return export(*args, **kwargs)

    monkeypatch.setattr(archive, "export_attempt", blocked)
    mark(store, entity_key=target["entity_key"], strat=row.strat_tag, attempt_id=row.id)
    thread = threading.Thread(target=lambda: results.append(service.preserve_native(row.id)))
    try:
        thread.start()
        assert entered.wait(2)
        ghost = NativeGhost(service)
        assert default_ghosts(ghost, target, store)["reason"] == "no_native_poses"
    finally:
        release.set()
        thread.join(5)
        archive.close()
    assert not thread.is_alive() and len(results) == 1
    assert results[0]["native_ghost_reason"] == "native ghost has no supported recorded poses"
    assert default_ghosts(ghost, target, store)["reason"] == "native_preservation_failed"


def test_missing_origin_is_explained_and_refusal_history_is_bounded(tmp_path):
    from test_replay_service import attempt, make_service
    from tests.test_ghost_session import a_store

    rows = [attempt(id=number) for number in range(130)]
    service = make_service(tmp_path, rows)
    for row in rows:
        service.preserve_native(row.id)
    assert service.native_preservation_reason(rows[0].id) is None
    assert service.native_preservation_reason(rows[-1].id) == "This recording has no native Mario poses."
    store = a_store(tmp_path)
    mark(store, entity_key="2-2", strat=None, attempt_id=rows[-1].id)
    answer = default_ghosts(NativeGhost(service), {"kind": "star", "entity_key": "2-2"}, store)
    assert answer["reason"] == "native_preservation_failed"
    assert "Mario poses were not recorded" in answer["reason_text"]


def test_real_missing_origin_refusal_is_available_before_asset_identity(tmp_path):
    from dataclasses import replace

    service, row, archive, store, target = refused_recording(tmp_path)
    service.tracker.db._attempts = [replace(row, native_clock_frame=None)]
    try:
        result = service.preserve_native(row.id)
        assert result["native_ghost_reason"] == "attempt has no exact native frame endpoints or owner kind"
        mark(store, entity_key=target["entity_key"], strat=row.strat_tag, attempt_id=row.id)
        answer = default_ghosts(NativeGhost(service), target, store)
        assert answer["reason"] == "native_preservation_failed"
        assert "exact starting or ending frame" in answer["reason_text"]
    finally:
        archive.close()


def test_stale_lookup_cannot_erase_new_projection_refusal(tmp_path, monkeypatch):
    from dataclasses import replace

    service, row, archive, _, _ = refused_recording(tmp_path)
    entered, release = threading.Event(), threading.Event()
    key, results = service._native_preservation_key, []

    def delayed(attempt):
        identity = key(attempt)
        if threading.current_thread() is query:
            entered.set()
            assert release.wait(5)
        return identity

    monkeypatch.setattr(service, "_native_preservation_key", delayed)
    query = threading.Thread(target=lambda: results.append(service.native_preservation_reason(row.id)))
    try:
        query.start()
        assert entered.wait(2)
        service.tracker.db._attempts = [replace(row, strat_tag="new projection")]
        refusal = service.preserve_native(row.id)["native_ghost_reason"]
    finally:
        release.set()
        query.join(5)
        archive.close()
    assert not query.is_alive() and results == [None]
    assert service.native_preservation_reason(row.id) == refusal


def test_older_pose_file_is_explained_kept_and_never_replaced(tmp_path):
    import json
    from ghost_native_fixture import frame, producer
    from sm64_events.ghost.archive import PoseArchive
    from sm64_events.ghost.clip import OLDER_POSE_FORMAT_TEXT
    from sm64_events.replay.service import saved_attempt_ids
    from test_ghost_recording import native_service
    from tests.test_ghost_session import a_store

    service, row, archive = native_service(tmp_path)
    fresh = PoseArchive(tmp_path / "fresh.sqlite")
    try:
        service.preserve_native(row.id)
        path, manifest = service.find_native(row.id)
        path.with_suffix(".json").write_text(json.dumps({**manifest, "version": 2}), encoding="utf-8")
        before = {item: item.read_bytes() for item in (path, path.with_suffix(".json"))}
        store = a_store(tmp_path)
        target = {"kind": "star", "entity_key": f"{row.course_id}-{row.star_id}", "strat": row.strat_tag}
        mark(store, entity_key=target["entity_key"], strat=row.strat_tag, attempt_id=row.id)
        answer = default_ghosts(NativeGhost(service), target, store)
        assert not answer["available"]
        assert (answer["reason"], answer["reason_text"]) == ("older_pose_format", OLDER_POSE_FORMAT_TEXT)
        # A newer archive must not silently overwrite his committed older recording.
        fresh.ingest(producer(), tuple(frame(n) for n in (1, 2, 3)))
        service.recorder.pose_archive = fresh
        assert service.preserve_native(row.id) == {
            "path": None, "native_ghost": None, "native_ghost_reason": OLDER_POSE_FORMAT_TEXT}
        assert {item: item.read_bytes() for item in before} == before
        assert saved_attempt_ids(service.cfg.save_root) == {row.id}  # pruning keeps it
    finally:
        archive.close()
        fresh.close()


def test_playable_saved_copy_wins_over_an_older_permanent_file(tmp_path):
    import json
    from pathlib import Path
    from test_ghost_recording import native_service

    service, row, archive = native_service(tmp_path)
    service.compressor = SimpleNamespace(enqueue=lambda *args, **kwargs: None)
    original = service.preserve_native(row.id)
    saved = service.save(row.id)
    archive.close()
    permanent = Path(original["path"]).with_suffix(".json")
    permanent.write_text(json.dumps({**original["native_ghost"], "version": 1}), encoding="utf-8")
    path, _ = service.find_native(row.id)
    assert path == Path(saved["path"]).with_suffix(".ghost")
    assert service.native_preservation_reason(row.id) is None


def test_an_older_file_is_logged_once_not_on_every_lookup(tmp_path, caplog):
    import json
    from test_ghost_recording import native_service

    service, row, archive = native_service(tmp_path)
    try:
        service.preserve_native(row.id)
        path, manifest = service.find_native(row.id)
        path.with_suffix(".json").write_text(json.dumps({**manifest, "version": 2}), encoding="utf-8")
        with caplog.at_level("WARNING", logger="sm64.replay"):
            for _ in range(5):
                assert service.find_native(row.id) is None
        assert caplog.text.count("preservation refused") == 1
    finally:
        archive.close()


def test_an_asset_with_an_unknown_input_is_not_played_and_says_why(tmp_path):
    """His rule (round 40): a playback copy that would draw "?" is not a ghost, PB included."""
    session, source, _ = native_session(tmp_path)
    replay = session._native._replay
    replay.find_native = lambda _: (tmp_path / "run.ghost", {"clip_id": "ab" * 16, "sha256": "cd" * 32,
                                                             "blink_frames": 0, "inputs_complete": False})

    async def check():
        await session.update({"enabled": True})
        payload = session.payload()["source"]
        assert not payload["available"] and payload["reason"] == "incomplete_inputs"
        assert "not known on every frame" in payload["reason_text"]
        assert not source.commands[-1]["enabled"]
        await session.stop()

    asyncio.run(check())
