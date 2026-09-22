"""The scorecard router, over HTTP -- the one goal KV and the resolved card.

`ranks/scorecard.py`'s own build logic (row shape, folding, Sigma math) is
`tests/test_scorecard.py`'s job; this file is only what the router adds on
top: reading/writing the KV, assembling `you`/`goal`/`fold` from the real
db + standards, and the coverage/pending flags the UI reads.
"""
import threading

import pytest

from import_fixture import make_client
from sm64_events.library.export_column import sheet_time
from sm64_events.ranks.classify import display_cs
from sm64_events.ranks.scorecard import SECRET_LABEL

# Every test builds its own client over its own tmp db, so the cases are
# independent; as one worker group this file was the merge check's long
# pole (429 s serial, 2026-09-21). `spread` lets each case take any free worker.
pytestmark = pytest.mark.spread


def test_goal_round_trip(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "division", "tier": "Gold",
                                    "division": "I"})
        assert response.status_code == 200

        card = client.get("/api/scorecard").json()
        assert card["goal"] == {"kind": "division", "tier": "Gold", "division": "I"}
        assert len(card["rows"]) == 17                     # 15 courses + Secret + Bowser
        assert any(tile["goal_cs"] for row in card["rows"] for tile in row["tiles"])
        assert card["goal_coverage"]["tiles"] == sum(
            len(row["tiles"]) for row in card["rows"])
        assert card["goal_coverage"]["covered"] > 0
        assert "goal_pending" not in card


def test_capless_floor_is_refused(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "division", "tier": "Iron",
                                    "division": "V"})
        assert response.status_code == 422


def test_an_unknown_division_is_refused(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "division", "tier": "Gold",
                                    "division": "VI"})
        assert response.status_code == 422


def test_an_unknown_goal_kind_is_refused(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal", json={"kind": "sponsor"})
        assert response.status_code == 422


def test_null_restores_the_automatic_goal(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        client.put("/api/scorecard/goal",
                   json={"kind": "division", "tier": "Gold", "division": "I"})
        response = client.put("/api/scorecard/goal", json=None)
        assert response.status_code == 200
        assert client.get("/api/scorecard").json()["goal"]["kind"] == "automatic"


def test_unset_goal_compares_your_times_with_the_automatic_goal(tmp_path):
    """A saved PB is compared immediately, even before choosing a goal."""
    with make_client(tmp_path) as (client, db, _svc):
        client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard", "time_cs": 886})

        card = client.get("/api/scorecard").json()
        assert card["goal"]["kind"] == "automatic"
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        tile = next(t for t in row["tiles"] if t["key"] == "star:1:0")
        pb_row = db.current_pb(1, 0, "igt")
        assert tile["you_cs"] == display_cs(pb_row["frames"])
        assert tile["goal_cs"] is not None
        assert tile["delta_cs"] == tile["you_cs"] - tile["goal_cs"]


def test_a_runner_without_times_keeps_the_automatic_rank_targets(tmp_path):
    """A runner nobody on the sheet is named -- the goal is accepted and
    resolves to an empty map, same as a division goal with no matching
    ladder anywhere. No `goal_pending` any more (task 6): the resolver is
    wired, so there is nothing left to be pending on."""
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal", json={
            "kind": "runner", "runner": "NobodyOnTheSheetIsNamedThis12345"})
        assert response.status_code == 200

        card = client.get("/api/scorecard").json()
        assert card["goal"]["sources"][1] == {"kind": "runner",
                                "runner": "NobodyOnTheSheetIsNamedThis12345"}
        assert "goal_pending" not in card
        assert any(tile["goal_cs"] for row in card["rows"] for tile in row["tiles"])
        assert card["goal"]["sources"][0]["kind"] == "automatic"


def test_a_runner_goal_resolves_against_the_sheet(tmp_path):
    """A real runner off the bundled Ultimate Sheet snapshot -- their sheet
    times become goal_cs on the entities they have one for, never on every
    tile (`library/ratings.py::runner_times`'s own absent-never-zero rule)."""
    from sm64_events.core.paths import bundled_sheet_library
    from sm64_events.library.ratings import runner_times
    from sm64_events.library.store import LibraryStore

    store = LibraryStore(bundled_path=bundled_sheet_library())
    store.load()
    times = runner_times(store.payload, {}, version="us")
    runner = next(name for name, by_entity in times.items()
                  if any(key.startswith("star:") for key in by_entity))

    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "runner", "runner": runner})
        assert response.status_code == 200

        card = client.get("/api/scorecard").json()
        assert card["goal"]["sources"][1] == {"kind": "runner", "runner": runner}
        assert "goal_pending" not in card
        tiles = [tile for row in card["rows"] for tile in row["tiles"]]
        assert any(tile["goal_cs"] is not None for tile in tiles)
        assert 0 < sum(t["player_goal_cs"] is not None for t in tiles) < len(tiles)
        assert card["goal_coverage"]["covered"] == len(tiles)


def _sheet_times(version):
    """{runner: {entity_key: time_cs}} off the BUNDLED snapshot, in one
    region -- the same reader the router grades a runner goal through, so an
    expectation here comes from today's shipped sheet rather than from a
    number copied out of a past run."""
    from sm64_events.core.paths import bundled_sheet_library
    from sm64_events.library.ratings import runner_times
    from sm64_events.library.store import LibraryStore

    store = LibraryStore(bundled_path=bundled_sheet_library())
    store.load()
    return runner_times(store.payload, {}, version=version)


def _runner_whose_regions_disagree():
    """A runner with a star time in BOTH regions where the two differ, plus
    the entity they differ on and the faster of the two. This is the only
    shape that can tell "both regions" apart from either single region --
    without it, a broken merge that silently kept one side would pass."""
    us_times, jp_times = _sheet_times("us"), _sheet_times("jp")
    for runner, by_entity in us_times.items():
        their_jp = jp_times.get(runner) or {}
        for key, us_cs in by_entity.items():
            jp_cs = their_jp.get(key)
            if jp_cs is not None and jp_cs != us_cs and key.startswith("star:"):
                return {"runner": runner, "key": key, "us": us_cs, "jp": jp_cs,
                        "faster": min(us_cs, jp_cs)}
    return None


def _tile(card, key):
    for row in card["rows"]:
        for tile in row["tiles"]:
            if tile["key"] == key:
                return tile
    return None


def test_the_scorecard_starts_on_the_detected_region(tmp_path):
    """Round 24, his words: "we should define their scorecard based on their
    detected region (e.g., US in my case). BUT it should be a deliberate
    choice." Untouched means FOLLOWING the detection, which is why the KV is
    absent rather than pre-seeded with ["us"] -- a mode flip still moves it."""
    with make_client(tmp_path) as (client, _db, service):
        card = client.get("/api/scorecard").json()
        assert card["regions"] == [service.ranks.grading_version]
        assert card["detected_region"] == service.ranks.grading_version

        service.ranks.grading_version = "jp"
        card = client.get("/api/scorecard").json()
        assert card["regions"] == ["jp"], (
            "an untouched scorecard must follow the detected region, not a "
            "region frozen at first read")
        assert card["detected_region"] == "jp"


