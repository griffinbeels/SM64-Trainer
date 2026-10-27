"""Record in the Scene viewer (M4 round 6, item 3): the timeline the page keeps and the Record row's
flow, in jsdom (tests/frontend/scenerecord.test.js). The export itself is tests/test_scene_export.py;
the video it makes on the real renderer is the round's GPU check.
"""
from frontend_runner import run_frontend


def test_the_page_keeps_one_entry_per_display_frame_and_ends_at_show_in_folder():
    run_frontend("scenerecord.test.js")
