"""Drive the real rival components and stylesheet against isolated HTTP fixtures.

No live trainer, emulator or production data is used. Component probes intercept
all requests; the real Practice entry uses an owned offline fixture server. An optional
SM64_RIVALS_UI_ROOT lets an isolated reviewer read a sibling's uncommitted UI;
the committed test defaults to this checkout's shipping sources.
"""
from copy import deepcopy
import json
import mimetypes
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright
import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = Path(os.environ.get("SM64_RIVALS_UI_ROOT", ROOT / "src" / "sm64_events" / "ui")).resolve()
TARGET = {"kind": "star", "entity_key": "2-1", "strat": "Standard"}
CURSOR = ["formframe", 450, "b" * 64]


def row(identity, player, frames, *, slot=None, pinned=False):
    return {"id": identity, "player_id": player.casefold(), "player_name": player,
            "strategy": "Standard", "strategy_name": "Standard", "frames": frames,
            "tier": "Bronze", "division": "V", "step": 5,
            "hue_degrees": 125 + (slot or 0) * 70, "slot": slot, "pinned": pinned}


def session(*, fresh=False):
    rows = [row(str(n) * 64, name, frames, slot=n-1)
            for n, (name, frames) in enumerate((('Alex', 600), ('Sam', 570), ('Kai', 540), ('Morgan', 510)), 1)]
    return {"target": TARGET, "rivals": {"rows": rows, "status": None, "strategy_name": "Standard",
        "pb": None if fresh else {**row("e" * 64, "You", 630), "tier": "Iron", "division": "I", "step": 4},
        "options": {"mode": "adaptive", "count": 4, "include_pb": True, "strategy_only": False,
                    "min_ahead": 1, "max_ahead": 5, "min_step": 0, "max_step": 44},
        "limits": {"last_step": 44, "max_ahead": 20, "counts": [1, 2, 3, 4, 6]}}}


