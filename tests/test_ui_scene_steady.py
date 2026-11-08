"""The Ghost Lab's controls never move when one of them changes (round 9, items 3 and 5).

Griffin, 2026-11-08: "when paused the ghost viewer ui is like this, but when playing, the UI is like
this. As you can see, the play bar is getting longer and shorter. This is really frustrating to use.
The UI should not be bouncing around like that when we go between playing / paused."

The real page through the UI fixture (test_ui_attempt_scene.py's scratch lab and fake renderer host,
headless, no GPU), on scene_sound_fixture.py's 40-frame recording with sound, so the frame count grows
a digit as it plays; the Ghosts tab's lists, the save-state strip's nine slots and a ghost made into a
scene are answered by stubs (test_ui_scene_ghosts.py's, and the slots below). At 850, 1200, 1280 and
1600 pixels the bar (picker, the run you watch, note, More, Close), the player card (the picture, every
control, the slider and readout of the play bar, every tile of the strip and its line) and the tray (its
tabs and its panel) keep their boxes through paused and playing, frame 1 and the last, Loop on and off,
the Sound menu and Mute in it, a ghost shown with its eye and hidden again, a strip press answered, the
Camera tab, Free camera, Set focus, four cinematic cameras, a tilt, Record, Stop and the video being
made; while the Camera tab shows, its rows' controls keep theirs too. Then a row is watched and its
scene is made slowly (the play bar wears the build's progress over the picture), and the readout is
planted with its widest values and every one-line note with a long sentence: none of it may move
anything either. Menus, the picture's overlays and the build's progress lie over the page and are not
measured. Restoring the readout sized by its digits turns this red (measured 2026-11-08 by serving
the play bar without its SteadyText). And as the page opens, no block stands emptier than a line.
"""
import asyncio
import base64
import json
from pathlib import Path

from fastapi import APIRouter, Response
from playwright.sync_api import sync_playwright

import scene_sound_fixture as sound_fixture
import test_ui_attempt_scene as attempt_scene
import test_ui_scene_ghosts as scene_ghosts

WIDTHS = (850, 1200, 1280, 1600)
DRAWN = "!!document.querySelector('.scene-lab-readout')"
PLAYING = "document.querySelector('.scene-lab-bar[aria-label=Playback] .primary').textContent.trim() === 'Pause'"
FRAME = "document.querySelector('.scene-lab-frame').textContent"
BUILD = {"what": "scene", "stage": "re-simulating", "done": 131, "total": 318}
WATCHED = "top-all-3"                        # the row made into a scene, slowly

# A tiny JPEG (one flat colour) for the saved tiles' pictures.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    "2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAAQABADASIAAhEBAxEB/8QAFQAB"
    "AQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhED"
    "EQA/AKpgA//Z")


def _slot(n: int, **fields) -> dict:
    return {"slot": n, "set": None, "state": None, "translated": None, "under": None, "picture": None,
            "loaded_last": False, **fields}


def _kept(identifier: str, level: int, place: str, build: str = "us") -> dict:
    return {"id": identifier, "name": f"{place} {identifier}", "level": level, "area": 1, "place": place, "build": build,
            "source": "lab", "first_seen_utc": "2026-11-08T12:00:00Z"}


# The nine slots as GET /slot-sets names them: the star's own save and the course's with pictures of the Lab's, a
# place for one saved in the game, one changed to work in the other version, the one loaded last, and empties.
SLOTS = {"context": {"course_id": 1, "course": "Bob-omb Battlefield", "star_id": 1, "star": "Big Bob-omb on the Summit",
                     "entity_key": "star:1:1"}, "game": {"build": "us"}, "slots": [
    _slot(1, set="star", state=_kept("a1", 9, "Bob-omb Battlefield"), translated=False,
          picture="/api/scene-lab/slot-sets/pictures/a1"),
    _slot(2, set="course", state=_kept("a2", 9, "Bob-omb Battlefield"), translated=False,
          picture="/api/scene-lab/slot-sets/pictures/a2", loaded_last=True),
    _slot(3, set="course", state=_kept("a3", 24, "Whomp's Fortress"), translated=False),
    _slot(4, set="course", state=_kept("a4", 9, "Bob-omb Battlefield", "jp"), translated=True,
          picture="/api/scene-lab/slot-sets/pictures/a4"),
    *(_slot(n) for n in range(5, 10))]}

