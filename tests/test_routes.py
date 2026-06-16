import pytest

from sm64_events.tracking.projection import Attempt
from sm64_events.tracking.routes import (export_route, resolve_import,
                                         route_stats, validate_route)


def att(**o):
    """Attempt factory: defaults to a segment success, override as needed."""
    d = dict(id=1, session_id=1, course_id=None, star_id=None, strat_tag=None,
             anchor_type="practice_reset", anchor_frame=0, outcome="success",
             outcome_detail=None, igt_frames=300, rta_frames=300,
             started_utc="t", ended_utc="t", cleared=False,
             cleared_reason=None, segment_id=None)
    d.update(o)
    return Attempt(**d)


def test_validate_route_accepts_minimal():
    validate_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]})


def test_validate_route_accepts_group_and_label():
    validate_route({"name": "R", "steps": [
        {"need": 2, "label": "Whomp's", "candidates": [
            {"type": "star", "course": 2, "star": 0},
            {"type": "segment", "segment_id": 5}]}]})


def test_validate_route_rejects_empty_name():
    with pytest.raises(ValueError, match="name"):
        validate_route({"name": "  ", "steps": [
            {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]})


def test_validate_route_allows_empty_steps():
    # an empty route is a valid draft — the builder creates it empty then adds
    # steps (live report 2026-06-14: POST {steps:[]} must not 409)
    validate_route({"name": "R", "steps": []})


def test_validate_route_rejects_non_list_steps():
    with pytest.raises(ValueError, match="steps"):
        validate_route({"name": "R", "steps": "nope"})


def test_validate_route_rejects_need_out_of_range():
    with pytest.raises(ValueError, match="need"):
        validate_route({"name": "R", "steps": [
            {"need": 2, "candidates": [{"type": "star", "course": 2, "star": 0}]}]})


def test_validate_route_rejects_bad_candidate_type():
    with pytest.raises(ValueError):
        validate_route({"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "banana"}]}]})


def test_validate_route_rejects_star_without_ints():
    with pytest.raises(ValueError):
        validate_route({"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "star", "course": 2}]}]})


def test_route_stats_single_step_uses_item_success_rate():
    # segment 1: 2 success + 1 reset -> 2/3
    attempts = [att(segment_id=1, outcome="success"),
                att(segment_id=1, outcome="success"),
                att(segment_id=1, outcome="reset")]
    steps = [{"need": 1, "candidates": [{"type": "segment", "segment_id": 1}]}]
    [s] = route_stats(steps, attempts)
    assert abs(s["step_rate"] - 2 / 3) < 1e-9
    assert abs(s["cumulative"] - 2 / 3) < 1e-9


def test_route_stats_no_data_is_zero_and_zeroes_downstream():
    attempts = [att(segment_id=1, outcome="success")]  # only step 1 has data
    steps = [{"need": 1, "candidates": [{"type": "segment", "segment_id": 1}]},
             {"need": 1, "candidates": [{"type": "star", "course": 9, "star": 9}]}]
    s1, s2 = route_stats(steps, attempts)
    assert s1["step_rate"] == 1.0 and s1["cumulative"] == 1.0
    assert s2["step_rate"] == 0.0 and s2["cumulative"] == 0.0


def test_route_stats_group_uses_best_k_product():
    # seg1 = 100% (1/1), seg2 = 50% (1 success, 1 reset), seg3 = 0% (no data)
    # need 2 -> best two rates = 1.0 * 0.5 = 0.5
    attempts = [att(segment_id=1, outcome="success"),
                att(segment_id=2, outcome="success"),
                att(segment_id=2, outcome="reset")]
    steps = [{"need": 2, "candidates": [
        {"type": "segment", "segment_id": 1},
        {"type": "segment", "segment_id": 2},
        {"type": "segment", "segment_id": 3}]}]
    [s] = route_stats(steps, attempts)
    assert abs(s["step_rate"] - 0.5) < 1e-9


def test_route_stats_star_item_ignores_segment_attempts():
    # an attempt on (course 2, star 0) as a STAR must not be confused with a
    # segment attempt; segment_id None is the discriminator
    attempts = [att(segment_id=None, course_id=2, star_id=0, outcome="success"),
                att(segment_id=None, course_id=2, star_id=0, outcome="death")]
    steps = [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]
    [s] = route_stats(steps, attempts)
    assert abs(s["step_rate"] - 0.5) < 1e-9


def test_export_route_embeds_segment_defs_and_keeps_stars():
    segs = {1: {"name": "LBLJ", "start_triggers": [{"type": "spawned"}],
                "end_triggers": [{"type": "level_enter", "to": 6}],
                "guards": []}}
    steps = [{"need": 1, "candidates": [{"type": "segment", "segment_id": 1}]},
             {"need": 1, "label": "star step",
              "candidates": [{"type": "star", "course": 2, "star": 0}]}]
    out = export_route("R", steps, segs)
    assert out["kind"] == "sm64-route" and out["version"] == 1 and out["name"] == "R"
    seg = out["steps"][0]["candidates"][0]
    assert seg == {"type": "segment", "segment": {
        "name": "LBLJ", "start_triggers": [{"type": "spawned"}],
        "end_triggers": [{"type": "level_enter", "to": 6}], "guards": []}}
    assert out["steps"][1]["label"] == "star step"
    assert out["steps"][1]["candidates"][0] == {"type": "star", "course": 2, "star": 0}


def test_export_route_raises_on_missing_segment():
    with pytest.raises(ValueError, match="missing segment"):
        export_route("R", [{"need": 1, "candidates": [
            {"type": "segment", "segment_id": 99}]}], {})


def _payload(*step_cands):
    return {"kind": "sm64-route", "version": 1, "name": "R",
            "steps": [{"need": 1, "candidates": list(cs)} for cs in step_cands]}


def test_resolve_import_reuses_exact_match_creates_rest():
    existing = [{"id": 7, "name": "LBLJ", "start_triggers": [{"type": "spawned"}],
                 "end_triggers": [{"type": "level_enter", "to": 6}], "guards": []}]
    payload = _payload(
        [{"type": "segment", "segment": {
            "name": "LBLJ", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}], "guards": []}}],
        [{"type": "segment", "segment": {
            "name": "New", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 9}], "guards": []}}])
    res = resolve_import(payload, existing)
    assert res["name"] == "R"
    assert res["reused"] == ["LBLJ"] and res["created"] == ["New"]
    assert res["steps"][0]["candidates"][0] == {"type": "segment", "segment_id": 7}
    assert res["steps"][1]["candidates"][0] == {"type": "segment", "create_index": 0}
    assert len(res["to_create"]) == 1 and res["to_create"][0]["name"] == "New"


def test_resolve_import_dedupes_repeated_new_segment():
    payload = _payload(
        [{"type": "segment", "segment": {"name": "Dup", "start_triggers": [],
                                         "end_triggers": [], "guards": []}}],
        [{"type": "segment", "segment": {"name": "Dup", "start_triggers": [],
                                         "end_triggers": [], "guards": []}}])
    res = resolve_import(payload, [])
    assert len(res["to_create"]) == 1                      # created once
    assert res["steps"][0]["candidates"][0]["create_index"] == 0
    assert res["steps"][1]["candidates"][0]["create_index"] == 0


def test_resolve_import_keeps_star_candidates():
    res = resolve_import(_payload([{"type": "star", "course": 2, "star": 0}]), [])
    assert res["steps"][0]["candidates"][0] == {"type": "star", "course": 2, "star": 0}


def test_resolve_import_rejects_bad_kind_or_version():
    with pytest.raises(ValueError):
        resolve_import({"kind": "nope", "version": 1, "name": "R",
                        "steps": [{"need": 1, "candidates": []}]}, [])
    with pytest.raises(ValueError):
        resolve_import({"kind": "sm64-route", "version": 99, "name": "R",
                        "steps": [{"need": 1, "candidates": []}]}, [])


def test_validate_route_accepts_valid_start_condition():
    validate_route({"name": "R", "start_condition": {"type": "reset_game"},
        "steps": [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]})


def test_validate_route_rejects_bad_start_condition():
    with pytest.raises(ValueError):
        validate_route({"name": "R", "start_condition": {"type": "nope"},
            "steps": [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]})


def test_export_import_roundtrips_start_condition():
    segs = {}
    out = export_route("R", [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}],
                       segs, start_condition={"type": "level_enter", "to": 9})
    assert out["start_condition"] == {"type": "level_enter", "to": 9}
    res = resolve_import(out, [])
    assert res["start_condition"] == {"type": "level_enter", "to": 9}
