"""Uploading a LiveSplit splits file, over HTTP."""
from import_fixture import make_client


def splits_for(names):
    segments = "".join(
        f"<Segment><Name>{name}</Name><BestSegmentTime>"
        f"<RealTime>00:00:{seconds:02d}.5000000</RealTime>"
        "</BestSegmentTime></Segment>"
        for seconds, name in enumerate(names, start=10))
    return ('<?xml version="1.0" encoding="UTF-8"?><Run version="1.7.0">'
            f"<Segments>{segments}</Segments></Run>").encode("utf-8")


def test_golds_land_on_your_own_segments(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        mine = db.segment_defs()[:2]
        body = client.post(
            "/api/import/livesplit",
            content=splits_for([row["name"] for row in mine])).json()
        assert body["imported"] == 2
        for row in mine:
            saved = db.current_pb(None, None, "rta", segment_id=row["id"])
            assert saved is not None, row["name"]
            assert saved["imported_from"] == "livesplit"


def test_a_split_naming_a_star_is_reported_rather_than_landed(tmp_path):
    """A gold is a real-time stretch of the run; a star's bests are Usamune
    IGT. Filing one against the other reads as a wildly good time."""
    with make_client(tmp_path) as (client, db, _svc):
        body = client.post("/api/import/livesplit",
                           content=splits_for(["BoB 1"])).json()
        assert body["imported"] == 0
        assert [r["reason"] for r in body["rejected"]] == ["not_a_segment"]
        assert db.pbs() == []


def test_a_split_this_database_has_never_heard_of_is_reported(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        body = client.post("/api/import/livesplit",
                           content=splits_for(["Chungus Skip"])).json()
        assert [(r["text"], r["reason"]) for r in body["rejected"]] == [
            ("Chungus Skip", "unknown_target")]


def test_a_file_that_is_not_a_splits_file_says_so(tmp_path):
    """"Nothing landed" and "that was a screenshot" look identical from the
    outside, and only one is worth acting on."""
    with make_client(tmp_path) as (client, _db, _svc):
        response = client.post("/api/import/livesplit",
                               content=b"this is not xml at all")
        assert response.status_code == 422
        assert "splits file" in response.json()["detail"]


def test_an_empty_body_is_refused(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        assert client.post("/api/import/livesplit",
                           content=b"").status_code == 422


def test_a_dry_run_writes_nothing(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        mine = db.segment_defs()[:2]
        data = splits_for([row["name"] for row in mine])
        preview = client.post("/api/import/livesplit?dry_run=true",
                              content=data).json()
        assert preview["dry_run"] is True
        assert db.pbs() == []
        real = client.post("/api/import/livesplit", content=data).json()
        assert preview["imported"] == real["imported"]


def test_a_strategy_can_be_named_for_the_whole_file(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        row = db.segment_defs()[0]
        client.post("/api/import/livesplit?strategy=Standard",
                    content=splits_for([row["name"]]))
        saved = db.current_pb(None, None, "rta", segment_id=row["id"],
                              strat_tag="Standard")
        assert saved is not None


def test_a_livesplit_import_can_be_undone_as_a_whole(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        mine = db.segment_defs()[:2]
        client.post("/api/import/livesplit",
                    content=splits_for([row["name"] for row in mine]))
        assert client.delete("/api/import/livesplit").json()["removed"] == 2
        assert db.pbs() == []
