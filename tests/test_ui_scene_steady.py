"""The Scene viewer's controls never move when one of them changes (round 9, item 3).

Griffin, 2026-11-08: "when paused the ghost viewer ui is like this, but when playing, the UI is like
this. As you can see, the play bar is getting longer and shorter. This is really frustrating to use.
The UI should not be bouncing around like that when we go between playing / paused."

The real page through the UI fixture (test_ui_attempt_scene.py's scratch lab and fake renderer host,
headless, no GPU), on scene_sound_fixture.py's 40-frame recording with sound, so the frame count
grows a digit and the Sound row's per-frame note changes as it plays. At 850, 1200 and 1600 pixels
every control, slider, readout and row of the player card, and the ghost panel under it, keeps its
box through paused and playing, frame 1 and the last, Loop on and off, Mute on and off, Free camera
and Set focus (the hint), four cinematic cameras (their settings), a tilted lens readout, and
Record, Stop and the video being made. Then the frame readout is planted with its widest values
(five-digit frames, an eight-digit game frame) and every one-line status (Sound, Record, Cameras,
Save states) with a long sentence, which must not move anything either: a box sized by its words is
exactly what bounced. Restoring the plain Play/Pause label, the readout sized by its digits, or a
status line that wraps turns this red (measured 2026-11-08). Save states are never pressed here:
that writes a slot.
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

import scene_sound_fixture as sound_fixture
import test_ui_attempt_scene as attempt_scene

WIDTHS = (850, 1200, 1600)
DRAWN = "!!document.querySelector('.scene-lab-readout')"
PLAYING = "document.querySelector('.scene-lab-bar[aria-label=Playback] .primary').textContent.trim() === 'Pause'"
FRAME = "document.querySelector('.scene-lab-frame').textContent"

# Every control and row the player card draws, and the ghost panel under it, by a stable key: the
# row (its aria-label, else its class) and the control's place in it. What the Record row shows
# after its button (the time, the video's progress, its sentence) is that row's own news: only its
# slot is measured.
MEASURE = r"""(() => {
  const player = document.querySelector('.scene-lab-player');
  const box = (el) => { const r = el.getBoundingClientRect();           // on the page, wherever it is scrolled
    return [r.left + scrollX, r.top + scrollY, r.width, r.height].map((v) => Math.round(v * 10) / 10); };
  const boxes = {};
  [...player.children].forEach((row, i) => { boxes[`row ${i} ${row.getAttribute('aria-label') || row.classList[0]}`] = box(row); });
  const news = (el) => !!el.closest('.scene-lab-record-status') && !el.matches('.scene-lab-record-status');
  // A cinematic camera's own settings come and go with it: their place is a row above, measured whole.
  const rows = player.querySelectorAll('.scene-lab-bar:not(.scene-lab-rig-options):not(.scene-lab-rig-size)');
  rows.forEach((row) => {
    const name = row.getAttribute('aria-label');
    [...row.querySelectorAll('button, input, select, output, .scene-lab-frame, .scene-lab-sound-note, '
      + '.scene-lab-sound-sync, .scene-lab-record-status')].filter((el) => !news(el))
      .forEach((el, i) => { boxes[`${name} ${i} ${el.tagName.toLowerCase()}`] = box(el); });
  });
  player.querySelectorAll('.scene-lab-rig-size').forEach((size, i) => { boxes[`cinematic settings ${i}`] = box(size); });
  const ghosts = document.querySelector('.scene-lab-ghosts');
  if (ghosts) boxes.ghosts = box(ghosts);
  return boxes;
})()"""

# The readouts at their widest: every frame-count digit run five digits and the game frame eight,
# written into the text nodes the page drew (its next change writes them back).
PLANT = r"""(() => {
  const frame = document.querySelector('.scene-lab-frame');
  const texts = (root, inSmall) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const found = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!!node.parentElement.closest('small') === inSmall && node.data.trim()) found.push(node);
    }
    return found;
  };
  const line = texts(frame, false), words = texts(frame, true);
  line.forEach((node, i) => { node.data = i ? '' : 'Frame 99999 of 99999 so far'; });
  words.forEach((node, i) => { node.data = i ? '' : 'level 99, area 9, game frame 99999999'; });
  // And every one-line status at a long sentence: the Sound row's note, the Record row's, Cameras'
  // and Save states'.
  const long = 'This sentence is much longer than any status line the viewer writes, so it has to be cut '
    + 'short where its place ends instead of pushing the controls after it onto a new line. '.repeat(2);
  const statuses = ['.scene-lab-sound-note', '.scene-lab-record-status .scene-lab-record-note',
    '.scene-lab-cameras .scene-lab-hint', '.scene-lab-moment .scene-lab-hint'].map((selector) => {
    const node = document.querySelector(selector);
    if (node?.firstChild?.nodeType === Node.TEXT_NODE) node.firstChild.data = long;
    else if (node) node.textContent = long;
    return !!node;
  });
  return [line.length, words.length, ...statuses];
})()"""


def _sound_and_exports(tmp_path: Path):
    """The scratch lab's one package swapped for the 40-frame recording with sound, and the Record
    row's videos made into scratch folders by the "frames" encoder over the fake host."""
    (tmp_path / "sound").mkdir()
    recording = sound_fixture.recording(tmp_path / "sound")

    def extra(app, lab, seen):
        package = sound_fixture.pk.with_audio(recording["package"], recording["rom"], recording["payload"])
        (tmp_path / "lab" / "packages" / attempt_scene.PACKAGE).write_bytes(package)
        lab.exports.clips_dir, lab.exports.jobs_dir = tmp_path / "clips", tmp_path / "jobs"
        lab.exports.options = {"host": "fake", "fake_host": {"delay_ms": 20}, "encoder": {"kind": "frames"}}
    return extra


