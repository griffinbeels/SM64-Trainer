"""Strategy classification banks only an eligible improvement, through the PB door."""
import asyncio

import pytest

from test_tracker_service import ev, make_rec, seed_id, star
from test_api import make_client


def completed(svc, anchor, frames, *, elapsed=650, timed_at="xcam"):
    asyncio.run(svc.publish(ev("practice_reset", anchor, {"igt_frames_before": 0})))
    event = star(anchor + elapsed, igt=frames)
    event.payload["igt_timed_at"] = timed_at
    asyncio.run(svc.publish(event))
    return max(svc.db.attempts(), key=lambda a: a.id).id


def test_first_pick_saves_fastest_eligible_and_preserves_replay(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2))
    completed(svc, 1000, 600)
    best = completed(svc, 2000, 480)
    completed(svc, 3000, 300, timed_at="grab")
    cleared = completed(svc, 4000, 330)
    asyncio.run(svc.clear_attempt(cleared))
    asyncio.run(svc.publish(ev("practice_reset", 5000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("death", 5030)))
    replays = []

    async def preserve(attempt_id):
        replays.append(attempt_id)
        return {"status": "saved"}
    svc.on_pb_saved = preserve
    saved = asyncio.run(svc.set_strat(2, 2, "Cannonless"))
    assert db.current_pb(2, 2, "igt", strat_tag="Cannonless")["attempt_id"] == best
    assert replays == [best] and saved["replay_save"]["status"] == "saved"
    golds = [e for e in sent if e.type == "pb_saved"]
    assert len(golds) == 1 and golds[0].payload["attempt_id"] == best
    assert "classification" in golds[0].payload
    asyncio.run(svc.set_strat(2, 2, "Cannonless"))
    assert len([e for e in sent if e.type == "pb_saved"]) == 1
    before = db.attempts(), db.pbs()
    asyncio.run(svc._reproject())
    assert (db.attempts(), db.pbs()) == before


def test_first_pick_uses_requested_star_clock(tmp_path):
    db, svc, _ = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2))
    completed(svc, 1000, 420, elapsed=650)
    rta_best = completed(svc, 2000, 600, elapsed=400)
    asyncio.run(svc.set_target(2, 2, "Cannonless", timer_mode="rta"))
    assert db.current_pb(2, 2, "rta", strat_tag="Cannonless")["attempt_id"] == rta_best
    assert db.current_pb(2, 2, "igt", strat_tag="Cannonless") is None


@pytest.mark.parametrize("door", ["card", "latest"])
def test_first_pick_through_either_door_saves_one_fastest_row(tmp_path, door):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2))
    best = completed(svc, 1000, 420)
    last = completed(svc, 2000, 600)
    if door == "card":
        asyncio.run(svc.set_strat(2, 2, "Cannonless"))
    else:
        asyncio.run(svc.set_attempt_strat(last, "Cannonless"))
    assert db.current_pb(2, 2, "igt", strat_tag="Cannonless")["attempt_id"] == best
    assert len([e for e in sent if e.type == "pb_saved"]) == 1


@pytest.mark.parametrize("frames,saved", [(420, True), (500, False), (600, False)])
def test_latest_correction_compares_destination_pb(tmp_path, frames, saved):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2, "Slide Kick"))
    previous = completed(svc, 1000, 500)
    asyncio.run(svc.save_pb(previous, "igt"))
    asyncio.run(svc.set_strat(2, 2, "Cannonless"))
    latest = completed(svc, 2000, frames)
    count = len([e for e in sent if e.type == "pb_saved"])
    result = asyncio.run(svc.set_attempt_strat(latest, "Slide Kick"))
    assert svc.strat_by_star[(2, 2)] == "Slide Kick"
    current = db.current_pb(2, 2, "igt", strat_tag="Slide Kick")
    assert current["attempt_id"] == (latest if saved else previous)
    assert len([e for e in sent if e.type == "pb_saved"]) == count + int(saved)
    assert bool(result) == saved


