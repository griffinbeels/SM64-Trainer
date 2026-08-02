"""`tools/what_happened.py` puts the journal and the UI log on ONE clock.

The interleaving IS the feature. "The cell disappeared" and "the level
changed" are useless separately and answer the question together — which came
first — so what these tests pin is the ordering and the pairing of a UI log
with its own journal.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from what_happened import merged, ui_rows  # noqa: E402


def _event(stamp, frame, kind, payload):
    return (stamp, frame, kind, json.dumps(payload))


def _write_ui(directory: Path, records):
    (directory / "ui_log.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8", newline="")


def test_ui_observations_interleave_with_events_by_wall_clock(tmp_path):
    _write_ui(tmp_path, [
        {"surface": "selector", "server_utc": "2026-08-02T21:42:19.240000+00:00",
         "frame": 2365499, "title": "Bowser in the Dark World",
         "cells": [{"name": "Reds", "active": True},
                   {"name": "Bowser 1 → WF", "active": False}]},
        {"surface": "selector", "server_utc": "2026-08-02T21:42:26.100000+00:00",
         "frame": 2365700, "title": "Bowser in the Dark World",
         "cells": [{"name": "Reds", "active": True}]},
    ])
    events = [
        _event("2026-08-02T21:42:19.176000Z", 2365499, "level_changed",
               {"from": 6, "to": 17}),
        _event("2026-08-02T21:42:19.211000Z", 0, "target_set",
               {"kind": "segment", "segment_id": 67}),
    ]
    rows = merged(events, ui_rows(tmp_path / "tracker.db", None), None)
    kinds = [row[2] for row in rows]
    assert kinds == ["level_changed", "target_set", "UI selector", "UI selector"]
    # And the reader can SEE the chip go: it is in the first observation and
    # gone from the second. That pair is the whole evidence for "it lingered,
    # then it was removed".
    assert "Bowser 1 → WF" in rows[2][3]
    assert "Bowser 1 → WF" not in rows[3][3]


def test_the_ui_log_is_read_from_beside_its_own_journal(tmp_path):
    """Three journals exist on this machine. Reading one checkout's screen
    against another's events would be confidently wrong, and the tool's whole
    reason for existing is that this mistake is silent."""
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    mine.mkdir(); theirs.mkdir()
    _write_ui(mine, [{"surface": "selector", "title": "MINE",
                      "server_utc": "2026-08-02T21:00:00+00:00", "frame": 1}])
    _write_ui(theirs, [{"surface": "selector", "title": "THEIRS",
                        "server_utc": "2026-08-02T21:00:00+00:00", "frame": 1}])
    drawn = ui_rows(mine / "tracker.db", None)
    assert len(drawn) == 1 and "MINE" in drawn[0][3]


def test_a_journal_with_no_ui_log_beside_it_is_not_an_error(tmp_path):
    """Every journal that predates this branch is in exactly this state, and
    so is a fresh clone."""
    assert ui_rows(tmp_path / "tracker.db", None) == []


def test_observations_older_than_the_events_are_dropped(tmp_path):
    """A `--session` read has no session id to filter the UI log by, so it is
    bounded by the earliest event instead. Without this, every observation the
    file has ever held would print above a one-minute session."""
    _write_ui(tmp_path, [
        {"surface": "selector", "server_utc": "2026-08-01T10:00:00+00:00",
         "frame": 1, "title": "yesterday", "cells": []},
        {"surface": "selector", "server_utc": "2026-08-02T21:42:20+00:00",
         "frame": 2, "title": "today", "cells": []},
    ])
    events = [_event("2026-08-02T21:42:19Z", 2365499, "level_changed",
                     {"from": 6, "to": 17})]
    rows = merged(events, ui_rows(tmp_path / "tracker.db", None), None)
    rendered = " ".join(row[3] for row in rows)
    assert "today" in rendered and "yesterday" not in rendered
