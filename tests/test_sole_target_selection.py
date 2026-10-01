"""The sole visible selector option replaces a retained target, without races."""
import asyncio

import pytest

from sm64_events.tracking.projection import Projector
from test_api import make_client
from test_target_queue import exit_def, jev


def held_projector():
    projector = Projector(segments=[exit_def(id=1), exit_def(id=2, start_from=16)])
    projector.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))
    projector.feed(jev(2, "area_changed", 1000, {"level": 6, "to": 1}))
    assert projector.target == ("segment", 1)
    assert projector._target_hooked
    return projector


@pytest.mark.parametrize("replacement", [
    {"kind": "segment", "segment_id": 2}, {"course_id": 2, "star_id": 1}])
def test_sole_visible_option_can_replace_the_observed_hooked_target(replacement):
    projector = held_projector()
    projector.feed(jev(3, "target_set", 1001, {
        **replacement, "auto": True,
        "sole_selection": {"target": ["segment", 1], "stage": [6, 1]}}))
    expected = ("segment", 2) if replacement.get("kind") else ("star", 2, 1)
    assert projector.target == expected
    assert projector._picked_target is None, "automatic selection is not a click"


@pytest.mark.parametrize("binding", [
    {"target": None, "stage": [6, 1]},
    {"target": ["segment", 2], "stage": [6, 1]},
    {"target": ["segment", 1], "stage": [6, 2]},
    {"target": ["segment", 1], "stage": [17, 1]}])
def test_delayed_sole_selection_cannot_override_a_new_target_or_place(binding):
    projector = held_projector()
    projector.feed(jev(3, "target_set", 1001, {
        "kind": "segment", "segment_id": 2, "auto": True, "sole_selection": binding}))
    assert projector.target == ("segment", 1)


def test_generic_automatic_fill_still_cannot_replace_a_hooked_target():
    projector = held_projector()
    projector.feed(jev(3, "target_set", 1001, {
        "kind": "segment", "segment_id": 2, "auto": True}))
    assert projector.target == ("segment", 1)


def test_level_scoped_sole_offer_survives_an_in_course_subarea_change():
    projector = held_projector()
    projector.feed(jev(3, "area_changed", 1001, {"level": 6, "to": 2}))
    projector.feed(jev(4, "target_set", 1002, {
        "course_id": 2, "star_id": 1, "auto": True,
        "sole_selection": {"target": ["segment", 1], "stage": [6, None]}}))
    assert projector.target == ("star", 2, 1)


def test_api_preserves_the_complete_sole_selection_binding(tmp_path):
    client, _service, db = make_client(tmp_path)
    with client:
        binding = {"target": None, "stage": [None, None]}
        response = client.post("/api/target", json={
            "course_id": 2, "star_id": 1, "auto": True, "sole_selection": binding})
        assert response.status_code == 200
        target_row = next(row for row in reversed(db.events()) if row.type == "target_set")
        assert target_row.payload["sole_selection"] == binding
        assert target_row.payload["auto"] is True


@pytest.mark.parametrize("binding", [{"target": None}, {"stage": [6, 1]},
                                   {"target": ["star", 2], "stage": [6, 1]}])
def test_sole_selection_requires_a_well_formed_target_and_stage_binding(tmp_path, binding):
    client, _service, _db = make_client(tmp_path)
    with client:
        response = client.post("/api/target", json={
            "course_id": 2, "star_id": 1, "sole_selection": binding})
        assert response.status_code == 422


def test_service_refuses_a_sole_offer_from_an_old_selector_stage(tmp_path):
    client, service, _db = make_client(tmp_path)
    with client:
        service._current_stage = {"mode": "stars", "level": 24, "area": 1, "course_id": 2}
        with pytest.raises(ValueError, match="selector changed"):
            asyncio.run(service.request_target("star", course_id=2, star_id=1,
                sole_selection={"target": None, "stage": [6, 1]}))
