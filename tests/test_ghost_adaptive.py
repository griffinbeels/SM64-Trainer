"""Adaptive rivals preserve current saves, canonical ranks and chosen slots."""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.ghost import adaptive
from sm64_events.ghost.adaptive import (
    PracticeIdentity, add_slot, checked_options, rank_frames, rank_recordings, select_rivals,
)
from sm64_events.ghost.pb import AttemptGhost, current_pb_ghost, eligible_attempt, saved_pb_basis
from sm64_events.ranks import classify, curves, scoring
from sm64_events.storage.db import Database
from sm64_events.tracking.projection import Attempt

IDENTITY = PracticeIdentity("star", "2-1", "igt", "us", "Standard")
SIGNATURE = "ab" * 32


class Standards:
    """Only the store boundary is a fixture; canonical strategy scoring is real."""
    calibration_revision = "compiled-current"
    grading_version = "us"

    def __init__(self, clock="igt"):
        self.clock, self.reads, self.requests = clock, 0, []
        self.curves = {version: curves.compile_curve([[time * scale, score] for time, score in
                          [(1000, 95), (1800, 70), (2000, 61.3), (3000, 25), (6000, 5)]])
                       for version, scale in (("us", 1), ("jp", .5))}

    @contextmanager
    def read_context(self):
        self.reads += 1
        yield

    def ladder_cs(self, key, strategy, version=None):
        self.requests.append((key, strategy, version))
        ladder = self.curves[version or self.grading_version]["ladder_cs"]
        if strategy == "Missing":
            return {}
        return {tier: round(value * (.75 if strategy == "Other" else 1)) for tier, value in ladder.items()}

    def clock_for(self, _key):
        return self.clock

    def overall_curve(self, key, version=None):
        self.requests.append((key, None, version))
        return self.curves[version or self.grading_version]


def recording(identity="one", frames=600, **values):
    return {"id": identity, "player_id": f"player:{identity}", "player_name": identity,
            "kind": "star", "entity_key": "2-1", "clock": "igt", "game_version": "us",
            "strategy": "Standard", "frames": frames, "certified": True, "valid": True,
            "full_start": True, "ghost": SimpleNamespace(origin=f"ghost:{identity}"), **values}


def ranked(step, identity=None, frames=None, **values):
    identity = identity or f"rank-{step}"
    return {**recording(identity, 2000 - step * 20 if frames is None else frames),
            "tier": "Platinum", "division": "V", "step": step, "current_strategy": "Standard", **values}


def basis(step=9, frames=1900, **values):
    return {"step": step, "frames": frames, "current_strategy": "Standard", **values}


def attempt(number=1, frames=600, **values):
    defaults = dict(id=number, session_id=1, course_id=2, star_id=1, strat_tag="Standard",
                    anchor_type="practice_reset", anchor_frame=0, outcome="success", outcome_detail=None,
                    igt_frames=frames, rta_frames=frames, started_utc="2026-10-07T00:00:00Z",
                    ended_utc="2026-10-07T00:00:20Z", cleared=False, cleared_reason=None,
                    game_version="us", valid=True, timed_at="xcam", native_clock_frame=0,
                    native_clock_kind="star", native_clock_utc="2026-10-07T00:00:00Z", closed_frame=frames)
    return Attempt(**(defaults | values))


def save(database, run, **values):
    return database.insert_pb(**(dict(course_id=run.course_id, star_id=run.star_id, segment_id=run.segment_id,
                                     strat_tag=run.strat_tag, timer_mode="igt" if run.segment_id is None else "rta",
                                     frames=run.igt_frames if run.segment_id is None else run.rta_frames,
                                     attempt_id=run.id, saved_utc="2026-10-07T00:00:20Z",
                                     game_version=run.game_version) | values))


def test_current_strategy_and_exact_displayed_frames_grade_every_row():
    store = Standards()
    row = recording(step=44, tier="Mario", division="I")
    result, = rank_recordings([row], store, IDENTITY)
    # Claimed ranks never participate; the strategy's canonical ladder does.
    assert (result["tier"], result["division"], result["step"]) == ("Platinum", "V", 20)
    assert result["calibration_revision"] == "compiled-current" and result["current_strategy"] == "Standard"
    assert row["step"] == 44 and store.reads == 1 and store.requests == [("star:2:1", "Standard", "us")]
    nearby = rank_frames(store, IDENTITY, 630)
    assert (nearby["tier"], nearby["division"], nearby["step"]) == ("Gold", "II", 18)
    overall = curves.progress_for_time(store.curves["us"], 2100)
    assert (overall["tier"], overall["division"]) == ("Gold", "I")
    assert rank_frames(store, replace(IDENTITY, strategy="Other"), 630) != nearby
    assert rank_frames(store, replace(IDENTITY, strategy="Missing"), 630) is None
    assert rank_frames(store, replace(IDENTITY, strategy=None), 630) is None