# Every box the page keeps still, by a stable key: the bar and its parts, the player card's rows and every
# control of the play bar and the strip, the tray, its tabs and its panel; and while the Camera tab shows,
# each of its rows and their controls ("camera: ..."), measured inside the panel's own scroll. What the
# Film row shows after its button (the time, the video's progress, its sentence) is that row's own news:
# only its place is measured. The bar's note is one line that is empty until it says something (0 px tall,
# then a line, measured 2026-11-08, moving nothing else): its place across the bar is measured.
MEASURE = r"""(() => {
  const page = (r) => [r.left + scrollX, r.top + scrollY, r.width, r.height];
  const round = (values) => values.map((v) => Math.round(v * 10) / 10);
  const box = (el) => round(page(el.getBoundingClientRect()));
  // What lies over the page and takes no room: the menus, the picture's overlays, a build's progress.
  const over = (el) => !!el.closest('.ghost-lab-pop, .scene-lab-overlay, .scene-lab-tag, .scene-lab-busy, .ghost-lab-build');
  const boxes = {};
  const each = (prefix, root, selector, measure = box) => [...root.querySelectorAll(selector)].filter((el) => !over(el))
    .forEach((el, i) => { boxes[`${prefix} ${i} ${el.tagName.toLowerCase()}`] = measure(el); });
  const bar = document.querySelector('.ghost-lab-top');
  boxes.bar = box(bar);
  each('bar', bar, '.scene-lab-stage-picker, .scene-lab-target, select, .ghost-lab-watching, .scene-recording, button');
  const note = box(bar.querySelector('.ghost-lab-top-note'));
  boxes['bar note'] = [note[0], note[2]];
  const player = document.querySelector('.scene-lab-player');
  boxes.player = box(player);
  [...player.children].forEach((row, i) => { boxes[`player row ${i} ${row.getAttribute('aria-label') || row.classList[0]}`] = box(row); });
  each('play bar', player.querySelector('.ghost-lab-play'), 'button, input, .scene-lab-frame, .scene-lab-frame small');
  each('strip', player.querySelector('.ghost-lab-strip'), '.ghost-lab-slot, .ghost-lab-slot > button, .ghost-lab-strip-line');
  const tray = document.querySelector('.ghost-lab-tray');
  boxes.tray = box(tray);
  each('tab', tray, '[role=tab]');
  const panel = tray.querySelector('.ghost-lab-panel:not([hidden])');
  boxes.panel = box(panel);
  if (panel.id === 'ghost-lab-panel-camera') {
    const origin = panel.getBoundingClientRect();
    const inside = (el) => { const r = el.getBoundingClientRect();
      return round([r.left - origin.left + panel.scrollLeft, r.top - origin.top + panel.scrollTop, r.width, r.height]); };
    const news = (el) => !!el.closest('.scene-lab-record-status') && !el.matches('.scene-lab-record-status');
    panel.querySelectorAll('.ghost-lab-row').forEach((row) => {
      const name = row.getAttribute('aria-label');
      boxes[`camera: ${name}`] = inside(row);
      [...row.querySelectorAll('button, input, select, output, .scene-lab-record-status')].filter((el) => !news(el) && !over(el))
        .forEach((el, i) => { boxes[`camera: ${name} ${i} ${el.tagName.toLowerCase()}`] = inside(el); });
    });
  }
  return boxes;
})()"""

