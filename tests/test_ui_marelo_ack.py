"""The rendered celebration returns its own calibration identity with its ACK."""
from frontend_runner import run_frontend


def test_celebration_ack_identity():
    run_frontend("marelocelebrate.test.js")
