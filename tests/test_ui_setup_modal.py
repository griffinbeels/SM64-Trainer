"""Drive the shipping wizard against controllable offline observations."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import default_wait_ms, get_driver
from uilab.trace import record
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
    """Click the live (not inert) modal button with this label, once it is
    there: a step's button can still be mid-swap after wait_step's fixed
    360 ms on a loaded machine (full run 35685744845: no such button yet)."""
    import json
    import time
    found = ("Array.from(document.querySelectorAll('.setup-modal button'))"
             ".filter(b => !b.closest('[inert]'))"
             f".find(b => (b.getAttribute('aria-label') || b.textContent.trim()) === {json.dumps(text)})")
    deadline = time.monotonic() + default_wait_ms() / 1000
    while not page.evaluate(f"!!{found}"):
        assert time.monotonic() < deadline, f"no live {text!r} button in the setup modal"
        page.wait_ms(50)
    page.evaluate(f"{found}.click()")


def wait_until(page, expression, what):
    """Poll a page predicate up to the run's wait bound: a fixed pause before
    an assertion is a coin-flip on a loaded machine (merge checks 2026-09-23)."""
    import time
    deadline = time.monotonic() + default_wait_ms() / 1000
    while not page.evaluate(expression):
        assert time.monotonic() < deadline, f"timed out waiting until {what}"
        page.wait_ms(50)


def closed(page):
    wait_until(page, "!document.querySelector('.setup-modal')", "the setup modal closes")


def connect(layer, observed):
    layer.update(pj64_dir="C:/Games/Project64 1.6", pj64_running=True)
    observed["target"] = dict(state="ready", pid=123, message="Project64 v1.6 found.")


INSTALLED = dict(consented_at="today", wrapper_present=True, wrapper_current=True,
                 wrapper_selected=True, state="needs_restart")


def installed(layer):
    layer.update(INSTALLED)


US_ROM = dict(state="supported", region="us", name="SM64 USAMUNE v1.93u", warning=None)
ALL_CHECKS = dict(plugin=True, pictures=True, inputs=True, game=True)

# Every state the setup modal commits to the DOM, in order, as
# "page|stage|step|card title|detected region|forward arrow", plus each card
# swap on the install page. A
# MutationObserver sees every commit before it can paint, so a card that
# flashes for one frame is still recorded.
_WATCH = """(() => {
  window.__seen = []; window.__swaps = [];
  const current = '.setup-modal > .setup-swap > .setup-swap-in';
  const read = () => {
    const modal = document.querySelector('.setup-modal');
    if (!modal) return null;
    const pane = document.querySelector(current + ' .setup-install');
    const title = pane?.querySelector('.setup-substep-swap > .setup-swap-in .setup-current h3');
    const region = pane?.querySelector('.setup-substep-swap > .setup-swap-in .setup-region.is-detected span');
    return [modal.dataset.page, pane?.dataset.stage || '', pane?.dataset.substep || '',
            (title?.textContent || '').trim(), region?.textContent || '',
            document.querySelector('.setup-arrow-ready') ? 'forward' : ''].join('|');
  };
  new MutationObserver(records => {
    for (const r of records) for (const node of r.addedNodes) {
      if (node.nodeType === 1 && node.matches('.setup-substep-swap > .setup-swap-out')
          && node.closest(current)) window.__swaps.push(node.parentElement.dataset.direction);
    }
    const now = read();
    if (now && now !== window.__seen.at(-1)?.[1]) window.__seen.push([Date.now(), now]);
  }).observe(document.body, {subtree: true, childList: true, attributes: true, characterData: true});
  return true;
})()"""


def seen(page):
    return [row.split("|") for _, row in page.evaluate("window.__seen")]


def stages(page):
    """The install page's cards in the order shown, repeats collapsed."""
    out = []
    for name, stage, *_ in seen(page):
        if name == "install" and stage and (not out or out[-1] != stage):
            out.append(stage)
    return out