def test_scope_preserves_default_identity_and_grades_the_same_recording_on_its_canonical_model():
    assert IDENTITY.scope == "strategy"
    overall = replace(IDENTITY, scope="overall")
    store = Standards()
    strategy, = rank_recordings([recording(frames=630)], store, IDENTITY)
    entity, = rank_recordings([recording(frames=630)], store, overall)
    assert (strategy["tier"], strategy["division"], strategy["step"]) == ("Gold", "II", 18)
    assert (entity["tier"], entity["division"], entity["step"]) == ("Gold", "I", 19)
    assert strategy["scope"] == "strategy" and entity["scope"] == "overall"
    assert store.requests == [("star:2:1", "Standard", "us"), ("star:2:1", None, "us")]
    store.grading_version = "jp"
    assert rank_frames(store, overall, 630, version=None) != rank_frames(store, overall, 630)
    assert rank_frames(store, replace(overall, strategy=None), 630) is not None


def test_overall_broadens_strategy_candidates_but_preserves_recorded_labels_and_pins():
    overall = replace(IDENTITY, scope="overall")
    records = [recording("standard", 630), recording("alternative", 630, strategy="Other")]
    strategy = rank_recordings(records, Standards(), IDENTITY)
    entity = rank_recordings(records, Standards(), overall)
    assert [row["id"] for row in strategy] == ["standard"]
    assert {row["strategy"] for row in entity} == {"Standard", "Other"}
    pb = basis(18, 660)
    assert {row["id"] for row in select_rivals(entity, pb, {}, {}, scope="overall")} == {"standard", "alternative"}
    assert select_rivals(entity, pb, {}, {}) == ()  # stale Overall grades cannot enter Strategy
    private = ranked(30, "private", strategy="Other", manual_only=True, certified=False, scope="overall")
    selected, = select_rivals([private], pb, {}, {"0": "private"}, scope="overall")
    assert selected["pinned"] and selected["strategy"] == "Other"
    assert select_rivals([private], pb, {}, {}, scope="overall") == ()
    unavailable, = select_rivals([private], pb, {}, {"0": "private"})
    assert unavailable["ghost"] is None


@pytest.mark.parametrize("bad", [
    {"certified": False}, {"valid": None}, {"full_start": False}, {"game_version": None},
    {"game_version": "jp"}, {"clock": "rta"}, {"kind": "segment"}, {"entity_key": "2-2"},
    {"strategy": None}, {"frames": True}, {"frames": 0}, {"ghost": None},
])
def test_overall_scope_keeps_provenance_rom_clock_and_target_refusals(bad):
    assert rank_recordings([recording(**({"strategy": "Other"} | bad))], Standards(), replace(IDENTITY, scope="overall")) == ()


@pytest.mark.parametrize("scope", [None, "all", "Strategy", True, []])
def test_invalid_matching_scope_is_refused(scope):
    with pytest.raises(ValueError, match="Strategy or Overall"):
        replace(IDENTITY, scope=scope)
    with pytest.raises(ValueError, match="Strategy or Overall"):
        select_rivals([], None, {}, {}, scope=scope)


@pytest.mark.parametrize("bad", [
    {"certified": None}, {"certified": False}, {"certified": 1},
    {"valid": None}, {"valid": False}, {"valid": 1},
    {"full_start": None}, {"full_start": False}, {"full_start": 1},
    {"kind": "segment"}, {"entity_key": "2-2"}, {"clock": "rta"},
    {"game_version": None}, {"game_version": "jp"}, {"practice": False, "certified": False},
    {"frames": True}, {"frames": 0}, {"frames": -1}, {"frames": 600.0},
    {"id": ""}, {"player_id": ""}, {"player_name": " "}, {"strategy": None}, {"strategy": "Other"}, {"ghost": None},
])
def test_uncertified_incompatible_and_malformed_metadata_cannot_be_ranked(bad):
    assert rank_recordings([recording(**bad)], Standards(), IDENTITY) == ()


def test_explicit_rom_and_canonical_clock_never_fall_back():
    store = Standards()
    jp = replace(IDENTITY, game_version="jp")
    result, = rank_recordings([recording(game_version="jp")], store, jp)
    assert result["step"] != rank_recordings([recording()], store, IDENTITY)[0]["step"]
    assert store.requests[0] == ("star:2:1", "Standard", "jp")
    assert rank_recordings([recording()], Standards("rta"), IDENTITY) == ()
    assert rank_frames(Standards("rta"), IDENTITY, 600) is None
    assert rank_recordings([recording()], store, replace(IDENTITY, game_version=None)) == ()


@pytest.mark.parametrize("scope", ["strategy", "overall"])
def test_segment_binding_requires_the_semantic_definition_and_clock(scope):
    identity = PracticeIdentity("segment", "12", "rta", "us", "Standard", SIGNATURE, scope)
    row = recording(kind="segment", entity_key="12", clock="rta", signature=SIGNATURE)
    assert rank_recordings([row], Standards("rta"), identity)[0]["step"] == 20
    assert rank_recordings([{**row, "signature": "cd" * 32}], Standards("rta"), identity) == ()
    assert rank_recordings([row], Standards("rta"), replace(identity, signature=None)) == ()


