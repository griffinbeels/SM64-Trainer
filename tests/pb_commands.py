"""Save or undo a PB the way the APP does since 2026-08-20 -- under the
strategy the run was tagged with, which must also be the one being practised
(`tracking/pbaction.py`). ONE expression of that rule for every test that
only needs A saved PB; the tests that are ABOUT the gate call
`svc.save_pb`/`svc.undo_pb` directly. Shared by tests/test_views.py and
tests/test_tracker_service.py, which each used to carry a copy.
"""
import asyncio


def save_pb(svc, db, attempt_id, mode):
    attempt = next(a for a in db.attempts() if a.id == attempt_id)
    tag = attempt.strat_tag or "Standard"
    if attempt.segment_id is not None:
        asyncio.run(svc.set_strat_segment(attempt.segment_id, tag))
    else:
        asyncio.run(svc.set_strat(attempt.course_id, attempt.star_id, tag))
    if not attempt.strat_tag:
        asyncio.run(svc.set_attempt_strat(attempt_id, tag))
    attempt = next(a for a in db.attempts() if a.id == attempt_id)
    if svc.pb_action(attempt, mode)[0] == "undo":
        # A valid run under a picked strategy already saved ITSELF (task
        # 0144's automatic PB); a test that only needs a saved PB gets it.
        row = db.current_pb(attempt.course_id, attempt.star_id, mode,
                            segment_id=attempt.segment_id,
                            strat_tag=attempt.strat_tag)
        return {"frames": row["frames"], "attempt_id": attempt_id,
                "timer_mode": mode, "strat_tag": row["strat_tag"]}
    return asyncio.run(svc.save_pb(attempt_id, mode))   # the real command


def undo_pb(svc, db, attempt_id, mode):
    """Undo the way the app does: only the active strategy's own PB."""
    attempt = next(a for a in db.attempts() if a.id == attempt_id)
    if attempt.segment_id is not None:
        asyncio.run(svc.set_strat_segment(attempt.segment_id, attempt.strat_tag))
    else:
        asyncio.run(svc.set_strat(attempt.course_id, attempt.star_id,
                                  attempt.strat_tag))
    return asyncio.run(svc.undo_pb(attempt_id, mode))