class PollScript:
    """Answers /api/setup with `base` until armed, then one scripted
    observation per request, holding the last: a one-poll flicker is exact
    instead of a sleep racing the modal's one-second poll."""

    def __init__(self, base):
        self.base, self.frames, self.served = base, None, []

    def arm(self, *frames):
        self.frames = list(frames)

    def __call__(self, _layer):
        import time
        if not self.frames:
            return self.base
        frame = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
        self.served.append((time.time(), frame))
        return frame


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
        assert "Open ROM" not in body(page)
        assert page.count(".setup-install-action") == 0
        page.click('.setup-arrow[aria-label="Back"]')
        wait_page(page, "connect")
        page.wait_ms(1500)
        assert page.count('.setup-modal[data-page="connect"]') == 1
        page.click('.setup-arrow[aria-label="Forward"]')
        wait_page(page, "install")
        page.click(".setup-not-now")
        closed(page)
        page.goto(url + "/ui/index.html")
        page.wait_ms(1500)
        assert page.count(".setup-modal") == 0
        assert page.problems() == []


def slows_before_rest(frames):
    """Compare speed, not frame distance, when a busy renderer skips a rAF.

    Exclude the interval landing on zero: its unknown stationary suffix can
    make a linear animation appear to slow down. Require measured motion.
    """
    moving = [row for row in frames if row.get("x", 0) > 1e-6]
    speeds = [abs(after["x"] - before["x"]) / (after["t"] - before["t"])
              for before, after in zip(moving, moving[1:], strict=False) if after["t"] > before["t"]]
    peak = max(speeds, default=0)
    return len(speeds) >= 3 and peak > 0 and speeds[-1] <= peak * .5, speeds


@pytest.mark.parametrize("times,positions,expected", [
    ([0, 80, 160, 210, 300], [26, 18.7706, 4.82477, 1.60562, 0], True),
    ([0, 120, 200, 250, 280, 295, 300, 340],
     [26 * (1 - min(t, 300) / 300) for t in [0, 120, 200, 250, 280, 295, 300, 340]], False),
    ([0, 80, 160, 240, 300], [26, 26, 26, 26, 0], False),
    ([0, 150, 300], [26, 1, 0], False),
], ids=["uneven-easing", "uneven-linear", "snap", "insufficient"])
def test_rest_probe_uses_elapsed_time_and_requires_observed_deceleration(times, positions, expected):
    frames = [{"t": t, "x": x} for t, x in zip(times, positions, strict=True)]
    assert slows_before_rest(frames)[0] is expected


def assert_game_found_moves_to_the_check(page, observed):
    """Open the game on "Open your game" and sample the stage change per
    frame: its acknowledgment, then the check card entering from aside and
    coming to rest while the old card leaves."""
    before = page.evaluate("document.querySelector('.setup-substep-swap > .setup-swap-in .setup-current h3').textContent")
    assert before == "Open your game"
    trace = record(page, watch={"in": ".setup-substep-swap > .setup-swap-in",
                                "out": ".setup-substep-swap > .setup-swap-out",
                                "title": ".setup-substep-swap > .setup-swap-in .setup-current h3"},
                   trigger=lambda _: observed.update(rom=dict(US_ROM)), ms=2600)
    titles = [before]
    for row in trace.of("title").frames:
        if row.get("text") and (not titles or titles[-1] != row["text"]):
            titles.append(row["text"])
    entering = [row for row, title in zip(trace.of("in").frames, trace.of("title").frames, strict=True)
                if title.get("text") == "Checking your setup" and "x" in row]
    leaving = [row for row in trace.of("out").frames if "x" in row]
    assert titles == ["Open your game", "Usamune found", "Checking your setup"]
    assert entering[0]["opacity"] < .5 and entering[0]["x"] > 10  # starts aside, not at rest
    assert any(0 < row["x"] < entering[0]["x"] and .1 < row["opacity"] < .95 for row in entering)
    assert entering[-1]["x"] == 0 and entering[-1]["opacity"] == 1
    resting, speeds = slows_before_rest(entering)
    assert resting, speeds
    assert leaving and leaving[-1]["opacity"] < leaving[0]["opacity"]


def assert_walked_four_cards(page):
    """Four cards in order, each once; "Open your game" changed its text in
    place (reopen, then rom) with no card swap of its own."""
    assert stages(page) == ["close", "install", "game", "check"]
    assert page.evaluate("window.__swaps") == ["forward"] * 3
    game = [step for name, stage, step, *_ in seen(page) if name == "install" and stage == "game"]
    assert game[0] == "reopen" and "rom" in game


