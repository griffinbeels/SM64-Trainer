"""Every part of the old Scene viewer is still reachable in the Ghost Lab (round 9, item 5), on the real page.

His words: "It's EXTREMELY cluttered and difficult to understand ... It needs to be organized so that similar
things are grouped aesthetically"; his pick: "the Three Tabs approach ... maybe we dont have a tab for Save
States, and instead it's just a thin strip that shows the screenshot previews, under the video player? Ghosts
tab selected by default"; "instead of "Scene viewer" it should be called "Ghost Lab"". Regrouping must lose
nothing, so FEATURES names every part the old page had (and the one it gained) with where it lives now, and
each must be visible after at most one step: opening its tab, or the bar's More menu, or the play bar's Sound
menu. The old title row is gone.

The world is test_ui_scene_steady.py's (the 40-frame recording with sound, the Ghosts tab's lists, the strip's
slots, a ghost made into a scene) with test_ui_scene_recording.py's live recording switch, through the UI
fixture (headless, no GPU).
"""
from playwright.sync_api import sync_playwright

import test_ui_attempt_scene as attempt_scene
import test_ui_scene_ghosts as scene_ghosts
import test_ui_scene_recording as recording
import test_ui_scene_steady as steady

# Where each part lives now: its container, and the one step that shows it (None: on the page as it opens).
PLACES = {
    "bar": (".ghost-lab-top", None),
    "picture": (".scene-lab-stage", None),
    "play bar": (".ghost-lab-play", None),
    "strip": (".ghost-lab-strip", None),
    "Ghosts tab": ("#ghost-lab-panel-ghosts", "Ghosts"),
    "Camera tab": ("#ghost-lab-panel-camera", "Camera"),
    "More": (".ghost-lab-more-pop", "More"),
    "Sound menu": (".ghost-lab-sound-pop", "Sound"),
}

