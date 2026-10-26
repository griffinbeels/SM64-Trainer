"""The Practice log's Ghost button (M4, lab only) on the real page.

His words (round 2, item 1): "Maybe a Ghost Button to the left of the play button, that
immediately takes you to the ghost viewer and starts it immediately?"

The UI fixture boots the real app offline with the scene lab mounted on a scratch lab folder:
test_scene_package.py's synthetic recording as the one package, fake_viewer_host.py as the
renderer host (a child process: no window, no GPU) and its FakeEvaluator. The lab's per-attempt
API -- GET /api/scene-lab/attempt-scenes?ids= and POST /api/scene-lab/open {"attempt"} -- is
stubbed here with its agreed shape, ahead of the lab's own routes: the server half is not what
these tests measure. The button's states and its polling are pinned in
tests/frontend/attemptscene.test.js, the viewer's autoplay in tests/frontend/sceneviewer.test.js.
"""
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import psutil
import pytest
from fastapi import APIRouter, HTTPException
from playwright.sync_api import sync_playwright

from frontend_runner import run_frontend
from source_scan import strip_comments

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import fake_viewer_host  # noqa: E402
import gpu_gate  # noqa: E402
import scene_session  # noqa: E402
import test_scene_lab_viewer as viewer_tests  # noqa: E402
import ui_fixture  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402

UI = ROOT / "src" / "sm64_events" / "ui"
PACKAGE = "synthetic.sm64scene"
CARD = ".log-card.log-card-active"
ROWS = f"{CARD} .attempt-table tr:not(.replay-row):not(.pb-replay-notice)"
NO_CAPTURE = "Capture was not recording when this attempt was played."
# The stub's answer by attempt id: the fixture's ids are whatever its seeding made, so the state
# is the id's remainder, and a card of four or more rows shows every state.
READY, CUTTING, UNAVAILABLE, UNKNOWN = 0, 1, 2, 3
CUT_AFTER_ASKS = 2


def test_the_ghost_button_states_and_polling_in_jsdom():
    run_frontend("attemptscene.test.js")


def test_the_ghost_button_never_loads_the_viewer():
    """The log is on every page; the viewer's code loads only once the lab answers (app.js)."""
    for name in ("components/attemptscene.js", "components/attemptlog.js"):
        source = strip_comments((UI / name).read_text(encoding="utf-8"))
        assert "sceneviewer" not in source and "scenelab.css" not in source, name


def _lab(folder: Path) -> Path:
    """A scratch lab: the synthetic recording packaged, its ROM, a plugin folder, the config."""
    synthetic = viewer_tests.synthetic
    rom, segment7 = synthetic._rom()
    session = folder / "tap-synthetic"
    session.mkdir(parents=True)
    records, sequence = [], 0
    for frame in range(6):
        records.append(synthetic._tap_record(synthetic.pk.tap.REC_TASK, sequence,
                                             synthetic._task_body(synthetic._rdram(rom, segment7, frame), frame)))
        sequence += 1
        if frame != 4:
            records.append(synthetic._tap_record(synthetic.pk.tap.REC_PRESENT, sequence,
                                                 synthetic._present_body(frame)))
            sequence += 1
    (session / "tap.bin").write_bytes(b"".join(records))
    (session / "session.txt").write_text(
        f"schema=scene-lab-tap/1\nwrapper_build=test\nqpc_frequency=10000000\nrdram_size={synthetic.RDRAM}\n"
        f"rom_header={synthetic.pk.rom_header_hex(rom)}\n", encoding="ascii")
    (session / "armed.txt").write_text(f"extra={synthetic.TIMER:x}:4\n# identity=global_timer:{synthetic.TIMER:x}:4\n",
                                       encoding="ascii")
    (folder / "packages").mkdir()
    viewer_tests._write_package(session, rom, folder / "packages" / PACKAGE)
    (folder / "rom.z64").write_bytes(rom)
    (folder / "Plugin").mkdir()
    config = folder / "lab.json"
    config.write_text(json.dumps({"rom": str(folder / "rom.z64"), "packages": str(folder / "packages"),
                                  "pj64_plugin": str(folder / "Plugin"),
                                  "map": str(folder / "MappingUS.map")}), encoding="utf-8")
    return config


