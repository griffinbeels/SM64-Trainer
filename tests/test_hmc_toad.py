"""The two Toad clocks must stay separate from each other and the star grab."""
import json

import pytest

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.library.audit import row_key, target_key
from sm64_events.library.practice_catalog import ensure_catalog
from sm64_events.library.placements import target_entity
from sm64_events.ranks.scopes import default_excluded, effective_excluded
from sm64_events.storage.db import Database
from sm64_events.tracking.defaults import reconcile_defaults, remember_deletion
from sm64_events.tracking.segments import MatchContext, SegmentDef, SegmentEngine
from test_defaults_corpus import Ev


def seed():
    return json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))


def toad_rows():
    return [s for s in seed()["segments"] if s.get("sheet_target")]


def target(row):
    return {**row["sheet_target"], "version": "jp", "entity_key": None,
            "miss_reason": "castle_movement", "subsections": [],
            "approaches": [{"name": row["name"], "ids": ["1", "2"]}]}


@pytest.mark.parametrize("suffix", ["result", "door"])
@pytest.mark.parametrize("grab", [None, 0, 1])
def test_only_the_required_toad_then_hmc_entrance_finishes(suffix, grab):
    row = next(r for r in toad_rows() if r["seed_key"].endswith(suffix))
    fields = {k: row[k] for k in ("name", "enabled", "start_triggers",
              "end_triggers", "waypoints", "guards", "clock_start", "match_mode")}
    engine = SegmentEngine([SegmentDef(id=501, **fields)])
    ctx = MatchContext(level=6, prev_level=7, area=3, num_stars=12,
                       route_segments=frozenset({501}))
    start = (Ev(1, "level_changed", 100, {"from": 7, "to": 6}) if suffix == "result"
             else Ev(1, "moment_reached", 100, {"kind": "door_open", "level": 6,
                  "area": 3, "landmark": {"key": "6:3:bhvDoor:1126,-1074,-2661"}}))
    assert engine.feed(start, ctx)[0] == []
    assert engine.armed_ids() == {501}
    if grab is not None:
        assert engine.feed(Ev(2, "star_collected", 400,
            {"course_id": 0, "star_id": grab, "igt_frames": 300}), ctx)[0] == []
    closed, _ = engine.feed(Ev(3, "warp_entered", 532,
        {"level": 6, "area": 3, "to": 7, "igt_frames": 432}), ctx)
    assert len(closed) == int(grab == 0)
    if closed:
        assert closed[0].rta_frames == 432
    # The later course load must not complete a second time.
    assert engine.feed(Ev(4, "level_changed", 609, {"from": 6, "to": 7}),
        MatchContext(level=7, prev_level=6, area=1, num_stars=13))[0] == []


def test_door_start_requires_the_hmc_door_and_route_or_target():
    row = next(r for r in toad_rows() if r["seed_key"].endswith("door"))
    fields = {k: row[k] for k in ("name", "enabled", "start_triggers",
              "end_triggers", "waypoints", "guards")}
    for landmark, selected in [("6:3:bhvDoor:717,-1177,-869", 501),
                               ("6:3:bhvDoor:1126,-1074,-2661", None)]:
        engine = SegmentEngine([SegmentDef(id=501, **fields)])
        ev = Ev(1, "moment_reached", 100, {"kind": "door_open", "level": 6,
                "area": 3, "landmark": {"key": landmark}})
        engine.feed(ev, MatchContext(level=6, prev_level=6, area=3, num_stars=12,
                                    target_segment=selected, route_segments=frozenset({999})))
        assert engine.armed_ids() == set()


@pytest.mark.parametrize("edited,deleted", [(False, False), (True, False), (False, True)])
def test_existing_sheet_ids_edits_and_deletions_survive_reconcile(tmp_path, edited, deleted):
    db = Database(tmp_path / "db.sqlite")
    try:
        row = toad_rows()[0]
        source = target(row)
        # Reproduce the old catalog, before the seed knew this clock.
        sid = db.insert_segment_def(source["label"], [], [], [], "2026-09-08",
            default_strat="Standard", category="Ultimate Sheet", parents=[])
        db.set_state("sheet_practice_catalog", {"targets": {target_key(source): sid}})
        db.insert_pb(None, None, "Standard", "rta", 444, None, "2026-09-08",
                     segment_id=sid, imported_from="Ultimate Sheet", game_version="us")
        if edited:
            db.update_segment_def(sid, name="My Toad clock", enabled=False)
        if deleted:
            db.delete_segment_def(sid)
        pbs = db.pbs()
        assert reconcile_defaults(db, seed()) == []
        definitions = db.segment_defs()
        matched = [d for d in definitions if d["seed_key"] == row["seed_key"]]
        if deleted:
            assert matched == []
        else:
            assert len(matched) == 1 and matched[0]["id"] == sid
            if edited:
                assert matched[0]["name"] == "My Toad clock"
                assert not matched[0]["enabled"] and matched[0]["start_triggers"] == []
            else:
                assert matched[0]["start_triggers"] == row["start_triggers"]
                assert matched[0]["end_triggers"] == row["end_triggers"]
            links = ensure_catalog({"targets": [source]}, {}, db)
            assert links[row_key(source, source["label"], ["1", "2"])] == f"segment:{sid}"
        assert reconcile_defaults(db, seed()) == []
        assert db.segment_defs() == definitions
        assert db.pbs() == pbs
    finally:
        db.close()