# (what it is, where it was before round 9, where it is now, a selector inside that place). Parts that only
# show in a state (a cinematic camera's settings, the cue, a notice, Drawing..., a player's "N more", the
# tuning panel, the stage's note, watching a row) are reached by the steps after the table.
FEATURES = [
    ("Course/star picker", "stage picker row", "bar", ".scene-lab-target"),
    ("Strategy dropdown", "stage picker row", "bar", ".scene-lab-stage-picker select"),
    ("Scene title", "title row", "bar", ".ghost-lab-watching[title*=' · ']"),
    ("Close", "title row", "bar", "button[aria-label='Close']"),
    ("Scene recording sign", "title row", "bar", ".scene-recording"),
    ("Stop/Start recording", "title row", "bar", ".scene-recording button[aria-label='Stop recording']"),
    ("Lab packages Opener", "above the player", "More", ".scene-lab-open select"),
    ("Picture", "player", "picture", "canvas"),
    ("Camera tag", "player", "picture", ".scene-lab-tag"),
    ("Restart", "transport", "play bar", "button[aria-label='Restart']"),
    ("Back 1", "transport", "play bar", "button[aria-label='Back 1']"),
    ("Play", "transport", "play bar", ".ghost-lab-play-main"),
    ("Forward 1", "transport", "play bar", "button[aria-label='Forward 1']"),
    ("Loop", "transport", "play bar", "button[aria-label='Loop']"),
    ("Frame slider", "transport", "play bar", "input[aria-label='Frame']"),
    ("Frame readout", "transport", "play bar", ".scene-lab-frame"),
    ("Action steps", "Action row", "play bar", "button[aria-label='On to the next action change']"),
    ("Action name", "Action row", "play bar", ".scene-lab-frame small"),
    ("Play speed", "camera path block", "play bar", "[aria-label='Play speed'] button"),
    ("Mute", "Sound row", "Sound menu", ".scene-lab-sound button[aria-pressed]"),
    ("Volume", "Sound row", "Sound menu", "input[aria-label='Volume']"),
    ("Music", "Sound row", "Sound menu", "button:text-is('Music')"),
    ("Sound effects", "Sound row", "Sound menu", "button:text-is('Sound effects')"),
    ("Record", "Record row", "Camera tab", ".ghost-lab-row[aria-label=Film] button:has-text('Record')"),
    ("Recorded camera", "camera row", "Camera tab", ".ghost-lab-row[aria-label=Camera] button:text-is('Recorded camera')"),
    ("Ghost views", "camera row", "Camera tab", ".ghost-lab-row[aria-label=Camera] .scene-lab-view"),
    ("Free camera", "camera row", "Camera tab", ".ghost-lab-row[aria-label=Camera] button:text-is('Free camera')"),
    ("Look at Mario", "camera row", "Camera tab", ".ghost-lab-row[aria-label=Camera] button:text-is('Look at Mario')"),
    ("Set focus", "camera row", "Camera tab", ".ghost-lab-row[aria-label=Camera] button:has-text('Set focus')"),
    ("Reset view", "camera row", "Camera tab", ".ghost-lab-row[aria-label=Camera] button:text-is('Reset view')"),
    ("HUD", "camera row", "Camera tab", ".ghost-lab-row[aria-label=Look] .ghost-lab-pair button:text-is('HUD')"),
    ("Mario", "camera path block", "Camera tab", ".ghost-lab-row[aria-label=Look] .ghost-lab-pair button:text-is('Mario')"),
    ("Cinematic cameras", "Cinematic row", "Camera tab", ".ghost-lab-row[aria-label=Cinematic] button:text-is('Orbit')"),
    ("Follow", "Cinematic row", "Camera tab", ".ghost-lab-row[aria-label=Cinematic] .scene-lab-rig-follow select"),
    ("Tilt", "Lens row", "Camera tab", ".ghost-lab-row[aria-label=Look] input[aria-label='Tilt']"),
    ("Level", "Lens row", "Camera tab", ".ghost-lab-row[aria-label=Look] button:text-is('Level')"),
    ("Lens", "Lens row", "Camera tab", ".ghost-lab-row[aria-label=Look] input[aria-label='Lens']"),
    ("Game's lens", "Lens row", "Camera tab", ".ghost-lab-row[aria-label=Look] button:text-is(\"Game's lens\")"),
    ("Flight speed", "camera row", "Camera tab", ".ghost-lab-row[aria-label=Fly] .scene-lab-speed"),
    ("Camera presets (views)", "Cameras row", "Camera tab", ".ghost-lab-row[aria-label=Views] .scene-lab-slot"),
    ("Camera path", "camera path block", "Camera tab", ".ghost-lab-row[aria-label=Path] button:text-is('Path')"),
    ("Pictures quality", "title row", "More", ".scene-lab-quality select"),
    ("Tune controls", "player", "More", "button:text-is('Tune controls')"),
    ("Every key (the hint)", "hint line", "More", ".ghost-lab-keys kbd"),
    ("Readout numbers", "under the player", "More", ".ghost-lab-numbers .scene-lab-readout"),
    ("Save states", "Save states row", "strip", ".ghost-lab-slot[data-slot='9'] button"),
    ("Save state's line", "Save states row", "strip", ".ghost-lab-strip-line"),
    ("Yours list", "ghost panel", "Ghosts tab", ".ghost-lab-segments button:text-is('Yours')"),
    ("Top list", "ghost panel", "Ghosts tab", ".ghost-lab-segments button:text-is('Top')"),
    ("Top: strategy (All strategies first)", "ghost panel", "Ghosts tab",
     ".scene-lab-ghost-section[aria-label='Top ghosts'] select[aria-label='Strategy for top ghosts']"),
    ("Find a player", "ghost panel", "Ghosts tab", "input[aria-label='Find a player by name']"),
    ("Show beside (Add/Remove)", "ghost panel", "Ghosts tab", "li.scene-lab-ghost > button.ghost-lab-eye"),
    ("Watch a row (new)", "-", "Ghosts tab", "li.scene-lab-ghost > button.ghost-lab-watch"),
    ("Their view", "ghost panel", "Ghosts tab", ".ghost-lab-scene-list .scene-lab-ghost-view"),
    ("A ghost's Mute", "ghost panel", "Ghosts tab", ".ghost-lab-scene-list .scene-lab-ghost-mute"),
    ("Remove all", "ghost panel", "Ghosts tab", ".ghost-lab-hide-all"),
]


def _open_place(page, place: str) -> str:
    """Shows `place` (its tab, or its menu, closing any other first); returns its container's selector."""
    container, step = PLACES[place]
    page.keyboard.press("Escape")                                   # any menu open closes
    page.wait_for_function("![...document.querySelectorAll('.ghost-lab-pop')].some((pop) => !pop.hidden)", timeout=5000)
    if step in ("Ghosts", "Camera"):
        page.locator(f"#ghost-lab-tab-{step.lower()}").click()
    elif step == "More":
        page.locator(".ghost-lab-more > button").click()
    elif step == "Sound":
        page.locator(".ghost-lab-sound > button").click()
    if step:
        page.locator(f"{container}:not([hidden])").wait_for(timeout=5000)
    return container


def _reached(page) -> list:
    """Each FEATURES row that is not visible after its one step: (feature, where, selector)."""
    missing = []
    for feature, _before, place, selector in FEATURES:
        container = _open_place(page, place)
        part = page.locator(f"{container} {selector}").first
        if not (part.count() and part.is_visible()):
            missing.append((feature, place, selector))
    page.keyboard.press("Escape")
    return missing


