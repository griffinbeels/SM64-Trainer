"""Fast interaction checks; browser workflows still own layout and integration."""
from frontend_runner import run_frontend


def test_timefields_component():
    run_frontend("timefields.test.js")