def test_at_least_one_region_stays_on(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        assert client.put("/api/scorecard/regions",
                          json={"regions": []}).status_code == 422
        assert client.put("/api/scorecard/regions",
                          json={"regions": ["eu"]}).status_code == 422
        # ...and the refusal changed nothing.
        assert client.get("/api/scorecard").json()["regions"] == ["us"]


def test_a_deliberate_region_pick_survives_a_detection_change(tmp_path):
    with make_client(tmp_path) as (client, _db, service):
        assert client.put("/api/scorecard/regions",
                          json={"regions": ["us"]}).status_code == 200
        service.ranks.grading_version = "jp"
        assert client.get("/api/scorecard").json()["regions"] == ["us"], (
            "a region he picked on purpose must outlive a detection change")


def test_both_regions_take_the_faster_time_per_star(tmp_path):
    """His round-24 rule, verbatim: "When combined for the scorecard, we
    simply take the faster time across both regions." Proved on a star where
    the two regions actually DISAGREE, so a merge that quietly kept one side
    cannot pass -- each single-region reading is asserted too."""
    case = _runner_whose_regions_disagree()
    assert case, ("no runner in the bundled snapshot has two DIFFERENT region "
                  "times on one star -- this test's premise no longer holds")
    with make_client(tmp_path) as (client, _db, _svc):
        assert client.put("/api/scorecard/goal", json={
            "kind": "runner", "runner": case["runner"]}).status_code == 200

        for region in ("us", "jp"):
            assert client.put("/api/scorecard/regions",
                              json={"regions": [region]}).status_code == 200
            tile = _tile(client.get("/api/scorecard").json(), case["key"])
            assert tile is not None, case
            assert tile["goal_cs"] == case[region], (region, tile, case)

        assert client.put("/api/scorecard/regions",
                          json={"regions": ["us", "jp"]}).status_code == 200
        card = client.get("/api/scorecard").json()
        assert card["regions"] == ["us", "jp"]
        tile = _tile(card, case["key"])
        assert tile["goal_cs"] == case["faster"], (tile, case)


def test_a_runner_goal_needs_a_name(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal", json={"kind": "runner"})
        assert response.status_code == 422


def test_a_custom_goal_saves_and_resolves(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "Sub 90 Attempt",
            "times": {"star:1:0": 886, "star:1:1": 1500}})
        assert response.status_code == 200
        assert response.json()["goal"]["sources"][1] == {"kind": "custom", "name": "Sub 90 Attempt"}

        card = client.get("/api/scorecard").json()
        assert card["goal"]["sources"][1] == {"kind": "custom", "name": "Sub 90 Attempt"}
        assert card["custom_goals"] == ["Sub 90 Attempt"]
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        tiles = {tile["key"]: tile for tile in row["tiles"]}
        assert tiles["star:1:0"]["goal_cs"] == 886
        assert tiles["star:1:1"]["goal_cs"] == 1500
        assert tiles["star:1:2"]["goal_cs"] is not None  # Rank remains active.


def test_a_custom_goal_needs_a_name(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal", json={
            "kind": "custom", "times": {"star:1:0": 886}})
        assert response.status_code == 422


def test_picking_a_custom_goal_that_was_never_saved_is_refused(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "custom", "name": "Never Saved"})
        assert response.status_code == 404


def test_reselecting_a_saved_custom_goal_needs_no_times(tmp_path):
    """The picker sends `{kind,name}` alone to RE-SELECT a goal it already
    knows about -- the same shape a division/runner pick already uses -- and
    the server must still resolve it from what was saved earlier."""
    with make_client(tmp_path) as (client, _db, _svc):
        client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "Sub 90 Attempt", "times": {"star:1:0": 886}})
        client.put("/api/scorecard/goal", json={"kind": "division", "tier": "Gold", "division": "I"})

        response = client.put("/api/scorecard/goal",
                              json={"kind": "custom", "name": "Sub 90 Attempt"})
        assert response.status_code == 200

        card = client.get("/api/scorecard").json()
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        tile = next(t for t in row["tiles"] if t["key"] == "star:1:0")
        assert tile["goal_cs"] == 886


def test_saving_under_an_existing_name_overwrites_it(tmp_path):
    """His rule verbatim: 'If I modify a saved comparison, it should just
    overwrite it.'"""
    with make_client(tmp_path) as (client, _db, _svc):
        client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "Sub 90 Attempt", "times": {"star:1:0": 886}})
        client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "Sub 90 Attempt",
            "times": {"star:1:0": 700, "star:1:1": 1200}})

        card = client.get("/api/scorecard").json()
        assert card["custom_goals"] == ["Sub 90 Attempt"]   # still ONE entry
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        tiles = {tile["key"]: tile for tile in row["tiles"]}
        assert tiles["star:1:0"]["goal_cs"] == 700           # overwritten
        assert tiles["star:1:1"]["goal_cs"] == 1200           # newly added


def test_custom_goals_survive_switching_the_active_goal_away(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "Sub 90 Attempt", "times": {"star:1:0": 886}})
        client.put("/api/scorecard/goal",
                   json={"kind": "division", "tier": "Gold", "division": "I"})

        card = client.get("/api/scorecard").json()
        assert card["goal"] == {"kind": "division", "tier": "Gold", "division": "I"}
        assert card["custom_goals"] == ["Sub 90 Attempt"]


def test_a_custom_goal_resolves_with_no_ranks(tmp_path):
    """A custom goal is hand-typed data with no ladder lookup -- it must
    still grade on a broadcast-only instance, unlike a division or runner
    goal (which need `service.ranks`)."""
    from sm64_events.server.app import create_app
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.server.poller import Poller
    from sm64_events.storage.db import Database
    from sm64_events.tracking.service import TrackerService
    from fastapi.testclient import TestClient
    from import_fixture import OfflineMemory

    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    service = TrackerService(db, broadcaster)          # ranks=None
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service,
                     adoptions_path=tmp_path / "library_adoptions.json",
                     mode_path=tmp_path / "tracker_mode.json")
    with TestClient(app) as client:
        client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "Sub 90 Attempt", "times": {"star:1:0": 886}})
        card = client.get("/api/scorecard").json()
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        tile = next(t for t in row["tiles"] if t["key"] == "star:1:0")
        assert tile["goal_cs"] == 886


def test_the_100c_cell_is_combined_and_carries_the_100c_pb(tmp_path):
    """Round 6's cell rule end to end: BOB's row has SIX cells, no separate
    reds cell, and the combined "Find the 8 Red Coins + 100c" cell is the
    100c ENTITY -- a 100c PB lands on it."""
    with make_client(tmp_path) as (client, _db, _svc):
        client.post("/api/import/manual", json={
            "entity_key": "star:1:6", "strat_tag": "100c + Reds · Standard",
            "time_cs": 12000})

        card = client.get("/api/scorecard").json()
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        assert len(row["tiles"]) == 6
        keys = [tile["key"] for tile in row["tiles"]]
        assert "star:1:3" not in keys
        combined = next(t for t in row["tiles"] if t["key"] == "star:1:6")
        assert combined["label"] == "Find the 8 Red Coins + 100c"
        assert combined["you_cs"] is not None


def test_the_card_follows_a_route_scope(tmp_path):
    """Round 6: "whatever is in the scope is what we generate a scorecard
    for". A seeded route's scorecard is that route's own rows, not the
    120-star template; an unknown route 404s like /api/marelo."""
    with make_client(tmp_path) as (client, db, _svc):
        # This harness seeds no routes (reconcile_defaults is main.py's boot
        # step, not the service's), so insert a small 16-star-style one: one
        # BOB visit holding the reds star WITHOUT its 100c.
        route_id = db.insert_route("Sixteen-ish", [
            {"need": 2, "candidates": [
                {"type": "star", "course": 1, "star": 0},
                {"type": "star", "course": 1, "star": 3}]}],
            "2026-08-24T00:00:00Z")
        card = client.get(f"/api/scorecard?scope=route:{route_id}").json()
        assert card["scope"] == f"route:{route_id}"
        assert card_keys_of(card) == ["star:1:0", "star:1:3"]
        assert card["rows"][0]["label"] == "Bob-omb Battlefield"

        assert client.get("/api/scorecard?scope=route:99999").status_code == 404
        assert client.get("/api/scorecard?scope=garbage").status_code == 404


