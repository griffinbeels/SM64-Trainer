"""Regrading scopes shares preparation without changing scores or watermarks."""
import asyncio

import pytest

from sm64_events.ranks import classify, scoring
from sm64_events.server import ranks_api
from test_ranks_api_marelo import make_client, _mario_reset_and_collect, _ev


@pytest.mark.parametrize("mode", list(classify.RANK_MODES))
@pytest.mark.parametrize("excluded", [False, True])
def test_regrade_matches_individual_scopes_and_reads_history_once(
        tmp_path, monkeypatch, mode, excluded):
    client, service = make_client(tmp_path, bundled_library=False)
    with client:
        assert client.put("/api/ranks/standards/star:8:2/Standard/Gold",
                          json={"seconds": 15}).status_code == 200
        asyncio.run(service.set_strat(9, 2, "Nuts Pless"))
        ids = []
        for index, duration in enumerate((420, 360, 480)):
            start = 1000 + 2000 * index
            _mario_reset_and_collect(service, start, start + duration, duration)
            ids.append(max(a.id for a in service.db.attempts()
                           if a.course_id == 9 and a.outcome == "success"))
        asyncio.run(service.save_pb(ids[0], "igt"))  # saved PB differs from fastest
        asyncio.run(service.set_strat(8, 2, "Standard"))
        asyncio.run(service.publish(_ev("practice_reset", 8000, {"igt_frames_before": 0})))
        asyncio.run(service.publish(_ev("star_collected", 8300,
                                       {"course_id": 8, "star_id": 2, "igt_frames": 300})))
        other = max(a.id for a in service.db.attempts() if a.course_id == 8)
        asyncio.run(service.save_pb(other, "igt"))
        service.db.set_state("rank_mode", mode)
        if excluded:
            asyncio.run(service.set_rank_excluded("star:8:2", True))
        route = service.db.insert_route("Two choices", [{"need": 1, "candidates": [
            {"type": "star", "course": 9, "star": 2},
            {"type": "star", "course": 8, "star": 2}]},
            {"need": 1, "candidates": [{"type": "star", "course": 9, "star": 2}]}],
            "2026-09-14T00:00:00Z")
        ids = ["overall", "course:9", "course:8", "course:99", f"route:{route}"]
        expected = dict.fromkeys(ids, 77)
        independent = {}
        for scope in ids:
            score = ranks_api._score_scope(service, scope)
            independent[scope] = score
            if score["tier"]:
                expected[scope] = scoring.progression_key(score["tier"], score["division"])
        aggregates = ranks_api._regrade_aggregates(service, ids)
        for scope, aggregate in aggregates.items():
            # Full numeric identity, not only a rank/division bucket.
            assert {k: v for k, v in aggregate.items() if k != "entities"} == {
                k: independent[scope][k] for k in aggregate if k != "entities"}
        assert expected["course:9"] != 77, "fixture must exercise a real grade"
        expected["route:999999"] = 77  # a deleted route must not abort startup
        service.db.set_state("marelo_watermarks", dict.fromkeys(expected, 77))

        calls = dict.fromkeys(("attempts", "pbs", "routes", "segment_defs"), 0)
        for name in calls:
            original = getattr(service.db, name)

            def counted(*args, _name=name, _original=original, **kwargs):
                calls[_name] += 1
                return _original(*args, **kwargs)

            monkeypatch.setattr(service.db, name, counted)
        ranks_api.absorb_after_regrade(service)
        assert service.marelo_watermarks() == expected
        assert calls["attempts"] == (0 if mode == "pb" else 1)
        assert calls["pbs"] == (1 if mode == "pb" else 0)
        assert calls["routes"] == 1
        assert calls["segment_defs"] <= 2


def test_no_watermarks_does_not_scan_history_or_catalogue(tmp_path, monkeypatch):
    client, service = make_client(tmp_path, bundled_library=False)
    with client:
        service.db.set_state("marelo_watermarks", {})

        def unexpected(*_args, **_kwargs):
            pytest.fail("empty watermark sweep prepared scoring data")

        for name in ("attempts", "pbs", "routes", "segment_defs"):
            monkeypatch.setattr(service.db, name, unexpected)
        ranks_api.absorb_after_regrade(service)
        assert service.marelo_watermarks() == {}
