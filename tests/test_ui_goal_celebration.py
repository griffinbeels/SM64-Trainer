"""The goal plate's celebration fires only for the goal on screen when a run
ended (tests/frontend/goalplate.test.js; round 13, 2026-09-28)."""
from frontend_runner import run_frontend


def test_only_the_goal_on_screen_can_celebrate():
    run_frontend("goalplate.test.js")