# Each block of the bar, the player card and the tray's tabs (and every SteadyText paragraph on the page) that
# stands emptier than one of its own lines: its height inside its padding less the height its visible content
# spans. A place kept for words or controls that are not there yet must not leave a hole (the lead's review,
# 2026-11-08: the cinematic settings' kept place stood ~100 px empty under the Cinematic row).
HOLES = r"""(() => {
  const DRAWS = ['INPUT', 'SELECT', 'BUTTON', 'CANVAS', 'IMG', 'PROGRESS', 'OUTPUT', 'svg'];
  const drawn = (root) => {
    let top = Infinity, bottom = -Infinity;
    const visit = (el) => {
      const style = getComputedStyle(el);
      if (style.display === 'none') return;
      const r = el.getBoundingClientRect();
      const own = style.visibility === 'visible' && r.height > 0 && (DRAWS.includes(el.tagName)
        || [...el.childNodes].some((node) => node.nodeType === Node.TEXT_NODE && node.data.trim()));
      if (own) { top = Math.min(top, r.top); bottom = Math.max(bottom, r.bottom); }
      if (!DRAWS.includes(el.tagName)) [...el.children].forEach(visit);
    };
    [...root.children].forEach(visit);
    return bottom > top ? bottom - top : 0;
  };
  const player = document.querySelector('.scene-lab-player');
  const blocks = [document.querySelector('.ghost-lab-top'), ...player.children, document.querySelector('.ghost-lab-tabs'),
    ...[...document.querySelectorAll('.ghost-lab .steady-text.is-block')].filter((block) => block.offsetParent)];
  return blocks.map((block) => {
    const style = getComputedStyle(block), r = block.getBoundingClientRect();
    const inside = r.height - ['paddingTop', 'paddingBottom', 'borderTopWidth', 'borderBottomWidth']
      .reduce((sum, side) => sum + parseFloat(style[side]), 0);
    const line = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.5;
    return { block: block.getAttribute('aria-label') || block.className, empty: Math.round(inside - drawn(block)),
      line: Math.round(line) };
  }).filter((hole) => hole.empty > hole.line);
})()"""

# The readout at its widest: the frame line five digits a side and the move's line long, written into the
# text nodes the page drew (its next change writes them back); and every one-line note at a long sentence:
# the bar's, the strip's, the tabs', the Views row's and the Film row's.
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
  words.forEach((node, i) => { node.data = i ? '' : 'Mario: reading automatic dialog while riding a shell at speed'; });
  const long = 'This sentence is much longer than any note the Ghost Lab writes, so it has to be cut '
    + 'short where its place ends instead of pushing the controls after it onto a new line. '.repeat(2);
  const notes = ['.ghost-lab-top-note', '.ghost-lab-strip-line', '.ghost-lab-tab-words small',
    '.scene-lab-cameras .scene-lab-hint', '.scene-lab-record-status .scene-lab-record-note'].map((selector) => {
    const nodes = [...document.querySelectorAll(selector)];
    nodes.forEach((node) => { node.textContent = long; });
    return nodes.length;
  });
  return [line.length, words.length, ...notes];
})()"""


def _stubs(tmp_path: Path):
    """The scratch lab's one package swapped for the 40-frame recording with sound, the Film row's videos made
    into scratch folders by the "frames" encoder over the fake host, test_ui_scene_ghosts.py's lists and socket
    answer, the strip's nine slots and a save's answer, and a watched row made into a scene only once the test
    says so (seen["release"]), with /status's "resim" saying how far it is meanwhile."""
    (tmp_path / "sound").mkdir()
    recording = sound_fixture.recording(tmp_path / "sound")

    def extra(app, lab, seen):
        package = sound_fixture.pk.with_audio(recording["package"], recording["rom"], recording["payload"])
        (tmp_path / "lab" / "packages" / attempt_scene.PACKAGE).write_bytes(package)
        lab.exports.clips_dir, lab.exports.jobs_dir = tmp_path / "clips", tmp_path / "jobs"
        lab.exports.options = {"host": "fake", "fake_host": {"delay_ms": 20}, "encoder": {"kind": "frames"}}
        scene_ghosts._stubs(app, lab, seen)
        seen.update(saved=[], building=False, release=False)
        router = APIRouter()

        @router.get("/api/scene-lab/slot-sets")
        def slots():
            return SLOTS

        @router.get("/api/scene-lab/slot-sets/pictures/{name}")
        def picture(name: str):
            return Response(JPEG, media_type="image/jpeg")

        @router.post("/api/scene-lab/moment")
        def moment(body: dict):
            return {"id": "planted", "exact": True, "build": "us", "frame": body.get("frame")}

        @router.post("/api/scene-lab/moment/{token}/write")
        def write(token: str, body: dict):
            seen["saved"].append(body["slot"])
            return {"written": True, "slot": body["slot"], "build": "us"}

        @router.get("/api/scene-lab/status")
        async def status():
            answer = await lab.status()
            return {**answer, "resim": BUILD} if seen["building"] else answer

        @router.post("/api/scene-lab/open")
        async def open_scene(body: dict):
            seen["opened"].append(body)
            if "ghost" in body:                # watched from a row: made into a scene once the test lets it
                seen["building"] = True
                for _ in range(1200):
                    if seen["release"]:
                        break
                    await asyncio.sleep(0.1)
                seen["building"] = False
                return await lab.open(attempt_scene.PACKAGE)
            described = {**await lab.open(attempt_scene.PACKAGE), "attempt": body["attempt"]}
            seen["host_pids"].append(described.get("renderer_pid"))
            return described

        app.include_router(router)
        for _ in router.routes:
            app.router.routes.insert(0, app.router.routes.pop())
    return extra


