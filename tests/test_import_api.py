"""The manual and sheet doors, over HTTP — and the undo and the celebration
guard, which every door shares.

The runner LIST is deliberately not tested here: `GET /api/library/runners`
already served it before this feature and `tests/test_library_api.py` owns it.
Reusing it is the point — the picker fills from the bundled snapshot with no
network wait, while the import itself reads a fresh fetch.
"""
import zipfile

import pytest
from import_fixture import make_client


def test_manual_import_lands_one_pb(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        response = client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard", "time_cs": 886})
        assert response.status_code == 200
        assert response.json()["imported"] == 1
        row = db.current_pb(1, 0, "igt", strat_tag="Standard")
        assert row["frames"] == 266
        assert row["imported_from"] == "manual"
        # The PB belongs to a real attempt row, the one the log draws.
        assert any(a.id == row["attempt_id"] for a in db.attempts())


def test_manual_import_carries_the_version_through(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard",
            "time_cs": 886, "game_version": "jp"})
        row = db.current_pb(1, 0, "igt", strat_tag="Standard")
        assert row["game_version"] == "jp"


def test_a_segment_on_the_igt_clock_is_refused(tmp_path):
    """Segments are RTA-only. The manual door sends IGT, so a segment typed
    there is refused rather than quietly re-clocked."""
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.post("/api/import/manual", json={
            "entity_key": "segment:6", "strat_tag": "Standard",
            "time_cs": 886})
        assert response.status_code == 422
        assert "IGT" in response.json()["detail"]


