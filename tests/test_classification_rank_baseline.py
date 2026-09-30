"""A classification reward starts on its destination's existing measurement."""
import asyncio
import json
from dataclasses import replace

import pytest

from sm64_events.ranks.standards import RankStandards
from sm64_events.tracking.views import build_session_view, classification_rank_baseline
from test_view_rank_region import KEY, attempt, ranks  # noqa: F401
from test_views import make


@pytest.mark.parametrize("kind", ["star", "segment", "pipe"])
@pytest.mark.parametrize("mode", ["pb", "avg10"])
def test_destination_snapshot_matches_canonical_session_banners(tmp_path, ranks, kind, mode):
    db, service = make(tmp_path)
    segment_id = None
    grading_key = KEY
    destination = "Standard"
    if kind != "star":
        pipe = kind == "pipe"
        segment_id = db.insert_segment_def(
            "Destination baseline", [{"type": "level_enter", "to": 17}] if pipe else [],
            [], [], "2026-09-08T00:00:00Z",
            seed_key="seg:reds->pipe:bitdw" if pipe else None)
        asyncio.run(service._segments_changed())
        grading_key = "star:16:0" if pipe else f"segment:{segment_id}"
        destination = "Standard (Pipe)" if pipe else "Standard"
        data = ranks.to_json()
        data["entities"][grading_key] = data["entities"].pop(KEY)
        for field in ("strategies", "jp_strategies"):
            data["entities"][grading_key][field][destination] = (
                data["entities"][grading_key][field].pop("Standard"))
        path = tmp_path / "destination-standards.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        ranks = RankStandards(path)
        ranks.load()
    service.ranks = ranks
    db.set_state("rank_mode", mode)
    old = "Old (Pipe)" if kind == "pipe" else "Old"
    clock = "igt" if kind == "star" else "rta"
    active = service.strat_by_star if kind == "star" else service.strat_by_segment
    identity = (2, 4) if kind == "star" else segment_id
    active[identity] = old

    def row(identifier, frames, version, strat):
        result = replace(attempt(identifier, frames, version),
                         session_id=service.session_id, strat_tag=strat,
                         segment_id=segment_id)
        return result if kind == "star" else replace(
            result, course_id=None, star_id=None, igt_frames=None, rta_frames=frames)

    rows = [row(11, 360, "us", destination), row(12, 630, "jp", destination),
            row(13, 30, "us", old), row(14, 340, "us", None)]
    for saved in rows:
        db.upsert_attempt(saved)
    dest = rows[1]
    db.insert_pb(dest.course_id, dest.star_id, destination, clock, 630, dest.id,
                 dest.ended_utc, segment_id=segment_id, game_version="jp")
    before_rows, before_pbs = db.attempts(), db.pbs()
    baseline = classification_rank_baseline(db, service, rows[-1], destination)
    assert db.attempts() == before_rows and db.pbs() == before_pbs
    assert active[identity] == old, "reading a reward baseline must not select its strategy"

    # Ask the real session builder what it would show on the destination
    # without retagging the candidate or saving its faster time.
    active[identity] = destination
    sections = build_session_view(db, service, "rta", scope="lifetime")
    [section] = [sec for sec in sections["stars" if kind == "star" else "segments"]
                 if sec["attempts"]]
    assert baseline == {"rank": section["rank"], "entity_rank": section["entity_rank"],
                        "game_version": sections["game_version"]}
    # A pipe fixture has only its borrowed Strategy ladder; with no Overall
    # curve to compare ROM quality, canonical basis selection takes raw time.
    expected = "Master" if kind == "pipe" and mode == "avg10" else "Grandmaster"
    assert baseline["rank"]["rank"] == expected
    if mode == "avg10":
        assert baseline["rank"]["basis"]["count"] == 1


def test_a_destination_with_no_saved_time_starts_at_its_own_floor(tmp_path, ranks):
    db, service = make(tmp_path)
    service.ranks = ranks
    current = replace(attempt(20, 300, "us"), strat_tag="Old", session_id=service.session_id)
    db.upsert_attempt(current)
    db.insert_pb(2, 4, "Old", "igt", 300, current.id, current.ended_utc)
    service.strat_by_star[(2, 4)] = "Old"
    result = classification_rank_baseline(db, service, current, "Standard")
    assert result["rank"]["reason"] == "unranked"
    assert result["entity_rank"] is None
