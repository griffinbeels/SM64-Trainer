"""The browser fixture must supply input from an unambiguous capture."""
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_fixture import FIXTURE_SEGMENT, serve_ui_live


@pytest.mark.parametrize("settings", [{}, {"seed_subsections": True},
    {"arm_segment": FIXTURE_SEGMENT, "seed_editor_fixtures": True}])
def test_fixture_attempt_inputs_are_reachable_through_the_real_api(settings):
    with serve_ui_live(**settings) as (base, tracker):
        attempts = tracker.db.attempts()
        problems = []
        shared = {}
        overlaps = 0
        for attempt in attempts:
            try:
                with urlopen(f"{base}/api/attempts/{attempt.id}/inputs") as response:
                    payload = json.load(response)
                if not payload["runs"]:
                    problems.append((attempt.id, attempt.anchor_frame, "empty"))
                for run in payload["runs"]:
                    for position in range(run["start"], run["start"] + run["length"]):
                        seam = next(row for row in payload["stretches"]
                                    if row[0] <= position < row[0] + row[2])
                        raw = seam[1] + position - seam[0]
                        key = (attempt.session_id, raw)
                        pad = (run["buttons"], run["stick_x"], run["stick_y"])
                        if key in shared:
                            overlaps += 1
                            assert pad == shared[key]
                        shared[key] = pad
            except HTTPError as error:
                problems.append((attempt.id, attempt.anchor_frame, error.code,
                                 error.read().decode()))
        assert attempts
        assert problems == []
        assert (tracker.session_id, 1045) not in shared  # the deliberate capture hole
        if settings.get("seed_subsections"):
            assert overlaps > 0


@pytest.mark.parametrize("target", [None, (2, 4)])
def test_reopening_a_scratch_snapshot_does_not_seed_inputs_into_its_history(tmp_path, target):
    path = tmp_path / "snapshot.db"
    with serve_ui_live(db_path=path) as (_, tracker):
        original_owner = tracker.session_id
        before = [tuple(row) for row in tracker.db._conn.execute(
            "SELECT * FROM input_chunks WHERE session_id=? ORDER BY id", (original_owner,))]
        assert before
    with serve_ui_live(db_path=path, target=target) as (_, tracker):
        assert tracker.session_id != original_owner
        after = [tuple(row) for row in tracker.db._conn.execute(
            "SELECT * FROM input_chunks WHERE session_id=? ORDER BY id", (original_owner,))]
        assert after == before
