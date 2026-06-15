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


def test_validate_route_rejects_empty_steps():
    with pytest.raises(ValueError, match="steps"):
        validate_route({"name": "R", "steps": []})


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
