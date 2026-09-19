"""The file a failed try of the ghost is read from, without screenshots.

His first try could only be diagnosed from two screenshots he took himself,
because nothing the overlay did reached disk. What matters here is not that
the writer works but that the RECORD CARRIES THE NUMBERS -- rectangles as four
comparable integers, not as a repr somebody has to parse by eye.
"""
import json

import pytest

from sm64_events.ghost.window import diag
from sm64_events.ghost.window.geometry import Rect


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "data" / "ghost_window.jsonl"
    monkeypatch.setenv(diag.PATH_ENV, str(path))
    return path


def test_a_rectangle_is_written_as_numbers_a_reader_can_subtract(log_file):
    diag.record("place_mismatch", level="error",
                wanted=Rect(x=120, y=80, width=1600, height=1224),
                got=Rect(x=180, y=120, width=2400, height=1836),
                game=Rect(x=120, y=80, width=1600, height=1224),
                insert_after=0, correction=None)
    entry = diag.read()[0]
    assert entry["wanted"] == [120, 80, 1600, 1224]
    assert entry["got"] == [180, 120, 2400, 1836]
    assert entry["correction"] is None
    assert entry["level"] == "error"
    assert entry["event"] == "place_mismatch"
    assert entry["utc"].endswith("+00:00") and isinstance(entry["pid"], int)


def test_the_log_lands_where_the_rest_of_the_app_keeps_its_data(monkeypatch,
                                                                tmp_path):
    monkeypatch.delenv(diag.PATH_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    assert diag.log_path().parent.name == "data"
    assert diag.log_path().name.endswith(".jsonl")


def test_every_line_stands_alone_so_a_torn_file_still_reads(log_file):
    for index in range(3):
        diag.record("decision", show=True, reason="follow", rect=None,
                    index=index)
    log_file.write_text(log_file.read_text(encoding="utf-8") + '{"half',
                        encoding="utf-8", newline="")
    entries = diag.read()
    assert [entry["index"] for entry in entries] == [0, 1, 2], (
        "the process that writes this file is the one that may have been killed")


def test_the_file_stays_bounded(log_file, monkeypatch):
    monkeypatch.setattr(diag, "TRIM_ABOVE_BYTES", 400)
    monkeypatch.setattr(diag, "MAX_RECORDS", 3)
    for index in range(40):
        diag.record("decision", index=index, note="x" * 40)
    entries = diag.read()
    assert len(entries) <= diag.MAX_RECORDS + 1
    assert entries[-1]["index"] == 39, "the newest line is the one kept"


def test_a_log_that_cannot_be_written_never_stops_the_overlay(monkeypatch,
                                                              tmp_path):
    # A directory where the file should be: opening it for append raises.
    blocked = tmp_path / "ghost_window.jsonl"
    blocked.mkdir()
    monkeypatch.setenv(diag.PATH_ENV, str(blocked))
    assert diag.record("decision", show=False, reason="gone") is None


def test_no_test_can_reach_the_checkouts_own_ghost_log(tmp_path):
    """His second try was read off `data/ghost_window.jsonl`, and that file
    carried rows from test runs on ports 8099 and 8123 interleaved with his
    own -- `GhostSession` writes a row every time it starts or ends the
    window, so every session test appended to the dev checkout's copy. The
    shared conftest now roots it under the test's own tmp dir, the same move
    it makes for the UI log. Mutation-proved: drop that fixture and this test
    reads the repo path."""
    assert diag.log_path().is_relative_to(tmp_path), diag.log_path()


# --- keeping a ten-times-a-second log readable ------------------------------

def test_a_run_of_identical_rows_becomes_one_row_and_a_count():
    written = []
    fold = diag.Collapsing(record=lambda event, **fields: written.append(
        {"event": event, **fields}))
    for _ in range(5):
        fold("page_poll", state="raised", detail="timeout")
    fold("page_poll", state="status", detail=None)
    assert [entry["state"] for entry in written] == ["raised", "raised", "status"]
    assert written[1]["repeated"] == 4


def test_the_first_row_of_every_kind_is_always_written():
    """The bound may never cost a KIND of answer: that is what made his
    second try unreadable."""
    written = []
    fold = diag.Collapsing(record=lambda event, **fields: written.append(
        {"event": event, **fields}))
    for state in ("no_window", "raised", "nothing", "status", "no_window"):
        fold("page_poll", state=state)
    assert [entry["state"] for entry in written if "repeated" not in entry] == [
        "no_window", "raised", "nothing", "status", "no_window"]


def test_a_long_steady_run_is_not_invisible_for_long():
    written = []
    fold = diag.Collapsing(record=lambda event, **fields: written.append(
        {"event": event, **fields}), flush_every=10)
    for _ in range(25):
        fold("page_poll", state="raised")
    counts = [entry["repeated"] for entry in written if "repeated" in entry]
    assert counts == [10, 10], "a run that never ends still lands periodically"


def test_the_childs_own_stream_is_kept_verbatim(log_file):
    """What the session forwards: the child's event exactly as it printed it,
    which is all that survives when the child dies before writing itself."""
    said = {"event": "error", "detail": "WebView2Error: runtime not found"}
    diag.record("child_said", level="error", said=said)
    entry = diag.read()[0]
    assert entry["said"] == said
    assert json.dumps(entry), "every record has to survive a round trip"
