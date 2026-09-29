"""Entering a level is never a reset, on any stage.

His ruling, 2026-09-28, after Bowser 1, 2 and 3 each logged "reset 00"06
in" on arrival: "i just entered the stage, we couldn't have possibly reset...
Need to make sure that this doesn't happen for ANY stage or any course /
star." Usamune zeroes its counter as Mario arrives -- a menu warp or a pipe
1-6 frames after the level edge, a painting 42-59 -- and that zero is an
anchor. Each test replays one arrival from his journals (payloads verbatim,
trimmed to the keys the tracker reads) and asserts it banks no reset row.
`tools/measure_entry_resets.py` is the corpus-wide count, per level.
"""
import dataclasses
import json

import pytest

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.defaults import reconcile_defaults
from sm64_events.tracking.projection import Projector
from sm64_events.tracking.segments import SegmentDef

W = "2026-09-28T12:00:00Z"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    db = Database(tmp_path_factory.mktemp("entry") / "t.db")
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    assert reconcile_defaults(db, seed) == []
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    rows = db.segment_defs()
    return ([SegmentDef(**{k: row[k] for k in keys}) for row in rows],
            {row["seed_key"]: row["id"] for row in rows})


def anchor(frame, before, action, prev, paused, *, fsd=10000, **extra):
    return ("practice_reset", frame, {
        "igt_frames_before": before, "mario_acted": True,
        "paused_frames_before": paused, "acted_tracking": True,
        "action": action, "prev_action": prev, "save_pending": False,
        "frames_since_door": None, "frames_since_dialog": fsd, "area": 1,
        "area_load": False, "teleport": False, **extra})


def resets(defs, rows, target=None, session=1):
    p = Projector(segments=defs)
    if target is not None:
        p.feed(EventRow(id=1, session_id=session, seq=1, type="target_set",
                        frame=0, wall_time_utc=W, payload=target))
    closed = []
    for jid, (type_, frame, payload) in enumerate(rows, start=10):
        closed += p.feed(EventRow(id=jid, session_id=session, seq=jid, type=type_,
                                  frame=frame, wall_time_utc=W, payload=payload))
    return [(a.segment_id, a.course_id, a.star_id, a.rta_frames)
            for a in closed if a.outcome == "reset"]


def test_a_pipe_into_bowser_2_is_not_a_reset(corpus):
    """Live journal ids 6627-6633, session 29: the BitFS pipe, and the zero
    two frames after the arena loads. Before: "#4 reset 00"06 in"."""
    defs, ids = corpus
    fight = ids["seg:bowser-2"]
    rows = [("warp_entered", 82402, {"level": 19, "area": 1, "action": 4864,
                                     "to": 33, "igt_frames": 1481}),
            ("level_changed", 82425, {"from": 19, "to": 33, "from_area": 1,
                                      "igt_frames": 1502}),
            ("area_changed", 82425, {"level": 33, "from": 1, "to": 1,
                                     "igt_frames": 1502}),
            ("target_set", 0, {"kind": "segment", "segment_id": fight, "auto": True}),
            anchor(82427, 1501, 6436, 4864, 2),
            ("spawned", 82427, {"level": 33, "kind": "spawn", "area": 1,
                                "igt_frames": 0}),
            ("mario_acted", 82464, {"action": 536875782})]
    assert resets(defs, rows) == []


def test_a_painting_into_whomps_fortress_is_not_a_reset(corpus):
    """console-support journal ids 16140-16150: into WF through the painting,
    the counter zeroing 45 frames later. The WF 100-coin engine armed on the
    entry and banked the zero as a 1.5 s reset of the 100-coin star."""
    defs, _ = corpus
    rows = [("level_changed", 8870061, {"from": 24, "to": 6, "from_area": 1,
                                        "igt_frames": 2652}),
            ("area_changed", 8870061, {"level": 6, "from": 1, "to": 1,
                                       "igt_frames": 2652}),
            anchor(8870061, 2651, 4866, 4866, 2),
            ("mario_acted", 8870092, {"action": 4903}),
            anchor(8871034, 148, 205521409, 4903, 823, save_pending=True),
            ("mario_acted", 8871041, {"action": 67109952}),
            ("warp_entered", 8871066, {"level": 6, "area": 1, "action": 4864,
                                       "to": 24, "igt_frames": 33}),
            ("level_changed", 8871144, {"from": 6, "to": 24, "from_area": 1,
                                        "igt_frames": 108}),
            ("area_changed", 8871144, {"level": 24, "from": 1, "to": 2,
                                       "igt_frames": 108}),
            ("area_changed", 8871189, {"level": 24, "from": 2, "to": 1,
                                       "igt_frames": 108}),
            anchor(8871189, 107, 6436, 4864, 47)]
    assert resets(defs, rows, target={"course_id": 2, "star_id": 6}) == []


