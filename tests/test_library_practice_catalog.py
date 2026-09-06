from sm64_events.library.audit import row_key
from sm64_events.library.practice_catalog import ensure_catalog
from sm64_events.storage.db import Database
from sm64_events.tracking.segments import validate_definition


def payload():
    def target(label, entity, piece, reason=None):
        return {"section": "Example", "group": "Example", "label": label,
                "version": None, "entity_key": entity, "miss_reason": reason,
                "approaches": [{"name": label, "ids": ["1"]},
                               {"name": "Fast route", "ids": ["2"]}],
                "subsections": [{"name": piece, "ids": ["1"]}]}
    return {"targets": [target("Star", "star:1:0", "Climb"),
                        target("New movement", None, "Door", "castle_movement")]}


def test_every_missing_movement_and_piece_gets_a_manual_home_once(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    try:
        source = payload()
        links = ensure_catalog(source, {}, db)
        defs = {f"segment:{d['id']}": d for d in db.segment_defs()}
        for target in source["targets"]:
            piece = target["subsections"][0]
            key = row_key(target, piece["name"], piece["ids"])
            definition = defs[links[key]]
            parent = target["entity_key"] or links[row_key(
                target, target["label"], ["1"])]
            assert definition["parents"] == [parent]
            assert definition["start_triggers"] == definition["end_triggers"] == []
            assert definition["enabled"]
            validate_definition(definition)
        before = db.segment_defs()
        assert ensure_catalog(source, {}, db) == links
        assert db.segment_defs() == before
    finally:
        db.close()


def test_renames_and_detection_edits_survive_and_deleted_pieces_do_not_reappear(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    try:
        source = payload()
        links = ensure_catalog(source, {}, db)
        star = source["targets"][0]
        key = row_key(star, "Climb", ["1"])
        sid = int(links[key].split(":")[1])
        db.update_segment_def(sid, name="My climb", enabled=False)
        ensure_catalog(source, {}, db)
        kept = next(d for d in db.segment_defs() if d["id"] == sid)
        assert kept["name"] == "My climb" and not kept["enabled"]
        db.delete_segment_def(sid)
        before = db.segment_defs()
        assert key not in ensure_catalog(source, {}, db)
        assert db.segment_defs() == before
    finally:
        db.close()


def test_route_rows_are_not_created_as_single_star_or_segment_entries(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    try:
        source = payload()
        route = source["targets"][1]
        route["miss_reason"] = "route"
        before = len(db.segment_defs())
        links = ensure_catalog({"targets": [route]}, {}, db)
        assert links == {} and len(db.segment_defs()) == before
    finally:
        db.close()


def test_repeated_piece_names_do_not_share_an_existing_child(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    try:
        source = payload()
        target = source["targets"][0]
        target["subsections"].append({"name": "Climb", "ids": ["2"]})
        old = db.insert_segment_def("Climb", [], [], [], "2026-09-06",
                                    parents=["star:1:0"])
        links = ensure_catalog(source, {}, db)
        first = links[row_key(target, "Climb", ["1"])]
        second = links[row_key(target, "Climb", ["2"])]
        assert first != second
        assert f"segment:{old}" not in {first, second}
        assert ensure_catalog(source, {}, db) == links
    finally:
        db.close()


def test_manual_entry_never_arms_from_a_real_event_sequence():
    from sm64_events.storage.db import EventRow
    from sm64_events.tracking.segments import SegmentDef, SegmentEngine, MatchContext
    engine = SegmentEngine([SegmentDef(
        id=123, name="Sheet piece", enabled=True, start_triggers=[],
        end_triggers=[], guards=[], parents=["star:1:0"])])
    for n, kind in enumerate(("attempt_anchor", "level_changed", "star_collected",
                              "warp_entered", "area_changed"), 1):
        event = EventRow(n, 1, n, kind, n * 30, "2026-09-06",
                         {"from": 6, "to": 9, "course_id": 1, "star_id": 0})
        closed, _ = engine.feed(event, MatchContext(level=9, prev_level=6, num_stars=0))
        assert closed == [] and engine.armed_ids() == set()


def test_target_relink_moves_managed_children_but_preserves_user_parent_edits(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    try:
        source = payload()
        target = source["targets"][1]
        links = ensure_catalog(source, {}, db)
        piece_key = row_key(target, "Door", ["1"])
        sid = int(links[piece_key].split(":")[1])
        replacement = db.insert_segment_def("Replacement", [], [], [], "2026-09-06")
        explicit = {row_key(target, target["label"], ["1"]): f"segment:{replacement}"}
        ensure_catalog(source, explicit, db)
        definition = next(d for d in db.segment_defs() if d["id"] == sid)
        assert definition["parents"] == [f"segment:{replacement}"]
        db.update_segment_def(sid, parents=["star:1:1"])
        ensure_catalog(source, {}, db)
        definition = next(d for d in db.segment_defs() if d["id"] == sid)
        assert definition["parents"] == ["star:1:1"]
    finally:
        db.close()


def test_a_manual_definition_cannot_have_only_one_configured_end():
    import pytest
    for empty in ("start_triggers", "end_triggers"):
        definition = {"name": "Partial", "start_triggers": [{"type": "spawned"}],
                      "end_triggers": [{"type": "spawned"}], "guards": []}
        definition[empty] = []
        with pytest.raises(ValueError, match="needs at least one trigger"):
            validate_definition(definition)