def test_metadata_snapshot_is_bounded_and_duplicate_identity_only_appears_once(monkeypatch):
    assert len(rank_recordings([recording(), recording()], Standards(), IDENTITY)) == 1
    monkeypatch.setattr(adaptive, "MAX_CANDIDATES", 2)
    with pytest.raises(ValueError, match="limit"):
        rank_recordings((recording(str(number)) for number in range(3)), Standards(), IDENTITY)


@pytest.mark.parametrize("bad", [
    {"mode": "other"}, {"count": 0}, {"count": 5}, {"count": True}, {"count": 1.0},
    {"include_pb": 1}, {"strategy_only": None}, {"min_ahead": -1}, {"max_ahead": 11},
    {"min_ahead": 6, "max_ahead": 5}, {"min_step": -1}, {"max_step": 45},
    {"min_step": 30, "max_step": 29}, {"unknown": 1},
])
def test_bad_settings_are_rejected_atomically(bad):
    before = dict(bad)
    with pytest.raises(ValueError):
        checked_options(bad)
    assert before == bad


@pytest.mark.parametrize("count", [1, 2, 4])
def test_automatic_rivals_spread_across_the_next_five_subdivisions(count):
    rows = [ranked(step) for step in range(45)]
    result = select_rivals(rows, basis(), {"count": count}, {})
    assert len(result) == count
    assert result[0]["step"] == 10
    if count > 1:
        assert result[-1]["step"] == 14
    assert all(10 <= row["step"] <= 14 and not row["pinned"] for row in result)
    assert len({row["id"] for row in result}) == count


def test_minimum_is_next_subdivision_even_when_stored_offsets_are_zero():
    rows = [ranked(9, "same", 1000), ranked(10, "next", 1700)]
    result = select_rivals(rows, basis(), {"min_ahead": 0, "max_ahead": 0}, {})
    assert [row["id"] for row in result] == ["next"]


def test_empty_range_uses_fastest_available_including_beaten_runs():
    rows = [ranked(19, "faster", 320), ranked(19, "equal", 321), ranked(19, "slower", 322),
            ranked(18, "lower", 300),
            ranked(19, "private", 318, manual_only=True),
            ranked(19, "other-strategy", 317, strategy="Other")]
    pb = basis(19, 321, game_version="us")
    for options in ({}, {"mode": "fixed", "min_step": 20, "max_step": 24}):
        selected = select_rivals(rows, pb, options, {})
        assert [row["id"] for row in selected] == ["lower", "faster", "equal", "slower"]
        assert all(not row["pinned"] for row in selected)
    assert [row["id"] for row in select_rivals(rows, None, {}, {})] == ["lower", "faster", "equal", "slower"]
    assert select_rivals(rows, pb, {}, {}, allow_fastest=False) == ()


def test_range_opponents_take_priority_over_same_subdivision_fallback():
    same, higher = ranked(19, "same", 320), ranked(20, "higher", 310)
    pb = basis(19, 321, game_version="us")
    assert [row["id"] for row in select_rivals([same, higher], pb, {}, {})] == ["higher"]
    previous = [{**higher, "slot": 1, "pinned": False}]
    rows = select_rivals([same, higher], pb, {}, {}, previous=previous)
    assert [(row["id"], row["slot"]) for row in rows] == [("higher", 1)]


@pytest.mark.parametrize("scope", ["strategy", "overall"])
def test_fastest_fallback_keeps_pb_separate_and_compatible_strategy_labels(scope):
    identity = replace(IDENTITY, scope=scope)
    ghost = AttemptGhost(7, "star", "2-1", "Standard", 300, pb=True)
    rows = rank_recordings([
        recording("own", 300, ghost=ghost), recording("available", 600),
        recording("alternative", 610, strategy="Other"),
        recording("other-rom", 590, game_version="jp"),
        recording("old-untrusted", 580, game_version=None, certified=False),
    ], Standards(), identity)
    pb = {**rank_frames(Standards(), identity, 300), "frames": 300,
          "ghost": ghost, "current_strategy": "Standard", "game_version": "us"}
    chosen = select_rivals(rows, pb, {}, {}, scope=scope)
    expected = ["available", "alternative"] if scope == "overall" else ["available"]
    assert [row["id"] for row in chosen] == expected
    assert pb["ghost"] is ghost and all(row["frames"] > pb["frames"] for row in chosen)
    assert all(row["id"] not in ("own", "other-rom", "old-untrusted") for row in chosen)
    assert [row["strategy"] for row in chosen] == (["Standard", "Other"] if scope == "overall" else ["Standard"])


