"""Every movement row must expand into a definition the ENGINE accepts, and
must obey the two grammar invariants the matcher forces on us (spec
2026-07-24-default-routes-corpus §4.1/§4.2). These are cheap structural gates;
the behavioural proof is tests/test_defaults_corpus.py's simulation layer."""
import importlib.util
import sys
from pathlib import Path

from sm64_events.tracking.segments import validate_definition

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location(
    "build_defaults_seed", TOOLS / "build_defaults_seed.py")
build_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_seed)

MOVEMENTS = build_seed.corpus_movements.MOVEMENTS


def test_movement_count():
    assert len(MOVEMENTS) == 56          # 55 + seg:bowser2->bits (Task 20)


def test_seed_keys_are_unique_and_prefixed():
    keys = [m["seed_key"] for m in MOVEMENTS]
    assert len(set(keys)) == len(keys)
    assert all(k.startswith("seg:") for k in keys)


def test_every_movement_expands_to_a_valid_definition():
    for row in MOVEMENTS:
        validate_definition(build_seed._movement_row(row))


def test_every_movement_is_route_scoped_and_categorised():
    for row in MOVEMENTS:
        expanded = build_seed._movement_row(row)
        assert expanded["guards"] == [{"type": "in_active_route"}], row["seed_key"]
        assert expanded["category"] == "Castle Movement", row["seed_key"]
        assert expanded["enabled"] is True, row["seed_key"]


def test_no_movement_ends_on_a_star_grab():
    """Spec §5.2: within one event `closed` is ordered stars-then-segments, so
    a movement ending on a star grab consumes the star attempt's turn and the
    star's own step can never complete — a permanent run stall."""
    for row in MOVEMENTS:
        assert row["end"]["type"] != "star_grabbed", row["seed_key"]


def test_a_waypoint_movement_never_repeats_its_start_clause_first():
    """Spec §4.2 authoring caveat: a re-entry's SECOND waypoint is its own
    start clause, which is only safe because _feed_waypoint advances progress
    before the major-action check. If the FIRST waypoint matched the start
    clause, the sequence could never begin."""
    for row in MOVEMENTS:
        if row["via"]:
            assert row["via"][0] != row["start"], row["seed_key"]


def test_star_started_movements_are_castle_secret_stars_or_a_named_exception():
    """A movement normally starts on a castle-secret grab (Toad/MIPS), because
    a COURSE star grab means you are still inside a stage.

    The one sanctioned exception is a star that physically opens the way out:
    Board Bowser's Sub is what makes DDD's sub enterable, and starting there is
    what keeps `level_exit from=23` from colliding with `level_enter to=19` —
    they are the same event (see test_defaults_corpus's same-event test).
    Listing the exception rather than dropping the rule keeps a careless new
    course-star start from slipping in."""
    allowed = {"seg:ddd->bitfs": (9, 0)}     # DDD — Board Bowser's Sub
    for row in MOVEMENTS:
        if row["start"]["type"] != "star_grabbed":
            continue
        course, star_id = row["start"]["course"], row["start"]["star"]
        if course == 0:
            continue
        assert allowed.get(row["seed_key"]) == (course, star_id), row["seed_key"]


def test_a_waypoint_movement_never_spans_a_star_grab_by_construction():
    """A waypoint def is silently cancelled by any star grab, so a movement
    that needs waypoints must not be expected to run through one. The corpus
    expresses that by ending such a movement at a region boundary — assert the
    two re-entry movements that precede a MIPS grab do exactly that."""
    by_key = {m["seed_key"]: m for m in MOVEMENTS}
    assert by_key["seg:sl->basement"]["end"] == {
        "type": "area_enter", "level": 6, "area": 3}
    assert by_key["seg:bbh->basement"]["end"] == {
        "type": "area_enter", "level": 6, "area": 3}
