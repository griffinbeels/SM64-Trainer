"""A run moved to another strategy moves on the ghost side too.

His report, 2026-10-13: "I just saved a Ghost in 'IN the Deep Freeze' as
'Standard' but after evaluating the strategy, realized I was doing the 'TJ
Strat' so I changed the classification for the ghost... It should no longer
appear for the old strategy." The tracker announces every reclassification
once (`on_attempts_reclassified`), the ghost session hands each run to the
population and retargets its committed pose clips, and the PB ghost follows.
"""
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from sm64_events.ghost.session import GhostSession
from test_tracker_service import ev, make_rec, seed_id, star
from tests.test_ghost_session import FakeBroadcaster, FakeTracker, a_store


def completed(svc, anchor, frames):
    asyncio.run(svc.publish(ev("practice_reset", anchor, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(anchor + 650, igt=frames)))
    return max(svc.db.attempts(), key=lambda a: a.id).id


def told(svc):
    calls = []
    svc.on_attempts_reclassified = lambda attempts: calls.append(
        [(attempt.id, attempt.strat_tag) for attempt in attempts])
    return calls


# -- the tracker announces each reclassification once -------------------------

def test_his_deep_freeze_run_is_announced_on_adoption_and_on_correction(tmp_path):
    db, svc, _ = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2))
    run = completed(svc, 1000, 226)
    calls = told(svc)
    asyncio.run(svc.set_strat(2, 2, "Standard"))  # his first pick adopts the untagged run
    assert calls == [[(run, "Standard")]]
    asyncio.run(svc.set_attempt_strat(run, "TJ Strat"))  # then he corrects it
    assert calls == [[(run, "Standard")], [(run, "TJ Strat")]]
    assert db.attempt(run).strat_tag == "TJ Strat"


def test_the_newest_row_and_the_pending_rows_it_adopts_are_one_announcement(tmp_path):
    _db, svc, _ = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2))
    older = completed(svc, 1000, 300)
    newest = completed(svc, 2000, 280)
    calls = told(svc)
    asyncio.run(svc.set_attempt_strat(newest, "TJ Strat"))
    assert calls == [[(newest, "TJ Strat"), (older, "TJ Strat")]]


def test_an_older_row_and_a_cleared_tag_each_announce(tmp_path):
    _db, svc, _ = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2, "Standard"))
    older = completed(svc, 1000, 300)
    newest = completed(svc, 2000, 280)
    calls = told(svc)
    asyncio.run(svc.set_attempt_strat(older, "TJ Strat"))
    asyncio.run(svc.set_attempt_strat(newest, None))  # un-labeled: the ghost side drops it
    assert calls == [[(older, "TJ Strat")], [(newest, None)]]
    asyncio.run(svc.set_attempt_strat(older, "TJ Strat"))  # nothing changed: nothing to tell
    asyncio.run(svc.set_strat(2, 2, "Owlless"))  # a deliberate "no strategy" is not adopted
    assert len(calls) == 2


def test_a_target_pick_announces_the_rows_it_adopts(tmp_path):
    _db, svc, _ = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2))
    run = completed(svc, 1000, 300)
    calls = told(svc)
    asyncio.run(svc.set_target(2, 2, "Owlless"))
    assert calls == [[(run, "Owlless")]]


def test_segments_announce_the_same_way(tmp_path):
    db, svc, _ = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    for start in (1000, 2000):  # two real LBLJ runs, left untagged
        asyncio.run(svc.publish(ev("level_changed", start, {"from": 16, "to": 6})))
        asyncio.run(svc.publish(ev("warp_entered", start + 85, {"level": 6, "area": 1, "to": 17})))
        asyncio.run(svc.publish(ev("level_changed", start + 108, {"from": 6, "to": 17})))
    older, newest = [a.id for a in db.attempts() if a.segment_id == lblj]
    calls = told(svc)
    asyncio.run(svc.set_attempt_strat(newest, "no bljs"))
    assert calls == [[(newest, "no bljs"), (older, "no bljs")]]