def test_pinned_beta_historical_source_preserves_missing_certificate_and_original_rom():
    row = recording("bundled", 600, source_status="bundled-author-recording", structurally_valid=True,
                    certified=False, full_start=None, recorded_game_version=None)
    graded, = rank_recordings([row], Standards(), IDENTITY)
    assert graded["certified"] is False and graded["recorded_game_version"] is None
    assert graded["full_start"] is None
    assert select_rivals([graded], basis(44, 100), {}, {})[0]["id"] == "bundled"
    # Incomplete remote coverage cannot block a complete pinned local bundle.
    remote = rank_recordings([recording("remote", 500)], Standards(), IDENTITY)[0]
    assert [item["id"] for item in select_rivals([graded, remote], basis(44, 100), {}, {},
                                               allow_fastest=False)] == ["bundled"]
    for bad in ({"structurally_valid": False}, {"source_status": "import"}, {"recorded_game_version": "unknown"}):
        assert rank_recordings([{**row, **bad}], Standards(), IDENTITY) == ()


def test_same_subdivision_fallback_preserves_pins_and_winners():
    rows = [ranked(19, "slow", 320, player_id="one"), ranked(19, "fast", 319, player_id="one"),
            ranked(19, "another", 320), ranked(10, "chosen", 700)]
    pins = {"0": "chosen", "3": "unavailable"}
    selected = select_rivals(rows, basis(19, 321), {}, pins)
    assert [(row["id"], row["pinned"]) for row in selected] == [
        ("chosen", True), ("fast", False), ("another", False), ("unavailable", True)]
    assert pins == {"0": "chosen", "3": "unavailable"}
    assert len({row["hue_degrees"] for row in selected}) == 4


def test_ceiling_caps_both_endpoints_and_only_faster_final_recordings_qualify():
    rows = [ranked(43, "lower", 10), ranked(44, "equal", 100), ranked(44, "slower", 101), ranked(44, "faster", 99)]
    result = select_rivals(rows, basis(44, 100), {}, {})
    assert [row["id"] for row in result] == ["faster"]
    # A genuinely lower same-ROM subdivision has a slower time; all of these
    # top-subdivision records then represent progress. The cap alone compares
    # times within the same final subdivision.
    assert {row["id"] for row in select_rivals(rows, basis(43, 102), {}, {})} == {"faster", "equal", "slower"}


def test_foreign_region_anchor_compares_strategy_steps_and_never_claims_faster_ceiling_time():
    # The current-ROM recording takes longer in frames but earns a higher rank
    # on its own strategy standards than the PB set on the other ROM.
    row = ranked(21, "higher-rank", 200, game_version="us")
    assert select_rivals([row], basis(20, 100, game_version="jp"), {}, {})[0]["id"] == "higher-rank"
    capped = ranked(44, "different-clock", 99, game_version="us")
    # A fallback can still race a current-ROM ghost against an incomparable
    # old PB; it does not claim that its raw time beats the other ROM's PB.
    assert select_rivals([capped], basis(44, 100, game_version="jp"), {}, {})[0]["id"] == "different-clock"
    assert select_rivals([capped], basis(44, 100, game_version="us"), {}, {})[0]["id"] == "different-clock"


def test_overall_cross_strategy_cap_still_requires_a_faster_comparable_time():
    rows = [ranked(44, "faster", 99, strategy="Other", scope="overall"),
            ranked(44, "slower", 101, strategy="Third", scope="overall")]
    pb = basis(44, 100, game_version="us")
    assert [row["id"] for row in select_rivals(rows, pb, {}, {}, scope="overall")] == ["faster"]
    assert [row["id"] for row in select_rivals(rows, {**pb, "game_version": "jp"}, {}, {}, scope="overall")] == ["faster", "slower"]
    assert select_rivals(rows, pb, {}, {}) == ()


def test_no_pb_begins_at_the_floor_and_fixed_range_can_include_slower_runs():
    rows = [ranked(step) for step in range(12)]
    result = select_rivals(rows, None, {}, {})
    assert result and all(1 <= row["step"] <= 5 for row in result)
    narrowed = select_rivals(rows, None, {"min_ahead": 2, "max_ahead": 2}, {})
    assert [row["step"] for row in narrowed] == [2]
    result = select_rivals([ranked(9, frames=2000)], basis(9, 1900),
                           {"mode": "fixed", "min_step": 9, "max_step": 9}, {})
    assert result[0]["frames"] == 2000