def test_a_zero_read_twice_on_one_frame_is_not_a_reset(corpus):
    """Repo journal ids 16237-16248: into BitDW from the lobby, and the one
    arrival zero journaled twice on the same frame. Before: a "Shig (Star)"
    reset of 0 frames on the 8 Red Coins star."""
    defs, _ = corpus
    rows = [("level_changed", 33207, {"from": 17, "to": 6, "from_area": 1}),
            ("area_changed", 33207, {"level": 6, "from": 1, "to": 1}),
            anchor(33207, 672, 16910512, 16910512, 3, fsd=None),
            ("mario_acted", 33207, {}),
            ("level_changed", 33246, {"from": 6, "to": 17, "from_area": 1}),
            ("area_changed", 33246, {"level": 17, "from": 1, "to": 1}),
            anchor(33246, 25, 6444, 6444, 15, fsd=None),
            ("mario_acted", 33246, {}),
            anchor(33246, 25, 6444, 6444, 0, fsd=None),
            ("mario_acted", 33246, {}),
            ("spawned", 33246, {"level": 17, "kind": "spawn"})]
    assert resets(defs, rows, target={"course_id": 16, "star_id": 0}) == []


def test_walking_into_bitdw_for_the_reds_is_not_a_reset(corpus):
    """Live journal ids 7015-7020, session 29: through the lobby's BitDW
    entrance with "8 Red Coins (Pipe)" auto-selected, the zero two frames
    after the arrival. Before: "#14 reset 00"06 in" on that card."""
    defs, ids = corpus
    reds = ids["seg:reds->pipe:bitdw"]
    rows = [("mario_acted", 108705, {"action": 16779404}),
            ("warp_entered", 109077, {"level": 6, "area": 1, "action": 4864,
                                      "to": 17, "igt_frames": 394}),
            ("level_changed", 109100, {"from": 6, "to": 17, "from_area": 1,
                                       "igt_frames": 415}),
            ("area_changed", 109100, {"level": 17, "from": 1, "to": 1,
                                      "igt_frames": 415}),
            ("target_set", 0, {"kind": "segment", "segment_id": reds, "auto": True}),
            anchor(109102, 414, 6450, 4864, 2),
            ("spawned", 109102, {"level": 17, "kind": "spawn", "area": 1,
                                 "igt_frames": 0})]
    assert resets(defs, rows) == []


def test_a_resets_own_reload_through_a_teleporters_fade_is_not_a_second_reset(corpus):
    """console-support journal ids 20178-20187: one L reset in WDW whose
    reload walks the area byte 1 -> 2 -> 1, zeroing the counter at each step,
    while Mario's teleporter fade-in byte still lingers. The fade-in was
    counted as an act, so the second zero banked a 22-frame "reset" of the
    star he had just grabbed. His only act was the game's own fade."""
    defs, _ = corpus
    fade_out, fade_in = 4918, 4919
    rows = [("warp_entered", 634490, {"level": 11, "area": 1, "action": fade_out,
                                      "to": 11, "igt_frames": 219}),
            anchor(634532, 257, fade_in, fade_out, 2, teleport=True),
            ("mario_acted", 634565, {"action": 67109952}),
            ("star_collected", 634744, {"course_id": 11, "star_id": 0,
                                        "igt_frames": 471, "igt_source": "counter"}),
            anchor(635534, 312, 4866, 4866, 0),
            ("mario_acted", 635534, {"action": fade_in}),
            ("area_changed", 635556, {"level": 11, "from": 1, "to": 2}),
            anchor(635556, 16, fade_in, fade_in, 7),
            ("area_changed", 635603, {"level": 11, "from": 2, "to": 1}),
            anchor(635603, 16, fade_in, fade_in, 46)]
    assert resets(defs, rows, target={"course_id": 11, "star_id": 0}) == []