def _settle(page, ms: int = 150) -> dict:
    page.wait_for_timeout(ms)
    return page.evaluate(MEASURE)


def _tab(page, name: str) -> None:
    page.locator(f"#ghost-lab-tab-{name.lower()}").click()
    page.locator(f"#ghost-lab-panel-{name.lower()}:not([hidden])").wait_for(timeout=5000)


def _shoot(page, shots: Path, name: str) -> None:
    page.locator(".scene-lab").screenshot(path=str(shots / f"steady-{name}.png"))


def _states(page, shots: Path, width: int) -> dict:
    """The page's boxes in every state one control can put it in, keyed by state."""
    seen = {}
    _playback_states(page, shots, width, seen)
    _ghost_and_strip_states(page, seen)
    _camera_states(page, seen)
    _record_states(page, shots, width, seen)
    _tab(page, "Ghosts")
    seen["ghosts tab again"] = _settle(page)
    return seen


def _playback_states(page, shots: Path, width: int, seen: dict) -> None:
    """Frame 1 and the last, Loop, Play and Pause, the Sound menu and Mute in it. In the first (the page as it
    opens) no block stands emptier than a line."""
    page.keyboard.press("Home")
    page.wait_for_function(f"{FRAME}.includes('Frame 1 of 40')")
    seen["frame 1"] = _settle(page)
    holes = page.evaluate(HOLES)
    assert not holes, (width, holes)
    _shoot(page, shots, f"paused-{width}")
    page.keyboard.press("End")
    page.wait_for_function(f"{FRAME}.includes('Frame 40 of 40')")
    seen["last frame"] = _settle(page)
    play_bar = page.locator(".ghost-lab-play")
    loop = play_bar.get_by_role("button", name="Loop", exact=True)
    if loop.get_attribute("aria-pressed") != "true":
        loop.click()
    seen["loop on"] = _settle(page)
    play_bar.locator(".primary").click()
    page.wait_for_function(PLAYING)
    seen["playing"] = _settle(page, 60)
    _shoot(page, shots, f"playing-{width}")
    seen["playing later"] = _settle(page, 400)
    play_bar.locator(".primary").click()
    page.wait_for_function(f"!({PLAYING})")
    seen["paused"] = _settle(page)
    loop.click()
    seen["loop off"] = _settle(page)
    sound = play_bar.get_by_role("button", name="Sound", exact=True)
    sound.click()
    page.locator(".ghost-lab-sound-pop .scene-lab-sound").wait_for(timeout=5000)
    seen["sound menu"] = _settle(page)
    mute = page.locator(".ghost-lab-sound-pop .scene-lab-sound button").first
    mute.click()
    assert mute.get_attribute("aria-pressed") == "true"
    seen["muted"] = _settle(page)
    mute.click()
    sound.click()
    page.locator(".ghost-lab-sound-pop").wait_for(state="detached", timeout=5000)