def test_strategy_is_always_required_for_automatic_and_chosen_rivals():
    rows = [ranked(10, "standard"), ranked(11, "other", strategy="Other")]
    pb = basis(strategy="Other", current_strategy="Standard")
    assert [row["id"] for row in select_rivals(rows, pb, {"strategy_only": False}, {})] == ["standard"]
    assert [row["id"] for row in select_rivals(rows, None, {"strategy_only": True}, {})] == ["standard"]
    assert len(select_rivals([ranked(1)], None, {"strategy_only": True}, {})) == 1
    chosen, = select_rivals([rows[1]], pb, {}, {"0": "other"})
    assert chosen["ghost"] is None and chosen["available"] is False
    assert select_rivals([rows[1]], basis(current_strategy="Other"), {}, {})[0]["id"] == "other"
    assert "strategy_only" not in checked_options({"strategy_only": False, "count": 2})
    assert checked_options({"strategy_only": True, "count": 2})["count"] == 2


def test_population_winner_is_per_stable_player_strategy_and_subdivision():
    rows = [ranked(10, "slow", 1700, player_id="A", player_name="Same name"),
            ranked(10, "fast", 1600, player_id="A", player_name="Same name"),
            ranked(10, "other-player", 1650, player_id="B", player_name="Same name"),
            ranked(10, "other-strategy", 1690, player_id="A", strategy="Other")]
    result = select_rivals(rows, basis(), {}, {})
    assert {row["id"] for row in result} == {"fast", "other-player"}


def test_sparse_ranges_never_substitute_an_outside_rank():
    result = select_rivals([ranked(8), ranked(10), ranked(14), ranked(15)], basis(), {}, {})
    assert [row["step"] for row in result] == [10, 14]


def test_pins_override_the_automatic_range_and_hidden_choices_survive_count_changes():
    pins = {"2": "chosen"}
    rows = [ranked(30, "chosen"), *[ranked(step) for step in range(10, 15)]]
    short = select_rivals(rows, basis(), {"count": 1}, pins)
    restored = select_rivals(rows, basis(), {}, pins)
    assert len(short) == 1 and short[0]["id"] != "chosen"
    assert restored[2]["id"] == "chosen" and restored[2]["pinned"]
    assert pins == {"2": "chosen"}


def test_changing_one_pin_preserves_other_slots_and_their_colors():
    rows = [ranked(step) for step in range(10, 15)] + [ranked(30, "chosen")]
    original = select_rivals(rows, basis(), {}, {})
    chosen = select_rivals(rows, basis(), {}, {"0": "chosen"}, previous=original)
    assert [row["id"] for row in chosen[1:]] == [row["id"] for row in original[1:]]
    assert [row["hue_degrees"] for row in chosen] == [row["hue_degrees"] for row in original]
    assert len({250, *[row["hue_degrees"] for row in chosen]}) == 5
    reset = select_rivals(rows, basis(), {}, {}, previous=chosen)
    assert reset[0]["id"] != "chosen" and not reset[0]["pinned"]


def test_missing_pin_is_an_occupied_unavailable_slot_instead_of_an_automatic_replacement():
    result = select_rivals([ranked(step) for step in range(10, 15)], basis(), {}, {"0": "missing"})
    assert result[0]["id"] == "missing" and result[0]["pinned"] and result[0]["ghost"] is None
    assert result[0]["available"] is False and result[0]["reason_text"]
    assert add_slot(result, 4) != 0


def test_uncertified_private_recording_can_be_pinned_but_never_automatic():
    row = ranked(10, "private", certified=False, valid=None, full_start=False, manual_only=True)
    assert select_rivals([row], basis(), {}, {}) == ()
    result, = select_rivals([row], basis(), {}, {"0": "private"})
    assert result["id"] == "private" and result["pinned"]


def test_pb_playback_identity_cannot_also_fill_a_rival_slot():
    ghost = AttemptGhost(2, "star", "2-1", "Standard", 1200, pb=True)
    row = ranked(12, "own-pb", 1200, ghost=ghost)
    pb = basis(9, 1900, ghost=ghost)
    assert select_rivals([row], pb, {}, {}) == ()
    assert select_rivals([row], pb, {"include_pb": False}, {})[0]["id"] == "own-pb"


@pytest.mark.parametrize("pins", [{}, {"0": "own-pb"}])
def test_certified_pb_lineage_is_played_once_in_fixed_or_manually_chosen_slots(tmp_path, pins):
    database = Database(tmp_path / "lineage.db")
    run = attempt(7)
    try:
        database.replace_attempts([run])
        save(database, run)
        pb = saved_pb_basis(database, Standards(), IDENTITY)
        row = ranked(pb["step"], "own-pb", pb["frames"], source=dict(pb["source"]),
                     ghost=SimpleNamespace(import_id="own-pb"))
        options = {"mode": "fixed"}
        selected = select_rivals([row], pb, options, pins)
        assert not [item for item in selected if item.get("ghost") is not None]
        selected_without_pb = select_rivals([row], pb, {**options, "include_pb": False}, pins)
        assert selected_without_pb[0]["ghost"] is row["ghost"]
    finally:
        database.close()