def test_install_reopen_verify_finish_and_practice_navigation(tmp_path):
    layer, observed = scenario()
    connect(layer, observed)
    with serve_ui(capture_layer_status=layer, capture_layer_install=INSTALLED,
                  setup_observer=lambda _: observed) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "platform")
        assert page.evaluate(_WATCH)
        page.click(".setup-platform-choice:first-child")
        wait_page(page, "install")
        wait_step(page, "close")
        layer["pj64_running"] = False
        wait_step(page, "install")
        assert "Open ROM" not in body(page)
        click_text(page, "What does this install change?")
        page.wait_ms(400)
        (tmp_path / "install-details.png").write_bytes(page.screenshot())
        click_text(page, "What does this install change?")
        click_text(page, "Install Practice Replay")
        wait_step(page, "reopen")
        layer["pj64_running"] = True
        wait_step(page, "rom")
        assert page.count(".setup-regions") == 0
        page.wait_ms(1600)  # he reads "Open your game", then opens it
        assert_game_found_moves_to_the_check(page, observed)
        wait_step(page, "verify")
        (tmp_path / "verify-us.png").write_bytes(page.screenshot())
        assert page.count(".setup-region.is-detected") == 1
        assert "US" in page.evaluate("document.querySelector('.setup-region.is-detected').textContent")
        assert page.count(".setup-completion") == 0  # a header and plugin heartbeat aren't enough
        observed["checks"] = dict(plugin=True, pictures=True, inputs=True, game=True)
        wait_page(page, "complete")
        assert_walked_four_cards(page)
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
        closed(page)
        result = page.evaluate("fetch('/api/setup').then(r=>r.json())")
        assert result["onboarding"]["completed_at"]
        assert result["platform"] == "emu"
        assert page.evaluate("localStorage.getItem('sm64.setupResume.v1')") is None
        assert page.problems() == []


_UNFOCUSED = ("(() => { Object.defineProperty(document, 'hasFocus', {configurable: true, value: () => false});"
              " window.dispatchEvent(new Event('blur')); return true; })()")
_VISIBILITY = ("(() => {{ Object.defineProperty(document, 'visibilityState', {{configurable: true, get: () => {state!r}}});"
               " document.dispatchEvent(new Event('visibilitychange')); return true; }})()")


def test_opening_with_everything_running_shows_one_calm_card(tmp_path):
    """His report, 2026-10-17: "it's bouncing visually between so many steps".
    Opened with Project64 and Usamune already running, Settings → Setup shows
    "Checking your setup", then "Setup checked" in place: no earlier page or
    card first, and one-poll flickers inside the stage or back to an earlier
    one change nothing. A step back that lasts shows after the hold."""
    layer, observed = scenario()
    connect(layer, observed)
    installed(layer)
    verify = dict(observed, rom=dict(US_ROM), checks={})
    ready = dict(verify, checks=dict(ALL_CHECKS))
    no_game = dict(verify, rom=dict(MISSING_ROM))
    script = PollScript(verify)
    with serve_ui(capture_layer_status=layer, setup_observer=script) as url, \
            get_driver().launch(viewport=(850, 600)) as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(1500)
        assert page.count(".setup-modal") == 0
        assert page.evaluate(_WATCH)
        script.arm(verify, ready, verify, ready, no_game, ready, ready)
        page.evaluate(_SETUP_SETUP)
        wait_until(page, "!!document.querySelector('.setup-arrow-ready')", "Setup checked offers Forward")
        (tmp_path / "opened-checked-850.png").write_bytes(page.screenshot())
        while script.frames != [ready]:
            page.wait_ms(100)
        page.wait_ms(2500)  # past the hold after the last one-poll step back
        served = [frame for _, frame in script.served]
        assert served[:6] == [verify, ready, verify, ready, no_game, ready]
        assert [row[0] for row in seen(page)] == ["install", "install"]
        assert [row[1:] for row in seen(page)] == [["check", "verify", "Checking your setup", "US", ""],
                                                   ["check", "ready", "Setup checked", "US", "forward"]]
        assert page.evaluate("window.__swaps") == []
        assert page.count(".setup-region.is-detected") == 1

        script.arm(no_game)  # the game really closed
        wait_until(page, "!!document.querySelector('.setup-install[data-stage=\"game\"]')",
                   "a lasting step back shows the game card")
        shown_at = page.evaluate("window.__seen.at(-1)[0]") / 1000
        first_back = next(at for at, frame in script.served[6:] if frame == no_game)
        assert 1.9 <= shown_at - first_back <= 4.5
        assert page.evaluate("window.__swaps") == ["back"]
        assert seen(page)[-1][1:] == ["game", "rom", "Open your game", "", ""]
        assert page.count(".setup-substep-swap > .setup-swap-in .setup-regions") == 0
        assert page.problems() == []


