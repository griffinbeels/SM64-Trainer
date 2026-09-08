"""Timeline playback keeps static drawing cached and interaction mappings current."""
from frontend_runner import run_frontend


def test_timeline_picture_render_cost():
    run_frontend("inputtimelineperformance.test.js")