def _stub_attempt_api(app, lab, seen: dict) -> None:
    """GET /api/scene-lab/attempt-scenes and POST /api/scene-lab/open {"attempt"}, ahead of the lab's routes."""
    router = APIRouter()

    @router.get("/api/scene-lab/attempt-scenes")
    def attempt_scenes(ids: str):
        asked = [int(part) for part in ids.split(",")]
        seen["asks"].append(asked)
        scenes = {}
        for attempt in asked:
            kind = attempt % 4
            seen["cutting_asks"][attempt] = seen["cutting_asks"].get(attempt, 0) + (kind == CUTTING)
            if kind == READY or (kind == CUTTING and seen["cutting_asks"][attempt] > CUT_AFTER_ASKS):
                scenes[str(attempt)] = {"state": "ready", "seconds": 0.2}
            elif kind == CUTTING:
                scenes[str(attempt)] = {"state": "cutting"}
            elif kind == UNAVAILABLE:
                scenes[str(attempt)] = {"state": "unavailable", "reason": NO_CAPTURE}
        return {"scenes": scenes}

    @router.post("/api/scene-lab/open")
    async def open_scene(body: dict):
        seen["opened"].append(body)
        if "attempt" not in body:
            return await lab.open(body["package"])
        if body["attempt"] % 4 == UNKNOWN:
            raise HTTPException(404, "The lab has no scene for this attempt.")
        described = await lab.open(PACKAGE)
        seen["host_pids"].append(described.get("renderer_pid"))
        return {**described, "attempt": body["attempt"]}

    app.include_router(router)
    for _ in router.routes:
        app.router.routes.insert(0, app.router.routes.pop())


@contextmanager
def _served(tmp_path, monkeypatch, *, lab_on: bool):
    seen = {"asks": [], "cutting_asks": {}, "opened": [], "host_pids": []}
    if lab_on:
        config = _lab(tmp_path / "lab")
        monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
        monkeypatch.setenv("SM64_SCENE_LAB", str(config))
        original = ui_fixture.create_app

        def create_app(*args, **kwargs):
            app = original(*args, **kwargs)
            lab = scene_lab_api.mount_scene_lab(app, config, session_options={
                "host_factory": scene_session.command_host(fake_viewer_host.command()),
                "evaluator_factory": lambda package, rom: fake_viewer_host.FakeEvaluator()})
            _stub_attempt_api(app, lab, seen)
            return app
        monkeypatch.setattr(ui_fixture, "create_app", create_app)
    else:
        monkeypatch.delenv("SM64_SCENE_LAB", raising=False)
    with ui_fixture.serve_ui() as base:
        yield base, seen
    for pid in seen["host_pids"]:                      # the lab closed its renderer host with the app
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE, pid


# Records when the click lands, each request and picture on the scene socket, each canvas draw, and
# when Play turns into Pause, all on the page's own clock.
TIMELINE = r"""
(() => {
  const at = window.__scene = { click: null, sends: [], pictures: [], draws: [], pause: null, opens: [] };
  document.addEventListener("click", (event) => {
    if (event.target.closest?.(".attempt-scene")) at.click = performance.now();
  }, true);
  const fetched = window.fetch.bind(window);
  window.fetch = (url, options) => {
    if (String(url).endsWith("/api/scene-lab/open")) at.opens.push(performance.now());
    return fetched(url, options);
  };
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    constructor(...args) {
      super(...args);
      this.addEventListener("message", (event) => {
        if (typeof event.data !== "string") at.pictures.push(performance.now());
      });
    }
    send(data) {
      try { const message = JSON.parse(data); if (!message.kind) at.sends.push([performance.now(), message.frame]); }
      catch { /* not a request */ }
      return super.send(data);
    }
  };
  for (const name of ["drawImage", "putImageData"]) {
    const native = CanvasRenderingContext2D.prototype[name];
    CanvasRenderingContext2D.prototype[name] = function (...args) {
      if (this.canvas.closest?.(".scene-lab")) at.draws.push(performance.now());
      return native.apply(this, args);
    };
  }
  new MutationObserver(() => {
    if (at.pause == null && [...document.querySelectorAll(".scene-lab-bar button")]
        .some((button) => button.textContent.trim() === "Pause")) at.pause = performance.now();
  }).observe(document, { subtree: true, childList: true, characterData: true });
})()
"""

