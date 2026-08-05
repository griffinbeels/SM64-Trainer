import json

from sm64_events.library import audit


def _payload():
    return {"schema_version": 1, "sheet_revision": "2026-08-04T20:14:25",
            "fetched_at": "x", "runners": ["Kally"], "targets": [
                {"entity_key": "star:1:0", "group": "1. Bob-omb Battlefield",
                 "section": "1. Bob-omb Battlefield",
                 "label": "Big Bob-omb on the Summit", "miss_reason": None,
                 "approaches": [
                     {"ids": ["1", "2"], "name": "Big Bob-omb on the Summit",
                      "times": {}, "best_cs": 4363, "best_runner": "Avatar",
                      "ideal_cs": None, "fill_rate": 0.28,
                      "entries": [{"runner": "Kally", "time_cs": 4380,
                                   "video": "https://youtu.be/z"}]},
                     {"ids": ["3"], "name": "Left side strat", "times": {},
                      "best_cs": 4290, "best_runner": "Kaylee",
                      "ideal_cs": None, "fill_rate": 0.06, "entries": []}],
                 "subsections": [
                     {"ids": ["1", "2"], "name": "Warp fadeout",
                      "best_cs": 1590, "best_runner": "taihou", "entries": []}]},
                {"entity_key": None, "group": "Castle Movements (Lobby)",
                 "section": "★ BoB", "label": "Lobby door (L) - BoB door",
                 "miss_reason": "castle_movement",
                 "approaches": [{"ids": ["1"], "name": "Lobby door (L) - BoB door",
                                 "times": {}, "best_cs": 276, "best_runner": "M",
                                 "ideal_cs": None, "fill_rate": 0.21,
                                 "entries": []}],
                 "subsections": []}]}


def test_computed_category_reads_the_payload():
    targets = _payload()["targets"]
    assert audit.computed_category(targets[0]) == "star"
    assert audit.computed_category(targets[1]) == "castle_movement"
    assert audit.computed_category({"entity_key": "segment:5",
                                    "miss_reason": None}) == "segment"


def test_audit_view_carries_the_ratio_that_decided_each_row():
    view = audit.audit_view(_payload(), {"targets": {}, "rows": {}})
    rows = {r["name"]: r for r in view["targets"][0]["rows"]}
    assert rows["Big Bob-omb on the Summit"]["ratio"] is None   # nothing before it
    assert rows["Left side strat"]["ratio"] == round(4290 / 4363, 3)
    assert rows["Warp fadeout"]["ratio"] == round(1590 / 4363, 3)
    assert rows["Big Bob-omb on the Summit"]["videos"] == 1
    assert rows["Big Bob-omb on the Summit"]["video"] == "https://youtu.be/z"


def test_a_near_boundary_row_is_flagged_for_a_human():
    payload = _payload()
    payload["targets"][0]["approaches"][1]["best_cs"] = 3000   # 0.69 of 4363
    view = audit.audit_view(payload, {"targets": {}, "rows": {}})
    target = view["targets"][0]
    assert "near-veto" in target["flags"]
    assert next(r for r in target["rows"] if r["name"] == "Left side strat")["near"]


def test_an_override_moves_a_row_between_the_two_lists():
    # Stamping a kind in place would leave the row in `approaches`, where every
    # consumer that only reads that list would still see it -- which is the
    # whole thing the correction exists to stop.
    payload = _payload()
    key = audit.row_key(payload["targets"][0], "Left side strat", ["3"])
    overrides = {"targets": {}, "rows": {
        key: {"kind": "subsection", "reason": "actually a split"}}}
    out = audit.apply_overrides(payload, overrides)
    target = out["targets"][0]
    assert [a["name"] for a in target["approaches"]] == ["Big Bob-omb on the Summit"]
    assert sorted(s["name"] for s in target["subsections"]) == [
        "Left side strat", "Warp fadeout"]