@pytest.mark.parametrize("different", [
    {"session_id": 2}, {"started_utc": "2026-10-08T00:00:00Z"}, {"attempt_id": 8},
])
def test_attempt_numbers_reused_by_other_profile_runs_are_distinct_rivals(different):
    source = {"session_id": 1, "attempt_id": 7, "started_utc": "2026-10-07T00:00:00Z"}
    row = ranked(20, "other-run", 600, source={**source, **different},
                 ghost=SimpleNamespace(import_id="other-run"))
    selected = select_rivals([row], basis(20, 600, source=source), {"mode": "fixed"}, {})
    assert selected[0]["id"] == "other-run"


def test_private_import_claimed_lineage_does_not_exclude_a_manual_rival():
    source = {"session_id": 1, "attempt_id": 7, "started_utc": "2026-10-07T00:00:00Z"}
    row = ranked(20, "private", 600, source=source, manual_only=True, certified=False,
                 ghost=SimpleNamespace(import_id="private"))
    selected = select_rivals([row], basis(20, 600, source=source), {"mode": "fixed"}, {"0": "private"})
    assert selected[0]["id"] == "private" and selected[0]["ghost"] is row["ghost"]


def test_add_fills_vacancy_then_replaces_slowest_automatic_and_protects_chosen():
    assert add_slot([], 4) == 0
    assert add_slot([{"slot": 0, "frames": 9000, "pinned": True}], 4) == 1
    rows = [{"slot": slot, "frames": frames, "pinned": slot == 0}
            for slot, frames in enumerate((9000, 100, 200, 300))]
    assert add_slot(rows, 4) == 3
    assert add_slot(rows, 4, replacement_slot=0) == 0
    with pytest.raises(ValueError, match="chosen"):
        add_slot([{**row, "pinned": True} for row in rows], 4)
    with pytest.raises(ValueError, match="visible"):
        add_slot(rows, 4, replacement_slot=True)


def test_latest_saved_strategy_slots_are_resolved_before_comparing_or_undoing(tmp_path):
    database = Database(tmp_path / "history.db")
    runs = [attempt(1, 600), attempt(2, 900), attempt(3, 750, strat_tag="Other")]
    try:
        database.replace_attempts(runs)
        save(database, runs[0])
        slower = save(database, runs[1])
        other = save(database, runs[2])
        assert saved_pb_basis(database, Standards(), IDENTITY)["attempt_id"] == 2
        assert saved_pb_basis(database, Standards(), replace(IDENTITY, strategy="Other"))["attempt_id"] == 3
        database.delete_pb(other)
        assert saved_pb_basis(database, Standards(), IDENTITY)["attempt_id"] == 2
        database.delete_pb(slower)
        result = saved_pb_basis(database, Standards(), IDENTITY)
        assert (result["attempt_id"], result["frames"], result["step"]) == (1, 600, 20)
        assert result["ghost"].pb and result["ghost"].origin == "attempt:1"
        assert result["source"] == {"session_id": 1, "attempt_id": 1, "started_utc": runs[0].started_utc}
    finally:
        database.close()


def test_overall_scope_changes_pb_grade_without_changing_current_strategy_saved_owner(tmp_path):
    database = Database(tmp_path / "scope-pb.db")
    runs = [attempt(1, 630, game_version=None), attempt(2, 540, strat_tag="Other")]
    store = Standards()
    overall_identity = replace(IDENTITY, scope="overall")
    try:
        database.replace_attempts(runs)
        for run in runs:
            save(database, run)
        strategy = saved_pb_basis(database, store, IDENTITY)
        overall = saved_pb_basis(database, store, overall_identity)
        assert strategy["attempt_id"] == overall["attempt_id"] == 1
        assert strategy["step"] == 18 and overall["step"] == 19
        assert overall["scope"] == "overall" and overall["strategy"] == "Standard"
        assert overall["ghost"].origin == strategy["ghost"].origin == "attempt:1"
        store.grading_version = "jp"
        legacy = saved_pb_basis(database, store, overall_identity)
        assert {key: legacy[key] for key in ("tier", "division", "step")} == rank_frames(store, overall_identity, 630, version=None)
        database.insert_pb(2, 1, "Standard", "igt", 300, None, "2026-10-08T00:00:00Z",
                           imported_from="Other ROM", game_version="jp")
        imported = saved_pb_basis(database, store, overall_identity)
        assert imported["ghost"] is None and imported["source"] is None
        assert {key: imported[key] for key in ("tier", "division", "step")} == rank_frames(store, overall_identity, 300, version="jp")
    finally:
        database.close()


def test_visible_local_save_remains_current_without_community_admission(tmp_path):
    database = Database(tmp_path / "history.db")
    clean, invalid = attempt(1), attempt(2, 500, valid=False, invalid_reason="no_reset")
    try:
        database.replace_attempts([clean, invalid])
        save(database, clean)
        latest = save(database, invalid)
        assert saved_pb_basis(database, Standards(), IDENTITY)["attempt_id"] == 2
        assert not eligible_attempt(invalid, IDENTITY)
        assert current_pb_ghost(database, "star", "2-1", "Standard").attempt_id == 2
        database.delete_pb(latest)
        assert saved_pb_basis(database, Standards(), IDENTITY)["attempt_id"] == 1
    finally:
        database.close()