def _states_reach(page, seen) -> None:
    """The parts that show in a state, each from its place: a cinematic camera's settings and the cue on the
    picture, a notice there (Shift with a slot holding no view), Drawing... while a picture is on its way, "N
    more" under a player Find a player found, the tuning panel under the tray, the bar's note for a star with no
    scene, and a watched row becoming the scene."""
    _open_place(page, "Camera tab")
    page.locator(".ghost-lab-row[aria-label=Cinematic] button:text-is('Orbit')").click()
    page.locator(".scene-lab-stage .scene-lab-rig-options").wait_for(timeout=5000)      # on the picture
    page.locator(".ghost-lab-row[aria-label=Camera] button:text-is('Recorded camera')").click()
    page.locator(".ghost-lab-row[aria-label=Camera] button:has-text('Set focus')").click()
    page.locator(".scene-lab-stage .scene-lab-cue").wait_for(timeout=5000)
    assert "W A S D" in page.locator(".ghost-lab-keys").text_content()   # the hint's keys, in More
    page.keyboard.press("Escape")
    page.keyboard.press("Shift+Digit5")
    page.wait_for_function("document.querySelector('.scene-lab-stage .scene-lab-notice')?.textContent"
                           ".includes('No camera saved in slot 5')", timeout=5000)
    page.keyboard.press("ArrowRight")
    page.wait_for_function("window.__drawing === true", timeout=5000)
    _open_place(page, "More")
    page.locator(".ghost-lab-more-pop button:text-is('Tune controls')").click()
    page.locator(".ghost-lab-tray ~ .scene-lab-tune").wait_for(timeout=5000)
    _open_place(page, "Ghosts tab")
    page.get_by_role("searchbox", name="Find a player by name").fill("sully")
    page.locator("#ghost-lab-panel-ghosts .scene-lab-ghost-more").wait_for(timeout=5000)
    page.get_by_role("searchbox", name="Find a player by name").fill("")
    page.get_by_role("searchbox", name="Find a player by name").press("Enter")
    seen["release"] = True                                          # the watched row's scene comes at once
    page.locator("li.scene-lab-ghost[data-ref='top-all-1'] > .ghost-lab-watch").click()
    page.wait_for_function("document.querySelector(`li[data-ref='top-all-1'] > .ghost-lab-watch`)"
                           "?.getAttribute('aria-pressed') === 'true'", timeout=20000)
    assert seen["opened"][-1] == {"ghost": "top-all-1"}
    assert page.locator(".ghost-lab-top .ghost-lab-watching strong").inner_text() == scene_ghosts.PLAYERS[1]
    scene_ghosts._pick(page, "Whomp's Fortress", "Blast Away the Wall")
    page.wait_for_function(f"document.querySelector('.ghost-lab-top .ghost-lab-top-note')?.textContent"
                           f" === {scene_ghosts.NO_SCENE!r}", timeout=20000)
    assert page.locator(".ghost-lab-top .ghost-lab-top-note").is_visible()


# Whether "Drawing..." ever showed on the picture (it shows while a picture is on its way).
DRAWING = r"""
(() => {
  window.__drawing = false;
  new MutationObserver(() => { if (document.querySelector('.scene-lab-stage .scene-lab-busy')) window.__drawing = true; })
    .observe(document, { subtree: true, childList: true });
})()
"""


def test_every_part_of_the_old_viewer_is_one_step_away_and_the_title_row_is_gone(tmp_path, monkeypatch):
    lease, folder = recording.Lease(), tmp_path / "lab-root"
    world, live = steady._stubs(tmp_path), recording._recording(lease, folder)

    def extra(app, lab, seen):
        world(app, lab, seen)
        live(app, lab, seen)
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=extra) as (base, seen), \
            sync_playwright() as play:
        scene_ghosts._answer_ghosts(monkeypatch, seen)
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.add_init_script(DRAWING)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            ready = page.locator(f"{attempt_scene.ROWS} .attempt-scene:not([disabled])").first
            ready.wait_for(timeout=20000)
            page.locator('.nav-item[title="Ghost Lab"]:visible').wait_for(timeout=5000)   # the sidebar's name
            assert page.locator('.nav-item[title="Scene viewer"]').count() == 0
            ready.click()
            page.wait_for_function(steady.DRAWN, timeout=20000)
            page.wait_for_function(f"!({steady.PLAYING})", timeout=30000)
            page.wait_for_function("document.querySelector('.ghost-lab-sound > button')?.title.startsWith('Volume')",
                                   timeout=20000)
            page.locator(".scene-recording button").wait_for(timeout=20000)
            assert page.locator("#ghost-lab-tab-ghosts").get_attribute("aria-selected") == "true"   # Ghosts first
            page.locator("li.scene-lab-ghost[data-ref='top-all-0'] > button.ghost-lab-eye").click()
            scene_ghosts._shown(page, "top-all-0")                    # a ghost beside the run: its tools show
            assert page.locator(".scene-lab .workshop-hero, .scene-lab-title").count() == 0   # the title row is gone
            for width in (850, 1280):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(150)
                missing = _reached(page)
                assert not missing, (width, missing)
            _open_place(page, "Camera tab")
            hud, mario = (page.locator(f".ghost-lab-pair button:text-is('{name}')").bounding_box() for name in ("HUD", "Mario"))
            assert abs(hud["y"] - mario["y"]) <= 1, (hud, mario)    # HUD and Mario on one line
            _states_reach(page, seen)
            assert errors == []
        finally:
            seen["release"] = True
            browser.close()
