"""The Scene viewer's stage picker and ghost panel end to end against the lab's REAL routes (M4 round 4,
items 2 and 3): no stub of GET /stage, GET /ghosts, POST /open or the socket's "ghosts" answer.

The UI fixture boots the real app with the scene lab mounted on a scratch lab: the attempt scenes are
attempt_scenes.AttemptScenes fed by test_scene_lab_ghosts.py's cutter double (one real package, cut for
the stage attempt), the renderer host is fake_viewer_host.py (headless, no GPU), and the ghost catalog
reads test_scene_lab_ghosts.py's journal and replay doubles over real native clips, the real rank
standards, a real local population and the real rival race. The page alone is driven, as he would:
pick a star, the stage opens, Your ghosts lists, Add, the lab draws it, Remove.
"""
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import fake_viewer_host  # noqa: E402
import gpu_gate  # noqa: E402
import package  # noqa: E402
import scene_attempt_fixture as fixture  # noqa: E402
import scene_session  # noqa: E402
import test_scene_lab_ghosts as catalog  # noqa: E402
import ui_fixture  # noqa: E402
from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders  # noqa: E402
from sm64_events.ghost.population import LocalPopulation  # noqa: E402
from sm64_events.ghost.rivals import Rivals  # noqa: E402
from sm64_events.ranks.standards import RankStandards  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402

STAGE = 12                                  # his fastest Standard run of Shoot into the Wild Blue (catalog.run's star)
YOURS = '11"33'                             # attempt 30's time: the ghost of his he adds
SULLY = "SullyLikesBigChungus"


def _world(folder: Path) -> SimpleNamespace:
    """What the catalog reads: his journal (three runs with poses), the rank standards, a population
    holding one other player's recording, and the rival race over them."""
    ranks = RankStandards(folder / "rank_standards.json", bundled_rank_standards(), bundled_sheet_ladders())
    ranks.load()
    journal = catalog.Journal()
    tracker = SimpleNamespace(db=journal, ranks=ranks, strat_by_star={(2, 2): "Standard"}, strat_by_segment={},
                              effective_game_version=lambda: "us", segment_defs=[])
    replay = catalog.Replay(folder / "replays", journal)
    population = LocalPopulation(folder / "population", definitions=())
    rows = [catalog.run(STAGE, igt=331, minutes=0), catalog.run(30, igt=340, minutes=1),
            catalog.run(31, igt=360, minutes=2)]
    journal.rows.update({row.id: row for row in rows})
    for row in rows:
        replay.commit(row.id)
    catalog.publish(population, player_id="p-sully", player_name=SULLY, frames=320)
    rivals = Rivals(tracker, population, SimpleNamespace(), lambda: "us")
    return SimpleNamespace(stage=rows[0], sources={"tracker": tracker, "replay": replay,
                                                   "ghost": SimpleNamespace(rivals=rivals)})


def _lab(folder: Path) -> tuple[Path, bytes]:
    """A scratch lab (a US cartridge, as the practice ROM is) and the stage's scene package."""
    rom = bytearray(fixture.rom())
    rom[0x3E] = ord("E")
    rom = bytes(rom)
    for name in ("packages", "Plugin", "lab"):
        (folder / name).mkdir(parents=True)
    (folder / "rom.z64").write_bytes(rom)
    session = fixture.write_session(folder / "recording", rom, [(1000 + index, 24, 1) for index in range(8)])
    scene = folder / "scene.sm64scene"
    package.write_package(package.tap_source(session, "next", states=True), rom, scene, package.ZLIB)
    config = folder / "lab.json"
    config.write_text(json.dumps({"rom": str(folder / "rom.z64"), "packages": str(folder / "packages"),
                                  "pj64_plugin": str(folder / "Plugin"), "lab_root": str(folder / "lab")}),
                      encoding="utf-8")
    return config, scene.read_bytes()


@contextmanager
def _served(tmp_path, monkeypatch):
    world = _world(tmp_path / "world")
    config, scene = _lab(tmp_path / "scratch-lab")
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    monkeypatch.setenv("SM64_SCENE_LAB", str(config))
    original, made = ui_fixture.create_app, {"cutters": []}

    def create_app(*args, **kwargs):
        app = original(*args, **kwargs)

        def cutter(folder, on_event):
            made["cutters"].append(catalog.Cutter(folder, on_event))
            return made["cutters"][-1]
        made["lab"] = scene_lab_api.mount_scene_lab(
            app, config, live=True, lease_factory=catalog.Lease, cutter_factory=cutter,
            ghost_sources=world.sources,
            session_options={"host_factory": scene_session.command_host(fake_viewer_host.command()),
                             "evaluator_factory": lambda package_path, rom: fake_viewer_host.FakeEvaluator()})
        return app
    monkeypatch.setattr(ui_fixture, "create_app", create_app)
    with ui_fixture.serve_ui() as base:
        made["lab"].scenes.observe_completed([world.stage])          # his run ended; its scene is cut
        made["cutters"][0].ready(STAGE, scene)
        yield base, made["lab"]