HARNESS = """
<script type="module">
import {h,render} from 'preact';
import {useState} from 'preact/hooks';
import {Rivals} from '/ui/components/rivals.js';
import {send} from '/ui/api.js';
function Surface() {
  const [state,setState] = useState(window.reviewInitial);
  window.reviewSet = setState;
  const write = async body => {
    try {const state = await send('PUT','/api/ghost',body); setState(state); return {applied:true};}
    catch(error) {return {applied:false,error:error.message};}
  };
  return h(Rivals,{session:state,write});
}
render(h(Surface),document.getElementById('app'));
</script>
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as play:
        opened = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            yield opened
        finally:
            opened.close()


def _search(route, query, state):
    cursor = query.get("cursor", [None])[0]
    if cursor is not None:
        try:
            assert json.loads(cursor) == CURSOR
        except (ValueError, AssertionError):
            route.fulfill(status=422, json={"detail": "Invalid fixture cursor encoding"})
            return
        if state["defer"]:
            state["pending"].append(route)
            return
        route.fulfill(json={"records": [row("c" * 64, "Zelda", 470)], "next_cursor": None})
    elif query.get("player", [""])[0].casefold().startswith("form"):
        route.fulfill(json={"records": [row("a" * 64, "FormFrame", 400), row("b" * 64, "FormFrame", 450)],
                            "next_cursor": CURSOR})
    elif query.get("player", [""])[0].casefold().startswith("alice"):
        route.fulfill(json={"records": [row("d" * 64, "Alice", 420)], "next_cursor": None})
    else:
        route.fulfill(json={"records": [], "next_cursor": None})


def _write(route, state):
    body = route.request.post_data_json
    assert body["target"] == TARGET
    state["writes"].append(body)
    command = body["rivals"]
    rows = state["session"]["rivals"]["rows"]
    if command.get("action") == "add":
        slot = command.get("slot", 0)
        rows[slot] = row(command["id"], "FormFrame", 400 if command["id"] == "a" * 64 else 450,
                         slot=slot, pinned=True)
    elif command.get("action") == "reset":
        slot = command["slot"]
        rows[slot] = session()["rivals"]["rows"][slot]
    else:
        state["session"]["rivals"]["options"] = deepcopy(command["options"])
    route.fulfill(json=state["session"])


def _request(route, state, markup):
    parsed = urlparse(route.request.url)
    if parsed.path == "/":
        route.fulfill(body=markup, content_type="text/html")
    elif parsed.path == "/api/ghost/rivals/search":
        query = parse_qs(parsed.query, keep_blank_values=True)
        state["requests"].append(query)
        assert json.loads(query["target"][0]) == TARGET
        _search(route, query, state)
    elif parsed.path == "/api/ghost" and route.request.method == "PUT":
        _write(route, state)
    elif parsed.path.startswith("/ui/"):
        path = (UI / parsed.path[4:]).resolve()
        if path.is_relative_to(UI) and path.is_file():
            route.fulfill(body=path.read_bytes(), content_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
        else:
            state["errors"].append("Unmatched UI asset: " + parsed.path)
            route.fulfill(status=404)
    else:
        state["errors"].append("Unexpected request: " + route.request.url)
        route.abort()


@pytest.fixture
def fixture(browser):
    context = browser.new_context(viewport={"width": 1500, "height": 1100})
    page = context.new_page()
    page.set_default_timeout(5000)
    state = {"session": session(), "writes": [], "errors": [], "requests": [], "pending": [], "defer": False}
    page.on("pageerror", lambda error: state["errors"].append(str(error)))
    page.on("console", lambda message: state["errors"].append(message.text) if message.type == "error" else None)
    page.add_init_script("window.reviewInitial = " + json.dumps(state["session"]))
    markup = (UI / "index.html").read_text(encoding="utf-8")
    markup = re.sub(r'<script type="module" src="/ui/app.js"></script>', HARNESS, markup)
    markup = markup.replace("</head>", '<style>body {max-width:none;margin:0;padding:0;} '
                            '#app {width:min(900px,calc(100% - 64px));margin:180px auto 0;}</style></head>')

    page.route("http://rivals.test/**", lambda route: _request(route, state, markup))
    page.goto("http://rivals.test/")
    page.get_by_role("heading", name="Your rivals").wait_for()
    try:
        yield page, state
    finally:
        context.close()


def settings(page):
    page.get_by_role("button", name="Rival settings", exact=True).click()
    page.get_by_role("dialog", name="Rival settings").wait_for()
    return page.get_by_role("dialog", name="Rival settings")


def _screenshot(page, temporary, name):
    destination = Path(os.environ.get("SM64_RIVALS_REVIEW_ARTIFACTS", temporary)) / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(destination))


@pytest.mark.parametrize("width", [1500, 850])
def test_settings_are_centered_over_roster_and_cancel_keeps_draft_local(fixture, tmp_path, width):
    page, state = fixture
    page.set_viewport_size({"width": width, "height": 1100})
    panel = settings(page)
    panel.get_by_role("combobox", name=re.compile("^Rivals")).select_option("3")
    panel.get_by_label("Also race my personal best").uncheck()
    assert state["writes"] == []
    roster, modal = page.locator(".rivals").bounding_box(), panel.bounding_box()
    _screenshot(page, tmp_path, f"rivals-settings-{width}.png")
    assert abs((roster["x"] + roster["width"] / 2) - (modal["x"] + modal["width"] / 2)) < 2
    assert abs((roster["y"] + roster["height"] / 2) - (modal["y"] + modal["height"] / 2)) < 2
    assert modal["x"] >= 0 and modal["x"] + modal["width"] <= width
    panel.get_by_role("button", name="Cancel", exact=True).click()
    assert state["writes"] == []
    panel = settings(page)
    assert panel.get_by_role("combobox", name=re.compile("^Rivals")).input_value() == "4"
    assert panel.get_by_label("Also race my personal best").is_checked()
    assert state["errors"] == []


def test_fresh_profile_can_open_rank_settings_without_a_personal_best(fixture):
    page, state = fixture
    fresh = session(fresh=True)
    state["session"] = fresh
    page.evaluate("state => window.reviewSet(state)", fresh)
    page.wait_for_function("document.querySelectorAll('.rival-row').length === 4")
    panel = settings(page)
    assert panel.get_by_role("group", name="Minimum rank").count() == 1
    assert panel.get_by_role("button", name="Cancel").is_visible()
    assert state["errors"] == []


def test_rank_change_uses_canonical_animation_and_reduced_motion_snaps(fixture):
    page, state = fixture
    settings(page)
    samples = page.evaluate("""async () => {
      const samples = [], icon = document.querySelector('.rival-rank');
      document.querySelector('[aria-label="Increase minimum rank"]').click();
      const until = performance.now()+1100;
      while(performance.now()<until) {
        samples.push({progress:Number(icon.dataset.progress),step:icon.dataset.displayedStep,
          scale:getComputedStyle(icon.querySelector('.hat')).scale});
        await new Promise(requestAnimationFrame);
      }
      return samples;
    }""")
    assert any(0 < item["progress"] < 1 for item in samples), samples
    assert {item["step"] for item in samples} == {"5", "6"}
    assert len({item["scale"] for item in samples}) > 2
    page.emulate_media(reduced_motion="reduce")
    page.get_by_role("button", name="Increase minimum rank").click()
    page.wait_for_function("document.querySelector('.rival-rank').dataset.displayedStep === '7'")
    assert page.locator(".rival-rank").first.get_attribute("data-progress") == "1"
    assert state["writes"] == [] and state["errors"] == []


def test_rapid_rank_presses_coalesce_then_midflight_reduced_motion_settles(fixture):
    page, state = fixture
    panel = settings(page)
    for _ in range(3):
        panel.get_by_role("button", name="Increase minimum rank").click()
    page.wait_for_function("document.querySelector('.rival-rank').dataset.displayedStep === '8'")
    page.wait_for_function("document.querySelector('.rival-rank').dataset.progress === '1'")
    panel.get_by_role("button", name="Decrease minimum rank").click()
    page.wait_for_function("Number(document.querySelector('.rival-rank').dataset.progress) < .9")
    page.emulate_media(reduced_motion="reduce")
    page.wait_for_function("document.querySelector('.rival-rank').dataset.displayedStep === '7' && "
                           "document.querySelector('.rival-rank').dataset.progress === '1'")
    assert state["writes"] == [] and state["errors"] == []


def test_search_fastest_or_slower_add_replace_and_symbol_reset(fixture):
    page, state = fixture
    page.get_by_role("button", name="Add Rival", exact=True).click()
    panel = page.get_by_role("dialog", name="Add Rival", exact=True)
    panel.get_by_role("searchbox", name="Search player").fill("Form")
    recordings = panel.get_by_label("Recording by FormFrame", exact=True)
    recordings.wait_for()
    assert recordings.input_value() == "a" * 64
    recordings.select_option("b" * 64)
    panel.get_by_role("button", name="Add", exact=True).click()
    reset = page.get_by_role("button", name="Restore automatic rival in FormFrame’s slot")
    reset.wait_for()
    assert state["writes"][-1]["rivals"] == {"action": "add", "id": "b" * 64}
    assert not reset.inner_text().strip()
    reset.click()
    assert state["writes"][-1]["rivals"] == {"action": "reset", "slot": 0}
    page.get_by_role("button", name="Replace Sam", exact=True).click()
    panel = page.get_by_role("dialog", name="Replace rival")
    panel.get_by_role("searchbox", name="Search player").fill("Form")
    panel.get_by_label("Recording by FormFrame").wait_for()
    panel.get_by_role("button", name="Add", exact=True).click()
    reset.wait_for()
    assert state["writes"][-1]["rivals"] == {"action": "add", "id": "a" * 64, "slot": 1}
    assert state["errors"] == []


def test_pagination_cursor_round_trips_and_old_page_cannot_replace_new_search(fixture):
    page, state = fixture
    page.get_by_role("button", name="Add Rival", exact=True).click()
    panel = page.get_by_role("dialog", name="Add Rival", exact=True)
    search = panel.get_by_role("searchbox", name="Search player")
    search.fill("Form")
    more = panel.get_by_role("button", name="More players", exact=True)
    more.wait_for()
    more.click()
    panel.get_by_text("Zelda", exact=True).wait_for()
    assert json.loads(state["requests"][-1]["cursor"][0]) == CURSOR
    search.fill("FormF")
    more.wait_for()
    state["defer"] = True
    more.click()
    assert len(state["pending"]) == 1
    search.fill("Alice")
    panel.get_by_text("Alice", exact=True).wait_for()
    state["pending"].pop().fulfill(json={"records": [row("c" * 64, "Zelda", 470)], "next_cursor": None})
    page.wait_for_timeout(150)
    assert panel.get_by_text("Alice", exact=True).is_visible()
    assert panel.get_by_text("FormFrame", exact=True).count() == 0
    assert state["errors"] == []


def _practice_entry(page, base):
    page.goto(base)
    page.locator(".ghost-row .rivals .rival-row").first.wait_for()
    # Control: the existing tab navigation must respond before testing the new flow.
    page.get_by_role("button", name="Rank", exact=True).click()
    page.locator(".rank-page").wait_for()
    page.get_by_role("button", name="Practice", exact=True).click()
    page.locator(".ghost-row .rivals .rival-row").first.wait_for()


def _edit_practice_draft(page):
    page.get_by_role("button", name="Rival settings", exact=True).click()
    panel = page.get_by_role("dialog", name="Rival settings")
    panel.wait_for()
    panel.get_by_role("combobox", name=re.compile("^Rivals")).select_option("3")
    panel.get_by_text("Appearance and sound", exact=True).click()
    opacity = panel.locator("input.ghost-opacity")
    opacity.evaluate("el => {el.value='.5';el.dispatchEvent(new Event('input',{bubbles:true}));}")
    page.wait_for_timeout(220)  # Longer than the old global autosave delay.
    return panel


@pytest.mark.parametrize("width", [1500, 850])
def test_real_practice_ghost_switch_reaches_rivals_and_saves_draft_once(browser, tmp_path, width):
    from tools.ghost_fixture import ghost_session, serve_ui_with_ghost
    payload = {**ghost_session(enabled=True), "rivals": session()["rivals"]}
    writes, errors, responses, failed = [], [], [], []
    with serve_ui_with_ghost(payload) as (base, _state):
        context = browser.new_context(viewport={"width": width, "height": 1100})
        page = context.new_page()
        page.set_default_timeout(10000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: responses.append(response.status)
                if "/api/" in response.url else None)
        page.on("response", lambda response: failed.append((response.status, response.url))
                if response.status >= 400 else None)
        def ghost_write(route):
            if route.request.method != "PUT":
                route.continue_()
                return
            body = route.request.post_data_json
            assert body["target"] == payload["target"]
            writes.append(body)
            payload["rivals"]["options"] = deepcopy(body["rivals"]["options"])
            payload["opacity"] = body.get("opacity", payload["opacity"])
            route.fulfill(json=payload)
        page.route("**/api/ghost", ghost_write)
        # The offline fixture has no replay service; its real availability is empty.
        page.route("**/api/replay/available", lambda route: route.fulfill(json={"available": []}))
        try:
            _practice_entry(page, base)
            panel = _edit_practice_draft(page)
            assert writes == []
            panel.get_by_role("button", name="Cancel", exact=True).click()
            assert writes == []
            page.get_by_role("button", name="Rival settings", exact=True).click()
            panel = page.get_by_role("dialog", name="Rival settings")
            panel.wait_for()
            assert panel.get_by_role("combobox", name=re.compile("^Rivals")).input_value() == "4"
            panel.get_by_text("Appearance and sound", exact=True).click()
            assert float(panel.locator("input.ghost-opacity").input_value()) == payload["opacity"]
            panel.get_by_role("button", name="Cancel", exact=True).click()
            panel = _edit_practice_draft(page)
            assert writes == []
            _screenshot(page, tmp_path, f"practice-rivals-settings-{width}.png")
            roster, modal = page.locator(".rivals").bounding_box(), panel.bounding_box()
            assert modal["x"] >= 0 and modal["x"] + modal["width"] <= width
            assert modal["y"] >= 0 and modal["y"] + modal["height"] <= 1100
            assert abs((roster["x"] + roster["width"] / 2) - (modal["x"] + modal["width"] / 2)) < 2
            panel.get_by_role("button", name="Done", exact=True).click()
            page.wait_for_function("!document.querySelector('.rival-dialog-host')")
            assert len(writes) == 1
            assert writes[0]["rivals"]["options"]["count"] == 3 and writes[0]["opacity"] == .5
            assert errors == [] and responses and all(200 <= value < 300 for value in responses), failed
        finally:
            context.close()