@pytest.mark.parametrize("frames,wins", [(420, True), (500, False), (600, False)])
def test_reclassifying_saved_latest_preserves_destination_winner(tmp_path, frames, wins):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2, "Slide Kick"))
    previous = completed(svc, 1000, 500)
    asyncio.run(svc.save_pb(previous, "igt"))
    asyncio.run(svc.set_strat(2, 2, "Cannonless"))
    latest = completed(svc, 2000, frames)
    asyncio.run(svc.save_pb(latest, "igt"))
    count = len([e for e in sent if e.type == "pb_saved"])
    asyncio.run(svc.set_attempt_strat(latest, "Slide Kick"))
    assert db.current_pb(2, 2, "igt", strat_tag="Slide Kick")["attempt_id"] == (latest if wins else previous)
    assert db.current_pb(2, 2, "igt", strat_tag="Cannonless") is None
    assert len([e for e in sent if e.type == "pb_saved"]) == count + int(wins)
    assert len(db.pbs()) == 2
    if not wins:
        asyncio.run(svc.set_attempt_strat(latest, "Cannonless"))
        assert db.current_pb(2, 2, "igt", strat_tag="Cannonless")["attempt_id"] == latest
        asyncio.run(svc.set_strat(2, 2, "Slide Kick"))
        asyncio.run(svc.undo_pb(previous, "igt"))
        assert db.current_pb(2, 2, "igt", strat_tag="Slide Kick") is None


def test_older_edit_does_not_bank_or_switch_strategy(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2, "Cannonless"))
    older = completed(svc, 1000, 420)
    completed(svc, 2000, 600)
    asyncio.run(svc.set_attempt_strat(older, "Slide Kick"))
    assert svc.strat_by_star[(2, 2)] == "Cannonless"
    assert not db.pbs() and not [e for e in sent if e.type == "pb_saved"]


def test_a_migrated_winner_saved_before_destination_does_not_duplicate(tmp_path):
    db, svc, _ = make_rec(tmp_path)
    asyncio.run(svc.set_target(2, 2, "Slide Kick"))
    previous = completed(svc, 1000, 500)
    asyncio.run(svc.set_strat(2, 2, "Cannonless"))
    latest = completed(svc, 2000, 420)
    asyncio.run(svc.save_pb(latest, "igt"))
    asyncio.run(svc.set_strat(2, 2, "Slide Kick"))
    asyncio.run(svc.save_pb(previous, "igt"))
    asyncio.run(svc.set_attempt_strat(latest, "Slide Kick"))
    assert len(db.pbs()) == 2
    assert db.current_pb(2, 2, "igt", strat_tag="Slide Kick")["attempt_id"] == latest
    asyncio.run(svc.undo_pb(latest, "igt"))
    assert db.current_pb(2, 2, "igt", strat_tag="Slide Kick")["attempt_id"] == previous


def test_segment_first_pick_and_correction_use_rta(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    sid = seed_id(db, "LBLJ")
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 16, "to": 6})))
    asyncio.run(svc.publish(ev("warp_entered", 1085, {"level": 6, "area": 1, "to": 17})))
    asyncio.run(svc.publish(ev("level_changed", 1108, {"from": 6, "to": 17})))
    aid = next(a.id for a in db.attempts() if a.segment_id == sid)
    asyncio.run(svc.set_strat_segment(sid, "no bljs"))
    assert db.current_pb(None, None, "rta", segment_id=sid, strat_tag="no bljs")["attempt_id"] == aid
    asyncio.run(svc.set_attempt_strat(aid, "fast", timer_mode="igt"))
    assert db.current_pb(None, None, "rta", segment_id=sid, strat_tag="fast")["attempt_id"] == aid
    assert [e.payload["timer_mode"] for e in sent if e.type == "pb_saved"] == ["rta", "rta"]


def test_http_classification_returns_gold_and_rejects_bad_clock_before_write(tmp_path):
    client, svc, db = make_client(tmp_path)
    with client:
        asyncio.run(svc.set_target(2, 2))
        best = completed(svc, 1000, 420)
        bad = client.post("/api/strat", json={"course_id": 2, "star_id": 2,
                          "strat_tag": "Cannonless", "timer_mode": "bad"})
        assert bad.status_code == 422 and db.attempts()[0].strat_tag is None
        response = client.post("/api/strat", json={"course_id": 2, "star_id": 2,
                               "strat_tag": "Cannonless", "timer_mode": "igt"})
        assert response.status_code == 200
        assert response.json()["automatic_pb"]["attempt_id"] == best
