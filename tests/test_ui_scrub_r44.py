"""Bounded replay scrubbing and cancellation, using shipped frontend modules."""
from frontend_runner import run_frontend


def test_scrub_request_ownership_and_pause_poll_recovery():
    run_frontend("reviewscrub_r44.test.js")