def test_the_card_follows_a_course_scope(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        card = client.get("/api/scorecard?scope=course:4").json()
        assert [row["label"] for row in card["rows"]] == ["Cool, Cool Mountain"]
        assert len(card["rows"][0]["tiles"]) == 6


def card_keys_of(card):
    return [tile["key"] for row in card["rows"] for tile in row["tiles"]]


def test_tiles_carry_no_rank_fields_since_the_caps_went(tmp_path):
    """Round 23 deleted the caps a line wore -- toggle, draw and the server's
    per-tile grading together ("Not going to use it ever"). A tile is the
    builder's own shape and nothing more; a fetch no longer grades 200
    tiles for an icon nobody can switch on."""
    with make_client(tmp_path) as (client, _db, _svc):
        client.put("/api/scorecard/goal", json={
            "kind": "division", "tier": "Bronze", "division": "V"})
        card = client.get("/api/scorecard").json()
        tile = card["rows"][0]["tiles"][0]
        assert {"key", "label", "clock", "you_cs", "goal_cs", "delta_cs",
                "strat"} <= set(tile), tile
        assert "you_rank" not in tile and "goal_rank" not in tile, tile


def test_the_card_ignores_the_same_segments_the_route_ranking_ignores(tmp_path):
    """Round 7 item 3: "By default, all segments should be ignored (other
    than Bowser segments / bowser fights, and other than the 100c
    segments)... This should match the route include/ignores logic."

    Driven through the REAL exclusion door on both sides: `Lakitu Skip` is
    default-excluded (no ranked category, no Bowser-entry seed key) and a
    Bowser course entry (`seg:bitdw-pipe`) is NOT, so a route step holding
    both must draw the entry's cell and drop Lakitu Skip's -- exactly what
    `/api/marelo` does with the same route."""
    with make_client(tmp_path) as (client, db, svc):
        by_name = {row["name"]: row for row in db.segment_defs()}
        skip = by_name["Lakitu Skip"]["id"]
        entry = next(row["id"] for row in db.segment_defs()
                     if row.get("seed_key") == "seg:bitdw-pipe")
        excluded = svc.rank_excluded()
        assert f"segment:{skip}" in excluded, "fixture assumption: Skip is ignored"
        assert f"segment:{entry}" not in excluded, "a Bowser entry ranks by default"

        route_id = db.insert_route("Mixed", [
            {"need": 2, "candidates": [
                {"type": "segment", "segment_id": skip},
                {"type": "segment", "segment_id": entry}]},
            {"need": 1, "candidates": [
                {"type": "segment", "segment_id": skip}]}],
            "2026-08-28T00:00:00Z")

        card = client.get(f"/api/scorecard?scope=route:{route_id}").json()
        assert card_keys_of(card) == [f"segment:{entry}"]
        assert len(card["rows"]) == 1, "the Skip-only step must draw no row"


def test_an_explicit_include_puts_a_segment_back_on_the_card(tmp_path):
    """The exclusion door is his to override — including a segment back into
    ranking puts its cell back on the card, since the card reads that same
    resolved set rather than the raw default."""
    with make_client(tmp_path) as (client, db, svc):
        skip = next(row["id"] for row in db.segment_defs()
                    if row["name"] == "Lakitu Skip")
        route_id = db.insert_route("Skip only", [
            {"need": 1, "candidates": [
                {"type": "segment", "segment_id": skip}]}],
            "2026-08-28T00:00:00Z")
        assert client.get(f"/api/scorecard?scope=route:{route_id}"
                          ).json()["rows"] == []

        client.post("/api/marelo/exclude",
                    json={"entity": f"segment:{skip}", "excluded": False})
        card = client.get(f"/api/scorecard?scope=route:{route_id}").json()
        assert card_keys_of(card) == [f"segment:{skip}"]


def test_scorecard_serves_broadcast_only_with_an_empty_goal_map(tmp_path):
    """`service.ranks` is None on a broadcast-only instance -- the card must
    still answer, just with nothing gradeable."""
    from sm64_events.server.app import create_app
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.server.poller import Poller
    from sm64_events.storage.db import Database
    from sm64_events.tracking.service import TrackerService
    from fastapi.testclient import TestClient
    from import_fixture import OfflineMemory

    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    service = TrackerService(db, broadcaster)          # ranks=None
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service,
                     adoptions_path=tmp_path / "library_adoptions.json",
                     mode_path=tmp_path / "tracker_mode.json")
    with TestClient(app) as client:
        response = client.get("/api/scorecard")
        assert response.status_code == 200
        card = response.json()
        assert card["goal_coverage"]["covered"] == 0


def test_all_three_db_touching_routes_answer_503_not_500_with_no_database(tmp_path):
    """`service.db is None` is the codebase's own definition of genuinely
    BROADCAST-ONLY (`tracking/service.py`'s header docstring) -- a real
    state: a second instance that lost the db lock. Every route that reads
    `service.db` must answer 503, never crash with an opaque 500, whether it
    reads it directly (`set_goal`) or through `current_card()` (the other
    two)."""
    from sm64_events.server.app import create_app
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.server.poller import Poller
    from sm64_events.tracking.service import TrackerService
    from fastapi.testclient import TestClient
    from import_fixture import OfflineMemory

    broadcaster = Broadcaster()
    service = TrackerService(None, broadcaster)         # db=None, ranks=None
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service,
                     adoptions_path=tmp_path / "library_adoptions.json",
                     mode_path=tmp_path / "tracker_mode.json")
    with TestClient(app) as client:
        get_response = client.get("/api/scorecard")
        assert get_response.status_code == 503
        assert get_response.json()["detail"]

        put_response = client.put("/api/scorecard/goal", json={
            "kind": "division", "tier": "Gold", "division": "I"})
        assert put_response.status_code == 503
        assert put_response.json()["detail"]

        csv_response = client.get("/api/scorecard/export.csv")
        assert csv_response.status_code == 503
        assert csv_response.json()["detail"]


def test_a_non_dict_goal_kv_serves_the_automatic_card(tmp_path):
    """A KV that is not the shape this store ever writes (corrupt, or from a
    schema this code has never seen) must read as absent rather than 500
    every route that touches it."""
    with make_client(tmp_path) as (client, db, _svc):
        db.set_state("scorecard_goal", "not a dict")
        card = client.get("/api/scorecard").json()
        assert card["goal"]["kind"] == "automatic"
        assert card["goal_coverage"]["covered"] > 0


def test_a_non_dict_custom_goal_store_serves_the_automatic_card(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        db.set_state("scorecard_custom_goals", ["not", "a", "dict"])
        card = client.get("/api/scorecard").json()
        assert card["goal"]["kind"] == "automatic"
        assert card["custom_goals"] == []


def _bob_workbook():
    """One target (Big Bob-omb on the Summit, opens at row 3) plus its grey
    subsection (row 4) -- row 2 is the section header, which `read_rows`
    never turns into a `SheetRow` at all."""
    from library_fixture import GREY, build_workbook
    from sm64_events.library import workbook as wb

    cells = {
        (1, 1): {"text": "Xcam IGT !"}, (1, 2): {"text": "Sheet Best"},
        (1, 3): {"text": "Player"}, (1, 4): {"text": "Ideal Run"},
        (1, 5): {"text": "Fill Rate"},
        (2, 1): {"text": "1. Bob-omb Battlefield"},
        (3, 1): {"text": "[1] Big Bob-omb on the Summit", "bold": True},
        (3, 2): {"text": "43.63"},
        (4, 1): {"text": "[1|2] Warp fadeout", "rgb": GREY},
        (4, 2): {"text": "15.90"},
    }
    return build_workbook({wb.SHEET_MAIN: cells,
                           wb.SHEET_LOG: {(1, 1): {"text": "46238.5"}}})


def test_the_column_endpoint_serves_one_line_per_worksheet_row(tmp_path, monkeypatch):
    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _bob_workbook)
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.get("/api/scorecard/column")
        assert response.status_code == 200
        payload = response.json()
        # Rows 2, 3, 4 -- the header row is not a worksheet data row at all,
        # so it never even reaches `read_rows`; row 4's last_row IS the count.
        assert payload["total_rows"] == 3 == len(payload["lines"])
        assert payload["sheet_revision"]
        assert isinstance(payload["mapped"], int)
        assert payload["mapped"] <= payload["total_rows"]