def _settle(page, ms: int = 150) -> dict:
    page.wait_for_timeout(ms)
    return page.evaluate(MEASURE)


def _states(page, shots: Path, width: int) -> dict:
    """The player card's boxes in every state one control can put it in, keyed by state."""
    seen = {}
    _playback_states(page, shots, width, seen)
    _camera_states(page, seen)
    _record_states(page, shots, width, seen)
    return seen


def _playback_states(page, shots: Path, width: int, seen: dict) -> None:
    """Frame 1 and the last, Loop, Play and Pause, Mute."""
    page.keyboard.press("Home")
    seen["frame 1"] = _settle(page)
    page.locator(".scene-lab-player").screenshot(path=str(shots / f"steady-paused-{width}.png"))
    page.keyboard.press("End")
    page.wait_for_function(f"{FRAME}.includes('Frame 40 of 40')")
    seen["last frame"] = _settle(page)
    loop = page.get_by_role("button", name="Loop")
    if loop.get_attribute("aria-pressed") != "true":
        loop.click()
    seen["loop on"] = _settle(page)
    page.locator(".scene-lab-bar[aria-label=Playback] .primary").click()
    page.wait_for_function(PLAYING)
    seen["playing"] = _settle(page, 60)
    page.locator(".scene-lab-player").screenshot(path=str(shots / f"steady-playing-{width}.png"))
    seen["playing later"] = _settle(page, 400)
    page.locator(".scene-lab-bar[aria-label=Playback] .primary").click()
    page.wait_for_function(f"!({PLAYING})")
    seen["paused"] = _settle(page)
    loop.click()
    seen["loop off"] = _settle(page)
    mute = page.locator(".scene-lab-sound button").first
    mute.click()
    assert mute.get_attribute("aria-pressed") == "true"
    seen["muted"] = _settle(page)
    mute.click()


