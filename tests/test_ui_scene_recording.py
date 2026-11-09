"""The live scene recording's sign and switch on the real page (clean-behavior, 2026-11-06): the fixture's
Ghost Lab, the lab's real routes and a lease double. The Ghost Lab's bar says in a few words that the lab
records his play (the whole sentence on hover and for a reader), one press stops it (the lease stops, the
switch is kept for the next server) and starts it again, at both supported widths, with no part of the sign
clipped and Stop and Start one width (round 9).
"""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

import test_ui_attempt_scene as attempt_scene
from frontend_runner import run_frontend
from sm64_events.server import scene_lab_api

ROW = ".scene-recording"
WORDS = {"running": "Recording your play so each attempt gets a scene.",
         "off": "Scene recording is off. Attempts you play now get no scene."}
SHORT = {"running": "Recording your play", "off": "Recording is off"}
# What the sign shows, and its button's box.
SIGN = """(() => { const row = document.querySelector('.scene-recording'), button = row.querySelector('button');
  const r = button.getBoundingClientRect();
  return { words: row.querySelector('.scene-recording-words .steady-text-shown').textContent, title: row.title,
    button: button.querySelector('.steady-text-shown').textContent, box: [Math.round(r.left), Math.round(r.width)] }; })()"""


def test_the_row_in_jsdom():
    run_frontend("scenerecording.test.js")


class Lease:
    def __init__(self):
        self.running, self.calls = False, []

    def start(self):
        self.running = True
        self.calls.append("start")

    def stop(self):
        self.running = False
        self.calls.append("stop")


class Cutter:
    def send(self, note):
        pass

    def close(self):
        pass


def _recording(lease, folder):
    """The served lab as main.py's is (live, following the fixture's own tracker and so its journal),
    with a lease double and a silent cutter, once the wrapper writes: the cutter says it records."""
    def rig(app, lab, seen):
        lab.live_on, lab.lease_factory, lab.cutter_factory = True, lambda root: lease, lambda folder, on_event: Cutter()
        lab._lab_root = lambda: folder
        lab.follow_attempts(seen["tracker"])
        lab.scenes.recording = {"recording": True, "reason": None}
    return rig


def _says(page, words: str) -> None:
    page.wait_for_function("words => document.querySelector(%r)?.textContent.includes(words)" % ROW, arg=words,
                           timeout=20000)


@pytest.mark.parametrize("width", [850, 1280])
def test_the_sign_shows_the_recording_and_the_switch_stops_and_starts_it(width, tmp_path, monkeypatch):
    lease, folder = Lease(), tmp_path / "lab-root"
    shots = tmp_path / "shots"
    shots.mkdir()
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_recording(lease, folder)) as (base, _seen), \
            sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": width, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            page.locator('.nav-item[title="Ghost Lab"]:visible').click()
            _says(page, WORDS["running"])
            assert page.locator(f"{ROW}.is-running").count() == 1
            light = page.evaluate("getComputedStyle(document.querySelector('.scene-recording-light')).backgroundColor")
            assert light not in ("rgba(0, 0, 0, 0)", "transparent")
            running = page.evaluate(SIGN)
            assert (running["words"], running["title"], running["button"]) == (SHORT["running"], WORDS["running"], "Stop")
            page.locator(".scene-lab .ghost-lab-top").screenshot(path=str(shots / "recording.png"))
            fits = page.evaluate("""(() => {
              const row = document.querySelector('.scene-recording'), hero = row.closest('.ghost-lab-top');
              const box = row.getBoundingClientRect(), outer = hero.getBoundingClientRect();
              const parts = [...row.children].filter((part) => !part.matches('.sr-only'))   // the sentence for a reader
                .map((part) => part.getBoundingClientRect());
              return { rowOverflow: row.scrollWidth - row.clientWidth, heroOverflow: hero.scrollWidth - hero.clientWidth,
                       inside: box.left >= outer.left - 0.5 && box.right <= outer.right + 0.5,
                       partsInside: parts.every((part) => part.left >= box.left - 0.5 && part.right <= box.right + 0.5) };
            })()""")
            assert fits == {"rowOverflow": 0, "heroOverflow": 0, "inside": True, "partsInside": True}, fits
            assert lease.calls == ["start"] and lease.running            # the server's start took the lease
            page.get_by_role("button", name="Stop recording").click()
            _says(page, WORDS["off"])
            assert lease.calls == ["start", "stop"] and not lease.running
            assert json.loads((folder / scene_lab_api.RECORDING_SWITCH).read_text()) == {"on": False}
            off = page.evaluate(SIGN)
            assert (off["words"], off["title"], off["button"]) == (SHORT["off"], WORDS["off"], "Start")
            assert off["box"] == running["box"], (running, off)              # Stop and Start: one place, one width
            page.locator(".scene-lab .ghost-lab-top").screenshot(path=str(shots / "off.png"))
            page.get_by_role("button", name="Start recording").click()
            page.wait_for_function("document.querySelector('.scene-recording:not(.is-off)') !== null", timeout=20000)
            assert lease.calls == ["start", "stop", "start"] and lease.running
            assert errors == []
        finally:
            browser.close()
    named = os.environ.get("SM64_RECORDING_SHOTS")
    if named:
        for shot in shots.iterdir():
            shot.replace(Path(named) / f"{width}-{shot.name}")