# The scene socket as the page uses it: what it sends (pictures and ghost lists), the lab's ghost
# answers, and each picture header's ghost counts.
SOCKET = r"""
(() => {
  const seen = window.__socket = { sent: [], answers: [], pictures: [] };
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    constructor(...args) {
      super(...args);
      this.addEventListener("message", (event) => {
        if (typeof event.data === "string") {
          const message = JSON.parse(event.data);
          if (message.kind === "ghosts") seen.answers.push(message);
          return;
        }
        const length = new DataView(event.data).getUint32(0, true);
        const header = JSON.parse(new TextDecoder().decode(new Uint8Array(event.data, 4, length)));
        seen.pictures.push({ id: header.id, ghosts: header.ghosts ?? null });
      });
    }
    send(data) {
      try { seen.sent.push(JSON.parse(data)); } catch { /* not a request */ }
      return super.send(data);
    }
  };
})()
"""
YOURS_ROWS = ".scene-lab-ghost-section[aria-label='Your ghosts'] .scene-lab-ghost-list > li"
TOP_ROWS = ".scene-lab-ghost-section[aria-label='Top ghosts'] .scene-lab-ghost-list > li"


def _answered(page, count: int) -> dict:
    page.wait_for_function(f"window.__socket.answers.length >= {count}", timeout=20000)
    return page.evaluate(f"window.__socket.answers[{count - 1}]")


def _picture_after(page, sent_before: int) -> dict:
    """The first picture the page asked for after `sent_before` messages, once it arrives."""
    page.wait_for_function("""(before) => {
      const asked = window.__socket.sent.slice(before).filter((m) => !m.kind).map((m) => m.id);
      return window.__socket.pictures.some((p) => asked.includes(p.id));
    }""", arg=sent_before, timeout=20000)
    return page.evaluate("""(before) => {
      const asked = window.__socket.sent.slice(before).filter((m) => !m.kind).map((m) => m.id);
      return window.__socket.pictures.find((p) => asked.includes(p.id));
    }""", sent_before)


def test_pick_a_star_the_stage_opens_your_ghosts_list_and_add_draws_one_then_remove(tmp_path, monkeypatch):
    with _served(tmp_path, monkeypatch) as (base, lab), sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            page.add_init_script(SOCKET)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            page.locator('.nav-item[title="Scene viewer"]:visible').click()
            page.locator(".scene-lab-target").click()                        # pick Shoot into the Wild Blue
            page.locator("[role=dialog] button", has_text="Whomp's Fortress").first.click()
            page.locator("[role=dialog] .starcell", has_text="Shoot into the Wild Blue").first.click()
            page.wait_for_function("!!document.querySelector('.scene-lab-readout')", timeout=30000)
            title = page.locator(".scene-lab-title").inner_text()
            assert title == "Whomp's Fortress · Shoot into the Wild Blue · Standard · 11\"03", title
            assert page.locator(".scene-lab-stage-picker select").input_value() == "Standard"

            page.locator(YOURS_ROWS).nth(2).wait_for(timeout=20000)
            rows = page.locator(YOURS_ROWS)
            assert [row.locator(".scene-lab-ghost-time").inner_text() for row in rows.all()] == ['11"03', '11"33', '12"00']
            assert rows.nth(0).locator(".scene-lab-ghost-stage").inner_text() == "The stage"
            page.locator(TOP_ROWS, has_text=SULLY).wait_for(timeout=20000)

            mine = page.locator(YOURS_ROWS, has_text=YOURS)
            sent = page.evaluate("window.__socket.sent.length")
            mine.get_by_role("button", name="Add").click()
            answer = _answered(page, 1)
            ref = page.evaluate("window.__socket.sent.filter((m) => m.kind === 'ghosts')[0].set[0].ref")
            assert answer["shown"] == [ref] and answer["refused"] == [], answer
            page.wait_for_function("(ref) => document.querySelector(`li[data-ref='${ref}']`)?.classList.contains('is-shown')",
                                   arg=ref)
            redrawn = _picture_after(page, sent + 1)
            assert redrawn["ghosts"] and redrawn["ghosts"]["asked"] == 1, redrawn       # drawn with the ghost, no reload

            sully = page.locator(TOP_ROWS, has_text=SULLY)
            sully.get_by_role("button", name="Add").click()
            both = _answered(page, 2)
            assert len(both["shown"]) == 2 and both["refused"] == [], both
            page.locator(f"{TOP_ROWS}.is-shown", has_text=SULLY).wait_for(timeout=20000)
            page.locator(".scene-lab").screenshot(path=str(tmp_path / "scene-ghosts-real-1280.png"))

            sent = page.evaluate("window.__socket.sent.length")
            mine.get_by_role("button", name="Remove").click()
            left = _answered(page, 3)
            assert len(left["shown"]) == 1 and ref not in left["shown"], left            # Sully's stays
            page.wait_for_function("(ref) => !document.querySelector(`li[data-ref='${ref}']`)?.classList.contains('is-shown')",
                                   arg=ref)
            redrawn = _picture_after(page, sent + 1)
            assert redrawn["ghosts"] and redrawn["ghosts"]["asked"] == 1, redrawn
            assert lab.session is not None and errors == []
        finally:
            browser.close()
