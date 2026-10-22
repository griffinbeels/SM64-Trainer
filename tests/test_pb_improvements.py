"""A normal PB save cannot lower the displayed best; ghost saving is separate."""
import asyncio
from dataclasses import replace

import pytest

from sm64_events.ranks.standards import RankStandards
from sm64_events.tracking.pbaction import pb_action
from sm64_events.tracking.views import _attempt_json, current_pbs_by_strat
from test_pbaction import BASE
from test_tracker_service import make


@pytest.mark.parametrize("frames,action", [(514, "save"), (515, None), (533, None)])
def test_normal_save_requires_a_strict_improvement(frames, action):
    actual, blocked = pb_action(BASE, "Standard", False, frames=frames, current_frames=515)
    assert actual == action
    assert blocked == (None if action else {"reason": "not_faster", "strat": None})
    assert pb_action(BASE, "Standard", True, frames=frames, current_frames=frames) == ("undo", None)


@pytest.mark.parametrize("clock", ["igt", "rta"])
@pytest.mark.parametrize("previous_rom", [None, "us", "jp"])
def test_api_and_row_refuse_slower_saves_in_the_same_clock_and_shared_rom_slot(tmp_path, clock, previous_rom):
    db, service = make(tmp_path)
    try:
        service.ranks = RankStandards(tmp_path / "ranks.json")
        service.ranks.load()
        service.ranks.set_threshold("star:8:0", "Standard", "Mario", 16.0)
        asyncio.run(service.set_strat(8, 0, "Standard", save_adopted=False))
        row = replace(BASE, id=100, course_id=8, star_id=0, game_version="us", igt_frames=533, rta_frames=540)
        db.upsert_attempt(row)
        db.insert_pb(8, 0, "Standard", clock, 515, None, "2026-10-20T00:00:00Z", game_version=previous_rom)
        assert service._pb_slot(row)["region_scope"] == "shared"
        before = db.pbs()
        assert service.pb_action(row, clock) == (None, {"reason": "not_faster", "strat": None})
        view = _attempt_json(row, clock, ranks=service.ranks, active_strat="Standard",
            pbs_by_strat=current_pbs_by_strat(before, running="us", ranks=service.ranks))
        assert view["pb_action"] is None and view["pb_blocked"]["reason"] == "not_faster"
        with pytest.raises(ValueError, match="not_faster"):
            asyncio.run(service.save_pb(row.id, clock))
        assert db.pbs() == before
        assert not [event for event in db.events() if event.type == "pb_saved"]
    finally:
        db.close()


def test_correcting_an_incorrect_save_still_uses_explicit_undo(tmp_path):
    db, service = make(tmp_path)
    try:
        asyncio.run(service.set_strat(8, 0, "Standard", save_adopted=False))
        first = replace(BASE, id=100, course_id=8, star_id=0, igt_frames=515)
        slower = replace(first, id=101, igt_frames=533)
        db.upsert_attempt(first)
        db.upsert_attempt(slower)
        asyncio.run(service.save_pb(first.id, "igt"))
        with pytest.raises(ValueError, match="not_faster"):
            asyncio.run(service.save_pb(slower.id, "igt"))
        asyncio.run(service.undo_pb(first.id, "igt"))
        asyncio.run(service.save_pb(slower.id, "igt"))
        assert db.current_pb(8, 0, "igt", strat_tag="Standard")["attempt_id"] == slower.id
    finally:
        db.close()


def test_clock_comparison_uses_the_clock_of_the_button_and_command(tmp_path):
    db, service = make(tmp_path)
    try:
        asyncio.run(service.set_strat(8, 0, "Standard", save_adopted=False))
        row = replace(BASE, id=100, course_id=8, star_id=0, igt_frames=500, rta_frames=540)
        db.upsert_attempt(row)
        for clock in ("igt", "rta"):
            db.insert_pb(8, 0, "Standard", clock, 515, None, "2026-10-20T00:00:00Z")
        for clock, expected in (("igt", "save"), ("rta", None)):
            assert service.pb_action(row, clock)[0] == expected
            view = _attempt_json(row, clock, active_strat="Standard",
                                 pbs_by_strat=current_pbs_by_strat(db.pbs()))
            assert view["pb_action"] == expected
    finally:
        db.close()


@pytest.mark.parametrize("frames,expected", [(500, "save"), (533, None)])
def test_regional_slot_ignores_a_faster_other_rom_but_compares_its_own_pb(tmp_path, frames, expected):
    db, service = make(tmp_path)
    try:
        service.ranks = RankStandards(tmp_path / "ranks.json")
        service.ranks.load()
        service.ranks.set_threshold("star:8:0", "Standard", "Mario", 16.0)
        service.ranks.set_threshold("star:8:0", "Standard", "Mario", 18.0, version="jp")
        asyncio.run(service.set_strat(8, 0, "Standard", save_adopted=False))
        row = replace(BASE, id=100, course_id=8, star_id=0, game_version="us", igt_frames=frames)
        db.upsert_attempt(row)
        db.insert_pb(8, 0, "Standard", "igt", 515, None, "2026-10-20T00:00:00Z", game_version="us")
        db.insert_pb(8, 0, "Standard", "igt", 300, None, "2026-10-20T00:00:01Z", game_version="jp")
        assert service._pb_slot(row)["region_scope"] == "regional"
        assert service.pb_action(row, "igt")[0] == expected
        view = _attempt_json(row, "igt", ranks=service.ranks, active_strat="Standard",
            pbs_by_strat=current_pbs_by_strat(db.pbs(), running="us", ranks=service.ranks))
        assert view["pb_action"] == expected
    finally:
        db.close()
