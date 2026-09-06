"""Task 0126: the Library and Practice are two views of one current standard."""
from import_fixture import make_client
from sm64_events.library.audit import row_key


def test_lakitu_all_strategies_have_the_same_standards_in_both_pages(tmp_path):
    with make_client(tmp_path) as (client, db, service):
        library = client.app.state.library
        index = next(n for n, t in enumerate(library.payload["targets"])
                     if t["label"].lower() == "lakitu skip")
        target = client.get(f"/api/library/target/{index}").json()
        strategies = {row["strategy"] for row in target["approaches"]}
        assert strategies == {"Standard", "JD beginning", "JD -> Speedkick ending"}
        for row in target["approaches"]:
            practice = client.get("/api/ranks/standards", params={
                "entity": row["entity_key"], "version": "us"}).json()
            assert row["ladder"] == practice["strategies"][row["strategy"]]
        row = target["approaches"][-1]
        original = dict(row["ladder"])
        service.ranks.set_threshold(row["entity_key"], row["strategy"], "Mario", 5.43)
        updated = client.get(f"/api/library/target/{index}").json()["approaches"][-1]
        assert updated["ladder"] == {**original, "Mario": 5.43}
        assert client.get("/api/ranks/standards", params={
            "entity": row["entity_key"]}).json()["strategies"][row["strategy"]] == updated["ladder"]


def test_subsection_is_parented_ranked_and_can_receive_a_manual_time(tmp_path):
    with make_client(tmp_path) as (client, db, service):
        payload = client.app.state.library.payload
        index, target = next((n, t) for n, t in enumerate(payload["targets"])
                             if t.get("entity_key") == "star:1:0" and t["subsections"])
        result = client.get(f"/api/library/target/{index}").json()
        piece = result["subsections"][0]
        assert piece["entity_key"].startswith("segment:")
        definition = next(d for d in db.segment_defs()
                          if f"segment:{d['id']}" == piece["entity_key"])
        assert definition["parents"] == ["star:1:0"]
        assert piece["ladder"]
        imported = client.post("/api/import/manual", json={
            "entity_key": piece["entity_key"], "strat_tag": piece["strategy"],
            "time_cs": 1000, "game_version": "us"})
        assert imported.status_code == 200, imported.text
        assert imported.json()["imported"] == 1
        pb = next(p for p in db.pbs() if p["segment_id"] == definition["id"])
        assert pb["timer_mode"] == "rta"


def test_every_current_star_movement_and_piece_has_an_identity_and_ladder(tmp_path):
    from sm64_events.library.placements import row_identity
    with make_client(tmp_path) as (client, db, service):
        adoptions = client.app.state.adoptions
        rows = adoptions.rows()
        missing = []
        for target in client.app.state.library.payload["targets"]:
            if target.get("miss_reason") in {"route", "not_a_target"}:
                continue
            for collection, kind in (("approaches", "approach"), ("subsections", "subsection")):
                for item in target[collection]:
                    identity = row_identity(target, item, kind, rows)
                    if identity is None or not service.ranks.ladders(identity[0]).get(identity[1]):
                        missing.append(row_key(target, item["name"], item["ids"]))
        assert missing == []


def test_ratings_never_reuse_a_deleted_movements_sheet_id(tmp_path):
    from sm64_events.library.ratings import runner_times
    with make_client(tmp_path) as (client, db, service):
        definition = next(d for d in db.segment_defs() if d["seed_key"] == "seg:bitfs-pipe")
        db.delete_segment_def(definition["id"])
        adoptions = client.app.state.adoptions
        adoptions.load()
        times = runner_times(client.app.state.library.payload, adoptions.rows())
        assert f"segment:{definition['id']}" not in times["DentoriousRed"]


def test_a_piece_added_by_refresh_lands_on_the_same_import(tmp_path, monkeypatch):
    from copy import deepcopy
    from sm64_events.library.ladders import fit_payload
    with make_client(tmp_path) as (client, db, service):
        library = client.app.state.library
        fresh = deepcopy(library.payload)
        target = next(t for t in fresh["targets"] if t.get("entity_key") == "star:1:0")
        piece = {"name": "New climb", "ids": ["99"], "best_cs": 1000,
                 "entries": [{"runner": "new-sheet-runner", "time_cs": 1000,
                              "version": None, "video": None}]}
        target["subsections"].append(piece)
        fit_payload(fresh)
        fresh["sheet_revision"] = "2064-01-01T00:00:00"
        monkeypatch.setattr(library, "refresh", lambda *a, **kw: library.absorb(fresh))
        result = client.post("/api/import/sheet", json={
            "runner": "new-sheet-runner", "refresh": True})
        assert result.status_code == 200, result.text
        assert result.json()["imported"] == 1 and result.json()["held"] == []
        entity = client.app.state.adoptions.rows()[row_key(target, piece["name"], piece["ids"])]
        sid = int(entity.split(":")[1])
        assert db.current_pb(None, None, "rta", segment_id=sid)["frames"] == 300
        assert service.ranks.ladders(entity)["Standard"] == piece["ladder"]


def test_unlinking_an_automatic_piece_survives_reload_and_reassignment(tmp_path):
    from sm64_events.server.import_api import sheet_row_placer
    with make_client(tmp_path) as (client, db, service):
        target = client.get("/api/library/entity/star:1:0").json()["targets"][0]
        piece = target["subsections"][0]
        original = piece["entity_key"]
        adoptions = client.app.state.adoptions
        assert client.post("/api/library/unadopt", json={
            "row_key": piece["row_key"]}).status_code == 200
        adoptions.load()
        assert not adoptions.rows().get(piece["row_key"])
        assert not service.ranks.ladders(original)
        assert sheet_row_placer(service, adoptions)(target, piece, "subsection") is None
        unlinked = client.get("/api/library/entity/star:1:0").json()["targets"][0]["subsections"][0]
        assert not unlinked["adopted"]
        assert client.post("/api/library/adopt", json={
            "row_key": piece["row_key"], "entity_key": original}).status_code == 200
        adoptions.load()
        assert adoptions.rows()[piece["row_key"]] == original
        assert service.ranks.ladders(original)["Standard"] == piece["ladder"]
