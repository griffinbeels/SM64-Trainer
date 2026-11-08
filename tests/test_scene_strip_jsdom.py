"""The Ghost Lab's save-state strip (ui/components/scenestrip.js) in jsdom: Usamune's nine slots under the
player, a press that saves the frame on screen with its picture, the one line that says what happened, and
the polling that stops with the page.
"""
from frontend_runner import run_frontend


def test_the_strip_in_jsdom():
    run_frontend("scenestrip.test.js")
