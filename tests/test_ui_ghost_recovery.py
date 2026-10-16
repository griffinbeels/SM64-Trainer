"""Ghost control recovery against the actual vendored Preact hook/control."""
from frontend_runner import run_frontend


def test_transient_ghost_reads_do_not_permanently_hide_active_controls():
    run_frontend("ghost-recovery.test.js")