def _ghost_and_strip_states(page, seen: dict) -> None:
    """A ghost shown beside the run with its eye, then Hide all; a strip tile pressed and its answer said."""
    eye = page.locator("li.scene-lab-ghost[data-ref='top-all-0'] > button.ghost-lab-eye")
    eye.click()
    scene_ghosts._shown(page, "top-all-0")
    seen["a ghost shown"] = _settle(page)
    page.locator(".ghost-lab-hide-all").click()
    scene_ghosts._shown(page, "top-all-0", False)
    seen["ghosts hidden"] = _settle(page)
    page.keyboard.press("End")
    page.locator(".ghost-lab-slot[data-slot='5'] button").click()
    page.wait_for_function("document.querySelector('.ghost-lab-strip-line').textContent === 'Frame 40 is in slot 5.'")
    seen["a save state saved"] = _settle(page)


def _camera_states(page, seen: dict) -> None:
    """The Camera tab, Free camera, Set focus, four cinematic cameras, a tilt."""
    _tab(page, "Camera")
    seen["camera tab"] = _settle(page)
    cameras = page.locator(".scene-lab-bar[aria-label=Camera]")
    cameras.get_by_role("button", name="Free camera", exact=True).click()
    page.wait_for_function("document.querySelector('.scene-lab-tag').textContent === 'Free camera'")
    seen["free camera"] = _settle(page)
    cameras.get_by_role("button", name="Recorded camera", exact=True).click()
    cameras.get_by_role("button", name="Set focus").click()
    page.wait_for_function("document.querySelector('.scene-lab-cue')?.textContent.includes('Click a surface')")
    seen["set focus"] = _settle(page)
    page.keyboard.press("Escape")
    cinematic = page.locator(".scene-lab-rigs")
    for kind in ("Orbit", "Front", "Side", "Tripod"):
        cinematic.get_by_role("button", name=kind, exact=True).click()
        page.locator(".scene-lab-rig-options").wait_for(timeout=5000)
        seen[f"{kind} camera"] = _settle(page)
    cameras.get_by_role("button", name="Recorded camera", exact=True).click()
    page.locator(".scene-lab-rig-options").wait_for(state="detached", timeout=5000)
    page.evaluate("""(() => { const tilt = document.querySelector('.scene-lab-lens input[aria-label=Tilt]');
      tilt.value = '-135'; tilt.dispatchEvent(new Event('input', { bubbles: true })); })()""")
    page.wait_for_function("document.querySelector('.scene-lab-lens output').textContent.includes('-135')")
    seen["tilted"] = _settle(page)
    cameras.get_by_role("button", name="Recorded camera", exact=True).click()


def _record_states(page, shots: Path, width: int, seen: dict) -> None:
    """Record, the video being made, the video made."""
    film = page.locator(".scene-lab-record")
    film.get_by_role("button", name="Record", exact=True).click()
    page.locator(".scene-lab-record-time", has_text="Recording 0:00").wait_for(timeout=5000)
    seen["recording"] = _settle(page)
    film.get_by_role("button", name="Stop", exact=True).click()
    page.locator(".scene-lab-record progress, .scene-lab-record-link").first.wait_for(timeout=10000)
    seen["making the video"] = _settle(page, 30)
    page.locator(".scene-lab-record-link").wait_for(timeout=60000)
    seen["video made"] = _settle(page)
    _shoot(page, shots, f"made-{width}")


def _moved(states: dict) -> list:
    """Each box that is not where the first state that drew it drew it: (state, key, first, now). The Camera
    tab's rows ("camera: ...") are compared among the states that show that tab."""
    moved = []
    for camera in (False, True):
        drawn = [(state, {key: value for key, value in boxes.items() if key.startswith("camera: ") == camera})
                 for state, boxes in states.items()]
        drawn = [(state, boxes) for state, boxes in drawn if boxes]
        if not drawn:
            continue
        (first_state, first), *rest = drawn
        for state, boxes in rest:
            for key in sorted(set(first) | set(boxes)):
                if first.get(key) != boxes.get(key):
                    moved.append((f"{first_state} -> {state}", key, first.get(key), boxes.get(key)))
    return moved


