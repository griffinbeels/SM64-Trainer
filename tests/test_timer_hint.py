"""The practice log's "Set Timer -> GrabX" hint, from the grab's payload to
the row the view ships (2026-10-17).

His words: "annotate the entry (TO THE LEFT OF THE ENTRY NUMBER) such that we
say "Set Timer -> GrabX" ... All of our practice entries should be xcam
times." The detector stamps what Usamune's own timer stops at on every grab
(`usamune_stop`); a row is hinted while that grab's setting AND his current
one would show a number other than the x-cam time the log records.
"""
import asyncio
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService
from sm64_events.tracking.views import build_session_view

T0 = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)
WF, COURSE, STAR = 24, 2, 1
_ABSENT = object()


def ev(type_, frame, payload=None):
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def reset(frame):
    return ev("practice_reset", frame,
              {"igt_frames_before": 300, "mario_acted": True, "area": 1,
               "prev_area": 1, "area_load": False, "teleport": False,
               "loads_journaled": True})


def grab(frame, stop=_ABSENT):
    payload = {"course_id": COURSE, "star_id": STAR, "igt_frames": 480,
               "igt_timed_at": "xcam"}
    if stop is not _ABSENT:  # absent = a grab journaled before the key existed
        payload["usamune_stop"] = stop
    return ev("star_collected", frame, payload)


def enter():
    return [ev("level_changed", 1000, {"from": 6, "to": WF}),
            ev("area_changed", 1000, {"level": WF, "from": 1, "to": 1})]


def play(db, *steps):
    """One server run over `db`. A strategy is picked so the startup prune
    (an unlabelled star does not survive a restart) keeps every row."""
    svc = TrackerService(db, Broadcaster())

    async def scenario():
        await svc.start()
        await svc.set_strat(COURSE, STAR, "Standard")
        for step in steps:
            if isinstance(step, Event):
                await svc.publish(step)
            else:
                await step(svc)
        while svc.auto_pb_tasks:
            await asyncio.gather(*list(svc.auto_pb_tasks))
    asyncio.run(scenario())
    return svc


def runs(*stops, start=1500):
    """One grab per stop, each opened by its own reset."""
    out = []
    for i, stop in enumerate(stops):
        frame = start + i * 1000
        out += [reset(frame - 400), grab(frame, stop)]
    return out


def star_rows(db, svc):
    view = build_session_view(db, svc, "igt", scope="lifetime")
    (sec,) = [s for s in view["stars"]
              if (s["course_id"], s["star_id"]) == (COURSE, STAR)]
    return {row["id"]: row for row in sec["attempts"]}, view


def hints(db, svc):
    rows, _ = star_rows(db, svc)
    by_id = {a.id: a for a in db.attempts()}
    return [(by_id[i].usamune_stop, rows[i]["timer_hint"]) for i in sorted(rows)]


def test_the_grab_s_setting_reaches_the_row_and_survives_a_reproject(tmp_path):
    db = Database(tmp_path / "t.db")
    play(db, *enter(), *runs(_ABSENT, "grab", "none", "xcam", "Grab!"))
    assert [a.usamune_stop for a in db.attempts()] == [None, "grab", "none", "xcam", None]
    # A restart re-projects from the journal; the stamp is the payload's, so
    # it comes back the same, through the stored column and the replay alike.
    play(db)
    assert [a.usamune_stop for a in db.attempts()] == [None, "grab", "none", "xcam", None]


def test_rows_are_hinted_while_his_current_setting_still_stops_short(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = play(db, *enter(), *runs(_ABSENT, "grab", "none"))
    # The legacy row's setting is unknown: never hinted.
    assert hints(db, svc) == [(None, False), ("grab", True), ("none", True)]


def test_one_grab_under_grabx_clears_every_hint_and_a_relapse_brings_them_back(tmp_path):
    db = Database(tmp_path / "t.db")
    svc = play(db, *enter(), *runs("grab", "none", "xcam"))
    assert hints(db, svc) == [("grab", False), ("none", False), ("xcam", False)]
    svc = play(db, *runs("grab", start=6500))
    assert hints(db, svc) == [("grab", True), ("none", True), ("xcam", False),
                              ("grab", True)]
    # A newer grab that says nothing (an older detector) is not evidence:
    # the current setting stays the last one a grab reported.
    svc = play(db, *runs(_ABSENT, start=8500))
    assert hints(db, svc) == [("grab", True), ("none", True), ("xcam", False),
                              ("grab", True), (None, False)]


def test_a_segment_closed_by_the_same_grab_is_never_hinted(tmp_path):
    made = {}

    async def define(svc):
        made["id"] = await svc.create_segment({
            "name": "pole to star", "match_mode": "strict",
            "start_triggers": [{"type": "moment_reached", "level": WF,
                                "kind": "pole_grab"}],
            "end_triggers": [{"type": "star_grabbed", "course": COURSE,
                              "star": STAR}]})

    pole = ev("moment_reached", 1200, {"kind": "pole_grab", "level": WF,
                                       "area": 1, "ordinal": 1})
    db = Database(tmp_path / "t.db")
    svc = play(db, define, *enter(), reset(1047), pole, grab(1500, "grab"))
    rows, view = star_rows(db, svc)
    assert [r["timer_hint"] for r in rows.values()] == [True]
    (seg,) = [s for s in view["segments"] if s["segment_id"] == made["id"]]
    assert seg["attempts"] and not any(r["timer_hint"] for r in seg["attempts"])
    assert all(a.usamune_stop is None for a in db.attempts() if a.segment_id is not None)
