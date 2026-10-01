"""The editor and recorder obey the server's shared/personal save capability."""
from frontend_runner import run_frontend


def test_segment_authoring_save_scope():
    run_frontend("segmentsavescope.test.js")