# The fixture's rows are short; the widest realistic ones are planted into the page's own session
# view: a death with its detail and a 1' time, and a run on a long-named strategy.
PLANT = r"""
(() => {
  const fetched = window.fetch.bind(window);
  window.fetch = async (url, options) => {
    const response = await fetched(url, options);
    if (!String(url).startsWith("/api/session")) return response;
    const view = await response.clone().json();
    const plant = (node) => {
      if (Array.isArray(node)) return node.forEach(plant);
      if (!node || typeof node !== "object") return;
      if (Array.isArray(node.attempts) && node.attempts.length >= 4) {
        Object.assign(node.attempts[1], { strat_tag: "Owlless Sideflip into the Cage (no BLJ, JP)", other_strat: true });
        Object.assign(node.attempts[2], { outcome: "death", outcome_detail: "finished_dead", igt: "1'02\"34",
          rta: "1'03\"10", igt_frames: 1870, rta_frames: 1893, pb_action: null, pb_blocked: null,
          pb_delta_frames: null, caveat: null });
      }
      Object.values(node).forEach(plant);
    };
    plant(view);
    return new Response(JSON.stringify(view), { status: response.status, headers: { "Content-Type": "application/json" } });
  };
})()
"""

MEASURE = """(() => {
  const box = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
  const card = document.querySelector(%s).getBoundingClientRect();
  return {
    overflow: document.documentElement.scrollWidth - window.innerWidth,
    card: [card.left, card.right],
    rows: [...document.querySelectorAll(%s)].map((row) => {
      const slots = row.querySelector('.attempt-action-slots'), ghost = slots.querySelector('.attempt-scene');
      const buttons = [...slots.querySelectorAll('button')];
      return { height: row.getBoundingClientRect().height,
        ghost: ghost && { disabled: ghost.disabled, title: ghost.title, label: ghost.getAttribute('aria-label'),
                          first: slots.firstElementChild === ghost, box: box(ghost) },
        replay: box(slots.querySelector('button[aria-label="View replay"]')),
        tops: buttons.map((button) => Math.round(button.getBoundingClientRect().top)),
        right: Math.max(...buttons.map((button) => button.getBoundingClientRect().right)),
        words: buttons.map((button) => button.textContent.trim() || button.getAttribute('aria-label')) };
    }),
  };
})()""" % (json.dumps(CARD), json.dumps(ROWS))


def _measure(page) -> dict:
    return json.loads(page.evaluate(f"JSON.stringify({MEASURE})"))


