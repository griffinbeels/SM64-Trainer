"""The paste door, over HTTP.

The format is the community's own vocabulary rather than one we invented, so
most of these are assertions about text a runner would actually have lying
around.
"""
from import_fixture import make_client


BLOCK = """# my golds
BoB 1\t0:23.57
WF 6, 8.86, LJ
Blast Away the Wall   10.03   Texture
Sneaky Chungus Skip\t12.00
"""


def test_a_pasted_block_lands_and_names_what_it_could_not_read(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        body = client.post("/api/import/paste", json={"text": BLOCK}).json()
        assert body["found"] == 3
        assert body["imported"] == 3
        assert body["dry_run"] is False
        assert [(r["line"], r["reason"]) for r in body["rejected"]] == [
            (5, "unknown_target")]
        # `BoB 1  0:23.57` names no strategy, so it stores NULL — one
        # spelling of an absence, never an empty string — and is counted.
        assert body["without_strategy"] == 1
        assert db.current_pb(1, 0, "igt")["frames"] == 708
        assert db.current_pb(1, 0, "igt")["strat_tag"] is None
        assert db.current_pb(2, 5, "igt", strat_tag="LJ")["frames"] == 266


def test_a_dry_run_writes_nothing_and_answers_the_same_question(tmp_path):
    """A preview computed a second way would answer a different question from
    the one the button then performs."""
    with make_client(tmp_path) as (client, db, _svc):
        preview = client.post("/api/import/paste",
                              json={"text": BLOCK, "dry_run": True}).json()
        assert preview["dry_run"] is True
        assert db.pbs() == []
        real = client.post("/api/import/paste", json={"text": BLOCK}).json()
        for key in ("found", "imported", "already_faster", "unmappable"):
            assert preview[key] == real[key], key
        assert preview["rejected"] == real["rejected"]


def test_the_rejects_carry_the_line_as_it_was_written(tmp_path):
    """He has to be able to find the line to fix it, which means its own text
    and its own number."""
    with make_client(tmp_path) as (client, _db, _svc):
        body = client.post("/api/import/paste", json={"text": BLOCK}).json()
        assert body["rejected"][0]["text"].startswith("Sneaky Chungus Skip")


def test_an_empty_block_is_not_an_error(tmp_path):
    with make_client(tmp_path) as (client, _db, _svc):
        body = client.post("/api/import/paste", json={"text": "   \n\n"}).json()
        assert body["found"] == 0
        assert body["rejected"] == []


def test_the_sheets_own_labels_resolve_in_a_pasted_block(tmp_path):
    """A block copied straight out of the Ultimate Sheet lands with no
    editing — that is what makes the sheet's vocabulary worth reusing."""
    with make_client(tmp_path) as (client, db, _svc):
        body = client.post("/api/import/paste", json={
            "text": "Big Bob-omb on the Summit\t43.63"}).json()
        assert body["imported"] == 1
        assert db.current_pb(1, 0, "igt")["frames"] == 1309


def test_a_segment_of_yours_lands_from_a_pasted_block_on_rta(tmp_path):
    """The block resolves a segment by NAME against this database, so the id
    it produces is ours and the RTA clock is chosen for it automatically."""
    with make_client(tmp_path) as (client, db, _svc):
        row = db.segment_defs()[0]
        body = client.post("/api/import/paste", json={
            "text": f"{row['name']}\t12.00"}).json()
        assert body["imported"] == 1
        assert db.current_pb(None, None, "rta", segment_id=row["id"]) is not None


def test_importing_the_same_block_twice_is_free(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        client.post("/api/import/paste", json={"text": BLOCK})
        before = len(db.pbs())
        second = client.post("/api/import/paste", json={"text": BLOCK}).json()
        assert second["imported"] == 0
        assert second["already_faster"] == 3
        assert len(db.pbs()) == before


def test_a_pasted_import_can_be_undone_as_a_whole(tmp_path):
    with make_client(tmp_path) as (client, db, _svc):
        client.post("/api/import/paste", json={"text": BLOCK})
        assert client.delete("/api/import/paste").json()["removed"] == 3
        assert db.pbs() == []
