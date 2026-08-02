"""The UI observation log — what the page PAINTED, stored beside the journal.

Why it exists at all is in `core/uilog.py`'s docstring. These tests pin the
three properties that decide whether it is trustworthy as an instrument:
it must never be able to break the page it observes, it must survive its own
crash, and it must stay bounded.
"""
import json
from datetime import datetime, timezone

import pytest

from sm64_events.core import uilog


@pytest.fixture()
def log(tmp_path, monkeypatch):
    path = tmp_path / "data" / "ui_log.jsonl"
    monkeypatch.setattr(uilog, "log_path", lambda: path)
    return path


SELECTOR = {"surface": "selector", "title": "Bowser in the Dark World",
            "note": "route active", "client_utc": "2026-08-02T21:42:19.180Z",
            "cells": [{"name": "Reds", "active": True},
                      {"name": "No Reds", "active": False},
                      {"name": "Bowser 1 → WF", "active": False}]}
TARGET = {"surface": "target", "cards": [
    {"label": "ACTIVE SEGMENT", "context": "Segment", "name": "WF → SSL",
     "strat": "Standard", "state": "Ready",
     "step": "Step 1 of 1 Waiting for Enter Shifting Sand Land"}]}


def test_a_record_round_trips_with_the_server_stamped_frame(log):
    uilog.record(SELECTOR, frame=2365499)
    (stored,) = uilog.read(log)
    assert stored["frame"] == 2365499
    assert stored["cells"][2]["name"] == "Bowser 1 → WF"
    # The CLIENT's own stamp is kept beside the server's, not instead of it:
    # the server's is the one the journal can be joined on, the client's is
    # what shows a lag between painting and recording.
    assert stored["client_utc"] == "2026-08-02T21:42:19.180Z"
    assert stored["server_utc"]


def test_an_unknown_surface_is_dropped_rather_than_stored_or_raised(log):
    """The endpoint takes a free-shaped body from a browser. An instrument
    that can 500 on a stale tab (or a second app posting to the same port) is
    worse than no instrument, so an unrecognised body is simply not one."""
    assert uilog.record({"surface": "whatever", "cells": []}) is None
    assert uilog.record({"cells": []}) is None
    assert uilog.record("not even a dict") is None
    assert uilog.read(log) == []


def test_unknown_fields_never_reach_disk(log):
    uilog.record({**SELECTOR, "evil": "x" * 5000, "nested": {"deep": [1] * 900}})
    (stored,) = uilog.read(log)
    assert "evil" not in stored and "nested" not in stored


def test_long_text_and_long_lists_are_bounded(log):
    uilog.record({"surface": "selector", "title": "T" * 5000,
                  "cells": [{"name": "n" * 900, "active": False}] * 500})
    (stored,) = uilog.read(log)
    assert len(stored["title"]) == uilog.MAX_TEXT
    assert len(stored["cells"]) == uilog.MAX_CELLS
    assert len(stored["cells"][0]["name"]) == uilog.MAX_TEXT


def test_a_torn_final_line_does_not_stop_the_rest_being_read(log):
    """A crash mid-append leaves half a line. Refusing to open after a crash
    would make this useless at the exact moment it is wanted."""
    uilog.record(SELECTOR)
    uilog.record(TARGET)
    with log.open("a", encoding="utf-8", newline="") as handle:
        handle.write('{"surface": "selector", "cel')
    kept = uilog.read(log)
    assert [entry["surface"] for entry in kept] == ["selector", "target"]


def test_the_file_stays_bounded(log, monkeypatch):
    monkeypatch.setattr(uilog, "TRIM_ABOVE_BYTES", 4000)
    monkeypatch.setattr(uilog, "MAX_RECORDS", 5)
    for index in range(60):
        uilog.record({"surface": "selector", "title": f"row {index}",
                      "cells": [{"name": "x" * 100, "active": False}] * 5})
    kept = uilog.read(log)
    assert len(kept) <= uilog.MAX_RECORDS
    # It keeps the NEWEST, which is the half a live report is about.
    assert kept[-1]["title"] == "row 59"