@pytest.mark.parametrize("width", [850, 1280])
def test_every_row_wears_its_scene_state_beside_play_without_wrapping(width, tmp_path, monkeypatch):
    with _served(tmp_path, monkeypatch, lab_on=True) as (base, seen), sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": width, "height": 1000})
            page.add_init_script(PLANT)
            page.goto(base)
            page.locator(f"{ROWS} .attempt-scene").first.wait_for(timeout=20000)
            page.locator(f"{ROWS} .attempt-outcome", has_text="death").wait_for(timeout=20000)
            shown = _measure(page)
            ghosts = [row["ghost"] for row in shown["rows"]]
            states = sorted({(g["disabled"], g["title"]) for g in ghosts if g})
            assert (False, "Open in Scene viewer") in states and (True, "Scene finishing…") in states
            assert (True, NO_CAPTURE) in states
            assert None in ghosts                                   # an attempt the lab does not know
            assert all(g["first"] and g["label"] == "Open in Scene viewer" for g in ghosts if g)
            for row in shown["rows"]:
                assert len(set(row["tops"])) == 1, row              # one line of buttons: nothing wrapped
                assert row["right"] <= shown["card"][1], row        # nothing clipped past the card
                if row["ghost"]:                                    # the play button's size, directly left of it
                    assert row["ghost"]["box"][2:] == row["replay"][2:], row
                    assert row["ghost"]["box"][0] < row["replay"][0]
            heights = [row["height"] for row in shown["rows"]]
            assert max(heights) - min(heights) < 1, heights        # the base log's rows differ by half a pixel
            assert shown["overflow"] <= 0
            page.locator(CARD).screenshot(path=str(tmp_path / f"ghost-rows-{width}.png"))
            # A cutting row turns on by itself, and the asking stops once nothing is cutting.
            page.wait_for_function(
                f"![...document.querySelectorAll({json.dumps(ROWS)} + ' .attempt-scene')]"
                ".some((b) => b.title === 'Scene finishing…')", timeout=10000)
            asks = len(seen["asks"])
            page.wait_for_timeout(2500)
            assert len(seen["asks"]) == asks
            (tmp_path / f"ghost-rows-{width}.json").write_text(json.dumps(shown, indent=1), encoding="utf-8")
        finally:
            browser.close()


def test_one_click_opens_the_attempt_in_the_scene_viewer_and_plays_it(tmp_path, monkeypatch):
    with _served(tmp_path, monkeypatch, lab_on=True) as (base, seen), sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.add_init_script(TIMELINE)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            ready = page.locator(f"{ROWS} .attempt-scene:not([disabled])").first
            ready.wait_for(timeout=20000)
            ready.click()
            page.locator(".scene-lab canvas").wait_for(timeout=20000)
            page.wait_for_function("window.__scene.pause != null && window.__scene.draws.length >= 2", timeout=20000)
            page.wait_for_function("window.__scene.sends.some(([, frame]) => frame > 0)", timeout=20000)
            at = json.loads(page.evaluate("JSON.stringify(window.__scene)"))
            assert page.locator(".nav-item.on").inner_text().strip() == "Scene viewer"
            assert [body for body in seen["opened"]] == [{"attempt": seen["opened"][0]["attempt"]}]
            assert seen["opened"][0]["attempt"] % 4 == READY
            frames = [frame for _, frame in at["sends"]]
            assert frames[:2] == [0, 0]                     # the first picture, then Play's own first frame
            assert at["click"] < at["opens"][0] < at["sends"][0][0] < at["draws"][0] <= at["pause"]
            assert errors == []
            page.screenshot(path=str(tmp_path / "ghost-viewer-1280.png"))
            timeline = {"open_ms": at["opens"][0] - at["click"], "first_request_ms": at["sends"][0][0] - at["click"],
                        "first_picture_drawn_ms": at["draws"][0] - at["click"], "play_ms": at["pause"] - at["click"],
                        "play_first_drawn_ms": at["draws"][1] - at["click"], "frames_asked": frames}
            (tmp_path / "ghost-timeline.json").write_text(json.dumps(timeline, indent=1), encoding="utf-8")
            print("ghost click timeline", json.dumps(timeline))
        finally:
            browser.close()


def test_with_the_lab_off_no_row_asks_or_draws_a_ghost_button(tmp_path, monkeypatch):
    with _served(tmp_path, monkeypatch, lab_on=False) as (base, _seen), sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            asked = []
            page.on("request", lambda request: asked.append(request.url))
            page.goto(base)
            page.locator(ROWS).nth(2).wait_for(timeout=20000)
            page.wait_for_timeout(1500)
            assert page.locator(".attempt-scene").count() == 0
            assert not [url for url in asked if "attempt-scenes" in url]
            assert page.locator(".nav-item", has_text="Scene viewer").count() == 0
        finally:
            browser.close()
