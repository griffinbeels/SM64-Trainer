"""Arrow ownership is shared by real native replay components."""
from frontend_runner import run_frontend


def test_only_the_active_replay_receives_arrow_shortcuts():
    run_frontend("replaykeys.test.js")