def test_the_column_job_reports_its_real_steps_and_ends_on_the_same_body(
        tmp_path, monkeypatch):
    """ROUND 26 item 1. The job door beside the synchronous GET, so a copy can
    say where it is: "We should show a status line that updates at every step
    of the process."

    Two claims. The steps are the export's OWN boundaries and must actually
    DIFFER as it runs -- a single message with a moving number would satisfy a
    weaker test while telling him nothing about which step he is on -- and the
    finished `result` must be byte-identical to what the synchronous door
    returns, because a second door onto one answer that can disagree with the
    first is the divergence this project has a rule against."""
    import time as _time

    released = threading.Event()

    def slow_workbook():
        # Held open so the poll below lands MID-JOB rather than racing it.
        released.wait(timeout=5)
        return _bob_workbook()

    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", slow_workbook)
    with make_client(tmp_path) as (client, _db, _svc):
        job_id = client.post("/api/scorecard/column").json()["job_id"]

        first = client.get(f"/api/scorecard/column/{job_id}").json()
        assert first["state"] == "running", first
        released.set()

        deadline = _time.monotonic() + 10
        # The first running status is an observation too. A fast worker can
        # finish before the next poll; discarding `first` made that a flake.
        seen, final = [first["message"]], None
        while _time.monotonic() < deadline:
            status = client.get(f"/api/scorecard/column/{job_id}").json()
            if not seen or seen[-1] != status["message"]:
                seen.append(status["message"])
            if status["state"] != "running":
                final = status
                break
            _time.sleep(0.02)

    assert final is not None, f"the job never finished; saw {seen}"
    assert final["state"] == "done", final
    assert len(seen) >= 2, f"the job reported one message for its whole run: {seen}"
    assert final["progress"] == 1.0
    # The closing sentence is what the UI shows him, so it carries the three
    # facts he asked about: how many rows, which worksheet rows they cover,
    # and how many carry a time.
    body = final["result"]
    assert str(body["total_rows"]) in final["message"], final["message"]
    assert str(body["total_rows"] + 1) in final["message"], final["message"]
    assert str(body["mapped"]) in final["message"], final["message"]

    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _bob_workbook)
    with make_client(tmp_path) as (client, _db, _svc):
        assert client.get("/api/scorecard/column").json()["lines"] == body["lines"]


def test_an_unknown_column_job_is_a_404(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        assert client.get("/api/scorecard/column/nope").status_code == 404


def test_the_column_lands_each_time_on_the_row_it_came_from(tmp_path, monkeypatch):
    """The paste anchor, end to end (round 19, item 3): he pastes into his
    own column's row-2 cell, so line 0 IS worksheet row 2. Bob-omb's target
    opens at row 3, which makes its time line 1 -- and line 0 stays empty
    because row 2 is a section header `read_rows` never emits."""
    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _bob_workbook)
    with make_client(tmp_path) as (client, db, _svc):
        db.insert_pb(1, 0, "Big Bob-omb on the Summit", "igt", 1324, None,
                     "2026-08-24T00:00:00Z")
        payload = client.get("/api/scorecard/column").json()
        # Row 2 is column A's section header -- no runner cell there -- so
        # the column opens with the legend's EMU cell (round 30 item 7); row
        # 3 holds a DATA row, so the N64 legend cell yields to the time.
        assert payload["lines"][0] == "EMU"
        assert payload["lines"][1] == sheet_time(display_cs(1324))   # row 3
        assert payload["lines"][2] == ""                      # row 4, unlinked
        assert payload["mapped"] == 1


