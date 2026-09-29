"""The practice log's open card when the target moves: the game's choice waits
for its first row, his pick opens at once (tests/frontend/logautoopen.test.js;
round 13, 2026-09-28)."""
from frontend_runner import run_frontend


def test_only_his_pick_or_a_first_row_moves_the_open_card():
    run_frontend("logautoopen.test.js")
