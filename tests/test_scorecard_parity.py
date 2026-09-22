"""An imported runner grades himself at exactly 0.00 on every tile, at every step.

Round 34 (2026-09-05), his definition of done, verbatim: "if I import a
player, and then set the scorecard to track that player, our definition of
done here is that for any given player we do that for, the scorecard should
match exactly... We need to find a combination of test players that results
in 100% coverage across every single row of the spreadsheet. If we were to
import all of their times, and then set the scorecard to all of them, it
should be +0.00 across every row. At each step (importing player 1 -> setting
scorecard to player 1, additionally importing player 2 -> setting scorecard to
player1 + player2, etc) it should be +0.00 for all cards, by definition."

The runner set is not hand-picked: `tools/scorecard_parity.py::greedy_cover`
picks, from the BUNDLED library seed, the fewest runners whose columns
between them hold every worksheet row the import can land on the card, so a
re-scraped seed re-picks its own set and the claim stays "every row" rather
than "these seven names". The two shadowing bugs this pins were both found
by this exact walk: round 33's (a slower strategy landing after a faster one)
and round 34's (a merged (JP)/(US) row landing both of a runner's times under
one strategy, the later ROM hiding the faster one -- six of Raisn's tiles and
two of RONC3NA's).
"""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

import scorecard_parity as parity  # noqa: E402

from calibration_reuse import reusing_calibrations  # noqa: E402
from sm64_events.core.paths import bundled_sheet_library  # noqa: E402


def test_every_importable_row_is_covered_and_every_step_grades_at_zero(tmp_path):
    library_path = bundled_sheet_library()
    assert library_path is not None, "no bundled library seed"
    payload = parity.load_payload(library_path)
    # The store OWNS the file it is given and rewrites it, so a walk that
    # points it at the seed edits a tracked file -- that happened on
    # 2026-09-05 and turned 14 unrelated tests red in the next full run,
    # with nothing failing at the time. `open_app` copies; this proves it.
    seed_before = library_path.read_bytes()

    with reusing_calibrations():
        app, service = parity.open_app(tmp_path, library_path)
    with TestClient(app) as client:
        keys = {tile["key"] for tile in parity.card_tiles(client, "overall")}
        runners, covered, total, tiles_unreached = parity.greedy_cover(payload, keys, limit=40)
        assert total > 0 and covered == total, (
            f"the cover reaches {covered} of {total} importable worksheet rows")
        assert not tiles_unreached, tiles_unreached
        assert 1 <= len(runners) <= 40, runners

        assert client.put("/api/scorecard/regions",
                          json={"regions": parity.REGIONS}).status_code == 200
        for step, runner in enumerate(runners, 1):
            landed = client.post("/api/import/sheet", json=parity.import_body(runner))
            assert landed.status_code == 200, landed.text
            assert client.put("/api/scorecard/goal",
                              json=parity.goal_for(runners[:step])).status_code == 200
            tiles = parity.card_tiles(client, "overall")
            graded = [tile for tile in tiles if tile.get("goal_cs") is not None]
            assert graded, f"step {step}: the goal grades nothing"
            mismatches = [(parity.classify(tile), tile["row"], tile["label"],
                           tile.get("you_cs"), tile.get("goal_cs"))
                          for tile in tiles if parity.classify(tile)]
            assert not mismatches, (
                f"step {step} (+{runner}, goal = {runners[:step]}): "
                f"{len(mismatches)} tiles do not read +0.00: {mismatches[:8]}")
        # Every tile the card carries has been graded at least once by the end.
        assert len(graded) == len(tiles), (len(graded), len(tiles))
    assert library_path.read_bytes() == seed_before, (
        "the walk rewrote the bundled library seed")


@pytest.mark.parametrize("regions", [["us"], ["jp"], ["us", "jp"]])
def test_the_region_toggle_moves_you_and_goal_together(tmp_path, regions):
    """Whichever regions the card includes, YOU and a runner's own GOAL are
    the same minimise-then-merge over the same rows -- so the runner still
    grades himself at zero with one region off, not only with both on."""
    library_path = bundled_sheet_library()
    payload = parity.load_payload(library_path)
    with reusing_calibrations():
        app, _service = parity.open_app(tmp_path, library_path)
    with TestClient(app) as client:
        keys = {tile["key"] for tile in parity.card_tiles(client, "overall")}
        runners, _covered, _total, _unreached = parity.greedy_cover(payload, keys, limit=1)
        runner = runners[0]
        assert client.post("/api/import/sheet",
                           json=parity.import_body(runner)).status_code == 200
        assert client.put("/api/scorecard/regions", json={"regions": regions}).status_code == 200
        assert client.put("/api/scorecard/goal",
                          json=parity.goal_for([runner])).status_code == 200
        tiles = parity.card_tiles(client, "overall")
        mismatches = [(parity.classify(tile), tile["label"], tile.get("you_cs"),
                       tile.get("goal_cs")) for tile in tiles if parity.classify(tile)]
        assert not mismatches, mismatches[:8]
        assert any(tile.get("goal_cs") is not None for tile in tiles)
