"""Assigning a library row to a segment the user built.

The sheet's movements are finer than our segments and its subsections have no
segment at all, so the user builds one and points a row at it -- we never
invent 113 segments nobody asked for."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.library import adoptions as ad
from sm64_events.library.audit import row_key
from sm64_events.library.build import SCHEMA_VERSION
from sm64_events.library.ladders import fit_payload
from sm64_events.library.store import LibraryStore
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.library_api import create_library_router

LADDER = {"Mario": 2.76, "Grandmaster": 2.80, "Master": 2.86, "Diamond": 2.93,
          "Platinum": 3.00, "Gold": 3.10, "Silver": 3.20, "Bronze": 3.40}


def _payload():
    def item(name, ladder=True, entries=40):
        return {"ids": ["1"], "name": name, "best_cs": 276, "best_runner": "M",
                "times": {}, "ideal_cs": None, "fill_rate": 0.2,
                "ladder": dict(LADDER) if ladder else None,
                "ladder_samples": entries,
                "entries": [{"runner": f"r{i}", "time_cs": 276 + i,
                             "video": None, "version": None, "platform": None}
                            for i in range(entries)]}
    return {"schema_version": SCHEMA_VERSION, "sheet_revision": "2026-08-05T09:15:18",
            "fetched_at": "x", "runners": [], "ladder_model": {}, "targets": [
                {"entity_key": None, "group": "Castle Movements (Lobby)",
                 "section": "★ BoB", "label": "Lobby door (L) - BoB door",
                 "version": None, "miss_reason": "castle_movement",
                 "approaches": [item("Lobby door (L) - BoB door"),
                                item("Thin one", ladder=False, entries=3)],
                 "subsections": [item("Volcano Entry")]}]}


@pytest.fixture()
def wiring(tmp_path):
    store = LibraryStore()
    store._payload = _payload()
    standards = RankStandards(tmp_path / "rank_standards.json")
    standards.load()
    adoptions = ad.Adoptions(tmp_path / "library_adoptions.json", store, standards)
    adoptions.load()
    target = store.payload["targets"][0]
    keys = {item["name"]: row_key(target, item["name"], item["ids"])
            for kind in ("approaches", "subsections")
            for item in target[kind]}
    return adoptions, standards, keys, tmp_path


def test_adopting_gives_the_segment_the_communitys_ladder(wiring):
    adoptions, standards, keys, _ = wiring
    result = adoptions.adopt(keys["Lobby door (L) - BoB door"], "segment:42")
    # The approach is named after its target, so filing it under a segment of
    # the same name would stutter; it lands as "Standard".
    assert result["strategy"] == ad.DEFAULT_STRATEGY
    assert standards.strategies("segment:42") == [ad.DEFAULT_STRATEGY]
    assert standards.ladder_cs("segment:42", ad.DEFAULT_STRATEGY)["Mario"] == 276
    assert standards.is_fitted("segment:42", ad.DEFAULT_STRATEGY)
    assert "segment:42" in standards.graded_entities()


def test_adopting_a_subsection_uses_standard_not_the_pieces_name(wiring):
    """A subsection row names the piece, not a distinct way to perform it.
    Linking Volcano Entry to the user's Volcano Entry segment therefore
    contributes its timings as Standard rather than a stuttering strategy
    called Volcano Entry."""
    adoptions, standards, keys, _ = wiring
    result = adoptions.adopt(keys["Volcano Entry"], "segment:42")
    assert result["strategy"] == ad.DEFAULT_STRATEGY
    assert standards.strategies("segment:42") == [ad.DEFAULT_STRATEGY]
    assert standards.ladder_cs("segment:42", ad.DEFAULT_STRATEGY)["Mario"] == 276


def test_unadopting_actually_takes_the_strategy_away(wiring):
    adoptions, standards, keys, _ = wiring
    key = keys["Lobby door (L) - BoB door"]
    adoptions.adopt(key, "segment:42")
    adoptions.unadopt(key)
    assert standards.strategies("segment:42") == []


def test_an_assignment_survives_a_restart(wiring, tmp_path):
    adoptions, standards, keys, _ = wiring
    adoptions.adopt(keys["Lobby door (L) - BoB door"], "segment:42")
    fresh_standards = RankStandards(tmp_path / "rank_standards.json")
    fresh_standards.load()
    fresh = ad.Adoptions(tmp_path / "library_adoptions.json", adoptions.store,
                         fresh_standards)
    fresh.load()
    assert fresh_standards.strategies("segment:42") == [ad.DEFAULT_STRATEGY]


def test_a_row_with_no_ladder_is_refused_by_name(wiring):
    adoptions, _, keys, _ = wiring
    with pytest.raises(ad.AdoptionError) as err:
        adoptions.adopt(keys["Thin one"], "segment:42")
    assert "only 3 recorded times" in str(err.value)


def test_an_unknown_row_is_refused(wiring):
    adoptions, _, _, _ = wiring
    with pytest.raises(ad.AdoptionError):
        adoptions.adopt("no such row", "segment:42")


def test_a_variant_qualified_entity_is_refused(wiring):
    adoptions, _, keys, _ = wiring
    adoptions.qualified = {"star:4:6"}
    with pytest.raises(ad.AdoptionError) as err:
        adoptions.adopt(keys["Lobby door (L) - BoB door"], "star:4:6")
    assert "exit-star variant" in str(err.value)


def test_an_existing_manual_cutoff_survives_assignment_of_a_sheet_foundation(wiring):
    """A named strategy can acquire a Sheet foundation while keeping an
    explicitly edited cutoff. Untouched tiers come from the assignment."""
    adoptions, standards, keys, _ = wiring
    standards.create_strategy("segment:42", ad.DEFAULT_STRATEGY)
    standards.set_threshold("segment:42", ad.DEFAULT_STRATEGY, "Mario", 2.00)
    result = adoptions.adopt(keys["Lobby door (L) - BoB door"], "segment:42")
    assert result["adopted"] is True
    assert adoptions.rows()[keys["Lobby door (L) - BoB door"]] == "segment:42"
    # The explicit edit survives the new fitted foundation.
    assert standards.ladder_cs("segment:42", ad.DEFAULT_STRATEGY)["Mario"] == 200
    assert standards.is_fitted("segment:42", ad.DEFAULT_STRATEGY)
    assert len(standards.ladder_cs("segment:42", ad.DEFAULT_STRATEGY)) > 1


def test_a_corrupt_assignments_file_is_simply_empty(tmp_path):
    path = tmp_path / "library_adoptions.json"
    path.write_text("{not json", encoding="utf-8")
    assert ad.load(path) == {}
    path.write_text(json.dumps({"rows": {"a": 5, "b": "segment:1"}}), encoding="utf-8")
    assert ad.load(path) == {"b": "segment:1"}


def test_the_routes_report_a_refusal_rather_than_failing_silently(wiring):
    adoptions, _, keys, _ = wiring
    app = FastAPI()
    app.include_router(create_library_router(adoptions.store, adoptions=adoptions))
    client = TestClient(app)
    ok = client.post("/api/library/adopt",
                     json={"row_key": keys["Lobby door (L) - BoB door"],
                           "entity_key": "segment:42"})
    assert ok.status_code == 200 and ok.json()["adopted"] is True
    # 409, not 400: the request is well formed and the refusal is about the
    # state of the world.
    bad = client.post("/api/library/adopt",
                      json={"row_key": keys["Thin one"], "entity_key": "segment:42"})
    assert bad.status_code == 409 and "no rank standards" in bad.json()["detail"]
    assert client.post("/api/library/adopt", json={}).status_code == 400
    assert client.get("/api/library/adoptions").json()["rows"]
    client.post("/api/library/unadopt",
                json={"row_key": keys["Lobby door (L) - BoB door"]})
    assert not any(client.get("/api/library/adoptions").json()["rows"].values())


def test_a_refresh_re_syncs_adopted_ladders(wiring, monkeypatch):
    """A refresh publishes observations and adopted standards together."""
    adoptions, standards, keys, _ = wiring
    key = keys["Lobby door (L) - BoB door"]
    adoptions.adopt(key, "segment:42")
    assert standards.ladder_cs("segment:42", ad.DEFAULT_STRATEGY)["Mario"] == 276

    moved = _payload()
    moved["sheet_revision"] = "2026-08-06T00:00:00"
    for entry in moved["targets"][0]["approaches"][0]["entries"]:
        entry["time_cs"] += 30
    fit_payload(moved)
    expected = round(moved["targets"][0]["approaches"][0]["ladder"]["Mario"] * 100)
    assert expected != 276

    def fake_refresh(fetch_fn, overrides=None):
        # Replace only the network/build boundary; actual activation remains
        # the same absorb transaction used by a successful real refresh.
        return adoptions.store.absorb(moved)
    monkeypatch.setattr(adoptions.store, "refresh", fake_refresh)

    app = FastAPI()
    app.include_router(create_library_router(adoptions.store, adoptions=adoptions))
    client = TestClient(app)
    response = client.post("/api/library/refresh")
    assert response.status_code == 200 and response.json()["applied"] is True
    assert standards.ladder_cs("segment:42", ad.DEFAULT_STRATEGY)["Mario"] == expected


def test_the_adopt_routes_are_absent_without_a_standards_store(wiring):
    adoptions, _, _, _ = wiring
    app = FastAPI()
    app.include_router(create_library_router(adoptions.store))
    client = TestClient(app)
    assert client.post("/api/library/adopt", json={}).status_code == 404
    assert client.get("/api/library").status_code == 200


def test_auto_match_pairs_by_normalized_name_only():
    """ROUND 6: "we should autoassign any segments that exist already."
    Ladder proximity was measured structurally unable here (the corpus
    segments' hand-seeded 3-tier rows never reach `_distance`'s 4-shared-tier
    floor; 184 rows x 18 vetted strategies scored zero pairs), so the auto
    key is normalized NAME equality -- case and punctuation blind, nothing
    fuzzier. Exactly one pair exists on today's snapshot (Lakitu skip), and
    a segment the user builds tomorrow with a movement's name pairs on the
    next page load."""
    segments = [(3, "Lakitu Skip"), (6, "BitFS Pipe Entry")]
    assert ad.auto_match("Lakitu skip", segments) == {
        "entity": "segment:3", "name": "Lakitu Skip"}
    assert ad.auto_match("lakitu-skip!", segments) == {
        "entity": "segment:3", "name": "Lakitu Skip"}
    assert ad.auto_match("Lobby door (L) - BoB door", segments) is None
    assert ad.auto_match("", segments) is None


def test_adopt_target_links_every_laddered_approach_and_reports_skips(wiring):
    """ROUND 7: "If we link a segment, then it should automatically load ALL
    strategies for that segment." One request adopts every laddered approach
    of the target; a row with no ladder skips WITH its reason and never
    sinks the batch; unadopt_target reverses all of it."""
    adoptions, standards, keys, _ = wiring
    result = adoptions.adopt_target(0, "segment:42")
    assert [row["strategy"] for row in result["adopted"]] == [ad.DEFAULT_STRATEGY]
    assert result["entity_key"] == "segment:42"
    assert result["skipped"] and result["skipped"][0]["name"] == "Thin one"
    assert "recorded times" in result["skipped"][0]["reason"]
    assert adoptions.rows() == {keys[name]: "segment:42"
                                for name in ("Lobby door (L) - BoB door", "Thin one")}
    assert standards.strategies("segment:42") == [ad.DEFAULT_STRATEGY]

    undone = adoptions.unadopt_target(0)
    assert undone["removed"] == 2  # explicit and inferred sibling assignments
    assert not any(adoptions.rows().values())
    assert standards.strategies("segment:42") == []


def test_adopt_target_refuses_an_unknown_index_and_an_empty_target(wiring):
    adoptions, _, _, _ = wiring
    with pytest.raises(ad.AdoptionError):
        adoptions.adopt_target(99, "segment:42")
    # a target whose every approach lacks a ladder must refuse loudly --
    # "linked" with zero strategies is indistinguishable from working
    adoptions.store.payload["targets"][0]["approaches"] = [
        row for row in adoptions.store.payload["targets"][0]["approaches"]
        if not row.get("ladder")]
    with pytest.raises(ad.AdoptionError) as err:
        adoptions.adopt_target(0, "segment:42")
    assert "no approach" in str(err.value)


def test_linked_targets_is_the_reverse_view_the_segment_editor_reads():
    """ROUND 8: the segment builder needs "which library target points at
    this segment" -- the reverse of the stored rows, computed from APPROACH
    assignments only (a piece link is a partial fact and must not present a
    whole target as linked)."""
    store = LibraryStore()
    store._payload = _payload()
    adoptions = ad.Adoptions("nonexistent-adoptions.json", store, None)
    assert adoptions.linked_targets() == {}
    target = store.payload["targets"][0]
    key = row_key(target, "Lobby door (L) - BoB door", ["1"])
    adoptions._rows = {key: "segment:42"}
    assert adoptions.linked_targets() == {
        "segment:42": [{"index": 0, "label": "Lobby door (L) - BoB door"}]}


def _hundred_coin_target(label, rows):
    return {"entity_key": "star:4:6", "label": label, "section": "4. Cool, Cool Mountain",
            "version": "jp", "subsections": [],
            "approaches": [{"name": name, "ids": list(ids)} for name, ids in rows]}


def test_every_worksheet_row_of_one_entity_files_under_a_slot_of_its_own():
    """Round 28, his ruling on the round-27 measurement: "fix all bugs and
    maximize compatibility with the sheet". Three shapes shared one slot:
    a 100-coin course's four routes each carry a "100 coin star Xcam" row;
    a target-named row on such a route cannot be Standard when four routes
    claim it; and BitDW reds carries FIVE "Red coin star Xcam" rows, one per
    pipe route. `sheet_strategy` is the one rule both doors read."""
    slide = _hundred_coin_target("Slip Slidin' Away + 100c", [
        ("Slip Slidin' Away + 100c", ["1", "2"]),
        ("100 coin star Xcam", ["1", "2"])])
    no_tp = _hundred_coin_target("Slide + 100c No teleporter route", [
        ("Slide + 100c No teleporter route", ["1", "2"]),
        ("100 coin star Xcam", ["1", "2"])])
    # The route's own row is the route, not "Standard" (four routes, one entity).
    assert ad.sheet_strategy(slide, slide["approaches"][0]) == "Slip Slidin' Away + 100c"
    # A sub-row is qualified by its route, so two courses' xcam rows differ.
    assert ad.sheet_strategy(slide, slide["approaches"][1]) == (
        "Slip Slidin' Away + 100c › 100 coin star Xcam")
    assert ad.sheet_strategy(no_tp, no_tp["approaches"][1]) == (
        "Slide + 100c No teleporter route › 100 coin star Xcam")

    bitdw = {"entity_key": "star:16:0", "label": "Bowser in the Dark World Red Coins",
             "section": "Bowser Courses", "version": "jp", "subsections": [],
             "approaches": [
                 {"name": "Bowser in the Dark World Red Coins", "ids": ["1", "2"]},
                 {"name": "Red coin star Xcam", "ids": ["1", "2"]},
                 {"name": "Shigeru w/ island red first pipe entry", "ids": ["3", "4"]},
                 {"name": "Red coin star Xcam", "ids": ["3", "4"]},
                 {"name": "Xiah cycle pipe entry", "ids": ["5", "6"]},
                 # The sheet's own bracket typo: [4|6] under Xiah's [5|6].
                 {"name": "Red coin star Xcam", "ids": ["4", "6"]}]}
    names = [ad.sheet_strategy(bitdw, item) for item in bitdw["approaches"]]
    assert names == [
        "Standard", "Standard › Red coin star Xcam",
        "Shigeru w/ island red first pipe entry",
        "Shigeru w/ island red first pipe entry › Red coin star Xcam",
        "Xiah cycle pipe entry",
        "Xiah cycle pipe entry › Red coin star Xcam"]
    assert len(set(names)) == len(names), "every row must be its own slot"
    # A vetted match still names an ordinary strategy row, and a piece is
    # always its segment's Standard.
    matched = {"name": "Log firsty", "ids": ["3"], "matched_strategy": "Log Firsty"}
    plain = {"entity_key": "star:12:1", "label": "Mystery of the Monkey Cage",
             "approaches": [{"name": "Mystery of the Monkey Cage", "ids": ["1"]}, matched]}
    assert ad.sheet_strategy(plain, matched) == "Log Firsty"
    assert ad.sheet_strategy(plain, plain["approaches"][0]) == "Standard"
    assert ad.sheet_strategy(plain, {"name": "Post pipe", "ids": ["1"]},
                             kind="subsection") == "Standard"


def test_the_two_doors_read_the_same_naming_rule():
    """A second copy of the slot rule in either door is the divergence this
    round fixed (the export had an older two-argument rule and fell back
    blind on a 100-coin route's own row). Both modules must call the shared
    function and neither may re-derive it."""
    import inspect

    from sm64_events.library import export_column, import_runner
    for module in (export_column, import_runner):
        source = inspect.getsource(module)
        assert "sheet_strategy(" in source, module.__name__
        assert "strategy_name(" not in source.replace("sheet_strategy(", ""), (
            f"{module.__name__} must not derive a row's slot on its own")


def _star_payload():
    """A star target with three approaches, as the bundled library shapes
    them: the star's own row (matched by the ladder matcher to a vetted
    twin, filed under Standard), a row wearing a vetted twin's name, a row
    with no match and a fitted ladder, and one too thin to fit."""
    def approach(name, ladder=True, matched=None):
        item = {"ids": ["1"], "name": name, "best_cs": 1126, "best_runner": "W",
                "times": {}, "ideal_cs": None, "fill_rate": 0.5,
                "ladder": {"Mario": 11.36, "Bronze": 14.95} if ladder else None,
                "ladder_samples": 40 if ladder else 3, "entries": []}
        if matched:
            item["matched_strategy"] = matched
        return item
    return {"schema_version": 2, "sheet_revision": "2026-09-05T00:00:00",
            "fetched_at": "x", "runners": [], "ladder_model": {}, "targets": [
                {"entity_key": "star:6:4", "group": "6. Hazy Maze Cave",
                 "section": "6. Hazy Maze Cave", "label": "A-Maze-ing Emergency Exit",
                 "version": None, "miss_reason": None, "subsections": [],
                 "approaches": [approach("A-Maze-ing Emergency Exit", matched="Rightside"),
                                approach("Left side TJ", matched="Leftside"),
                                approach("Chimney hop"),
                                approach("Thin one", ladder=False)]},
                {"entity_key": "star:3:6", "group": "3. Cool, Cool Mountain",
                 "section": "3. Cool, Cool Mountain", "label": "Big Penguin Race + 100c",
                 "version": None, "miss_reason": None, "subsections": [],
                 "approaches": [approach("Big Penguin Race + 100c")]}]}


def test_the_whole_library_reaches_the_sheet_layer_under_the_import_slots():
    """Round 33: "for a lot of the 'Standard' times, we are lacking rank
    standards." The star's own row -- matched to a vetted twin by ladder and
    so SKIPPED by `adoptable` -- is the row round 28 files under Standard, so
    Standard had his PB and no ladder. `library_ladders` maps every fitted
    star approach under `sheet_strategy`'s slot: the star row is Standard, a
    matched row wears its twin's name, an unmatched row its own; a thin row
    contributes nothing; a variant-qualified entity is skipped."""
    ladders = ad.library_ladders(_star_payload(), {}, qualified={"star:3:6"})
    assert set(ladders) == {"star:6:4", "star:3:6"}
    assert "Big Penguin Race + 100c" in ladders["star:3:6"]["strategies"]
    assert set(ladders["star:6:4"]["strategies"]) == {"Standard", "Leftside", "Chimney hop"}
    assert ladders["star:6:4"]["strategies"]["Standard"]["Mario"] == 11.36


def test_a_loaded_store_grades_standard_from_the_star_row(tmp_path):
    """`Adoptions.load()` applies the whole library's ladders, so a fresh
    standards store grades Standard on a star whose vetted seed never
    defined it. A manually edited twin keeps its edit over the same fit."""
    store = LibraryStore()
    store._payload = _star_payload()
    standards = RankStandards(tmp_path / "rank_standards.json")
    standards.load()
    standards.create_strategy("star:6:4", "Leftside")
    standards.set_threshold("star:6:4", "Leftside", "Mario", 11.00)
    adoptions = ad.Adoptions(tmp_path / "library_adoptions.json", store, standards)
    adoptions.load()
    ladders = standards.ladders("star:6:4")
    assert ladders["Standard"]["Mario"] == 11.36 and standards.is_fitted("star:6:4", "Standard")
    assert ladders["Leftside"] == {"Mario": 11.00, "Bronze": 14.95}
    assert standards.is_fitted("star:6:4", "Leftside")