def test_pb_anchor_uses_its_original_region_and_exact_strategy_clock(tmp_path):
    database = Database(tmp_path / "history.db")
    runs = [attempt(1, 600), attempt(2, 300, game_version="jp"),
            attempt(3, 400, strat_tag="Unknown", game_version=None),
            attempt(4, 200, strat_tag="Legacy", valid=None)]
    try:
        database.replace_attempts(runs)
        for run in runs:
            save(database, run)
        save(database, runs[0], timer_mode="rta", frames=100)
        database.insert_pb(2, 1, "Imported", "igt", 100, None, "2026-10-07T00:00:20Z", game_version="us")
        result = saved_pb_basis(database, Standards(), IDENTITY)
        assert result["attempt_id"] == 2 and result["ghost"] is None
        assert {key: result[key] for key in ("step", "tier", "division")} == rank_frames(Standards(), IDENTITY, 300, version="jp")
        assert saved_pb_basis(database, Standards(), replace(IDENTITY, game_version="jp"))["attempt_id"] == 2
        assert saved_pb_basis(database, Standards("rta"), IDENTITY) is None
    finally:
        database.close()


def test_reported_wild_blue_legacy_pb_remains_playable_and_matches_practice_rank(tmp_path):
    from sm64_events.ranks.standards import RankStandards
    from sm64_events.tracking.views import _strat_rank, grading_basis

    # Round9's actual saved owner: PB321frames, ROM null on both rows. Its
    # judged native span is complete; unknown historical ROM is not a new
    # community certificate and must not erase the user's own saved ghost.
    identity = replace(IDENTITY, entity_key="2-2")
    run = attempt(13194, 321, session_id=50, star_id=2, game_version=None,
                  rta_frames=319, anchor_frame=30978, native_clock_frame=30978,
                  closed_frame=31297, started_utc="2026-10-06T21:31:30.931468Z",
                  native_clock_utc="2026-10-06T21:31:30.931468Z",
                  ended_utc="2026-10-06T21:31:41.611583Z")
    database = Database(tmp_path / "reported.db")
    bundled = Path(adaptive.__file__).parents[1] / "data"
    ranks = RankStandards(tmp_path / "standards.json", bundled / "rank_standards.seed.json",
                          bundled / "sheet_ladders.seed.json")
    ranks.load()
    try:
        database.replace_attempts([run])
        save(database, run)
        pb = database.current_pb(2, 2, "igt", strat_tag="Standard")
        practice = _strat_rank(ranks, "star:2:2", "Standard", grading_basis("pb", pb, [], "Standard", "igt"))
        result = saved_pb_basis(database, ranks, identity)
        assert result["frames"] == 321 and classify.display_cs(result["frames"]) == 1070
        assert (result["tier"], result["division"]) == (practice["rank"], practice["division"])
        assert result["step"] == scoring.progression_key(practice["rank"], practice["division"])
        assert result["ghost"].attempt_id == 13194 and result["ghost"].pb
        assert result["source"] == {"session_id": 50, "attempt_id": 13194, "started_utc": run.started_utc}
        assert not eligible_attempt(run, identity)
        assert rank_recordings([recording(game_version=None, entity_key="2-2", frames=321)], ranks, identity) == ()
        # This fixture also catches stale strategy cutoffs: each resolution
        # reads the current store, just like the live practice banner does.
        ranks.set_threshold("star:2:2", "Standard", "Mario", 20)
        after = saved_pb_basis(database, ranks, identity)
        assert after["tier"] == "Mario" and after["step"] != result["step"]
    finally:
        database.close()


def test_reported_gold_i_range_is_the_next_tiers_five_subdivisions():
    saved = basis(scoring.progression_key("Gold", "I"), 321)
    rows = [ranked(step, frames=320 - index) for index, step in enumerate(range(20, 25))]
    selected = select_rivals(rows, saved, {}, {})
    assert (selected[0]["step"], selected[-1]["step"]) == (
        scoring.progression_key("Platinum", "V"), scoring.progression_key("Platinum", "I"))


def test_imported_baseline_and_legacy_unknown_validity_anchor_without_fabricated_playback(tmp_path):
    database = Database(tmp_path / "baseline.db")
    ranks = Standards()
    run = attempt(game_version=None, valid=None, native_clock_frame=None, closed_frame=None, timed_at=None)
    try:
        database.replace_attempts([run])
        save(database, run)
        legacy = saved_pb_basis(database, ranks, IDENTITY)
        assert legacy["ghost"].attempt_id == run.id and not eligible_attempt(run, IDENTITY)
        imported = database.insert_pb(2, 1, "Standard", "igt", 900, None, "2026-10-08T00:00:00Z",
                                      imported_from="Developer simulation", game_version=None)
        ranks.grading_version = "jp"
        result = saved_pb_basis(database, ranks, IDENTITY)
        assert result["pb_id"] == imported and result["attempt_id"] is None
        assert result["ghost"] is None and result["source"] is None
        assert {key: result[key] for key in ("step", "tier", "division")} == rank_frames(ranks, IDENTITY, 900, version=None)
        database.delete_pb(imported)
        assert saved_pb_basis(database, ranks, IDENTITY)["ghost"].attempt_id == run.id
    finally:
        database.close()