def test_confirmed_setup_continues_while_visible_and_waits_while_hidden(tmp_path):
    """His report, 2026-09-17: the wizard sat on Setup checked instead of
    continuing. He plays in Project64, so the trainer is on screen without
    focus; the confirmation must still continue. A hidden trainer waits, so
    the completion is never spent where nobody can see it."""
    layer, observed = scenario()
    connect(layer, observed)
    with serve_ui(capture_layer_status=layer, capture_layer_install=INSTALLED,
                  setup_observer=lambda _: observed) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "platform")
        page.click(".setup-platform-choice:first-child")
        wait_page(page, "install")
        wait_step(page, "close")
        layer["pj64_running"] = False
        wait_step(page, "install")
        click_text(page, "Install Practice Replay")
        wait_step(page, "reopen")
        layer["pj64_running"] = True  # he goes back to Project64: the trainer loses focus
        observed["rom"] = dict(state="supported", region="us", name="SM64 USAMUNE v1.93u", warning=None)
        wait_step(page, "verify")
        assert page.evaluate(_UNFOCUSED)
        assert page.evaluate(_VISIBILITY.format(state="hidden"))
        observed["checks"] = dict(plugin=True, pictures=True, inputs=True, game=True)
        wait_step(page, "ready")
        page.wait_ms(2500)
        assert page.count('.setup-modal[data-page="install"]') == 1  # hidden: waits
        assert page.evaluate(_VISIBILITY.format(state="visible"))  # on screen, still unfocused
        wait_page(page, "complete")
        (tmp_path / "continued-while-unfocused.png").write_bytes(page.screenshot())
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
        closed(page)
        page.wait_for(".setup-capability-note")
        assert "JP tracking" in page.evaluate("document.querySelector('.setup-capability-note').textContent")
        assert page.problems() == []


def test_console_branch_can_switch_to_emulator_or_finish():
    layer, observed = scenario()
    with serve_ui(capture_layer_status=layer, setup_observer=lambda _: observed) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "platform")
        page.click(".setup-platform-choice:last-child")
        wait_page(page, "console")
        page.wait_for(f'{CURRENT} .setup-console')
        assert "in development" in body(page)
        click_text(page, "Set up Emulator")
        wait_page(page, "connect")
        page.click('.setup-arrow[aria-label="Back"]')
        wait_page(page, "platform")
        page.click(".setup-platform-choice:last-child")
        wait_page(page, "console")
        page.wait_for(f'{CURRENT} .setup-console')
        observed["rom"] = dict(state="jp", region="jp", name="SM64 USAMUNE JP", warning=JP_WARNING)
        click_text(page, "Explore the app")
        closed(page)
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
        remove = ("Array.from(document.querySelectorAll('.setup-modal button'))"
                  ".find(b=>b.textContent.trim()==='Remove Practice Replay')")
        wait_until(page, f"!!{remove}", "Manage shows Remove Practice Replay")
        assert page.evaluate(f"{remove}.disabled")
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
        assert "Open your game" in body(page) and "File → Open ROM" in body(page)
        assert page.count(".setup-install-action") == 0
        assert page.count(".setup-completion") == 0
        (tmp_path / "recognized-idle-project64.png").write_bytes(page.screenshot())
        assert page.problems() == []