def test_a_column_export_that_cannot_read_the_sheet_says_so(tmp_path, monkeypatch):
    """Mirrors `/api/import/sheet`'s own wording -- "could not read the
    sheet" and "you have nothing on it" must never look identical."""
    def boom(*_args, **_kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", boom)
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.get("/api/scorecard/column")
        assert response.status_code == 503
        assert "sheet" in response.json()["detail"]


def test_column_resolve_reads_your_pb_and_checks_an_explicit_version(tmp_path):
    """`_column_resolve` is the endpoint's `resolve` -- exercised directly
    (rather than through the HTTP door) because it is what actually reads
    the db, which `tests/test_export_column.py`'s stubs never do."""
    from sm64_events.server.scorecard_api import _column_resolve
    with make_client(tmp_path) as (_client, db, svc):
        db.insert_pb(1, 0, "Standard", "igt", 886, None, "2026-08-23T00:00:00Z")
        resolve = _column_resolve(svc)
        assert resolve("star:1:0", "Standard", "igt", None) == (display_cs(886), None)
        # No PB was ever set as JP, and the default grading version is US --
        # an explicitly-versioned JP row must not print this US time.
        assert resolve("star:1:0", "Standard", "igt", "jp") is None
        assert resolve("star:1:0", "Standard", "igt", "us") == (display_cs(886), None)
        assert resolve("star:1:0", "Other Strat", "igt", None) is None


def test_column_resolve_reads_a_segment_pb_on_its_own_clock(tmp_path):
    from sm64_events.server.scorecard_api import _column_resolve
    with make_client(tmp_path) as (_client, db, svc):
        piece = db.insert_segment_def("A Movement", [], [], [],
                                      "2026-08-23T00:00:00Z")
        db.insert_pb(None, None, "Standard", "rta", 476, None,
                     "2026-08-23T00:00:00Z", segment_id=piece)
        resolve = _column_resolve(svc)
        assert resolve(f"segment:{piece}", "Standard", "rta", None) == (display_cs(476), None)


def test_the_endpoints_placer_is_the_same_one_the_import_door_uses(tmp_path):
    """`scorecard_api.py` no longer carries its own copy of the placement
    rule -- it calls `import_api.py::sheet_row_placer` directly, so this is
    really exercising that shared function's name-match path (round 6, the
    same one an unlinked castle-movement row gets on import). Every fresh
    database seeds a "Lakitu Skip" segment (see `test_import_api.py`)."""
    from sm64_events.server.import_api import sheet_row_placer
    with make_client(tmp_path) as (_client, db, svc):
        lakitu_id = {d["seed_key"]: d["id"]
                    for d in db.segment_defs()}["seg:lakitu-skip"]
        place = sheet_row_placer(svc, None)
        placed = place({"section": "Castle Movements", "label": "Lakitu Skip",
                        "entity_key": None},
                       {"name": "Lakitu Skip", "ids": ["1"]}, "approach")
        assert placed[0] == f"segment:{lakitu_id}"


def test_the_endpoints_placer_lands_a_bowser_row_on_the_seeded_movement(tmp_path):
    """Same shared placer, its Bowser seed-key path: the sheet says
    `segment:6`, and it resolves by seed_key to whichever id THIS database
    holds for the BitFS pipe entry."""
    from sm64_events.server.import_api import sheet_row_placer
    with make_client(tmp_path) as (_client, db, svc):
        bitfs_id = {d["seed_key"]: d["id"]
                   for d in db.segment_defs()}["seg:bitfs-pipe"]
        place = sheet_row_placer(svc, None)
        placed = place({"section": "Bowser Courses",
                        "label": "Bowser in the Fire Sea Course",
                        "entity_key": "segment:6"},
                       {"name": "Bowser in the Fire Sea Course", "ids": ["1"]},
                       "approach")
        assert placed == (f"segment:{bitfs_id}", "rta", "Standard")


def _bob_omb_rows(strategy_row=False):
    """The Bob-omb target as one worksheet row plus its payload -- either the
    star's OWN row (named after the target) or a named-STRATEGY row under it.
    Round 25 treats the two differently, so both shapes are needed."""
    from sm64_events.library.sheet import SheetRow

    name = "Fast rollout" if strategy_row else "Big Bob-omb on the Summit"
    rows = [SheetRow(row=2, group="G", section="1. Bob-omb Battlefield",
                     label=name, ids=frozenset({"1"}),
                     kind="approach", opens_target=True, version=None,
                     best_cs=None, best_runner="", ideal_cs=None,
                     fill_rate=None)]
    payload = {"targets": [
        {"section": "1. Bob-omb Battlefield", "entity_key": "star:1:0",
         "label": "Big Bob-omb on the Summit",
         "approaches": [{"name": name, "ids": ["1"]}],
         "subsections": []}]}
    return rows, payload


def test_a_stars_own_row_prefers_the_time_filed_under_its_own_name(tmp_path):
    """ROUND 27. A star's own row asks for ITS OWN name first and only falls
    back to "your best on this star, however you set it".

    Both halves are his, a round apart, and the order is what serves both.
    Round 25: a column he PLAYED came back nearly empty, because his times
    are filed under the names HE picked -- so the blind fallback exists.
    Round 27: a column he IMPORTED must export back identically, and the
    import files a star row under that row's OWN name -- so the name is asked
    first. Measured over the live sheet through the real endpoints: with the
    blind ask alone, 104 of one runner's 601 filled rows exported the wrong
    number; asking by name first, 28.

    Here both exist, and the row's own slot must win. That slot is
    "Standard" -- a row named after the star IS its Standard strategy (his
    rule, 2026-09-02), and the import files it there."""
    from sm64_events.library.export_column import column_lines
    from sm64_events.server.import_api import sheet_row_placer
    from sm64_events.server.scorecard_api import _column_resolve

    with make_client(tmp_path) as (_client, db, svc):
        db.insert_pb(1, 0, "Standard", "igt", 1324, None,
                     "2026-08-24T00:00:00Z")
        db.insert_pb(1, 0, "Some other way round", "igt", 900, None,
                     "2026-08-24T00:00:00Z")
        rows, payload = _bob_omb_rows()
        place = sheet_row_placer(svc, None)
        lines = column_lines(rows, payload, _column_resolve(svc), place=place)
        assert lines == [sheet_time(display_cs(1324))], (
            "an imported star row must export the time it was imported with")


def test_a_stars_own_row_falls_back_to_his_best_when_the_name_has_nothing(tmp_path):
    """Round 25's half, kept: with NOTHING filed under the row's own name --
    the ordinary shape of a time he played, since he names his own strategies
    -- the star's row still carries his best on that star."""
    from sm64_events.library.export_column import column_lines
    from sm64_events.server.import_api import sheet_row_placer
    from sm64_events.server.scorecard_api import _column_resolve

    with make_client(tmp_path) as (_client, db, svc):
        db.insert_pb(1, 0, "Standard", "igt", 900, None,
                     "2026-08-24T00:00:00Z")
        rows, payload = _bob_omb_rows()
        place = sheet_row_placer(svc, None)
        lines = column_lines(rows, payload, _column_resolve(svc), place=place)
        assert lines == [sheet_time(display_cs(900))], (
            "a played time must still reach the star's own row")


def test_an_imported_strategy_row_exports_the_time_it_was_imported_with(tmp_path):
    """Round 19's finding, guarded where it now lives. A sheet row naming a
    STRATEGY is landed by `POST /api/import/sheet` under the sheet's own
    approach name, so this door asks for that same name and gets the same
    time back -- and a FASTER time set another way must NOT appear under it,
    which is what makes this test able to fail. Until round 19 the export
    refused the name half of the import's own rule and every line of a
    freshly imported column came back blank."""
    from sm64_events.library.export_column import column_lines
    from sm64_events.server.import_api import sheet_row_placer
    from sm64_events.server.scorecard_api import _column_resolve

    with make_client(tmp_path) as (_client, db, svc):
        db.insert_pb(1, 0, "Fast rollout", "igt", 1324, None,
                     "2026-08-24T00:00:00Z")
        db.insert_pb(1, 0, "Some other way round", "igt", 900, None,
                     "2026-08-24T00:00:00Z")
        rows, payload = _bob_omb_rows(strategy_row=True)
        place = sheet_row_placer(svc, None)
        lines = column_lines(rows, payload, _column_resolve(svc), place=place)
        assert lines == [sheet_time(display_cs(1324))]


def test_a_subsection_he_has_linked_exports_on_its_own_clock(tmp_path):
    """His ask, the subsection half: a piece exports once he has LINKED the
    sheet row to a movement he built, under that link's own strategy
    ("Standard" -- a subsection row names the piece, not a way of doing it)
    and on that piece's own clock. Unlinked it stays blank, which is exactly
    what the import does with the same row, through this same placer."""
    from sm64_events.library.audit import row_key
    from sm64_events.library.export_column import column_lines
    from sm64_events.library.sheet import SheetRow
    from sm64_events.server.import_api import sheet_row_placer
    from sm64_events.server.scorecard_api import _column_resolve

    class _Links:
        def __init__(self, rows):
            self._rows = rows

        def rows(self):
            return dict(self._rows)

    with make_client(tmp_path) as (_client, db, svc):
        piece = db.insert_segment_def("Warp fadeout", [], [], [],
                                      "2026-08-24T00:00:00Z")
        db.insert_pb(None, None, "Standard", "rta", 477, None,
                     "2026-08-24T00:00:00Z", segment_id=piece)
        target = {"section": "1. Bob-omb Battlefield", "entity_key": "star:1:0",
                  "label": "Big Bob-omb on the Summit",
                  "approaches": [],
                  "subsections": [{"name": "Warp fadeout", "ids": ["1", "2"]}]}
        rows = [SheetRow(row=2, group="G", section="1. Bob-omb Battlefield",
                         label="Warp fadeout", ids=frozenset({"1", "2"}),
                         kind="subsection", opens_target=True, version=None,
                         best_cs=None, best_runner="", ideal_cs=None,
                         fill_rate=None)]
        payload = {"targets": [target]}
        resolve = _column_resolve(svc)

        unlinked = sheet_row_placer(svc, _Links({}))
        assert column_lines(rows, payload, resolve, place=unlinked) == [""]

        key = row_key(target, "Warp fadeout", ["1", "2"])
        linked = sheet_row_placer(svc, _Links({key: f"segment:{piece}"}))
        assert column_lines(rows, payload, resolve, place=linked) == [
            sheet_time(display_cs(477))]


def test_a_row_naming_a_strategy_this_database_never_heard_of_stays_blank(
        tmp_path):
    """The other half of that fallback, and why it is safe: the name is
    only ever a LOOKUP. A sheet approach nobody here has a PB under
    resolves to None and the line stays blank -- the export can print a
    missing time, never a wrong one."""
    from sm64_events.library.export_column import column_lines
    from sm64_events.library.sheet import SheetRow
    from sm64_events.server.scorecard_api import _column_resolve

    with make_client(tmp_path) as (_client, db, svc):
        db.insert_pb(1, 0, "Big Bob-omb on the Summit", "igt", 1324, None,
                     "2026-08-24T00:00:00Z")
        rows = [SheetRow(row=2, group="G", section="1. Bob-omb Battlefield",
                         label="Whomp fortress skip", ids=frozenset({"1"}),
                         kind="approach", opens_target=True, version=None,
                         best_cs=None, best_runner="", ideal_cs=None,
                         fill_rate=None)]
        payload = {"targets": [
            {"section": "1. Bob-omb Battlefield", "entity_key": "star:1:0",
             "label": "Big Bob-omb on the Summit",
             "approaches": [{"name": "Whomp fortress skip", "ids": ["1"]}],
             "subsections": []}]}
        lines = column_lines(rows, payload, _column_resolve(svc))
        assert lines == [""]


# --- CSV export ------------------------------------------------------------

def test_export_csv_headers_content_type_and_disposition(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.get("/api/scorecard/export.csv")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert response.headers["content-disposition"] == \
            'attachment; filename="scorecard.csv"'
        # RFC 4180 line endings -- every line, not just some (a mixed file
        # is the shape a naive text-mode write would produce).
        body = response.content
        assert b"\n" not in body.replace(b"\r\n", b"")
        lines = response.text.split("\r\n")
        assert lines[0] == "Course,Star,Record,Goal,You,Delta"


def test_export_csv_prints_a_known_row_and_its_signed_delta(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard", "time_cs": 886})
        client.put("/api/scorecard/goal",
                   json={"kind": "division", "tier": "Gold", "division": "I"})

        card = client.get("/api/scorecard").json()
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        tile = next(t for t in row["tiles"] if t["key"] == "star:1:0")
        assert tile["you_cs"] is not None and tile["goal_cs"] is not None, (
            "Gold I must grade star:1:0 in the bundled seed, or this test "
            "proves nothing about the You/Delta columns")

        lines = client.get("/api/scorecard/export.csv").text.split("\r\n")
        matching = [line for line in lines
                   if line.startswith(f"{row['label']},{tile['label']},")]
        assert len(matching) == 1, lines
        cells = matching[0].split(",")
        assert cells[4] == sheet_time(tile["you_cs"])          # You
        sign = "-" if tile["delta_cs"] < 0 else "+"
        assert cells[5] == f"{sign}{sheet_time(abs(tile['delta_cs']))}"  # Delta


def test_export_csv_carries_a_stage_rta_row_per_course_and_one_upstairs_row(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        card = client.get("/api/scorecard").json()
        lines = [line for line in
                client.get("/api/scorecard/export.csv").text.split("\r\n")
                if line]

        expected = 1                                            # header
        for row in card["rows"]:
            expected += len(row["tiles"])
            if row["course_id"] is not None:
                expected += 1                                    # Stage RTA
        expected += 1                                             # Upstairs RTA
        assert len(lines) == expected

        course_row = next(r for r in card["rows"] if r["course_id"] == 1)
        assert f"{course_row['label']},Stage RTA," in "\n".join(lines)
        assert any(line.startswith(",Upstairs RTA,") for line in lines)


def test_export_csv_serves_broadcast_only_with_no_record_column(tmp_path):
    """No `service.ranks` -> no ladder to grade on, same as `GET
    /api/scorecard` -- the export must still answer rather than 500."""
    from sm64_events.server.app import create_app
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.server.poller import Poller
    from sm64_events.storage.db import Database
    from sm64_events.tracking.service import TrackerService
    from fastapi.testclient import TestClient
    from import_fixture import OfflineMemory

    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    service = TrackerService(db, broadcaster)          # ranks=None
    poller = Poller(OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service,
                     adoptions_path=tmp_path / "library_adoptions.json",
                     mode_path=tmp_path / "tracker_mode.json")
    with TestClient(app) as client:
        response = client.get("/api/scorecard/export.csv")
        assert response.status_code == 200
        assert response.text.split("\r\n")[0] == "Course,Star,Record,Goal,You,Delta"


def test_a_saved_custom_goal_snaps_every_time_onto_the_displayable_set(tmp_path):
    """Round 5 (2026-08-24): the server snaps too (core/timefmt.attainable_cs,
    the import's own door), so a hand-posted payload cannot store a
    centisecond the timer can never show -- the field already displays the
    snapped value, and the store must agree with every cell the UI draws."""
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "snap check",
            "times": {"star:1:0": 5101, "star:1:1": 5100}})
        assert response.status_code == 200
        card = client.get("/api/scorecard").json()
        tiles = {tile["key"]: tile
                 for row in card["rows"] for tile in row["tiles"]}
        assert tiles["star:1:0"]["goal_cs"] == 5103   # 51"01 unattainable at 30fps
        assert tiles["star:1:1"]["goal_cs"] == 5100   # already displayable: unmoved


def test_castle_stars_are_off_the_card_until_included(tmp_path):
    """His round-11 ruling: "nobody wants to track [Toad/MIPS]... ignore
    these by default, too. If the user includes them manually, then it
    should be included in the secret section." Same one exclusion door as
    everything else -- an include reaches the Secret card AND the rating."""
    with make_client(tmp_path) as (client, _db, _svc):
        card = client.get("/api/scorecard").json()
        keys = card_keys_of(card)
        for star_id in range(5):
            assert f"star:0:{star_id}" not in keys

        client.post("/api/marelo/exclude",
                    json={"entity": "star:0:0", "excluded": False})
        card = client.get("/api/scorecard").json()
        secret = next(row for row in card["rows"]
                      if row["label"] == SECRET_LABEL)
        secret_keys = [tile["key"] for tile in secret["tiles"]]
        assert "star:0:0" in secret_keys, (
            "an included castle star must land back in the Secret card")
        assert "star:0:1" not in secret_keys, "the include is per star"


def test_a_multi_goal_takes_the_fastest_offer_and_unions_coverage(tmp_path):
    """Round 16, correcting round 14's reading of "MAX TIME": "I meant we
    should take the FASTEST time from all of the runners / all of the
    options provided... we collect all of the options' fastest times, and
    select the fastest time from all of them." Coverage is still the union
    ("if you are tracking multiple different runners, then you should have
    100% coverage across all stars").

    Two saved custom goals stand in for two runners: a custom source
    resolves with no ladder or sheet lookup, so the MERGE is the only thing
    this test can fail on."""
    with make_client(tmp_path) as (client, _db, _svc):
        # A: FASTER on star:1:0, silent on star:1:1
        client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "A", "times": {"star:1:0": 4000}})
        # B: slower on star:1:0, and the only one covering star:1:1
        client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "B",
            "times": {"star:1:0": 4500, "star:1:1": 6000}})

        response = client.put("/api/scorecard/goal", json={
            "kind": "multi",
            "sources": [{"kind": "custom", "name": "A"},
                        {"kind": "custom", "name": "B"}]})
        assert response.status_code == 200
        assert response.json()["goal"] == {
            "kind": "multi",
            "sources": [{"kind": "automatic"}, {"kind": "custom", "name": "A"},
                        {"kind": "custom", "name": "B"}]}

        card = client.get("/api/scorecard").json()
        tiles = {tile["key"]: tile
                 for row in card["rows"] for tile in row["tiles"]}
        # the FASTEST offer wins -- the best anybody you picked has done
        assert tiles["star:1:0"]["goal_cs"] == 4000
        # ...and a star only ONE source covers is still covered
        assert tiles["star:1:1"]["goal_cs"] == 6000