@pytest.mark.parametrize("hidden", [
    {"cleared": True}, {"anchor_type": "state_loaded"}, {"state_loads": 1}, {"invalid_reason": "save_state"},
])
def test_canonical_pb_visibility_excludes_hidden_and_save_state_runs(tmp_path, hidden):
    database = Database(tmp_path / "visibility.db")
    runs = [attempt(1), attempt(2, 500, **hidden)]
    try:
        database.replace_attempts(runs)
        for run in runs:
            save(database, run)
        assert saved_pb_basis(database, Standards(), IDENTITY)["attempt_id"] == 1
    finally:
        database.close()


def test_saved_pb_ranks_keep_practice_identity_but_wrong_backing_owner_cannot_play(tmp_path):
    database = Database(tmp_path / "history.db")
    run = attempt()
    try:
        database.replace_attempts([run])
        save(database, run, star_id=2)
        assert saved_pb_basis(database, Standards(), IDENTITY) is None
        result = saved_pb_basis(database, Standards(), replace(IDENTITY, entity_key="2-2"))
        assert result["frames"] == run.igt_frames and result["ghost"] is None
    finally:
        database.close()


@pytest.mark.parametrize("bad", [
    {"valid": None}, {"valid": False}, {"outcome": "death"}, {"cleared": True},
    {"anchor_type": "state_loaded"}, {"state_loads": 1}, {"game_version": None},
    {"game_version": "jp"}, {"native_clock_frame": None}, {"closed_frame": None},
    {"native_clock_utc": None}, {"native_clock_frame": 100, "closed_frame": 99},
    {"timed_at": None}, {"timed_at": "grab"},
])
def test_population_admission_requires_judged_complete_supported_evidence(bad):
    assert not eligible_attempt(attempt(**bad), IDENTITY)


def test_segment_and_reattributed_star_keep_their_native_clock_contract(tmp_path):
    identity = PracticeIdentity("segment", "3", "rta", "us", "Standard", SIGNATURE)
    run = attempt(course_id=None, star_id=None, segment_id=3, timed_at=None, native_clock_kind="segment")
    assert eligible_attempt(run, identity)
    assert eligible_attempt(attempt(star_id=6, timed_at=None, native_clock_kind="segment"),
                            replace(IDENTITY, entity_key="2-6"))
    assert not eligible_attempt(attempt(timed_at=None, native_clock_kind="segment"), IDENTITY)
    database = Database(tmp_path / "segments.db")
    try:
        database.replace_attempts([run])
        save(database, run)
        result = saved_pb_basis(database, Standards("rta"), identity)
        assert result["ghost"].kind == "segment" and result["ghost"].entity_key == "3"
    finally:
        database.close()


def test_a_player_takes_one_automatic_seat_per_rival_group():
    """His rule (round 39): "per rival group, there should be only unique players"."""
    same = [ranked(step, f"same-{step}", player_id="player:same") for step in range(10, 15)]
    assert [row["id"] for row in select_rivals(same, basis(), {"count": 4}, {})] == ["same-10"]
    mixed = same + [ranked(11, "other", player_id="player:other"), ranked(13, "third", player_id="player:third")]
    assert [row["id"] for row in select_rivals(mixed, basis(), {"count": 4}, {})] == ["same-10", "other", "third"]
    # A carried-over automatic seat counts as that player's seat; a second one is dropped.
    previous = [{**same[2], "slot": 2, "pinned": False}, {**same[3], "slot": 3, "pinned": False}]
    carried = select_rivals(mixed, basis(), {"count": 4}, {}, previous=previous)
    assert [(row["id"], row["slot"]) for row in carried] == [("other", 0), ("third", 1), ("same-12", 2)]
    # A pin is his explicit choice; it still occupies that player's seat.
    pinned = select_rivals(mixed, basis(), {"count": 4}, {"0": "same-14"})
    assert [row["id"] for row in pinned] == ["same-14", "other", "third"]
    # The empty-range fallback keeps one fastest recording per player.
    fastest = select_rivals([ranked(19, "a-slow", 330, player_id="p:a"), ranked(19, "a-fast", 320, player_id="p:a"),
                             ranked(18, "a-other", 318, player_id="p:a"), ranked(18, "b", 325, player_id="p:b")],
                            basis(19, 321), {}, {})
    assert [row["id"] for row in fastest] == ["a-other", "b"]
