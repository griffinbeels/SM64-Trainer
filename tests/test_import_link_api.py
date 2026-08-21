"""Importing from a link to somebody's own spreadsheet, over HTTP.

Nothing here touches the network: the fetch is replaced, because what is under
test is which SHAPE the workbook turns out to be and what happens next.
"""
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from library_fixture import build_workbook

import sm64_events
from sm64_events.library import workbook as wb
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

LINK = "https://docs.google.com/spreadsheets/d/1J20aivGnvLlAuyRIMMclIFUmrk/edit"


class OfflineMemory:
    attached = False

    def attach(self):
        return False

    def detach(self):
        pass


@contextmanager
def make_client(tmp_path):
    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    ranks = RankStandards(
        tmp_path / "rs.json",
        seed_path=(Path(sm64_events.__file__).parent / "data"
                   / "rank_standards.seed.json"))
    ranks.load()
    service = TrackerService(db, broadcaster, ranks=ranks)
    poller = Poller(OfflineMemory(), [], service)
    with TestClient(create_app(poller, broadcaster, service=service)) as client:
        yield client, db


def personal_sheet():
    return build_workbook({
        "Times": {
            (1, 1): {"text": "Star"}, (1, 2): {"text": "Time"},
            (2, 1): {"text": "BoB 1"}, (2, 2): {"text": "0:23.57"},
            (3, 1): {"text": "WF 6"}, (3, 2): {"text": "8.86"},
            (3, 3): {"text": "LJ"},
            (5, 1): {"text": "Chungus Skip"}, (5, 2): {"text": "12.00"},
        },
    })


def ultimate_copy():
    """Row 1 is the runner header row and columns 7+ are the runners — the
    sheet's own shape (`library/sheet.py::FIRST_RUNNER_COL`), which is what a
    personal COPY of it also has."""
    return build_workbook({
        wb.SHEET_MAIN: {
            (1, 7): {"text": "Someone"},
            # A NUMBERED section is what tells the mapper which course the
            # rows under it belong to (`library/mapping.py::section_course`).
            (2, 1): {"text": "1. Bob-omb Battlefield"},
            (3, 1): {"text": "[1] Big Bob-omb on the Summit", "bold": True},
            (3, 2): {"text": "43.63"},
            (3, 4): {"text": "Someone"},
            (3, 7): {"text": "43.63"},
        },
        "Log (Main)": {(1, 1): {"text": "46238.84334791667"}},
    })


def serving(monkeypatch, data):
    monkeypatch.setattr("sm64_events.server.import_api._fetch_bytes",
                        lambda _url: data)


def test_a_personal_grid_lands_its_times(tmp_path, monkeypatch):
    serving(monkeypatch, personal_sheet())
    with make_client(tmp_path) as (client, db):
        body = client.post("/api/import/link", json={"url": LINK}).json()
        assert body["shape"] == "grid"
        assert body["imported"] == 2
        assert db.current_pb(1, 0, "igt")["frames"] == 708


def test_an_unreadable_row_names_its_tab_and_its_row(tmp_path, monkeypatch):
    """"Row 5 of Times" is advice somebody can follow."""
    serving(monkeypatch, personal_sheet())
    with make_client(tmp_path) as (client, _db):
        body = client.post("/api/import/link", json={"url": LINK}).json()
        texts = [row["text"] for row in body["rejected"]]
        assert any(text.startswith("Times!5: Chungus Skip") for text in texts), texts


def test_a_copy_of_the_ultimate_sheet_is_recognised_and_asks_who_you_are(
        tmp_path, monkeypatch):
    """That shape has a column per runner and no way to guess which is
    yours."""
    serving(monkeypatch, ultimate_copy())
    with make_client(tmp_path) as (client, _db):
        response = client.post("/api/import/link", json={"url": LINK})
        assert response.status_code == 422
        assert "runner" in response.json()["detail"]


def test_a_copy_of_the_ultimate_sheet_extracts_the_named_runner(
        tmp_path, monkeypatch):
    serving(monkeypatch, ultimate_copy())
    with make_client(tmp_path) as (client, _db):
        body = client.post("/api/import/link",
                           json={"url": LINK, "runner": "Someone"}).json()
        assert body["shape"] == "ultimate"
        assert body["found"] >= 1


def test_a_link_that_is_not_a_google_sheet_is_refused_before_any_fetch(
        tmp_path, monkeypatch):
    """The SERVER does the fetching, so an unrestricted version would reach
    inside this machine's network on somebody's say-so."""
    def never(_url):
        raise AssertionError("a refused link must not be fetched at all")

    monkeypatch.setattr("sm64_events.server.import_api._fetch_bytes", never)
    with make_client(tmp_path) as (client, _db):
        for url in ("http://127.0.0.1:8065/api/session",
                    "file:///c:/Windows/win.ini",
                    "https://example.com/spreadsheets/d/abcdefghijklmnop/edit"):
            response = client.post("/api/import/link", json={"url": url})
            assert response.status_code == 422, url
            assert "Google Sheets" in response.json()["detail"]


def test_a_sheet_that_cannot_be_reached_says_so_and_says_why(
        tmp_path, monkeypatch):
    def boom(_url):
        raise OSError("HTTP Error 404: Not Found")

    monkeypatch.setattr("sm64_events.server.import_api._fetch_bytes", boom)
    with make_client(tmp_path) as (client, _db):
        response = client.post("/api/import/link", json={"url": LINK})
        assert response.status_code == 503
        # The likeliest cause by far, named where the person can act on it.
        assert "shared" in response.json()["detail"]


def test_a_dry_run_writes_nothing(tmp_path, monkeypatch):
    serving(monkeypatch, personal_sheet())
    with make_client(tmp_path) as (client, db):
        preview = client.post("/api/import/link",
                              json={"url": LINK, "dry_run": True}).json()
        assert preview["dry_run"] is True
        assert db.pbs() == []
        real = client.post("/api/import/link", json={"url": LINK}).json()
        assert preview["imported"] == real["imported"]


def test_a_link_import_can_be_undone_as_a_whole(tmp_path, monkeypatch):
    serving(monkeypatch, personal_sheet())
    with make_client(tmp_path) as (client, db):
        client.post("/api/import/link", json={"url": LINK})
        assert client.delete("/api/import/link").json()["removed"] == 2
        assert db.pbs() == []
