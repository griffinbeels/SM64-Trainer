import pytest

from sm64_events.storage.db import EventRow
from sm64_events.tracking.segments import (MatchContext, SegmentDef,
                                           validate_definition, vocab)

W = "2026-06-11T12:00:00Z"


def jev(id, type, frame, payload=None, session_id=1):
    # local copy of test_projection.py's factory (tests/ is not a package)
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


def test_validate_accepts_a_seed_shaped_definition():
    validate_definition({
        "name": "LBLJ",
        "start_triggers": [{"type": "level_enter", "to": 6, "from": 16}],
        "end_triggers": [{"type": "level_enter", "to": 17}],
        "guards": []})  # no raise


def test_validate_rejects_unknown_trigger_type():
    with pytest.raises(ValueError, match="unknown trigger type"):
        validate_definition({"name": "x",
                             "start_triggers": [{"type": "nope"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": []})


def test_validate_rejects_missing_required_param():
    with pytest.raises(ValueError, match="level_enter"):
        validate_definition({"name": "x",
                             "start_triggers": [{"type": "level_enter"}],
                             "end_triggers": [{"type": "spawned"}],
                             "guards": []})


def test_vocab_lists_triggers_guards_and_level_enum():
    v = vocab()
    keys = {t["key"] for t in v["triggers"]}
    assert {"level_enter", "level_exit", "area_enter", "warp_entered",
            "key_grabbed", "star_grabbed", "spawned",
            "attempt_anchor"} <= keys
    assert v["levels"]["17"] == "Bowser in the Dark World"
    assert {g["key"] for g in v["guards"]} == {"prev_level",
                                               "star_count_min",
                                               "star_count_max"}
