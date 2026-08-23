"""The scorecard router, over HTTP -- the one goal KV and the resolved card.

`ranks/scorecard.py`'s own build logic (row shape, folding, Sigma math) is
`tests/test_scorecard.py`'s job; this file is only what the router adds on
top: reading/writing the KV, assembling `you`/`goal`/`fold` from the real
db + standards, and the coverage/pending flags the UI reads.
"""
from import_fixture import make_client
from sm64_events.ranks.classify import display_cs


def test_goal_round_trip(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "division", "tier": "Gold",
                                    "division": "I"})
        assert response.status_code == 200

        card = client.get("/api/scorecard").json()
        assert card["goal"] == {"kind": "division", "tier": "Gold", "division": "I"}
        assert len(card["rows"]) == 16                     # 15 courses + Secret
        assert any(tile["goal_cs"] for row in card["rows"] for tile in row["tiles"])
        assert card["goal_coverage"]["tiles"] == sum(
            len(row["tiles"]) for row in card["rows"])
        assert card["goal_coverage"]["covered"] > 0
        assert "goal_pending" not in card


def test_iron_division_is_refused(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "division", "tier": "Iron",
                                    "division": "I"})
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


def test_null_clears_the_goal(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        client.put("/api/scorecard/goal",
                   json={"kind": "division", "tier": "Gold", "division": "I"})
        response = client.put("/api/scorecard/goal", json=None)
        assert response.status_code == 200
        assert client.get("/api/scorecard").json()["goal"] is None


def test_no_goal_serves_your_times_uncolored(tmp_path):
    """A saved PB appears as you_cs with goal_cs None everywhere -- no goal
    is set, so nothing on the card has anything to grade against."""
    with make_client(tmp_path) as (client, db, _svc):
        client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard", "time_cs": 886})

        card = client.get("/api/scorecard").json()
        assert card["goal"] is None
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        tile = next(t for t in row["tiles"] if t["key"] == "star:1:0")
        pb_row = db.current_pb(1, 0, "igt")
        assert tile["you_cs"] == display_cs(pb_row["frames"])
        assert tile["goal_cs"] is None
        assert tile["delta_cs"] is None


def test_a_runner_goal_is_accepted_but_pending_until_task_6(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal",
                              json={"kind": "runner", "runner": "Suigi"})
        assert response.status_code == 200

        card = client.get("/api/scorecard").json()
        assert card["goal"] == {"kind": "runner", "runner": "Suigi"}
        assert card["goal_pending"] is True
        assert not any(tile["goal_cs"] for row in card["rows"] for tile in row["tiles"])
        assert card["goal_coverage"]["covered"] == 0


def test_a_runner_goal_needs_a_name(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.put("/api/scorecard/goal", json={"kind": "runner"})
        assert response.status_code == 422


def test_your_100c_pbs_variant_folds_its_exit_star(tmp_path):
    """A course whose 100c PB was saved under a labelled exit-star variant
    folds that star out of both sums -- the same star the variant's own
    label names, read off `ranks.variant_of`."""
    with make_client(tmp_path) as (client, _db, _svc):
        client.post("/api/import/manual", json={
            "entity_key": "star:1:6", "strat_tag": "100c + Reds · Standard",
            "time_cs": 12000})

        card = client.get("/api/scorecard").json()
        row = next(r for r in card["rows"] if r["course_id"] == 1)
        folded = {tile["key"]: tile["folded"] for tile in row["tiles"]}
        assert folded["star:1:3"] is True
        assert folded["star:1:0"] is False


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
        assert resolve("star:1:0", "Standard", "igt", None) == display_cs(886)
        # No PB was ever set as JP, and the default grading version is US --
        # an explicitly-versioned JP row must not print this US time.
        assert resolve("star:1:0", "Standard", "igt", "jp") is None
        assert resolve("star:1:0", "Standard", "igt", "us") == display_cs(886)
        assert resolve("star:1:0", "Other Strat", "igt", None) is None


def test_column_resolve_reads_a_segment_pb_on_its_own_clock(tmp_path):
    from sm64_events.server.scorecard_api import _column_resolve
    with make_client(tmp_path) as (_client, db, svc):
        piece = db.insert_segment_def("A Movement", [], [], [],
                                      "2026-08-23T00:00:00Z")
        db.insert_pb(None, None, "Standard", "rta", 476, None,
                     "2026-08-23T00:00:00Z", segment_id=piece)
        resolve = _column_resolve(svc)
        assert resolve(f"segment:{piece}", "Standard", "rta", None) == display_cs(476)


def test_column_placer_name_matches_an_entity_less_target(tmp_path):
    """Round 6's auto-match, the same one `server/import_api.py::
    sheet_row_placer` grants an unlinked castle-movement row. Every fresh
    database seeds a "Lakitu Skip" segment (see `test_import_api.py`)."""
    from sm64_events.server.scorecard_api import _column_placer
    with make_client(tmp_path) as (_client, db, svc):
        lakitu_id = {d["seed_key"]: d["id"]
                    for d in db.segment_defs()}["seg:lakitu-skip"]
        place = _column_placer(svc, None)
        placed = place({"section": "Castle Movements", "label": "Lakitu Skip",
                        "entity_key": None},
                       {"name": "Lakitu Skip", "ids": ["1"]}, "approach")
        assert placed[0] == f"segment:{lakitu_id}"


def test_column_placer_lands_a_bowser_row_on_the_seeded_movement(tmp_path):
    """The sheet says `segment:6`; the placer resolves it by seed_key to
    whichever id THIS database holds for the BitFS pipe entry."""
    from sm64_events.server.scorecard_api import _column_placer
    with make_client(tmp_path) as (_client, db, svc):
        bitfs_id = {d["seed_key"]: d["id"]
                   for d in db.segment_defs()}["seg:bitfs-pipe"]
        place = _column_placer(svc, None)
        placed = place({"section": "Bowser Courses",
                        "label": "Bowser in the Fire Sea Course",
                        "entity_key": "segment:6"},
                       {"name": "Bowser in the Fire Sea Course", "ids": ["1"]},
                       "approach")
        assert placed == (f"segment:{bitfs_id}", "rta", None)
