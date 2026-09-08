"""Drive the shipping wizard against controllable offline observations."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver
from ui_fixture import serve_ui
from uilab_project import _SETUP_SETUP
from sm64_events.core.onboarding import JP_WARNING

CURRENT = ".setup-modal > .setup-swap > .setup-swap-in"
FRESH = dict(state="not_installed", pj64_dir=None, pj64_running=False,
             consented_at=None, wrapper_present=False, wrapper_current=False,
             wrapper_selected=False, layer_alive=False)
MISSING_ROM = dict(state="missing", region=None, name=None, warning=None)


def scenario():
    layer = dict(FRESH)
    observed = {"target": {"state": "missing"}, "rom": dict(MISSING_ROM), "checks": {}}
    return layer, observed


def wait_page(page, name):
    page.wait_for(f'.setup-modal[data-page="{name}"]')
    page.wait_ms(360)  # settle the outgoing view before strict selector clicks


def wait_step(page, name):
    page.wait_for(f'{CURRENT} .setup-install[data-substep="{name}"]')
    page.wait_ms(360)


def body(page):
    return page.evaluate("document.querySelector('.setup-modal').innerText")


def click_text(page, text):
    import json
    page.evaluate("Array.from(document.querySelectorAll('.setup-modal button'))"
                  ".filter(b => !b.closest('[inert]'))"
                  f".find(b => (b.getAttribute('aria-label') || b.textContent.trim()) === {json.dumps(text)}).click()")


def connect(layer, observed):
    layer.update(pj64_dir="C:/Games/Project64 1.6", pj64_running=True)
    observed["target"] = dict(state="ready", pid=123, message="Project64 v1.6 found.")


def installed(layer):
    layer.update(consented_at="today", wrapper_present=True, wrapper_current=True,
                 wrapper_selected=True, state="needs_restart")


def test_first_run_reveals_only_the_current_page_and_back_stays_put(tmp_path):
    layer, observed = scenario()
    with serve_ui(capture_layer_status=layer, setup_observer=lambda _: observed) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "platform")
        assert page.count(".setup-platform-choice") == 2
        assert page.count(".modal-close") == 0
        assert "Install Practice Replay" not in body(page)
        page.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}))")
        assert page.count(".setup-modal") == 1
        page.click(".setup-platform-choice:first-child")
        wait_page(page, "connect")
        assert "Usamune" not in body(page)
        assert page.count(".setup-regions") == 0
        connect(layer, observed)
        page.wait_for(".setup-connect .is-checked")
        assert page.count('.setup-modal[data-page="connect"]') == 1
        wait_page(page, "install")
        wait_step(page, "close")
        assert "Open Usamune" not in body(page)
        assert page.count(".setup-install-action") == 0
        page.click('.setup-arrow[aria-label="Back"]')
        wait_page(page, "connect")
        page.wait_ms(1500)
        assert page.count('.setup-modal[data-page="connect"]') == 1
        page.click('.setup-arrow[aria-label="Forward"]')
        wait_page(page, "install")
        page.click(".setup-not-now")
        assert page.count(".setup-modal") == 0
        page.goto(url + "/ui/index.html")
        page.wait_ms(1500)
        assert page.count(".setup-modal") == 0
        assert page.problems() == []


def test_install_reopen_verify_finish_and_practice_navigation(tmp_path):
    layer, observed = scenario()
    connect(layer, observed)
    with serve_ui(capture_layer_status=layer, setup_observer=lambda _: observed) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "platform")
        page.click(".setup-platform-choice:first-child")
        wait_page(page, "install")
        wait_step(page, "close")
        layer["pj64_running"] = False
        wait_step(page, "install")
        assert "Open Usamune" not in body(page)
        click_text(page, "What does this install change?")
        page.wait_ms(400)
        (tmp_path / "install-details.png").write_bytes(page.screenshot())
        click_text(page, "What does this install change?")
        installed(layer)
        click_text(page, "Install Practice Replay")
        wait_step(page, "reopen")
        layer["pj64_running"] = True
        wait_step(page, "rom")
        assert page.count(".setup-regions") == 0
        observed["rom"] = dict(state="supported", region="us", name="SM64 USAMUNE v1.93u", warning=None)
        wait_step(page, "verify")
        (tmp_path / "verify-us.png").write_bytes(page.screenshot())
        assert page.count(".setup-region.is-detected") == 1
        assert "US" in page.evaluate("document.querySelector('.setup-region.is-detected').textContent")
        assert page.count(".setup-completion") == 0  # a header and plugin heartbeat aren't enough
        observed["checks"] = dict(plugin=True, pictures=True, inputs=True, game=True)
        wait_page(page, "complete")
        assert page.count(".setup-cast img") == 3
        assert page.count(".setup-not-now") == 0
        assert page.count('.setup-confetti i') == 24
        assert page.evaluate("Array.from(document.querySelectorAll('.setup-confetti i')).some(el => Number(getComputedStyle(el).opacity) > .1)")
        (tmp_path / "complete.png").write_bytes(page.screenshot())
        page.wait_ms(800)  # Entry is finished; sample the continuing float.
        heights = []
        for _ in range(4):
            heights.append(page.evaluate("Array.from(document.querySelectorAll('.setup-cast img')).map(el => el.getBoundingClientRect().y)"))
            page.wait_ms(400)
        assert all(max(row[i] for row in heights) - min(row[i] for row in heights) > 3 for i in range(3))
        page.wait_ms(1000)
        assert page.evaluate("Array.from(document.querySelectorAll('.setup-confetti i')).every(el => Number(getComputedStyle(el).opacity) === 0)")
        click_text(page, "Ready to practice!")
        page.wait_ms(400)
        assert page.count(".setup-modal") == 0
        result = page.evaluate("fetch('/api/setup').then(r=>r.json())")
        assert result["onboarding"]["completed_at"]
        assert result["platform"] == "emu"
        assert page.evaluate("localStorage.getItem('sm64.setupResume.v1')") is None
        assert page.problems() == []


def test_jp_finishes_available_setup_and_keeps_warning(tmp_path):
    layer, observed = scenario()
    connect(layer, observed)
    observed.update(rom=dict(state="jp", region="jp", name="SM64 USAMUNE JP", warning=JP_WARNING),
                    checks=dict(plugin=True, pictures=True, inputs=False, game=False))
    with serve_ui(capture_layer_status=layer, setup_observer=lambda _: observed) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "platform")
        page.click(".setup-platform-choice:first-child")
        wait_page(page, "install")
        installed(layer)
        wait_page(page, "complete")
        assert "JP tracking isn't supported yet" in body(page)
        (tmp_path / "complete-jp.png").write_bytes(page.screenshot())
        click_text(page, "Ready to practice!")
        page.wait_ms(400)
        assert "JP tracking" in page.evaluate("document.querySelector('.setup-capability-note').textContent")
        assert page.problems() == []


def test_console_branch_can_switch_to_emulator_or_finish():
    layer, observed = scenario()
    with serve_ui(capture_layer_status=layer, setup_observer=lambda _: observed) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "platform")
        page.click(".setup-platform-choice:last-child")
        wait_page(page, "console")
        assert "in development" in body(page)
        click_text(page, "Set up Emulator")
        wait_page(page, "connect")
        page.click('.setup-arrow[aria-label="Back"]')
        wait_page(page, "platform")
        page.click(".setup-platform-choice:last-child")
        wait_page(page, "console")
        observed["rom"] = dict(state="jp", region="jp", name="SM64 USAMUNE JP", warning=JP_WARNING)
        click_text(page, "Explore the app")
        page.wait_ms(500)
        assert page.count(".setup-modal") == 0
        assert page.evaluate("fetch('/api/setup').then(r=>r.json())")["platform"] == "n64"
        assert page.count(".setup-capability-note") == 0
        assert page.problems() == []


def test_manual_entry_keeps_management_available_and_motion_has_real_intermediate_frames(tmp_path):
    with serve_ui() as url, get_driver().launch(viewport=(850, 600)) as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(1500)
        assert page.count(".setup-modal") == 0
        page.evaluate(_SETUP_SETUP)
        wait_page(page, "install")
        click_text(page, "Manage Practice Replay")
        page.wait_ms(400)
        assert page.evaluate("Array.from(document.querySelectorAll('.setup-modal button'))"
                             ".find(b=>b.textContent.trim()==='Remove Practice Replay').disabled")
        page.click('.setup-arrow[aria-label="Back"]')
        wait_page(page, "connect")
        # Sample a real click's transition over animation frames, not CSS declarations.
        result = page.evaluate("""(async () => {
          const before = document.querySelector('.setup-modal > .setup-swap').getBoundingClientRect().height;
          document.querySelector('.setup-arrow[aria-label="Back"]').click();
          await new Promise(r => setTimeout(r, 90));
          const node = document.querySelector('.setup-modal > .setup-swap > .setup-swap-in');
          const middle = getComputedStyle(node).transform;
          const dot = parseFloat(getComputedStyle(document.querySelector('.setup-dot.is-current')).width);
          const direction = node.parentElement.dataset.direction;
          const height = node.parentElement.getBoundingClientRect().height;
          await new Promise(r => setTimeout(r, 400));
          return {before, height, middle, dot, direction, after:getComputedStyle(node).transform};
        })()""")
        assert result["direction"] == "back"
        assert result["middle"] not in ("none", "matrix(1, 0, 0, 1, 0, 0)")
        assert result["after"] in ("none", "matrix(1, 0, 0, 1, 0, 0)")
        assert result["height"] != result["before"]
        assert 9 < result["dot"] < 26
        (tmp_path / "platform-850.png").write_bytes(page.screenshot())
        bounds = page.evaluate("""(() => {const r=document.querySelector('.modal').getBoundingClientRect();
          return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,w:innerWidth,h:innerHeight};})()""")
        assert 0 <= bounds["left"] < bounds["right"] <= bounds["w"]
        assert 0 <= bounds["top"] < bounds["bottom"] <= bounds["h"]
        forward = page.evaluate("""(async () => {
          document.querySelector('.setup-arrow[aria-label="Forward"]').click();
          await new Promise(r => setTimeout(r, 90));
          const node = document.querySelector('.setup-modal > .setup-swap > .setup-swap-in');
          const middle = getComputedStyle(node).transform;
          await new Promise(r => setTimeout(r, 400));
          return {middle, after:getComputedStyle(node).transform};
        })()""")
        assert forward["middle"] not in ("none", "matrix(1, 0, 0, 1, 0, 0)")
        assert forward["after"] in ("none", "matrix(1, 0, 0, 1, 0, 0)")
        page.emulate_motion(True)
        page.click('.setup-arrow[aria-label="Back"]')
        wait_page(page, "platform")
        page.click(".setup-platform-choice:first-child")
        wait_page(page, "connect")
        assert page.evaluate("getComputedStyle(document.querySelector('.setup-swap-in')).animationName") == "none"
        assert page.problems() == []


@pytest.mark.parametrize("current", [True, False])
def test_existing_install_skips_first_use_or_enters_repair_directly(current):
    layer, observed = scenario()
    connect(layer, observed)
    installed(layer)
    layer.update(consented_at=None, wrapper_current=current, state="not_installed")
    # No ROM or fresh live counters: installed is not the same as verified gameplay.
    with serve_ui(capture_layer_status=layer, setup_observer=lambda _: observed) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        if current:
            page.wait_ms(1500)
            assert page.count(".setup-modal") == 0
            page.evaluate(_SETUP_SETUP)
            wait_page(page, "install")
            wait_step(page, "rom")
            assert page.count(".setup-install-action") == 0
            assert page.count(".setup-completion") == 0
        else:
            wait_page(page, "install")
            wait_step(page, "close")
            assert page.count(".setup-platform-choice") == 0
        assert page.problems() == []


def symbol_centers(page):
    return page.evaluate("""Array.from(document.querySelectorAll(
      '.setup-arrow, .setup-disclosure-symbol, .setup-choice-arrow'))
      .filter(el => !el.closest('[inert]')).map(el => {
        const r = el.getBoundingClientRect(), path = el.querySelector('svg path');
        if (!path) return {missing:true};
        const ink = path.getBBox();
        const center = new DOMPoint(ink.x + ink.width/2, ink.y + ink.height/2)
          .matrixTransform(path.getScreenCTM());
        return {dx:Math.abs(center.x-r.x-r.width/2), dy:Math.abs(center.y-r.y-r.height/2)};
      })""")


def test_existing_install_rechecks_unresolved_emulator_before_step_three(tmp_path):
    observed = {"target": {"state": "unsupported", "message": "This build needs verification."},
                "rom": dict(MISSING_ROM), "checks": {}}
    with serve_ui(setup_observer=lambda _: observed) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(500)
        page.evaluate(_SETUP_SETUP)
        wait_page(page, "connect")
        assert page.count(".setup-install") == 0
        assert page.count('.setup-dot.is-done') == 1
        assert "needs verification" in body(page)
        (tmp_path / "connection-recheck.png").write_bytes(page.screenshot())
        observed["target"] = {"state": "ready", "pid": 123, "version": None,
                              "build": "Known unversioned Project64 1.6"}
        wait_page(page, "install")
        wait_step(page, "rom")
        assert "Open Usamune" in body(page)
        assert page.count(".setup-install-action") == 0
        assert page.count(".setup-completion") == 0
        (tmp_path / "recognized-idle-project64.png").write_bytes(page.screenshot())
        assert page.problems() == []


def test_afk_recording_pause_keeps_setup_checked(tmp_path):
    from dataclasses import asdict
    from test_onboarding import runtime, installed as runtime_layer

    probe, now, target, inputs, recorder, memory, poller = runtime()
    recorder["frame_source_health"]["plugin_pid"] = 123
    layer = asdict(runtime_layer())

    def observe(status):
        now[0] += 5  # Every poll exceeds the picture freshness window.
        inputs["frames"] += 150
        poller.latest.global_timer += 150
        if not recorder.get("idle"):
            recorder["frame_source_health"]["delivered"] += 150
        return probe(status)

    with serve_ui(capture_layer_status=layer, setup_observer=observe) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(1000)
        page.evaluate(_SETUP_SETUP)
        wait_page(page, "install")
        wait_step(page, "ready")
        recorder["idle"] = True
        page.wait_ms(4500)
        assert 'data-substep="ready"' in page.evaluate("document.querySelector('.setup-install').outerHTML")
        assert "Setup checked" in body(page)
        assert page.count('.setup-not-now') == 0
        assert page.count('.setup-arrow-ready[aria-label="Forward"]') == 1
        glow = []
        for _ in range(3):
            glow.append(float(page.evaluate("getComputedStyle(document.querySelector('.setup-arrow-ready'), '::after').opacity")))
            page.wait_ms(500)
        assert max(glow) - min(glow) > .1
        assert all(max(c["dx"], c["dy"]) < .75 for c in symbol_centers(page))
        (tmp_path / "setup-checked-while-afk.png").write_bytes(page.screenshot())
        layer["layer_alive"] = False
        wait_step(page, "verify")
        assert "Checking your setup" in body(page)
        assert page.count('.setup-not-now') == 1
        assert page.count('.setup-arrow-ready') == 0
        assert page.problems() == []


@pytest.mark.parametrize("width", [850, 1440])
def test_symbols_are_centered_and_footer_survives_text_growth(tmp_path, width):
    with serve_ui() as url, get_driver().launch(viewport=(width, 600)) as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(500)
        page.evaluate(_SETUP_SETUP)
        wait_page(page, "install")
        centers = symbol_centers(page)
        assert len(centers) >= 3
        assert all(not c.get("missing") and max(c["dx"], c["dy"]) < .75 for c in centers)
        # Falsify the measurement: a shifted painted symbol must fail it.
        page.evaluate("document.querySelector('.setup-arrow svg').style.transform='translateX(4px)'")
        assert any(c["dx"] > 3 for c in symbol_centers(page))
        page.evaluate("document.querySelector('.setup-arrow svg').style.transform=''")
        click_text(page, "I don't have Usamune")
        page.wait_ms(400)
        assert all(max(c["dx"], c["dy"]) < .75 for c in symbol_centers(page))
        focus = page.evaluate("""(() => {
          const b=document.querySelector('.setup-arrow'); b.focus();
          const s=getComputedStyle(b), r=b.getBoundingClientRect();
          return {visible:b.matches(':focus-visible'), outline:s.outlineWidth,
            named:!!b.getAttribute('aria-label'), size:Math.min(r.width,r.height),
            inside:r.top>=0 && r.bottom<=innerHeight};
        })()""")
        assert focus == dict(visible=True, outline="3px", named=True, size=44, inside=True)
        (tmp_path / f"polished-{width}.png").write_bytes(page.screenshot())
        page.evaluate("document.documentElement.style.fontSize='24px'")
        page.wait_ms(500)
        layout = page.evaluate("""(() => {
          const nav=document.querySelector('.setup-navigation').getBoundingClientRect();
          const dots=document.querySelector('.setup-progress').getBoundingClientRect();
          const a=document.querySelector('[aria-label="Forward"]').getBoundingClientRect();
          const b=document.querySelector('.setup-not-now')?.getBoundingClientRect();
          const modal=document.querySelector('.modal');
          return {center:Math.abs(dots.x+dots.width/2-nav.x-nav.width/2),
            overlap:!!b && Math.min(a.right,b.right)>Math.max(a.left,b.left)
              && Math.min(a.bottom,b.bottom)>Math.max(a.top,b.top),
            overflow:modal.scrollWidth>modal.clientWidth, bottom:nav.bottom<=innerHeight};
        })()""")
        assert layout["center"] < .75 and not layout["overlap"]
        assert not layout["overflow"] and layout["bottom"]
        (tmp_path / f"large-text-{width}.png").write_bytes(page.screenshot())
        page.click('.setup-arrow[aria-label="Back"]')
        wait_page(page, "connect")
        page.click('.setup-arrow[aria-label="Back"]')
        wait_page(page, "platform")
        assert all(max(c["dx"], c["dy"]) < .75 for c in symbol_centers(page))
        assert page.problems() == []
