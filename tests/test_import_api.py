"""The two import doors, over HTTP.

The runner LIST is deliberately not tested here: `GET /api/library/runners`
already served it before this feature and `tests/test_library_api.py` owns it.
Reusing it is the point — the picker fills from the bundled snapshot with no
network wait, while the import itself reads a fresh fetch.
"""
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import sm64_events
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService


class OfflineMemory:
    attached = False

    def attach(self):
        return False

    def detach(self):
        pass


def bundled_standards_seed():
    """The REAL shipped ladders, so the stars a sheet import lands on actually
    grade. A hand-made seed would leave every imported time unrankable and the
    celebration guard below unable to fail."""
    return (Path(sm64_events.__file__).parent / "data"
            / "rank_standards.seed.json")


@contextmanager
def make_client(tmp_path):
    """Driven through the real lifespan, deliberately: importing requires a
    live session the way every other write command does, so a harness that
    skipped startup would be exercising a path the app never takes."""
    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    ranks = RankStandards(tmp_path / "rs.json",
                          seed_path=bundled_standards_seed())
    ranks.load()
    service = TrackerService(db, broadcaster, ranks=ranks)
    poller = Poller(OfflineMemory(), [], service)
    with TestClient(create_app(poller, broadcaster, service=service)) as client:
        yield client, db, service


def test_manual_import_lands_one_pb(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        response = client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard", "time_cs": 886})
        assert response.status_code == 200
        assert response.json()["imported"] == 1
        row = db.current_pb(1, 0, "igt", strat_tag="Standard")
        assert row["frames"] == 266
        assert row["attempt_id"] is None
        assert row["imported_from"] == "manual"


def test_manual_import_carries_the_version_through(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard",
            "time_cs": 886, "game_version": "jp"})
        row = db.current_pb(1, 0, "igt", strat_tag="Standard")
        assert row["game_version"] == "jp"


def test_a_segment_key_is_refused(tmp_path):
    """Segments are RTA-only, and a segment id means nothing outside the
    database that assigned it."""
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.post("/api/import/manual", json={
            "entity_key": "segment:6", "strat_tag": "Standard",
            "time_cs": 886})
        assert response.status_code == 422
        assert "only stars" in response.json()["detail"]


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
    Measured 2026-08-20: DentoriousRed resolves to 14 star times."""
    with make_client(tmp_path) as (client, db, _svc):
        payload = client.post("/api/import/sheet", json={
            "runner": "DentoriousRed", "refresh": False}).json()
        assert payload["found"] == 14
        assert payload["imported"] == 14
        assert payload["rejected"] == {"subsections": 0, "no_entity": 1,
                                       "segments": 2}
        assert payload["sheet_revision"]
        assert all(row["imported_from"] == "sheet:DentoriousRed"
                   for row in db.pbs())


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
        tmp_path):
    """Real names from the roster: `adelyn :3`, `bee :3`, `Salt & Ginger`,
    `ガミル`. The source is `sheet:<name>`, so the undo path carries a SECOND
    colon, a space and sometimes non-ASCII — and a source that cannot be
    deleted is an import that cannot be undone."""
    from urllib.parse import quote

    awkward = "adelyn :3"
    with make_client(tmp_path) as (client, db, _svc):
        client.post("/api/import/manual", json={
            "entity_key": "star:1:0", "strat_tag": "Standard",
            "time_cs": 886})
        # Stand in for that runner's import without needing them on the sheet.
        db.insert_pb(course_id=2, star_id=0, strat_tag="Standard",
                     timer_mode="igt", frames=300, attempt_id=None,
                     saved_utc="2026-08-20T00:00:00Z",
                     imported_from=f"sheet:{awkward}")
        response = client.delete(
            f"/api/import/{quote(f'sheet:{awkward}', safe='')}")
        assert response.status_code == 200
        assert response.json()["removed"] == 1
        assert db.current_pb(2, 0, "igt", strat_tag="Standard") is None
        assert db.current_pb(1, 0, "igt", strat_tag="Standard") is not None


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