def test_source_placement_uses_seed_identity_even_after_a_rename():
    definitions = [{"id": 503 + i, "seed_key": row["seed_key"], "name": "Renamed"}
                   for i, row in enumerate(toad_rows())]
    for i, row in enumerate(toad_rows()):
        assert target_entity(target(row), definitions) == f"segment:{503+i}"
    source = target(toad_rows()[0])
    for label in ("HMC result - Enter HMC", "HMC result - HMC door (Toad)"):
        assert target_entity({**source, "label": label}, definitions) is None


def test_a_missing_optional_defaults_bundle_keeps_library_placement_available(monkeypatch):
    from sm64_events.library import seed_targets
    monkeypatch.setattr(seed_targets, "bundled_defaults_seed", lambda: None)
    seed_targets._bindings.cache_clear()
    try:
        assert seed_targets.seed_for({"section": "★ HMC", "label": "anything"}) is None
    finally:
        seed_targets._bindings.cache_clear()


def test_deleting_a_seeded_toad_never_recreates_it_as_a_manual_sheet_entry(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    try:
        assert reconcile_defaults(db, seed()) == []
        row = toad_rows()[0]
        definition = next(d for d in db.segment_defs() if d["seed_key"] == row["seed_key"])
        remember_deletion(db, "segments", row["seed_key"])
        db.delete_segment_def(definition["id"])
        before = db.segment_defs()
        assert ensure_catalog({"targets": [target(row)]}, {}, db) == {}
        assert db.segment_defs() == before
    finally:
        db.close()


def test_all_16_star_routes_complete_toad_between_hmc_visits():
    for route in seed()["routes"]:
        if route["category"] != "Main Categories/16 Star":
            continue
        steps = route["steps"]
        index = next(i for i, s in enumerate(steps) if any(
            c.get("seed_key") == "seg:hmc-toad-result" for c in s["candidates"]))
        assert steps[index-1]["candidates"] == [{"type": "star", "course": 0, "star": 0}]
        assert all(c.get("course") == 6 for c in steps[index-2]["candidates"])
        assert all(c.get("course") == 6 for c in steps[index+1]["candidates"])
        assert not any(c.get("seed_key") == "seg:hmc-toad-door"
                       for s in steps for c in s["candidates"])


def test_toad_segments_rank_separately_and_explicit_exclusion_wins():
    definitions = [{**row, "id": i+503} for i, row in enumerate(toad_rows())]
    excluded = default_excluded(definitions)
    assert "star:0:0" in excluded
    assert not {"segment:503", "segment:504"} & excluded
    assert "segment:503" in effective_excluded(excluded, [], ["segment:503"])


def test_each_source_clock_and_region_reaches_its_own_practice_standards(tmp_path):
    from import_fixture import make_client
    with make_client(tmp_path) as (client, db, service):
        # main.py reconciles the shipped corpus before starting the app;
        # this minimal API fixture starts with only the legacy DB migration.
        assert reconcile_defaults(db, seed()) == []
        client.app.state.adoptions.load()
        payload = client.app.state.library.payload
        entities = set()
        for definition in toad_rows():
            index = next(i for i, t in enumerate(payload["targets"])
                         if t["label"] == definition["name"])
            detail = client.get(f"/api/library/target/{index}").json()
            row = detail["approaches"][0]
            entity = row["entity_key"]
            entities.add(entity)
            assert entity != "star:0:0"
            assert row["strategy"] == "Standard"
            sid = int(entity.split(":")[1])
            assert next(d for d in db.segment_defs() if d["id"] == sid)["seed_key"] == definition["seed_key"]
            for version, field in [("us", "ladder"), ("jp", "ladder_jp")]:
                response = client.get("/api/ranks/standards", params={"entity": entity,
                                                                           "version": version})
                assert response.status_code == 200
                assert response.json()["strategies"]["Standard"] == row[field]
                assert len(row[field]) == 8
                region_times = [e["time_cs"] for e in row["entries"]
                                if e.get("version") == version]
                assert response.json()["sheet_best"]["Standard"]["time_cs"] == min(region_times)
            assert row["ladder"] != row["ladder_jp"]
        assert len(entities) == 2
        assert not entities & service.rank_excluded()
        # Explicit row assignment is still the user's authority.
        source = target(toad_rows()[0])
        key = row_key(source, source["label"], ["1", "2"])
        alternate = db.insert_segment_def("My separate clock", [], [], [], "2026-09-08")
        assert ensure_catalog({"targets": [source]}, {key: f"segment:{alternate}"}, db) == {}