def test_a_multi_goal_attributes_every_tile_to_the_source_that_set_it(tmp_path):
    """Round 15: "that clearly tells us that player two is the reason that
    the goal is that time." The winner -- the FASTEST offer since round 16
    -- is recorded where the comparison happens, so the card's coloured dot
    and the number beside it cannot disagree. Ties keep the EARLIER source,
    so attribution never depends on iteration order."""
    with make_client(tmp_path) as (client, _db, _svc):
        client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "one",
            "times": {"star:1:0": 4500, "star:1:1": 7000, "star:1:2": 1000}})
        client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "two",
            "times": {"star:1:0": 4000, "star:1:2": 1000}})
        client.put("/api/scorecard/goal", json={
            "kind": "multi",
            "sources": [{"kind": "custom", "name": "one"},
                        {"kind": "custom", "name": "two"}]})

        tiles = {tile["key"]: tile
                 for row in client.get("/api/scorecard").json()["rows"]
                 for tile in row["tiles"]}
        assert tiles["star:1:0"]["goal_source"] == 2     # "two" is FASTER here
        assert tiles["star:1:1"]["goal_source"] == 1     # only "one" covers it
        assert tiles["star:1:2"]["goal_source"] == 1     # tie -> the earlier pick
        # The rank covers a tile neither custom set names.
        assert tiles["star:1:4"]["goal_cs"] is not None
        assert tiles["star:1:4"]["goal_source"] == 0


def test_a_single_goal_attributes_nothing(tmp_path):
    """One source needs no legend, so every tile's attribution is null --
    the card draws dots only when there is something to tell apart."""
    with make_client(tmp_path) as (client, _db, _svc):
        client.put("/api/scorecard/goal", json={
            "kind": "division", "tier": "Bronze", "division": "I"})
        tiles = [tile for row in client.get("/api/scorecard").json()["rows"]
                 for tile in row["tiles"]]
        assert any(tile["goal_cs"] is not None for tile in tiles)
        assert all(tile["goal_source"] is None for tile in tiles)


