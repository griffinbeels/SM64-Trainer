from sm64_events.library.audit import row_key
from sm64_events.library.placements import automatic_rows, row_identity


def test_name_match_supplies_every_strategy_and_only_the_matching_parents_piece():
    target = {"group": "Castle", "section": "Grounds", "label": "Lakitu skip",
              "entity_key": None, "version": None,
              "approaches": [{"name": name, "ids": [str(i)]}
                             for i, name in enumerate(
                                 ["Lakitu skip", "JD beginning", "JD -> Speedkick ending"])],
              "subsections": [{"name": "Door", "ids": ["1"]}]}
    defs = [{"id": 42, "name": "Lakitu Skip", "parents": []},
            {"id": 70, "name": "Door", "parents": ["segment:42"]},
            {"id": 71, "name": "Door", "parents": ["star:1:0"]}]
    rows = automatic_rows({"targets": [target]}, {}, defs)
    assert [row_identity(target, i, "approach", rows) for i in target["approaches"]] == [
        ("segment:42", "Standard"), ("segment:42", "JD beginning"),
        ("segment:42", "JD -> Speedkick ending")]
    assert row_identity(target, target["subsections"][0], "subsection", rows) == (
        "segment:70", "Standard")


def test_seeded_segment_ids_are_resolved_locally_and_deleted_targets_stay_absent():
    target = {"label": "Bowser course", "entity_key": "segment:6"}
    from sm64_events.library.placements import target_entity
    assert target_entity(target, [{"id": 92, "name": "Renamed",
                                   "seed_key": "seg:bitfs-pipe"}]) == "segment:92"
    assert target_entity(target, [{"id": 6, "name": "Unrelated"}]) is None


def test_a_subsection_without_its_own_link_never_becomes_a_whole_star_time():
    target = {"group": "", "section": "", "label": "Star", "version": None,
              "entity_key": "star:1:0"}
    item = {"name": "Climb", "ids": ["1"]}
    assert row_identity(target, item, "subsection", {}) is None
    assert row_identity(target, item, "subsection", {
        row_key(target, item["name"], item["ids"]): "segment:72"}) == (
        "segment:72", "Standard")
