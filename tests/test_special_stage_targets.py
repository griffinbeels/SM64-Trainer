"""PSS arrival memory uses played history, independent of the log's scope."""
import asyncio
from dataclasses import replace

import pytest

from sm64_events.server.broadcaster import Broadcaster
from sm64_events.tracking.service import TrackerService
from sm64_events.tracking.views import build_session_view
from test_views import make, star


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
        assert view["last_completed_star_by_course"]["19"] == last_star
        expected = "Under 21" if last_star else "Box"
        assert view["last_strat_by_star"][f"19:{last_star}"] == expected


def test_imports_and_cleared_successes_do_not_replace_last_played_pss_star(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_strat(19, 1, "Under 21"))
    asyncio.run(svc.publish(star(1000, course=19, star_id=1, igt=700)))
    [played] = db.attempts()
    db.upsert_attempt(replace(played, id=played.id + 1, star_id=0, timed_by="imported"))
    db.upsert_attempt(replace(played, id=played.id + 2, star_id=0, cleared=True))
    view = build_session_view(db, svc, "igt")
    assert view["last_completed_star_by_course"]["19"] == 1


def test_pss_without_played_history_has_no_remembered_choice(tmp_path):
    db, svc = make(tmp_path)
    assert "19" not in build_session_view(db, svc, "igt")["last_completed_star_by_course"]
