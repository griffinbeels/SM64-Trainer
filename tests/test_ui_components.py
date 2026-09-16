"""Fast interaction checks; browser workflows still own layout and integration."""
from frontend_runner import run_frontend


def test_timefields_component():
    run_frontend("timefields.test.js", "standardwrites.test.js")


def test_inputtimeline_picture_reading():
    run_frontend("inputtimeline.test.js", "videopicture.test.js")
