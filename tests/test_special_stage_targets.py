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