def test_a_failing_listener_never_stops_the_reclassification(tmp_path, caplog):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2))
    run = completed(svc, 1000, 226)

    def broken(_attempts):
        raise RuntimeError("ghost side is down")
    svc.on_attempts_reclassified = broken
    asyncio.run(svc.set_strat(2, 2, "Standard"))
    pb = db.current_pb(2, 2, "igt", strat_tag="Standard")
    assert db.attempt(run).strat_tag == "Standard" and pb["attempt_id"] == run
    asyncio.run(svc.set_attempt_strat(run, "TJ Strat"))
    assert db.attempt(run).strat_tag == "TJ Strat"
    assert db.current_pb(2, 2, "igt", strat_tag="TJ Strat")["attempt_id"] == run
    assert sent[-1].type == "attempts_invalidated"
    assert "could not follow reclassified attempts" in caplog.text


def test_the_app_wires_both_attempt_hooks_to_the_ghost(tmp_path):
    from sm64_events.main import _ghost_follows_attempts
    _db, svc, _ = make_rec(tmp_path)
    seen = []

    class Ghost:
        def attempt_saved(self, attempt):
            seen.append(("saved", attempt.id, attempt.strat_tag))

        def attempts_reclassified(self, attempts):
            seen.append(("reclassified", *[(a.id, a.strat_tag) for a in attempts]))

    _ghost_follows_attempts(svc, Ghost())
    asyncio.run(svc.set_target(2, 2, "Standard"))
    run = completed(svc, 1000, 226)
    asyncio.run(svc.set_attempt_strat(run, "TJ Strat"))
    assert seen == [("saved", run, "Standard"), ("reclassified", (run, "TJ Strat"))]


# -- the ghost session forwards, retargets, then re-resolves ------------------

def test_the_session_retargets_before_the_population_reclassifies_then_refreshes(tmp_path):
    """The population re-admits through `find_native`, which refuses a clip
    still naming the old strategy: every retarget comes first."""
    order = []

    def retarget_native(attempt_id):
        order.append(("retarget", attempt_id))
        if attempt_id == 8:
            raise OSError("clip folder unavailable")  # logged; the others still follow
        return True
    replay = SimpleNamespace(retarget_native=retarget_native)
    session = GhostSession(tracker=FakeTracker(), inputs=SimpleNamespace(templates=a_store(tmp_path)),
                           replay=replay, broadcaster=FakeBroadcaster())
    session._population_recorder = SimpleNamespace(reclassify=lambda attempt: order.append(("population", attempt.id)))
    session._rivals = SimpleNamespace(pb_changed=lambda target: order.append(("pb_changed", target)),
                                      pb_preserved=lambda attempt_id: order.append(("pb_preserved", attempt_id)))

    async def refresh(*, resolve=True, publish=True):
        order.append(("refresh", resolve))
    session.refresh = refresh
    runs = [SimpleNamespace(id=7, course_id=10, star_id=2, segment_id=None, strat_tag="TJ Strat"),
            SimpleNamespace(id=8, course_id=None, star_id=None, segment_id=4, strat_tag=None)]

    async def check():
        task = session.attempts_reclassified(runs)
        assert order == []  # nothing runs on the tracker's call itself
        await task
        assert not session._reclassify_tasks

    asyncio.run(check())
    assert order == [("retarget", 7), ("retarget", 8),
                     ("population", 7), ("population", 8),
                     ("pb_changed", {"kind": "star", "entity_key": "10-2"}),
                     ("pb_changed", {"kind": "segment", "entity_key": "4"}),
                     ("pb_preserved", 7), ("refresh", True)]


def test_a_recorder_without_reclassify_and_a_stopped_session_are_quiet(tmp_path):
    session = GhostSession(tracker=FakeTracker(), inputs=SimpleNamespace(templates=a_store(tmp_path)),
                           replay=None, broadcaster=FakeBroadcaster())
    async def stopped():
        return None
    session._population_recorder = SimpleNamespace(enqueue=lambda attempt: None, stop=stopped)
    run = SimpleNamespace(id=7, course_id=10, star_id=2, segment_id=None, strat_tag="TJ Strat")

    async def check():
        task = session.attempts_reclassified([run])
        await task  # no retarget door: it only re-resolves
        await session.stop()
        assert session.attempts_reclassified([run]) is None

    asyncio.run(check())


# -- the committed clips follow the strategy ----------------------------------