def _while_a_row_becomes_the_scene(page, shots: Path, measured: dict, seen: dict) -> None:
    """A row watched: its scene is made slowly, the old one plays on and the play bar wears the progress; then
    the widest readouts and the long notes, at every width; then the scene is let through."""
    page.locator(f"li.scene-lab-ghost[data-ref='{WATCHED}'] > .ghost-lab-watch").click()
    page.wait_for_function("document.querySelector('.ghost-lab-play .ghost-lab-build-share')?.textContent.includes('%')",
                           timeout=10000)
    assert seen["opened"][-1] == {"ghost": WATCHED}
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 1000})
        measured[width]["a row becoming the scene"] = _settle(page, 200)
        _shoot(page, shots, f"building-{width}")
    planted = page.evaluate(PLANT)
    assert planted[0] >= 1 and planted[1] >= 1 and all(planted[2:]), planted
    for width in WIDTHS:
        page.set_viewport_size({"width": width, "height": 1000})
        measured[width]["widest readouts, long notes"] = _settle(page, 200)
        _shoot(page, shots, f"widest-{width}")
    seen["release"] = True
    page.wait_for_function("!document.querySelector('.ghost-lab-play .ghost-lab-build')", timeout=20000)


def measure_everything(tmp_path, monkeypatch, *, served=None) -> dict:
    """Every width's boxes in every state, keyed by width then state. `served(page)` may change how the page is
    served before it loads (a guard's mutation check)."""
    shots = tmp_path / "shots"
    shots.mkdir()
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_stubs(tmp_path)) as (base, seen), \
            sync_playwright() as play:
        scene_ghosts._answer_ghosts(monkeypatch, seen)
        browser = play.chromium.launch(headless=True, args=["--disable-gpu", "--autoplay-policy=no-user-gesture-required"])
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/replay/reveal", lambda route: route.fulfill(json={"ok": True}))
            if served:
                served(page)
            page.goto(base)
            ready = page.locator(f"{attempt_scene.ROWS} .attempt-scene:not([disabled])").first
            ready.wait_for(timeout=20000)
            ready.click()
            page.wait_for_function(DRAWN, timeout=20000)
            page.wait_for_function("document.querySelector('.ghost-lab-sound > button')?.title.startsWith('Volume')",
                                   timeout=20000)                       # the recording's sound is on the page
            page.wait_for_function(f"!({PLAYING})", timeout=30000)      # its first play ran out
            page.locator("li.scene-lab-ghost[data-ref='top-all-0']").wait_for(timeout=20000)
            page.locator(".ghost-lab-slot.is-saved img").first.wait_for(timeout=20000)   # the strip read the slots
            measured = {}
            for width in WIDTHS:
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(200)
                measured[width] = _states(page, shots, width)
            _while_a_row_becomes_the_scene(page, shots, measured, seen)
            assert seen["saved"] == [5] * len(WIDTHS)
            assert errors == []
        finally:
            seen["release"] = True
            browser.close()
    (tmp_path / "steady-boxes.json").write_text(json.dumps(measured, indent=1), encoding="utf-8")
    return measured


def test_no_control_moves_or_resizes_when_another_changes_state(tmp_path, monkeypatch):
    measured = measure_everything(tmp_path, monkeypatch)
    moved = {width: _moved(states) for width, states in measured.items()}
    assert all(not rows for rows in moved.values()), json.dumps(moved, indent=1)[:6000]
    for width, states in measured.items():                             # the measure reached every part
        keys, camera = set(states["frame 1"]), set(states["camera tab"])
        for part in ("bar ", "play bar ", "strip ", "tab ", "player row "):
            assert any(key.startswith(part) for key in keys), (width, part, sorted(keys))
        assert sum(key.startswith("strip ") and key.endswith(" button") for key in keys) == 9, (width, sorted(keys))
        for row in ("Camera", "Cinematic", "Look", "Views", "Path", "Fly", "Film"):
            assert f"camera: {row}" in camera, (width, row, sorted(camera))