def test_a_key_that_is_neither_a_star_nor_a_segment_is_refused(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.post("/api/import/manual", json={
            "entity_key": "area:6:1", "strat_tag": "Standard",
            "time_cs": 886})
        assert response.status_code == 422
        assert "only stars and your own segments" in response.json()["detail"]


def test_importing_the_same_time_twice_is_free(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        body = {"entity_key": "star:1:0", "strat_tag": "Standard",
                "time_cs": 886}
        client.post("/api/import/manual", json=body)
        before = len(db.pbs())
        second = client.post("/api/import/manual", json=body).json()
        assert second["imported"] == 0
        assert second["already_faster"] == 1
        assert len(db.pbs()) == before


def test_the_sheet_door_lands_a_runners_column_from_the_snapshot(tmp_path):
    """`refresh: false` reads the bundled snapshot, so this needs no network.
    Measured 2026-08-20: DentoriousRed resolves to 14 star times; since round
    3 (2026-08-23) the two Bowser rows land too, on the seeded movements."""
    with make_client(tmp_path) as (client, db, _svc):
        payload = client.post("/api/import/sheet", json={
            "runner": "DentoriousRed", "refresh": False}).json()
        assert payload["found"] == 16
        assert payload["imported"] == 16
        # One row per HELD entry, named, in the same `{text, reason}`
        # shape every door answers with -- he reviews these by name (round
        # 3). Nothing is REJECTED by this door any more: a row it cannot
        # place is kept (round 28), and `held` names it.
        assert payload["rejected"] == []
        assert [{"reason": row["reason"], "text": row["text"]}
                for row in payload["held"]] == [
            {"reason": "no_entity",
             "text": "CCM wooden door - Enter BitDW (LBLJ) — 0'09\"23"}]
        assert [cell["time_cs"] for cell in db.held_times()] == [923]
        assert payload["sheet_revision"]
        assert all(row["imported_from"] == "sheet:DentoriousRed"
                   for row in db.pbs())
        # The Bowser rows: the BitFS pipe entry (the stage's No Reds card)
        # and the BitS one, on the segment's own RTA clock, 39.43s -> frames.
        by_seed = {d["seed_key"]: d["id"] for d in db.segment_defs()}
        bitfs = db.current_pb(None, None, "rta",
                              segment_id=by_seed["seg:bitfs-pipe"],
                              strat_tag="Zero Cycle")
        assert bitfs and bitfs["frames"] == 1183
        assert db.current_pb(None, None, "rta",
                             segment_id=by_seed["seg:bits-pipe"])


def test_a_bowser_row_lands_on_this_databases_row_for_the_movement(tmp_path):
    """The sheet says `segment:6`; what lands on is whichever id THIS
    database holds for `seg:bitfs-pipe`. Re-seeding the movement under a new
    id must move the import with it -- the number is never trusted."""
    with make_client(tmp_path) as (client, db, _svc):
        old = {d["seed_key"]: d for d in db.segment_defs()}["seg:bitfs-pipe"]
        db.delete_segment_def(old["id"])
        fresh_id = db.insert_segment_def(
            old["name"], old["start_triggers"], old["end_triggers"],
            old["guards"], "2026-08-23T00:00:00Z", seed_key="seg:bitfs-pipe")
        assert fresh_id != old["id"]

        payload = client.post("/api/import/sheet", json={
            "runner": "DentoriousRed", "refresh": False}).json()
        assert not any(row["reason"] == "segments" for row in payload["rejected"])
        landed = db.current_pb(None, None, "rta", segment_id=fresh_id,
                               strat_tag="Zero Cycle")
        assert landed and landed["frames"] == 1183
        assert db.current_pb(None, None, "rta", segment_id=old["id"]) is None


VOLCANO_ENTRY_ROW = ("7. Lethal Lava Land||Hot-Foot-It into the Volcano"
                     "||Volcano entry||1|2|3|4|5")


def test_a_subsection_linked_in_the_library_imports_onto_that_segment(tmp_path):
    """His question, 2026-08-23: "If an entry is a subsection AND we've
    successfully linked an actual subsection segment that we've recorded to
    that library entry, then when we import, it should import correctly. Is
    this the case?" Now it is: the link is made the way the Library tab makes
    it, and the import lands the piece on the linked segment as Standard, on
    the segment's clock. The piece he did NOT link stays in the list."""
    with make_client(tmp_path) as (client, db, _svc):
        piece = db.insert_segment_def(
            "Volcano entry", [{"type": "level_enter", "to": 22}],
            [{"type": "area_enter", "level": 22, "area": 2}], [],
            "2026-08-23T00:00:00Z")
        linked = client.post("/api/library/adopt", json={
            "row_key": VOLCANO_ENTRY_ROW, "entity_key": f"segment:{piece}"})
        assert linked.status_code == 200, linked.text
        assert linked.json()["strategy"] == "Standard"

        payload = client.post("/api/import/sheet", json={
            "runner": "GTM", "refresh": False}).json()
        landed = db.current_pb(None, None, "rta", segment_id=piece,
                               strat_tag="Standard")
        assert landed and landed["frames"] == 242          # 8.06s, rounded up
        texts = [row["text"] for row in payload["held"]]
        assert not any("Volcano entry" in text for text in texts), texts
        assert "Hot-Foot-It into the Volcano — Inside the volcano — 0'08\"53" in texts


def test_a_movement_that_name_matches_one_of_your_segments_imports_onto_it(tmp_path):
    """The Library pairs an entity-less target with a segment of the same
    name unasked (round 6); the import honours the same pairing. Every
    database seeds "Lakitu Skip", so GTM's "Lakitu skip" row lands on it
    under the sheet's own approach name."""
    with make_client(tmp_path) as (client, db, _svc):
        lakitu = {d["seed_key"]: d["id"]
                  for d in db.segment_defs()}["seg:lakitu-skip"]
        payload = client.post("/api/import/sheet", json={
            "runner": "GTM", "refresh": False}).json()
        landed = db.current_pb(None, None, "rta", segment_id=lakitu,
                               strat_tag="JD -> Speedkick ending")
        assert landed and landed["frames"] == 166           # 5.53s
        assert not any(row["text"].startswith("Lakitu skip")
                       for row in payload["rejected"])


@pytest.mark.parametrize("failure", [
    # The network never answered.
    OSError("no route to host"),
    # It ANSWERED and the answer is not readable. Found by driving the real
    # drawer, 2026-08-20: the panel sat on "Downloading the current sheet…"
    # forever because the route caught only OSError and this 500'd instead.
    LookupError("no sheet named 'Log' in the workbook"),
    zipfile.BadZipFile("File is not a zip file"),
])
def test_a_sheet_that_cannot_be_read_says_so_rather_than_hanging(
        tmp_path, monkeypatch, failure):
    """"Could not read the sheet" and "this runner has no times" look
    identical from the outside, and only one of them is worth retrying. Every
    way a remote document nobody here controls can fail must reach the person
    waiting as an answer."""
    def boom(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr("sm64_events.server.import_api.fetch", boom)
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.post(
            "/api/import/sheet",
            json={"runner": "DentoriousRed", "refresh": True})
        assert response.status_code == 503
        assert "sheet" in response.json()["detail"]


def test_removing_a_source_erases_only_its_own_rows(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard",
            "time_cs": 886})
        client.post("/api/import/sheet",
                    json={"runner": "DentoriousRed", "refresh": False})
        sheet_rows = len(db.pbs()) - 1
        assert client.delete("/api/import/manual").json()["removed"] == 1
        assert len(db.pbs()) == sheet_rows


def test_a_runner_name_with_a_colon_and_a_space_round_trips_through_delete(
        tmp_path, monkeypatch):
    """Real names from the roster: `adelyn :3`, `bee :3`, `Salt & Ginger`,
    `ガミル`. The source is `sheet:<name>`, so the undo path carries a SECOND
    colon, a space and sometimes non-ASCII — and a source that cannot be
    deleted is an import that cannot be undone. What is under test is the
    ROUTE: that the name reaches the service exactly as it was typed."""
    from urllib.parse import quote

    awkward = "sheet:adelyn :3"
    with make_client(tmp_path) as (client, _db, svc):
        asked_for = []

        async def record(source):
            asked_for.append(source)
            return 3

        monkeypatch.setattr(svc, "remove_imported", record)
        response = client.delete(f"/api/import/{quote(awkward, safe='')}")
        assert response.status_code == 200
        assert response.json()["removed"] == 3
        assert asked_for == [awkward]


def test_an_import_absorbs_the_rank_it_produced(tmp_path):
    """Nothing may appear without a gesture he made, and a full-screen MARELO
    takeover for a climb he did not run for is the exact shape he calls a bug
    (2026-08-01).

    Asserting on the WATERMARK, not on a celebration payload: a fresh scope's
    first rank is SEEDED silently and its first view ABSORBS, so a bare "is
    there a celebration" check on a new database is green whatever the code
    does -- the first version of this test passed with the absorb removed. The
    watermark is the state the next fetch would celebrate against, so moving it
    is the actual guarantee."""
    from sm64_events.ranks import scoring
    from sm64_events.server.ranks_api import _score_scope

    def overall_key(service):
        scored = _score_scope(service, "overall")
        return scoring.progression_key(scored["tier"], scored["division"])

    with make_client(tmp_path) as (client, _db, service):
        # A slow first time, then look at the rank -- that seeds the watermark
        # and absorbs the arrival, which is the state a real user is in.
        client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard",
            "time_cs": 6300})
        client.get("/api/marelo")
        before = service.marelo_watermarks()["overall"]

        client.post("/api/import/sheet",
                    json={"runner": "DentoriousRed", "refresh": False})
        climbed = overall_key(service)
        assert climbed > before, "the import must actually raise the rank, " \
            "or this test proves nothing"
        assert service.marelo_watermarks()["overall"] == climbed
        assert client.get("/api/marelo").json().get("celebration") is None


