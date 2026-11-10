"""The Scene viewer's sound (M4 E4-audio, lab only): its map, steering, worklet and controls in jsdom.

The sound's timing against shown pictures needs a browser playing real audio; that measurement is
the lead's page check, not a unit test.
"""
from frontend_runner import run_frontend


def test_the_sound_follows_the_pictures_and_its_controls_say_what_they_do():
    run_frontend("sceneaudio.test.js")