def test_a_targets_key_includes_its_rom_version():
    # BBH opens two targets both called "Go on a Ghost Hunt", one per version.
    jp = {"section": "5. Big Boo's Haunt", "label": "Go on a Ghost Hunt",
          "version": "jp"}
    us = {**jp, "version": "us"}
    assert audit.target_key(jp) != audit.target_key(us)
    assert audit.target_key({**jp, "version": None}).endswith("Ghost Hunt")


def test_a_rows_key_includes_its_bracket_ids():
    # "Warp fadeout" appears twice under Big Bob-omb, for [1|2] and [3|4].
    target = _payload()["targets"][0]
    assert (audit.row_key(target, "Warp fadeout", ["1", "2"])
            != audit.row_key(target, "Warp fadeout", ["3", "4"]))


def test_an_override_can_repoint_a_target_and_clear_its_reason():
    payload = _payload()
    overrides = {"targets": {
        audit.target_key(payload["targets"][1]):
            {"category": "segment", "entity_key": "segment:12"}}, "rows": {}}
    target = audit.apply_overrides(payload, overrides)["targets"][1]
    assert target["entity_key"] == "segment:12"
    assert target["miss_reason"] is None
    assert target["audited"] is True


def test_round_trip_through_disk_keeps_only_known_fields(tmp_path):
    path = tmp_path / "library_overrides.json"
    audit.save_overrides(path, {"targets": {"a": {"category": "star",
                                                  "junk": "drop me",
                                                  "reason": ""}},
                                "rows": {"b": {"kind": "subsection"}}})
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored == {"targets": {"a": {"category": "star"}},
                      "rows": {"b": {"kind": "subsection"}}}
    assert audit.load_overrides(path) == stored


def test_a_missing_or_broken_overrides_file_is_simply_empty(tmp_path):
    assert audit.load_overrides(tmp_path / "nope.json") == {"targets": {}, "rows": {}}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert audit.load_overrides(broken) == {"targets": {}, "rows": {}}


def test_entity_choices_offer_stars_and_the_segments_it_is_given():
    choices = audit.entity_choices({5: "BitDW Pipe Entry"})
    keys = {c["key"] for c in choices}
    assert "star:1:0" in keys and "star:16:0" in keys and "star:19:0" in keys
    assert {"key": "segment:5", "name": "BitDW Pipe Entry",
            "kind": "segment"} in choices


def test_shared_entities_are_flagged():
    payload = _payload()
    payload["targets"][1]["entity_key"] = "star:1:0"
    payload["targets"][1]["miss_reason"] = None
    view = audit.audit_view(payload, {"targets": {}, "rows": {}})
    assert all("shared-entity" in t["flags"] for t in view["targets"])


def test_the_audit_view_carries_each_rows_ladder_and_the_model():
    from sm64_events.library import ladders
    payload = ladders.fit_payload(_payload())
    view = audit.audit_view(payload, {"targets": {}, "rows": {}})
    rows = {r["name"]: r for r in view["targets"][0]["rows"]}
    # too few entries to place eight tiers -- but the row still reports its
    # best, its entry count and its videos, because "few people have run this"
    # is a fact about the community rather than a hole in the library.
    thin = rows["Big Bob-omb on the Summit"]
    assert thin["ladder"] is None
    assert thin["best_cs"] == 4363 and thin["entries"] == 1 and thin["videos"] == 1
    assert view["ladder_model"]["min_entries"] == ladders.MIN_ENTRIES
    assert view["ranks"][0] == "Mario" and "Iron" not in view["ranks"]


def test_a_row_with_enough_times_carries_a_ladder_into_the_view():
    from sm64_events.library import ladders
    payload = _payload()
    payload["targets"][0]["approaches"][0]["entries"] = [
        {"runner": f"r{i}", "time_cs": 4300 + i * 5, "video": None}
        for i in range(40)]
    view = audit.audit_view(ladders.fit_payload(payload),
                            {"targets": {}, "rows": {}})
    ladder = {r["name"]: r for r in view["targets"][0]["rows"]}[
        "Big Bob-omb on the Summit"]["ladder"]
    assert ladder and ladder["Mario"] < ladder["Bronze"]