def test_a_multi_goal_validates_every_source_like_a_single_one(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        assert client.put("/api/scorecard/goal", json={
            "kind": "multi",
            "sources": [{"kind": "division", "tier": "Iron", "division": "V"}],
        }).status_code == 422
        assert client.put("/api/scorecard/goal", json={
            "kind": "multi",
            "sources": [{"kind": "custom", "name": "never saved"}],
        }).status_code == 404
        assert client.put("/api/scorecard/goal", json={
            "kind": "multi", "sources": [{"kind": "nonsense"}],
        }).status_code == 422


def test_a_multi_goal_mixes_a_division_with_a_per_entity_source(tmp_path):
    """The mixed pick he named -- "10 players plus a rank standard like
    Toad 1" -- where the division COVERS the stars a per-entity source
    misses, while a faster per-entity time wins wherever it exists (round
    16: the fastest offer, not the slowest)."""
    with make_client(tmp_path) as (client, _db, _svc):
        client.put("/api/scorecard/goal", json={
            "kind": "custom", "name": "fast one",
            "times": {"star:1:0": 100}})       # 1.00s: faster than any tier
        client.put("/api/scorecard/goal", json={
            "kind": "multi",
            "sources": [{"kind": "custom", "name": "fast one"},
                        {"kind": "division", "tier": "Bronze", "division": "I"}]})
        mixed = {tile["key"]: tile
                 for row in client.get("/api/scorecard").json()["rows"]
                 for tile in row["tiles"]}

        client.put("/api/scorecard/goal", json={
            "kind": "division", "tier": "Bronze", "division": "I"})
        alone = {tile["key"]: tile
                 for row in client.get("/api/scorecard").json()["rows"]
                 for tile in row["tiles"]}

        # the faster per-entity time wins its own star...
        assert mixed["star:1:0"]["goal_cs"] == 100
        assert mixed["star:1:0"]["goal_source"] == 1
        # ...and the division still covers every star it alone reaches
        other = next(key for key, tile in alone.items()
                     if key != "star:1:0" and tile["goal_cs"] is not None)
        assert mixed[other]["goal_cs"] == alone[other]["goal_cs"]
        assert mixed[other]["goal_source"] == 0


def test_a_runner_goal_walks_the_sheet_once_and_reuses_it_until_something_moves(tmp_path, monkeypatch):
    """`runner_times` walks EVERY target, approach, subsection and entry on
    the sheet and returns every runner's map, of which a runner goal keeps
    one. Measured 2026-09-01 on a snapshot of his own db through the real
    endpoint: 7.6 ms with no goal, 23.8 ms against one runner, 49.6 ms
    against his stored goal (a division + three runners) and 68.9 ms against
    four runners -- a multi goal walked the sheet once per runner SOURCE,
    and since round 20 that fetch runs on every completed attempt, on the
    same process as the 30 fps poller. So: one walk per (sheet payload,
    adoptions, grading version), reused across sources AND across fetches,
    and thrown away the moment any of the three moves."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from import_fixture import bundled_standards_seed
    from sm64_events.core.paths import bundled_sheet_library
    from sm64_events.library.store import LibraryStore
    from sm64_events.ranks.standards import RankStandards
    from sm64_events.server import scorecard_api
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.storage.db import Database
    from sm64_events.tracking.service import TrackerService

    store = LibraryStore(bundled_path=bundled_sheet_library()); store.load()
    ranks = RankStandards(tmp_path / "rs.json", seed_path=bundled_standards_seed()); ranks.load()
    service = TrackerService(Database(tmp_path / "t.db"), Broadcaster(), ranks=ranks)

    class StubAdoptions:
        def __init__(self): self.linked = {}
        def rows(self): return dict(self.linked)
    adoptions = StubAdoptions()

    walks = []
    real_runner_times = scorecard_api.runner_times
    def counting_runner_times(*args, **kwargs):
        walks.append(kwargs.get("version"))
        return real_runner_times(*args, **kwargs)
    monkeypatch.setattr(scorecard_api, "runner_times", counting_runner_times)

    app = FastAPI()
    app.include_router(scorecard_api.create_scorecard_router(
        service, library=store, adoptions=adoptions))
    with TestClient(app) as client:
        client.put("/api/scorecard/goal", json={"kind": "multi", "sources": [
            {"kind": "runner", "runner": "ikori"}, {"kind": "runner", "runner": "RONC3NA"},
            {"kind": "runner", "runner": "Suigi"}]})
        first = client.get("/api/scorecard").json()
        assert walks == ["us"], "three runner sources are ONE walk"
        again = client.get("/api/scorecard").json()
        assert walks == ["us"], "nothing moved, so nothing is walked again"
        assert again["rows"] == first["rows"]
        ranks.grading_version = "jp"
        client.get("/api/scorecard")
        assert walks == ["us", "jp"], "the grading version is part of the key"
        adoptions.linked["some-row"] = "segment:1"
        client.get("/api/scorecard")
        assert len(walks) == 3, "a new adoption is a new key"
        client.get("/api/scorecard")
        assert len(walks) == 3


def test_column_resolve_answers_the_leftovers_ask_with_the_fastest_unclaimed_pb(tmp_path):
    """Round 28: `strat_tag=None` with `excluding` is the star's own row
    asking for whatever the block's other rows do NOT claim -- the fastest
    of those, on the row's ROM. A PB with no strategy is unclaimed by every
    row; a PB set on the other ROM is never an answer for a versioned row."""
    from sm64_events.server.scorecard_api import _column_resolve
    with make_client(tmp_path) as (_client, db, svc):
        db.insert_pb(1, 0, "Left side clip", "igt", 560, None, "2026-09-04T00:00:00Z")
        db.insert_pb(1, 0, "Backflip WK", "igt", 640, None, "2026-09-04T00:00:01Z")
        db.insert_pb(1, 0, None, "igt", 700, None, "2026-09-04T00:00:02Z")
        db.insert_pb(1, 0, "Log firsty", "igt", 500, None, "2026-09-04T00:00:03Z",
                     game_version="jp")
        resolve = _column_resolve(svc)
        # Every named sibling claimed: the strategy-less time is what is left.
        assert resolve("star:1:0", None, "igt", None,
                       excluding={"Left side clip", "Backflip WK", "Log firsty"}
                       ) == (display_cs(700), None)
        # One sibling unclaimed: his own pick wins over the strategy-less one.
        assert resolve("star:1:0", None, "igt", None,
                       excluding={"Left side clip", "Log firsty"}) == (display_cs(640), None)
        # On the US row the JP time is not an answer, however fast.
        assert resolve("star:1:0", None, "igt", "us", excluding=set()) == (display_cs(560), None)
        assert resolve("star:1:0", None, "igt", "jp", excluding=set()) == (display_cs(500), None)
        # Nothing left over: blank, never a sibling's time.
        assert resolve("star:1:0", None, "igt", "jp",
                       excluding={"Log firsty"}) is None


def test_the_held_lookup_matches_a_versioned_row_to_its_own_rom(tmp_path):
    from sm64_events.server.scorecard_api import _held_lookup
    with make_client(tmp_path) as (_client, db, svc):
        db.hold_times("sheet:Raisn", [
            {"row_key": "piece", "game_version": "jp", "time_cs": 2683, "reason": "subsections"},
            {"row_key": "piece", "game_version": "us", "time_cs": 2850, "reason": "subsections"},
            {"row_key": "door", "game_version": "jp", "time_cs": 390, "reason": "no_entity"},
        ], "2026-09-04T00:00:00Z")
        held = _held_lookup(svc)
        assert held("piece", "jp") == (2683, None)
        assert held("piece", "us") == (2850, None)
        # A JP-stamped target's rows carry no version of their own but their
        # cells do: an unversioned row takes what the row holds.
        assert held("door", None) == (390, None)
        assert held("piece", None) in ((2683, None), (2850, None))
        assert held("nowhere", None) is None


def test_column_resolve_answers_the_pbs_own_platform(tmp_path):
    """The stamp reaches the column through the PB's attempt (main's v27):
    an imported time whose source named the console resolves as
    `(cs, "n64")`; a PB with no attempt behind it resolves with None, which
    `_column_body` turns into the emulator once."""
    import asyncio

    from sm64_events.server.scorecard_api import _column_resolve
    from sm64_events.tracking.importing import ImportCandidate
    with make_client(tmp_path) as (_client, db, svc):
        asyncio.run(svc.import_times("sheet:Raisn", [ImportCandidate(
            entity_key="star:1:0", strat_tag="Standard", time_cs=886,
            platform="n64")]))
        db.insert_pb(1, 1, "Standard", "igt", 900, None, "2026-08-23T00:00:00Z")
        resolve = _column_resolve(svc)
        # 886 cs lands as frames and reads back as the displayable 886.
        assert resolve("star:1:0", "Standard", "igt", None) == (886, "n64")
        assert resolve("star:1:1", "Standard", "igt", None) == (display_cs(900), None)


def test_the_column_body_pairs_every_line_with_its_platform(tmp_path, monkeypatch):
    """`cells` is `lines` with the machine beside each time: a timed cell
    always names emu or n64 (an unstamped PB resolves to the emulator HERE,
    once), an empty cell names nothing."""
    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _bob_workbook)
    with make_client(tmp_path) as (client, db, _svc):
        db.insert_pb(1, 0, "Big Bob-omb on the Summit", "igt", 886, None,
                     "2026-08-23T00:00:00Z")
        body = client.get("/api/scorecard/column").json()
        assert [cell["text"] for cell in body["cells"]] == body["lines"]
        # Round 30 item 7: the column OPENS with the legend -- on the live
        # sheet worksheet rows 2 and 3 are a section header and a blank, so
        # both cells land (tools/roundtrip_sheet.py shows them). This stub
        # puts its TARGET on row 3, so the N64 cell yields to the time: a
        # legend never prints over a data row.
        assert body["cells"][0] == {"text": "EMU", "platform": "emu", "legend": True}
        assert body["cells"][1]["text"] and "legend" not in body["cells"][1]
        timed = [cell for cell in body["cells"] if cell["text"] and not cell.get("legend")]
        assert timed and all(cell["platform"] == "emu" for cell in timed), body["cells"]
        assert body["mapped"] == len(timed), "the legend cells are not times"
        assert all(cell["platform"] is None for cell in body["cells"] if not cell["text"])


def test_column_http_response_keeps_selected_attempts_public_recording(tmp_path, monkeypatch):
    import asyncio
    from sm64_events.tracking.importing import ImportCandidate

    url = "https://youtu.be/abcdefghijk?t=12&feature=shared"
    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _bob_workbook)
    with make_client(tmp_path) as (client, _db, svc):
        asyncio.run(svc.import_times("manual", [ImportCandidate(
            "star:1:0", "Standard", 886, video=url)]))
        response = client.get("/api/scorecard/column")
        assert response.status_code == 200
        linked = [cell for cell in response.json()["cells"] if cell.get("video")]
        assert linked == [{"text": "8.86", "platform": "emu", "video": url}]
        asyncio.run(svc.import_times("manual", [ImportCandidate(
            "star:1:0", "Standard", 800)]))
        response = client.get("/api/scorecard/column")
        assert not any(cell.get("video") for cell in response.json()["cells"])


def test_the_sheet_style_is_one_stored_preference_over_its_defaults(tmp_path):
    """Round 29 item 2: three colours and a font, read as defaults until he
    sets them, validated as #RRGGBB and a plain font name (it is written
    straight into an inline style), stored upper-cased, and handed back
    beside the defaults so Reset knows where to go."""
    from sm64_events.server.scorecard_api import DEFAULT_SHEET_STYLE
    with make_client(tmp_path) as (client, _db, _svc):
        fresh = client.get("/api/scorecard/sheet_style").json()
        assert fresh == {"style": DEFAULT_SHEET_STYLE, "defaults": DEFAULT_SHEET_STYLE}

        picked = {"emu_fill": "#a5a9f1", "n64_fill": "#FF6D01",
                  "font_color": "#000000", "font_family": "Roboto Mono"}
        saved = client.put("/api/scorecard/sheet_style", json=picked)
        assert saved.status_code == 200
        assert saved.json()["style"] == {**picked, "emu_fill": "#A5A9F1"}
        assert client.get("/api/scorecard/sheet_style").json()["style"] == {
            **picked, "emu_fill": "#A5A9F1"}

        bad_colour = client.put("/api/scorecard/sheet_style",
                                json={**picked, "n64_fill": "orange"})
        assert bad_colour.status_code == 422 and "n64_fill" in bad_colour.json()["detail"]
        bad_font = client.put("/api/scorecard/sheet_style",
                              json={**picked, "font_family": 'x"; color: red'})
        assert bad_font.status_code == 422
        # a refused write changes nothing
        assert client.get("/api/scorecard/sheet_style").json()["style"]["n64_fill"] == "#FF6D01"


def test_a_held_cell_prints_back_with_the_platform_its_legend_named(tmp_path):
    """The hold carries the platform (round 29 item 2), the lookup answers
    the pair, and the column body resolves it like any PB's -- so a held N64
    cell paints orange rather than falling to the emulator default."""
    from sm64_events.server.scorecard_api import _held_lookup
    with make_client(tmp_path) as (_client, db, svc):
        db.hold_times("sheet:Raisn", [
            {"row_key": "row:a", "game_version": None, "time_cs": 923,
             "reason": "no_entity", "platform": "n64"},
            {"row_key": "row:b", "game_version": "jp", "time_cs": 1200,
             "reason": "no_entity"}], "2026-09-04T00:00:00Z")
        held = _held_lookup(svc)
        assert held("row:a", None) == (923, "n64")
        assert held("row:b", "jp") == (1200, None)
        assert held("row:b", "us") is None
        assert held("row:c", None) is None


def test_your_time_is_your_fastest_across_strategies_not_your_latest_save(tmp_path):
    """Round 33, his case: RONC3NA imported onto an empty log, a goal that
    includes RONC3NA, and A-Maze-Ing Emergency Exit read YOU 11"50 against
    GOAL 11"26 -- "I literally am ronc3na in this case. Both should
    automatically be matching." The strategy-blind `current_pb` answers the
    LATEST save across strategies, and the import lands a star's rows in
    sheet order, so the slower row landed second and won. YOU is the
    fastest current row across every strategy now, which is exactly what a
    runner goal offers for that runner."""
    with make_client(tmp_path) as (client, db, _svc):
        db.insert_pb(1, 0, "Standard", "igt", 266, None, "2026-09-05T00:00:01Z")
        db.insert_pb(1, 0, "Left side TJ", "igt", 300, None, "2026-09-05T00:00:02Z")
        card = client.get("/api/scorecard").json()
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        tile = next(t for t in row["tiles"] if t["key"] == "star:1:0")
        assert tile["you_cs"] == display_cs(266), tile
        # An untagged save counts too, when it is the fastest.
        db.insert_pb(1, 1, None, "igt", 250, None, "2026-09-05T00:00:03Z")
        db.insert_pb(1, 1, "Standard", "igt", 280, None, "2026-09-05T00:00:04Z")
        card = client.get("/api/scorecard").json()
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        tile = next(t for t in row["tiles"] if t["key"] == "star:1:1")
        assert tile["you_cs"] == display_cs(250), tile


def _you(client, key):
    card = client.get("/api/scorecard").json()
    return next(t for r in card["rows"] for t in r["tiles"] if t["key"] == key)["you_cs"]


def test_your_time_is_your_fastest_across_the_regions_the_card_includes(tmp_path):
    """Round 34, his case again: RONC3NA re-imported, and the two 100-coin
    tiles whose sheet row is a MERGED (JP)/(US) approach read +0.73 and
    +1.30 against his own column. The import lands both of a runner's times
    under ONE strategy (JP first), and a slot keyed by strategy alone let the
    US row shadow the faster JP one. A PB slot is per ROM as well now, and
    YOU merges across the regions the card includes -- exactly what the goal
    side does for the runner -- so with both regions on the JP time is his,
    with US only the US time is, and an unversioned row counts either way."""
    with make_client(tmp_path) as (client, db, _svc):
        db.insert_pb(11, 6, "Secrets + 100c", "igt", 2227, None, "2026-09-05T00:00:01Z",
                     imported_from="sheet:RONC3NA", game_version="jp")
        db.insert_pb(11, 6, "Secrets + 100c", "igt", 2249, None, "2026-09-05T00:00:02Z",
                     imported_from="sheet:RONC3NA", game_version="us")
        assert client.put("/api/scorecard/regions",
                          json={"regions": ["us", "jp"]}).status_code == 200
        assert _you(client, "star:11:6") == display_cs(2227), "the faster JP row is shadowed"
        assert client.put("/api/scorecard/regions",
                          json={"regions": ["us"]}).status_code == 200
        assert _you(client, "star:11:6") == display_cs(2249), "a JP row is not a US time"
        assert client.put("/api/scorecard/regions",
                          json={"regions": ["jp"]}).status_code == 200
        assert _you(client, "star:11:6") == display_cs(2227)
        # A row with no ROM stamped grades on whatever is running: it counts
        # under every region setting, and it is the fastest here.
        db.insert_pb(11, 6, "Secrets + 100c", "igt", 2200, None, "2026-09-05T00:00:03Z")
        assert _you(client, "star:11:6") == display_cs(2200)
        assert client.put("/api/scorecard/regions",
                          json={"regions": ["us"]}).status_code == 200
        assert _you(client, "star:11:6") == display_cs(2200)