def test_linking_a_held_row_lands_its_time_the_moment_the_link_is_made(tmp_path):
    """Round 28, the other half of holding a cell. GTM's "Volcano entry" piece
    is held by the import (no segment is linked); building the segment and
    linking the row through the Library's own adopt door lands the held time
    there at once -- through the ordinary improvement rule -- and releases
    the hold, so the column prints the real PB from then on."""
    with make_client(tmp_path) as (client, db, _svc):
        payload = client.post("/api/import/sheet", json={
            "runner": "GTM", "refresh": False}).json()
        held_texts = [row["text"] for row in payload["held"]]
        assert any("Volcano entry" in text for text in held_texts), held_texts
        assert any(cell["row_key"] == VOLCANO_ENTRY_ROW for cell in db.held_times())

        piece = db.insert_segment_def(
            "Volcano entry", [{"type": "level_enter", "to": 22}],
            [{"type": "area_enter", "level": 22, "area": 2}], [],
            "2026-09-04T00:00:00Z")
        linked = client.post("/api/library/adopt", json={
            "row_key": VOLCANO_ENTRY_ROW, "entity_key": f"segment:{piece}"})
        assert linked.status_code == 200, linked.text
        assert linked.json()["held"] == {"landed": 1, "released": 1}
        landed = db.current_pb(None, None, "rta", segment_id=piece,
                               strat_tag="Standard")
        assert landed and landed["frames"] == 242          # 8.06s, rounded up
        assert landed["imported_from"] == "sheet:GTM"
        assert not any(cell["row_key"] == VOLCANO_ENTRY_ROW
                       for cell in db.held_times())
        # Linking the same row again lands nothing and releases nothing.
        assert client.post("/api/library/adopt", json={
            "row_key": VOLCANO_ENTRY_ROW, "entity_key": f"segment:{piece}"}
            ).json()["held"] == {"landed": 0, "released": 0}


def test_removing_a_source_erases_its_held_cells_too(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        client.post("/api/import/sheet", json={"runner": "GTM", "refresh": False})
        assert db.held_times(source="sheet:GTM")
        client.delete("/api/import/sheet:GTM")
        assert db.held_times(source="sheet:GTM") == []