def test_the_stored_line_is_not_crlf_on_windows(log):
    """Read back line-by-line and diffed by eye; a whole-file rewrite that
    flips line endings doubles the churn in any diff of it."""
    uilog.record(SELECTOR)
    uilog.record(TARGET)
    assert b"\r\n" not in log.read_bytes()


def test_render_names_which_cell_was_highlighted(log):
    """The ACTIVE cell is half of what these reports are about ("it still says
    WF -> SSL as the active segment"), and a bare list of names cannot say
    it — so the renderer brackets it."""
    line = uilog.render({**SELECTOR, "frame": 1})
    assert "[Reds]" in line and "Bowser 1 → WF" in line
    assert "[Bowser 1 → WF]" not in line


def test_render_says_NO_CELLS_rather_than_drawing_an_empty_line(log):
    """"Nothing was offered" is the whole content of two of the reports that
    produced this module. It must not render as blank space."""
    assert "NO CELLS" in uilog.render({"surface": "selector", "cells": []})
    assert "NO CARDS" in uilog.render({"surface": "target", "cards": []})


def test_render_shows_every_card_that_was_up_at_once(log):
    """Several objective cards can render together (an active star plus pinned
    segments) and WHICH were up is part of the observation."""
    line = uilog.render({"surface": "target", "cards": [
        {"label": "ACTIVE TARGET", "name": "Boil the Big Bully"},
        {"label": "ACTIVE SEGMENT", "name": "Bowser 1 → WF", "state": "Running"}]})
    assert "Boil the Big Bully" in line and "Bowser 1 → WF" in line
    assert "Running" in line


def test_the_endpoint_records_and_never_raises_on_a_bad_body(log):
    """Wired at the composition surface (server/app.py) because it needs the
    poller for the frame. Driven here through the real app so the route, the
    frame stamp and the drop path are one test rather than three."""
    from fastapi.testclient import TestClient
    from sm64_events.server.app import create_app
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.server.poller import Poller

    class OfflineMemory:
        attached = False
        def attach(self): return False
        def detach(self): pass

    broadcaster = Broadcaster()
    app = create_app(Poller(OfflineMemory(), [], broadcaster), broadcaster)
    with TestClient(app) as client:
        assert client.post("/api/uilog", json=SELECTOR).json() == {"recorded": True}
        assert client.post("/api/uilog", json={"surface": "nope"}).json() == {
            "recorded": False}
    surfaces = [entry["surface"] for entry in uilog.read(log)]
    assert surfaces == ["selector"]
    # No emulator attached -> no frame. Recorded anyway: a UI observation with
    # no game frame still has a wall clock, and refusing it would blind the
    # log exactly while the emulator is closed.
    assert uilog.read(log)[0]["frame"] is None


def test_the_log_lives_beside_the_journal_it_belongs_to():
    """Three journals exist on this machine and all are valid. The UI log sits
    in the SAME directory as the db so `tools/what_happened.py` picking the
    freshest journal picks the matching UI log for free — reading one
    checkout's screen against another's events would be confidently wrong."""
    from sm64_events.core.paths import db_path
    assert uilog.log_path().parent == db_path().parent


def test_sanitize_is_pure_so_the_endpoint_has_no_shaping_of_its_own(tmp_path):
    """The route is three lines on purpose: everything it decides is decided
    here, where it is testable without a server."""
    assert uilog.sanitize(SELECTOR)["surface"] == "selector"
    assert uilog.sanitize({"surface": "target", "cards": []}) == {
        "surface": "target", "cards": []}
    assert json.dumps(uilog.sanitize(TARGET))  # stays JSON-serialisable


def test_record_stamps_a_supplied_clock(log):
    when = datetime(2026, 8, 2, 21, 42, 19, tzinfo=timezone.utc)
    stored = uilog.record(SELECTOR, frame=7, now=when)
    assert stored["server_utc"] == when.isoformat()
