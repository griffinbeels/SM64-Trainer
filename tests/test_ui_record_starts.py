"""Recorded alternate starts preserve one path and one save definition."""
from frontend_runner import run_frontend


def test_recorded_alternate_starts():
    run_frontend("recordstarts.test.js")
