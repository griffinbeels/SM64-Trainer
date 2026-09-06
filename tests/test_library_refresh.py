"""Every way the sheet reaches the app re-derives the sheet-fitted rank
standards (round 33, 2026-09-05).

His words: "any time we pull in the spreadsheet, we should probably do a quick
rank standards update. For any strategy that we add ('Standard' included) and
for any existing strategies, we should automatically adjust the rank standards
/ add any new rank standards. This includes whenever we import a runner's
times, or copy sheet column... We should also automatically refresh the rank
standards upon app startup... If we don't have internet or the process fails,
we should fail silently and just not update automatically (other than
including a mention in the logs)."

The stub workbook is a live-shaped sheet with twelve runners timing
A-Maze-ing Emergency Exit's own row (his reported star, whose vetted seed
defines no Standard), so the row fits a ladder and files under Standard; its Log
is stamped far in the future so every door treats it as newer than the bundled
snapshot.
"""
import logging
import time

from fastapi.testclient import TestClient
from import_fixture import OfflineMemory, bundled_standards_seed, make_client
from library_fixture import build_workbook

from sm64_events.library import workbook as wb
from sm64_events.library.ladders import fit_ladder
from sm64_events.library.sheet import parse_time
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

RUNNERS = [f"r{index}" for index in range(12)]
STUB_TIMES = [f"{11.10 + index * 0.13:.2f}" for index in range(len(RUNNERS))]


def stub_standard_ladder() -> dict:
    """The ladder the stub's twelve times fit to -- what the store must grade
    Standard against once any door has absorbed the stub."""
    return fit_ladder(sorted(parse_time(text) for text in STUB_TIMES))


def _fresh_workbook():
    cells = {(1, 1): {"text": "Xcam IGT !"}, (1, 2): {"text": "Sheet Best"},
             (1, 3): {"text": "Player"}, (1, 4): {"text": "Ideal Run"},
             (1, 5): {"text": "Fill Rate"},
             (2, 1): {"text": "6. Hazy Maze Cave"},
             (3, 1): {"text": "[5] A-Maze-ing Emergency Exit", "bold": True},
             (3, 2): {"text": "11.10"}}
    for index, runner in enumerate(RUNNERS):
        cells[(1, 7 + index)] = {"text": runner}
        cells[(3, 7 + index)] = {"text": STUB_TIMES[index]}
    # A Log entry in 2064: newer than any bundled snapshot, so the door
    # applies it rather than answering "not newer than what we have".
    return build_workbook({wb.SHEET_MAIN: cells,
                           wb.SHEET_LOG: {(1, 1): {"text": "60000.5"}}})


def test_an_import_that_refreshes_the_sheet_gives_standard_its_ladder(tmp_path, monkeypatch):
    monkeypatch.setattr("sm64_events.server.import_api.fetch", _fresh_workbook)
    with make_client(tmp_path) as (client, _db, svc):
        assert "Standard" not in svc.ranks.seeded_strategies("star:6:4"), (
            "the seed already grades Standard here; pick another star")
        before = svc.ranks.ladders("star:6:4").get("Standard")
        landed = client.post("/api/import/sheet", json={"runner": "r0", "refresh": True}).json()
        assert landed["imported"] == 1, landed
        assert svc.ranks.is_fitted("star:6:4", "Standard")
        assert svc.ranks.ladders("star:6:4")["Standard"] == stub_standard_ladder()
        assert svc.ranks.ladders("star:6:4")["Standard"] != before, (
            "the bundled fit and the stub's must differ for this to prove anything")


def test_the_column_export_refreshes_the_library_from_the_bytes_it_fetched(tmp_path, monkeypatch):
    monkeypatch.setattr("sm64_events.server.scorecard_api.fetch", _fresh_workbook)
    with make_client(tmp_path) as (client, _db, svc):
        before = client.app.state.library.revision
        job = client.post("/api/scorecard/column").json()["job_id"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = client.get(f"/api/scorecard/column/{job}").json()
            if status["state"] != "running":
                break
            time.sleep(0.05)
        assert status["state"] == "done", status
        assert client.app.state.library.revision != before, "the fetched sheet was not absorbed"
        assert svc.ranks.ladders("star:6:4")["Standard"] == stub_standard_ladder()


def _app(tmp_path, refresh_on_start):
    db = Database(tmp_path / "t.db")
    broadcaster = Broadcaster()
    ranks = RankStandards(tmp_path / "rs.json", seed_path=bundled_standards_seed())
    ranks.load()
    service = TrackerService(db, broadcaster, ranks=ranks)
    app = create_app(Poller(OfflineMemory(), [], service), broadcaster, service=service,
                     adoptions_path=tmp_path / "library_adoptions.json",
                     mode_path=tmp_path / "tracker_mode.json",
                     library_path=tmp_path / "sheet_library.json.gz",
                     refresh_library_on_start=refresh_on_start)
    return app, service


def _settle(predicate, timeout_s=15):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_startup_refreshes_the_library_and_the_standards(tmp_path, monkeypatch):
    monkeypatch.setattr("sm64_events.library.source.fetch", _fresh_workbook)
    app, service = _app(tmp_path, refresh_on_start=True)
    with TestClient(app):
        assert _settle(lambda: app.state.library.revision.startswith("2064")), (
            app.state.library.revision)
        assert _settle(lambda: service.ranks.ladders("star:6:4").get("Standard")
                       == stub_standard_ladder()), service.ranks.ladders("star:6:4")


def test_a_failed_startup_refresh_is_one_log_line_and_nothing_else(tmp_path, monkeypatch, caplog):
    def offline():
        raise OSError("no route to host")
    monkeypatch.setattr("sm64_events.library.source.fetch", offline)
    app, service = _app(tmp_path, refresh_on_start=True)
    revision_before = app.state.library.revision
    with caplog.at_level(logging.INFO):
        with TestClient(app) as client:
            assert _settle(lambda: any("library refresh at startup skipped" in record.message
                                       for record in caplog.records))
            assert client.get("/health").status_code == 200
    assert app.state.library.revision == revision_before
    assert service.ranks.ladders("star:6:4").get("Standard") != stub_standard_ladder()


def test_the_startup_refresh_is_off_unless_asked(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("sm64_events.library.source.fetch", lambda: calls.append(1) or b"")
    app, _service = _app(tmp_path, refresh_on_start=False)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        time.sleep(0.3)
    assert calls == [], "the app reached for the sheet without being asked"