def _camera_states(page, seen: dict) -> None:
    """Free camera, Set focus, four cinematic cameras, a tilt."""
    page.get_by_role("button", name="Free camera").click()
    page.wait_for_function("document.querySelector('.scene-lab-tag').textContent === 'Free camera'")
    seen["free camera"] = _settle(page)
    page.get_by_role("button", name="Recorded camera").click()
    page.get_by_role("button", name="Set focus").click()
    page.wait_for_function("document.querySelector('.scene-lab-hint').textContent.includes('Click a surface')")
    seen["set focus"] = _settle(page)
    page.keyboard.press("Escape")
    cinematic = page.locator(".scene-lab-rigs")
    for kind in ("Orbit", "Front", "Side", "Tripod"):
        cinematic.get_by_role("button", name=kind, exact=True).click()
        page.locator(".scene-lab-rig-options").wait_for(timeout=5000)
        seen[f"{kind} camera"] = _settle(page)
    page.get_by_role("button", name="Recorded camera").click()
    page.locator(".scene-lab-rig-options").wait_for(state="detached", timeout=5000)
    page.evaluate("""(() => { const tilt = document.querySelector('.scene-lab-lens input[aria-label=Tilt]');
      tilt.value = '-135'; tilt.dispatchEvent(new Event('input', { bubbles: true })); })()""")
    page.wait_for_function("document.querySelector('.scene-lab-lens output').textContent.includes('-135')")
    seen["tilted"] = _settle(page)
    page.get_by_role("button", name="Recorded camera").click()


def _record_states(page, shots: Path, width: int, seen: dict) -> None:
    """Record, the video being made, the video made."""
    page.get_by_role("button", name="Record", exact=True).click()
    page.locator(".scene-lab-record-time", has_text="Recording 0:00").wait_for(timeout=5000)
    seen["recording"] = _settle(page)
    page.get_by_role("button", name="Stop", exact=True).click()
    page.locator(".scene-lab-record progress, .scene-lab-record-link").first.wait_for(timeout=10000)
    seen["making the video"] = _settle(page, 30)
    page.locator(".scene-lab-record-link").wait_for(timeout=60000)
    seen["video made"] = _settle(page)
    page.locator(".scene-lab-player").screenshot(path=str(shots / f"steady-made-{width}.png"))


def _moved(states: dict) -> list:
    """Each box that is not where the first state drew it: (state, key, first, now)."""
    (first_state, first), *rest = states.items()
    moved = []
    for state, boxes in rest:
        for key in sorted(set(first) | set(boxes)):
            if first.get(key) != boxes.get(key):
                moved.append((f"{first_state} -> {state}", key, first.get(key), boxes.get(key)))
    return moved


def test_no_control_moves_or_resizes_when_another_changes_state(tmp_path, monkeypatch):
    shots = tmp_path / "shots"
    shots.mkdir()
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_sound_and_exports(tmp_path)) as (base, _), \
            sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu", "--autoplay-policy=no-user-gesture-required"])
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/replay/reveal", lambda route: route.fulfill(json={"ok": True}))
            page.goto(base)
            ready = page.locator(f"{attempt_scene.ROWS} .attempt-scene:not([disabled])").first
            ready.wait_for(timeout=20000)
            ready.click()
            page.wait_for_function(DRAWN, timeout=20000)
            page.locator(".scene-lab-sound").wait_for(timeout=20000)          # the recording's sound is on the page
            page.wait_for_function(f"!({PLAYING})", timeout=30000)             # its first play ran out
            measured = {}
            for width in WIDTHS:
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(200)
                measured[width] = _states(page, shots, width)
            planted = page.evaluate(PLANT)
            assert planted[0] >= 1 and planted[1] >= 1 and all(planted[2:]), planted
            for width in WIDTHS:
                page.set_viewport_size({"width": width, "height": 1000})
                measured[width]["widest readouts, long statuses"] = _settle(page, 200)
                page.locator(".scene-lab-bar[aria-label=Playback]").screenshot(
                    path=str(shots / f"steady-widest-{width}.png"))
            assert errors == []
        finally:
            browser.close()
    (tmp_path / "steady-boxes.json").write_text(json.dumps(measured, indent=1), encoding="utf-8")
    moved = {width: _moved(states) for width, states in measured.items()}
    assert all(not rows for rows in moved.values()), json.dumps(moved, indent=1)[:6000]
    for width, states in measured.items():                             # the measure reached every row
        keys = set(states["frame 1"])
        assert any(key.startswith("Playback ") for key in keys) and any(key.startswith("Sound ") for key in keys)
        assert any(key.startswith("Record ") for key in keys) and "ghosts" in keys, (width, sorted(keys))