def retag(replay, row, strat):
    """The projected row as the database now stores it (rows are frozen)."""
    rows = replay.tracker.db._attempts
    rows[rows.index(row)] = row = replace(row, strat_tag=strat)
    return row


def committed_under_standard(tmp_path):
    """A PB run whose poses were committed and its video saved as Standard,
    then reclassified TJ Strat after the pose archive forgot it."""
    from test_ghost_recording import native_service
    service, row, archive = native_service(tmp_path)
    service.compressor = SimpleNamespace(enqueue=lambda *args, **named: None)
    row = retag(service, row, "Standard")
    service.preserve_native(row.id)
    saved = Path(service.save(row.id)["path"])
    archive.close()
    service.recorder.pose_archive = None
    return service, retag(service, row, "TJ Strat"), saved


def test_a_retagged_pb_clip_is_found_only_once_it_is_retargeted(tmp_path):
    service, row, saved = committed_under_standard(tmp_path)
    native = service.cfg.save_root.resolve() / "native" / f"attempt_{row.id}.json"
    assert service.find_native(row.id) is None  # the clip still names Standard
    assert service.retarget_native(row.id) is True
    path, manifest = service.find_native(row.id)
    assert path.with_suffix(".json") == native and manifest["target"]["strat"] == "TJ Strat"
    sidecar = json.loads(saved.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["native_ghost"]["target"]["strat"] == "TJ Strat"
    native.unlink()  # the saved video's copy of the manifest followed too
    assert service.find_native(row.id)[0] == saved.with_suffix(".ghost")
    assert service.retarget_native(row.id) is False  # already current


def test_a_pb_resave_after_the_archive_forgot_keeps_the_committed_clip(tmp_path):
    service, row, _saved = committed_under_standard(tmp_path)
    result = service.preserve_native(row.id)
    assert result["native_ghost_reason"] is None and result["native_ghost"]["target"]["strat"] == "TJ Strat"
    assert service.find_native(row.id) is not None


def test_the_playback_copy_follows_and_another_entity_is_refused(tmp_path):
    service, row, saved = committed_under_standard(tmp_path)
    native = service.cfg.save_root.resolve() / "native"
    inputs = native / "inputs"
    inputs.mkdir()
    for suffix in (".ghost", ".json"):
        (inputs / f"attempt_{row.id}{suffix}").write_bytes((native / f"attempt_{row.id}{suffix}").read_bytes())
    foreign = json.loads(saved.with_suffix(".json").read_text(encoding="utf-8"))
    for key, value in (("kind", "segment"), ("entity_key", "2-3")):
        moved = {**foreign, "native_ghost": {**foreign["native_ghost"],
                 "target": {**foreign["native_ghost"]["target"], key: value}}}
        saved.with_suffix(".json").write_text(json.dumps(moved), encoding="utf-8")
        before = saved.with_suffix(".json").read_bytes()
        service.retarget_native(row.id)
        assert saved.with_suffix(".json").read_bytes() == before  # not this run's: untouched
    copy = json.loads((inputs / f"attempt_{row.id}.json").read_text(encoding="utf-8"))
    assert copy["target"]["strat"] == "TJ Strat"


def test_the_pb_ghost_plays_again_after_its_strategy_is_corrected(tmp_path):
    from test_ghost_pb import pb_world, save_pb
    session, replay, row, archive, db, _store, _capture = pb_world(tmp_path)
    row = retag(replay, row, "Standard")
    session._tracker.strat_tag = "Standard"

    async def check():
        await session.update({"enabled": True})
        save_pb(db, row, strat="Standard")
        await session.pb_changed(row, True)
        assert session.payload()["selection"]["pb"]["native_available"]
        archive.close()
        replay.recorder.pose_archive = None
        moved = retag(replay, row, "TJ Strat")
        session._tracker.strat_tag = "TJ Strat"
        db.retag_pbs_for_attempt(row.id, "TJ Strat")
        await session.refresh(resolve=True)
        assert not session.payload()["selection"]["pb"]["native_available"]
        await session.attempts_reclassified([moved])
        body = session.payload()
        assert body["selection"]["pb"]["attempt_id"] == row.id
        assert body["selection"]["pb"]["native_available"] and body["source"]["available"]
        await session.stop()

    try:
        asyncio.run(check())
    finally:
        archive.close()
