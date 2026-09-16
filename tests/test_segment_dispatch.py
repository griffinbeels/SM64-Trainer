"""Journal volume must not multiply work across unrelated idle segments."""
from dataclasses import replace

from sm64_events.storage.db import EventRow
from sm64_events.tracking import segments


def event(kind, frame=10, **payload):
    return EventRow(id=frame, session_id=1, seq=frame, type=kind, frame=frame,
                    wall_time_utc="2026-09-09T12:00:00Z", payload=payload)


def definition(sid=1, **changes):
    return replace(segments.SegmentDef(
        id=sid, name=f"movement {sid}", enabled=True,
        start_triggers=[{"type": "level_enter", "to": 6}],
        end_triggers=[{"type": "level_enter", "to": 19}],
        guards=[], match_mode="loose"), **changes)


def test_unrelated_journal_events_do_not_scan_idle_start_clauses(monkeypatch):
    engine = segments.SegmentEngine([definition(sid) for sid in range(500)])
    matched = []
    original = engine._first_match

    def count(*args):
        matched.append(args)
        return original(*args)

    monkeypatch.setattr(engine, "_first_match", count)
    assert engine.feed(event("padding"), segments.MatchContext(None, None, None)) == ([], [])
    assert matched == [], "an irrelevant event visited every idle definition"


def test_unknown_events_still_expire_an_active_segment():
    engine = segments.SegmentEngine([definition()])
    ctx = segments.MatchContext(level=6, prev_level=23, num_stars=None)
    engine.feed(event("level_changed", **{"from": 23, "to": 6}), ctx)
    assert engine.armed_ids() == {1}
    closed, notices = engine.feed(
        event("future_event", 11 + segments.MIN_BUDGET_FRAMES), ctx)
    assert closed == []
    assert engine.armed_ids() == set()
    assert [n["event"] for n in notices] == ["segment_disarmed"]


def test_new_unannotated_trigger_keeps_matching_any_event(monkeypatch):
    trigger = segments.TriggerType(
        "future", "Future", "Future", {}, "future",
        lambda _clause, ev, _ctx: ev.type == "new_event")
    monkeypatch.setitem(segments.TRIGGERS, "future", trigger)
    engine = segments.SegmentEngine([
        definition(start_triggers=[{"type": "future"}])])
    _, notices = engine.feed(event("new_event"), segments.MatchContext(None, None, None))
    assert engine.armed_ids() == {1}
    assert [n["event"] for n in notices] == ["segment_armed"]