def test_afk_recording_pause_keeps_setup_checked(tmp_path):
    """AFK revokes GPU demand (control PASSIVE, recorder idle): the GPU
    observation's receipt keeps Setup checked while game and input movement
    continue, and a dead layer still sends it back to checking."""
    from dataclasses import asdict, replace
    from sm64_events.replay import capturecontrol as C
    from test_onboarding import runtime, installed as runtime_layer
    from test_setup_gpu import fixture

    gpu, gpu_now, control, gpu_recorder, receipt = fixture()
    probe, now, target, inputs, recorder, memory, poller = runtime()
    recorder.clear()
    recorder.update(gpu_recorder)
    layer = asdict(runtime_layer())

    def observe(status):
        now[0] += 5  # Every poll exceeds the picture freshness window.
        gpu_now[0] = now[0]
        inputs["frames"] += 150
        poller.latest.global_timer += 150
        if not recorder.get("idle"):
            control[0] = replace(control[0], ack_heartbeat=control[0].ack_heartbeat + 1)
            receipt["delivered"] += 150
        return probe(replace(status, gpu_observation=gpu()))

    with serve_ui(capture_layer_status=layer, setup_observer=observe) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        page.wait_ms(1000)
        page.evaluate(_SETUP_SETUP)
        wait_page(page, "install")
        wait_step(page, "ready")
        recorder["idle"] = gpu_recorder["idle"] = True
        control[0] = replace(control[0], state=C.PASSIVE)
        # No 4500ms leg here any more. Standing in the AFK state past the
        # picture-freshness window is
        # test_ui_setup_gpu.py::test_gpu_setup_active_idle_and_invalidated_evidence's
        # claim, asserted there against the receipt and heartbeat that must
        # NOT move; this test owns the rendered wizard, and its own
        # assertions below (the ready copy, the forward arrow, three glow
        # samples half a second apart, then the dead layer) already span
        # several of `observe`'s polls -- each of which advances the clock by
        # more than that window.
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


def test_restored_bundle_recovers_the_open_setup_modal(tmp_path, monkeypatch):
    """Exercise source rediscovery through the real installer and API, offline."""
    import ui_fixture
    from types import SimpleNamespace
    from sm64_events.core.capturelayer import CaptureLayer, RENDERER_DLL, WRAPPER_DLL, WRAPPER_INI

    folder = tmp_path / "PJ64"
    plugin = folder / "Plugin"
    plugin.mkdir(parents=True)
    source = tmp_path / WRAPPER_DLL
    expected = b"matching offline wrapper"
    (plugin / WRAPPER_DLL).write_bytes(expected)
    renderer = tmp_path / RENDERER_DLL
    renderer.write_bytes(b"matching offline renderer")
    (plugin / RENDERER_DLL).write_bytes(renderer.read_bytes())
    (plugin / WRAPPER_INI).write_text(f"wrapped={RENDERER_DLL}")
    registry = SimpleNamespace(get=lambda key, name: WRAPPER_DLL if name == "Graphics Dll" else None)
    processes = SimpleNamespace(pj64_image_path=lambda: str(folder / "Project64.exe"))
    layer = CaptureLayer(registry, processes, tmp_path / "capture.json",
                         lambda: source if source.is_file() else None,
                         renderer_source=renderer)
    monkeypatch.setattr(ui_fixture, "_FixtureCaptureLayer", lambda *a, **kw: layer)
    observed = dict(target=dict(state="ready", pid=123),
                    rom=dict(state="supported", region="us", name="SM64 USAMUNE v1.93u"),
                    checks=dict(plugin=True, pictures=True, inputs=True, game=True))
    with serve_ui(setup_observer=lambda _: observed) as url, get_driver().launch() as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "install")
        wait_step(page, "unavailable")
        assert "Practice Replay unavailable" in body(page)
        assert not layer.status().steps  # no fictitious update instructions
        (tmp_path / "missing-bundle.png").write_bytes(page.screenshot())
        source.write_bytes(expected)
        wait_page(page, "complete")
        assert "Practice Replay unavailable" not in body(page)
        assert page.evaluate("fetch('/api/setup').then(r=>r.json())")["emu"]["installation_verified"]
        (tmp_path / "restored-bundle.png").write_bytes(page.screenshot())
        assert page.problems() == []


