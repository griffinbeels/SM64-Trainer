"""The verdict functions gates call, driven without an emulator."""
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.buffer import BufferMemory
from sm64_events.memory.layout import US
from sm64_events.sync import checks as C


def _snap(timer, action):
    return GameSnapshot(wall_time_utc=datetime(2026, 8, 15, tzinfo=timezone.utc),
                        global_timer=timer, mario_action=action,
                        mario_action_timer=0, num_stars=0,
                        last_completed_course=0, last_completed_star=0)


def test_ticks_verify_a_frame_counter_and_fail_a_stall():
    good = C.check_ticks([(0.0, 100), (0.5, 115), (1.0, 130)])
    assert good.status == "verified" and good.measured["per_second"] == 30.0
    bad = C.check_ticks([(0.0, 100), (1.0, 110)])
    assert bad.status == "failed" and bad.measured["per_second"] == 10.0
    assert C.check_ticks([(0.0, 5)]).status == "failed"


def test_equals_names_both_numbers_on_failure():
    assert C.check_equals(24, 24, "curr_level").status == "verified"
    verdict = C.check_equals(9, 24, "curr_level")
    assert verdict.status == "failed" and verdict.measured == {"read": 9, "expected": 24}


def test_parse_frames_accepts_the_three_spellings():
    assert C.parse_frames("0'20\"20") == 606
    assert C.parse_frames("20.2") == 606
    assert C.parse_frames("f606") == 606
    assert C.parse_frames("  ") is None


def test_the_u16_scan_finds_the_one_address_that_holds_the_value():
    mem = BufferMemory()
    mem.write_u16(US.usamune_star_result, 606)
    mem.write_u16(0x80300010, 900)
    image = mem._read_raw(0, len(mem._buf))
    assert C.scan_u16(image, 606) == [US.usamune_star_result]
    assert C.scan_u16(image, 607) == [US.usamune_star_result]      # ±2 tolerance
    assert C.scan_u16(image, 700) == []


def test_the_u32_scan_and_the_intersection():
    mem = BufferMemory()
    mem.write_u32(US.usamune_timer, 1234)
    mem.write_u32(0x80300020, 1234)
    image = mem._read_raw(0, len(mem._buf))
    found = C.scan_u32(image, 1234)
    assert set(found) == {US.usamune_timer, 0x80300020}
    assert C.survivors([found, [US.usamune_timer, 0x80300099]]) == [US.usamune_timer]


def test_first_edge_and_frames_between():
    snaps = [_snap(10, 0x1), _snap(11, 0x1), _snap(12, 0x1302), _snap(13, 0x1302)]
    at = C.first_edge(snaps, frozenset({0x1302}))
    assert at == 2
    assert C.frames_between(snaps, 0, at) == 2
    assert C.first_edge(snaps, frozenset({0x9999})) is None


def test_await_event_matches_type_and_payload():
    events = [{"type": "level_changed", "payload": {"to": 24}},
              {"type": "star_collected", "payload": {"course_id": 2}},
              {"type": "star_collected", "payload": {"course_id": 3}}]
    assert C.await_event(events, "star_collected", lambda p: p["course_id"] == 3)["payload"]["course_id"] == 3
    assert C.await_event(events, "death") is None


def test_pool_contains_wants_a_slot_boundary():
    assert C.pool_contains(US.object_pool, US.object_pool + 5 * 0x260)
    assert not C.pool_contains(US.object_pool, US.object_pool + 5 * 0x260 + 4)
    assert not C.pool_contains(US.object_pool, US.object_pool - 4)
