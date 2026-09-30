"""PSS arrival remembers explicit choices and play, independent of log scope."""
import asyncio

import pytest

from sm64_events.server.broadcaster import Broadcaster
from sm64_events.tracking.service import TrackerService
from sm64_events.tracking.views import build_session_view
from sm64_events.tracking.importing import ImportCandidate
from test_views import ev, make, star


@pytest.mark.parametrize("last_star", [0, 1])
def test_pss_last_completed_star_survives_restart_and_session_scope(tmp_path, last_star):
    db, svc = make(tmp_path)
    for slot, strategy in [(0, "Box"), (1, "Under 21")]:
        asyncio.run(svc.set_strat(19, slot, strategy))
    asyncio.run(svc.publish(star(1000, course=19, star_id=1 - last_star, igt=700)))
    asyncio.run(svc.publish(star(2000, course=19, star_id=last_star, igt=700)))
    restarted = TrackerService(db, Broadcaster())
    asyncio.run(restarted.start())
    for scope in ("session", "lifetime"):
        view = build_session_view(db, restarted, "igt", scope=scope)
        assert view["last_star_choice_by_course"]["19"] == last_star
        expected = "Under 21" if last_star else "Box"
        assert view["last_strat_by_star"][f"19:{last_star}"] == expected


def test_imports_and_cleared_successes_do_not_replace_last_played_pss_star(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_strat(19, 1, "Under 21"))
    asyncio.run(svc.publish(star(1000, course=19, star_id=1, igt=700)))
    asyncio.run(svc.publish(star(2000, course=19, star_id=0, igt=700)))
    cleared = next(a for a in db.attempts() if a.star_id == 0)
    asyncio.run(svc.clear_attempt(cleared.id))
    asyncio.run(svc.import_times("manual", [ImportCandidate(
        entity_key="star:19:0", strat_tag="Box", time_cs=1800)]))
    restarted = TrackerService(db, Broadcaster())
    asyncio.run(restarted.start())
    assert build_session_view(db, restarted, "igt")["last_star_choice_by_course"]["19"] == 1


def test_pss_without_played_history_has_no_remembered_choice(tmp_path):
    db, svc = make(tmp_path)
    assert "19" not in build_session_view(db, svc, "igt")["last_star_choice_by_course"]
    asyncio.run(svc.set_target(19, 0, auto=True))
    assert "19" not in build_session_view(db, svc, "igt")["last_star_choice_by_course"]


def test_explicit_pss_choice_survives_reset_only_practice_and_restart(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(star(1000, course=19, star_id=0, igt=700)))
    asyncio.run(svc.set_target(19, 1, "Under 21"))
    asyncio.run(svc.publish(ev("practice_reset", 2000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(ev("practice_reset", 2500, {"igt_frames_before": 450})))
    # Route-driven convenience fills cannot replace his deliberate choice.
    asyncio.run(svc.set_target(19, 0, auto=True))
    restarted = TrackerService(db, Broadcaster())
    asyncio.run(restarted.start())
    view = build_session_view(db, restarted, "igt")
    assert view["last_star_choice_by_course"]["19"] == 1
    assert view["last_strat_by_star"]["19:1"] == "Under 21"
    # An actual newer completion does replace the explicit choice.
    asyncio.run(restarted.publish(star(3000, course=19, star_id=0, igt=700)))
    assert build_session_view(db, restarted, "igt")["last_star_choice_by_course"]["19"] == 0


def test_departing_secret_course_auto_offer_expires_without_changing_target(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("stage_changed", 1000, {
        "course_id": 19, "level": 27, "area": 1, "mode": "stars"})))
    asyncio.run(svc.request_target("star", course_id=19, star_id=1, strat_tag="Under 21"))
    before = len(db.events())
    # The VCUTM row can remain painted through the course exchange. Its late
    # auto offer is a no-op, while a deliberate invalid pick is still refused.
    assert asyncio.run(svc.request_target("star", course_id=22, star_id=0, auto=True)) == {}
    assert len(db.events()) == before
    assert svc.target == ("star", 19, 1)
    assert svc.last_star_choice_by_course["19"] == 1
    with pytest.raises(ValueError, match="standing in"):
        asyncio.run(svc.request_target("star", course_id=22, star_id=0))


def test_auto_offer_cannot_restore_departed_star_between_level_and_stage_events(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 6, "to": 18})))
    asyncio.run(svc.publish(ev("stage_changed", 1000, {
        "course_id": 22, "level": 18, "area": 1, "mode": "stars"})))
    asyncio.run(svc.request_target("star", course_id=22, star_id=0, auto=True))
    assert svc.target == ("star", 22, 0)
    asyncio.run(svc.publish(ev("level_changed", 2000, {"from": 18, "to": 27})))
    assert svc.target is None
    # The API still holds stage22 here; the projector already knows level27.
    asyncio.run(svc.request_target("star", course_id=22, star_id=0, auto=True))
    assert svc.target is None
    assert svc.last_star_choice_by_course == {}
    asyncio.run(svc.publish(ev("stage_changed", 2000, {
        "course_id": 19, "level": 27, "area": 1, "mode": "stars"})))
    asyncio.run(svc.request_target("star", course_id=19, star_id=1, auto=True))
    assert svc.target == ("star", 19, 1)


@pytest.mark.parametrize("offered", [0, 1])
def test_late_pss_auto_offer_cannot_replace_explicit_star_or_strategy(tmp_path, offered):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("level_changed", 1000, {"from": 6, "to": 27})))
    asyncio.run(svc.publish(ev("stage_changed", 1000, {
        "course_id": 19, "level": 27, "area": 1, "mode": "stars"})))
    asyncio.run(svc.request_target("star", course_id=19, star_id=1, strat_tag="Under 21"))
    before = len(db.events())
    asyncio.run(svc.request_target("star", course_id=19, star_id=offered,
                                   strat_tag="Stale strategy", auto=True))
    assert len(db.events()) == before
    assert svc.target == ("star", 19, 1) and svc.strat_tag == "Under 21"
    # Even a fill already past the API check must decline at projection.
    asyncio.run(svc.set_target(19, offered, "Stale strategy", auto=True))
    assert svc.target == ("star", 19, 1) and svc.strat_tag == "Under 21"
    assert svc.last_star_choice_by_course["19"] == 1