def _installed_renderer(tmp_path, plugin):
    """The bundled renderer, installed beside the wrapper with the ini naming it."""
    from sm64_events.core.capturelayer import RENDERER_DLL, WRAPPER_INI
    renderer = tmp_path / RENDERER_DLL
    renderer.write_bytes(b"this checkout's bundled renderer")
    (plugin / RENDERER_DLL).write_bytes(renderer.read_bytes())
    (plugin / WRAPPER_INI).write_text(f"wrapped={RENDERER_DLL}")
    return renderer


@pytest.mark.parametrize("auto_refresh", [False, True])
def test_source_build_mismatch_waits_for_explicit_install_in_setup(tmp_path, monkeypatch, auto_refresh):
    """Source and packaged servers preserve external copies until explicit Install."""
    import ui_fixture
    from test_capturelayer import FakeProcesses, FakeRegistry, write_overlay
    from sm64_events.core.capturelayer import (
        CaptureLayer, GRAPHICS_DLL_VALUE, REGISTRY_DLL_SUBKEY, RENDERER_DLL, WRAPPER_DLL, WRAPPER_INI,
    )

    folder = tmp_path / "Project64 with a manually staged capture layer"
    plugin = folder / "Plugin"
    plugin.mkdir(parents=True)
    source = tmp_path / WRAPPER_DLL
    bundled, staged = b"this checkout's bundled wrapper", b"manually staged candidate wrapper"
    source.write_bytes(bundled)
    installed_dll = plugin / WRAPPER_DLL
    installed_dll.write_bytes(staged)
    renderer = _installed_renderer(tmp_path, plugin)
    registry = FakeRegistry({(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE): WRAPPER_DLL})
    processes = FakeProcesses(str(folder / "Project64.exe"))
    settings = tmp_path / "capture.json"
    # Prior installation consent must not authorize a source checkout to replace
    # a different candidate automatically when the emulator closes.
    write_overlay(settings, consented_at="2026-09-01T00:00:00Z",
                  pj64_dir=str(folder), wrapped=RENDERER_DLL, installed_sha256="0" * 64)
    layer = CaptureLayer(registry, processes, settings, source, renderer_source=renderer,
                         auto_refresh=auto_refresh)
    monkeypatch.setattr(ui_fixture, "_FixtureCaptureLayer", lambda *a, **kw: layer)
    observed = dict(target=dict(state="ready", pid=123),
                    rom=dict(MISSING_ROM), checks={})

    with serve_ui(setup_observer=lambda _: observed) as url, get_driver().launch(viewport=(850, 600)) as page:
        page.goto(url + "/ui/index.html")
        wait_page(page, "install")
        wait_step(page, "close")
        assert page.count(f"{CURRENT} .setup-install-action") == 0

        processes.image_path = None
        observed["target"] = dict(state="ready", pid=None)
        assert layer.refresh_if_stale() is False
        assert installed_dll.read_bytes() == staged
        wait_step(page, "install")
        state = page.evaluate("fetch('/api/setup').then(r=>r.json())")["emu"]
        assert state["wrapper_present"] and not state["wrapper_current"]
        assert not state["installation_verified"]
        action = next(step for step in state["steps"] if step["id"] == "install")
        assert action["action"] == "install" and not action["done"]
        assert "this build" in action["label"]
        button = f"{CURRENT} .setup-install-action button"
        page.wait_for(button)
        assert page.evaluate(f"!document.querySelector({button!r}).disabled")
        assert installed_dll.read_bytes() == staged
        (tmp_path / "source-mismatch-explicit-install.png").write_bytes(page.screenshot())

        page.click(button)
        wait_step(page, "reopen")
        assert installed_dll.read_bytes() == bundled
        assert (plugin / WRAPPER_INI).read_text().strip() == f"wrapped={RENDERER_DLL}"
        state = page.evaluate("fetch('/api/setup').then(r=>r.json())")["emu"]
        assert state["installation_verified"] and state["wrapper_current"]
        assert state["verification"]["step"] == "reopen"
        (tmp_path / "source-build-explicitly-installed.png").write_bytes(page.screenshot())
        assert page.problems() == []
