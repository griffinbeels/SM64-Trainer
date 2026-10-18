"""The Lineup's rival gestures, driven on the real Practice page.

Every test boots the real `index.html`, stylesheet and practice log through
`tools/ghost_fixture.py` (an offline app with an isolated ghost session) and
acts on the active card's Lineup: the seats and their order, the pinned seat's
lock, the swap popover under a seat, the Rivals section of the one Ghost
settings sheet, and the Strategy/Overall tabs that choose the ghost scope.
No live trainer, emulator or production data is used. The fixture records
every body the page PUT in `state["writes"]`; tests that need a deferred or
refused answer intercept the request with Playwright instead.
"""
import contextlib
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from ghost_fixture import (FIXTURE_TARGET, LINEUP_PLAYERS, ghost_session,  # noqa: E402
                           lineup_rivals, lineup_row, serve_ui_with_ghost)
from sm64_events.core.timefmt import format_igt_short  # noqa: E402

CARD = ".log-card.log-card-active"
WIDTHS = (1500, 850)
CURSOR = ["formframe", 450, "b" * 64]
# The page's timed polls, cancelled by a navigation of the test's own.
POLLS = ("/api/pause", "/api/replay/status")


def lineup_session(**rivals):
    return {**ghost_session(enabled=True), "rivals": lineup_rivals(**rivals)}


def record(identity, player, frames, *, tier="Diamond", division="II"):
    """One search result as GET /api/ghost/rivals/search answers it."""
    return {"id": identity, "player_id": player.casefold(), "player_name": player,
            "strategy": FIXTURE_TARGET["strat"], "frames": frames, "tier": tier, "division": division}


SEARCH = [record("a" * 64, "FormFrame", 320), record("b" * 64, "FormFrame", 326),
          record("d" * 64, "Alice", 322), record("c" * 64, "Zelda", 318)]


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as play:
        opened = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            yield opened
        finally:
            opened.close()


def watch(page):
    """Page errors, console errors and every >=400 answer, collected as they happen."""
    seen = {"errors": [], "failed": [], "searches": []}
    page.on("pageerror", lambda error: seen["errors"].append(str(error)))
    page.on("console", lambda message: seen["errors"].append(message.text) if message.type == "error" else None)
    page.on("response", lambda response: seen["failed"].append((response.status, response.url))
            if response.status >= 400 else None)
    page.on("requestfailed", lambda request: None if any(request.url.split("?")[0].endswith(poll) for poll in POLLS)
            else seen["failed"].append(("failed", request.url)))
    page.on("request", lambda request: seen["searches"].append(parse_qs(urlparse(request.url).query))
            if "/api/ghost/rivals/search" in request.url else None)
    return seen


def enter_practice(page, base):
    page.goto(base)
    page.locator(f"{CARD} .lineup-seat.is-you").wait_for()
    # Control: the existing tab navigation must respond before testing the new flow.
    page.get_by_role("button", name="Rank", exact=True).click()
    page.locator(".rank-page").wait_for()
    page.get_by_role("button", name="Practice", exact=True).click()
    page.locator(f"{CARD} .lineup-seat.is-you").wait_for()


@contextlib.contextmanager
def practice(browser, session, *, width=1500, search=None):
    with serve_ui_with_ghost(deepcopy(session)) as (base, state):
        state["search"] = list(search or SEARCH)
        context = browser.new_context(viewport={"width": width, "height": 1100})
        page = context.new_page()
        page.set_default_timeout(10000)
        seen = watch(page)
        # The offline fixture has no replay service; its real availability is empty.
        page.route("**/api/replay/available", lambda route: route.fulfill(json={"available": []}))
        try:
            enter_practice(page, base)
            yield page, state, seen
        finally:
            context.close()


SEATS = """card => [...card.querySelectorAll('.lineup-row > .lineup-seat')].map(s => {
  const r = s.getBoundingClientRect();
  return {key: s.dataset.key, you: s.classList.contains('is-you'), pinned: s.classList.contains('is-pinned'),
    beaten: s.classList.contains('is-beaten'), next: s.classList.contains('is-next'), noGhost: s.classList.contains('no-ghost'),
    lock: Boolean(s.querySelector(':scope > .lineup-lock')), opacity: Number(getComputedStyle(s).opacity),
    name: (s.querySelector('.lineup-name-text') || {}).textContent ?? null,
    time: (s.querySelector('.lineup-time') || {}).textContent ?? null,
    left: r.left, right: r.right, top: r.top, bottom: r.bottom};
})"""

BOX = "el => { const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; }"


def seats(page):
    return page.locator(CARD).evaluate(SEATS)


def settle(page, predicate_js):
    """Wait until the Lineup's DOM satisfies `predicate_js(card)` and its motion has stopped."""
    page.wait_for_function(f"""(() => {{
      const card = document.querySelector('{CARD}');
      if (!card || !({predicate_js})(card)) return false;
      return card.querySelector('.lineup').getAnimations({{subtree: true}}).length === 0;
    }})()""")


def artifact(page, tmp_path, name):
    destination = Path(os.environ.get("SM64_RIVALS_REVIEW_ARTIFACTS", tmp_path)) / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(destination))


def clean(seen):
    assert seen["errors"] == [] and seen["failed"] == [], seen


# --------------------------------------------------------------------------
# The seats
# --------------------------------------------------------------------------

@pytest.mark.parametrize("width", WIDTHS)
def test_seats_sit_slowest_to_fastest_with_you_first(browser, tmp_path, width):
    with practice(browser, lineup_session(), width=width) as (page, state, seen):
        settle(page, "card => card.querySelectorAll('.lineup-seat').length === 5")
        shown = seats(page)
        players = [("You", 343), *((name, frames) for name, _tier, _division, frames in LINEUP_PLAYERS)]
        assert [s["key"] for s in shown] == ["you", "s0", "s1", "s2", "s3"]
        assert [(s["name"], s["time"]) for s in shown] == [(name, format_igt_short(frames)) for name, frames in players]
        assert shown[0]["you"] and shown[0]["time"] == '11"43'
        lefts = [s["left"] for s in shown]
        assert lefts == sorted(lefts) and len(set(lefts)) == 5, lefts
        assert [s["next"] for s in shown] == [False, True, False, False, False]
        card = page.locator(CARD).evaluate(BOX)
        assert all(card["left"] - .5 <= s["left"] and s["right"] <= card["right"] + .5 for s in shown), (card, shown)
        artifact(page, tmp_path, f"lineup-seats-{width}.png")
        assert state["writes"] == []
        clean(seen)


def beaten_session():
    """Two rivals slower than your 11"43: one pinned, one the trainer chose."""
    rows = [lineup_row(0, "Alexander_Speedruns", "Diamond", "V", 350, pinned=True),
            lineup_row(1, "Quinn", "Platinum", "I", 347),
            lineup_row(2, "Kai", "Diamond", "III", 334), lineup_row(3, "Morgan", "Diamond", "I", 331)]
    return lineup_session(rows=rows)


@pytest.mark.parametrize("width", WIDTHS)
def test_a_beaten_pinned_rival_stays_left_of_you_with_its_lock_and_is_not_dimmed(browser, tmp_path, width):
    """Slowest sits left, so a pinned rival you have beaten stays to your left.
    The lock says why it never moves on its own, and only an UNPINNED beaten
    rival is dimmed: a pinned one is your choice, drawn at full strength."""
    with practice(browser, beaten_session(), width=width) as (page, state, seen):
        settle(page, "card => card.querySelectorAll('.lineup-seat').length === 5")
        shown = {s["key"]: s for s in seats(page)}
        assert [s["key"] for s in seats(page)] == ["s0", "s1", "you", "s2", "s3"]
        pinned, beaten = shown["s0"], shown["s1"]
        assert pinned["pinned"] and pinned["lock"] and not pinned["beaten"], pinned
        assert pinned["opacity"] == 1, pinned
        assert beaten["beaten"] and not beaten["pinned"] and not beaten["lock"], beaten
        assert beaten["opacity"] < 1, beaten
        assert pinned["right"] <= shown["you"]["left"] + .5
        artifact(page, tmp_path, f"lineup-beaten-pinned-{width}.png")
        clean(seen)


UNPINNED = """card => { const s = card.querySelector('.lineup-seat[data-key="s0"]');
  return s && !s.querySelector('.lineup-lock') && s.classList.contains('is-beaten'); }"""
UNPIN_WRITE = {"rivals": {"scope": "strategy", "action": "reset", "slot": 0}, "target": FIXTURE_TARGET}


def test_the_lock_unpins_its_seat_from_the_keyboard_and_the_seat_then_dims(browser):
    """The write the lock sends, through the gesture that has no hover."""
    with practice(browser, beaten_session()) as (page, state, seen):
        lock = page.locator(f'{CARD} .lineup-seat[data-key="s0"] > .lineup-lock')
        assert lock.get_attribute("aria-label") == "Unpin Alexander_Speedruns"
        lock.focus()
        page.keyboard.press("Enter")
        settle(page, UNPINNED)
        assert state["writes"] == [UNPIN_WRITE]
        clean(seen)


def test_a_pointer_click_on_the_lock_unpins_its_seat(browser):
    """The mouse is how he unpins. A real pointer moves onto the lock before
    it presses, so this is the click a DOM `.click()` would not exercise."""
    with practice(browser, beaten_session()) as (page, state, seen):
        page.locator(f'{CARD} .lineup-seat[data-key="s0"] > .lineup-lock').click(timeout=3000)
        assert pump_until(page, lambda: state["writes"]), "the pointer click on the lock sent nothing"
        settle(page, UNPINNED)
        assert state["writes"] == [UNPIN_WRITE]
        clean(seen)


# --------------------------------------------------------------------------
# The swap popover
# --------------------------------------------------------------------------

def open_swap(page, key):
    seat = page.locator(f'{CARD} .lineup-seat[data-key="{key}"]')
    seat.hover()
    seat.locator(":scope > .lineup-swap").click()
    popover = page.locator(f"{CARD} .rival-swap")
    popover.wait_for()
    popover.locator(".rival-swap-status").filter(has_text="Searching").wait_for(state="detached")
    # The popover listens for Escape and a click away one task after it opens.
    page.wait_for_timeout(50)
    return seat, popover


def result_names(popover):
    return popover.locator(".rival-swap-result .rival-swap-who strong").all_inner_texts()


@pytest.mark.parametrize("width", WIDTHS)
def test_swap_opens_under_its_seat_searches_and_races_into_that_slot(browser, tmp_path, width):
    kai = lineup_row(2, *LINEUP_PLAYERS[2])
    search = [*SEARCH, record(kai["id"], "Kai", kai["frames"])]
    with practice(browser, lineup_session(), width=width, search=search) as (page, state, seen):
        seat, popover = open_swap(page, "s1")
        assert popover.get_attribute("aria-label") == "Swap Quinn"
        place, under, card = popover.evaluate(BOX), seat.evaluate(BOX), page.locator(CARD).evaluate(BOX)
        assert place["top"] >= under["bottom"] - 1, (place, under)
        assert place["left"] < under["right"] and place["right"] > under["left"], (place, under)
        assert card["left"] - .5 <= place["left"] and place["right"] <= card["right"] + .5, (place, card)
        assert place["right"] <= width
        assert result_names(popover) == ["FormFrame", "Alice", "Zelda", "Kai"]
        # A run already racing is offered as such, never a second time.
        kai_result = popover.locator(".rival-swap-result").filter(has_text="Kai")
        assert kai_result.locator(".rival-swap-pick").inner_text().strip() == "Racing"
        assert kai_result.locator(".rival-swap-pick").is_disabled()
        artifact(page, tmp_path, f"lineup-swap-{width}.png")

        popover.get_by_role("searchbox", name="Find a player").fill("ali")
        page.wait_for_function(f"""[...document.querySelectorAll('{CARD} .rival-swap-who strong')]
          .map(n => n.textContent).join() === 'Alice'""")
        asked = seen["searches"][-1]
        assert asked["player"] == ["ali"] and asked["scope"] == ["strategy"]
        assert json.loads(asked["target"][0]) == FIXTURE_TARGET

        # The slower of two runs by one player, picked from the player's own list.
        popover.get_by_role("searchbox", name="Find a player").fill("form")
        recordings = popover.get_by_label("Recording by FormFrame", exact=True)
        recordings.wait_for()
        assert recordings.input_value() == "a" * 64, "the fastest run is offered first"
        recordings.select_option("b" * 64)
        popover.locator(".rival-swap-result").filter(has_text="FormFrame").get_by_role("button", name="Race").click()
        popover.wait_for(state="detached")
        assert state["writes"] == [{"rivals": {"scope": "strategy", "action": "add", "id": "b" * 64, "slot": 1},
                                    "target": FIXTURE_TARGET}]
        settle(page, """card => { const s = card.querySelector('.lineup-seat[data-key="s1"]');
          return s && s.querySelector('.lineup-lock') && s.textContent.includes('FormFrame'); }""")
        assert {s["key"]: s["time"] for s in seats(page)}["s1"] == format_igt_short(326)
        clean(seen)


def test_escape_and_a_click_away_close_the_swap_and_write_nothing(browser):
    with practice(browser, lineup_session()) as (page, state, seen):
        _seat, popover = open_swap(page, "s2")
        page.keyboard.press("Escape")
        popover.wait_for(state="detached")
        _seat, popover = open_swap(page, "s2")
        page.locator(f"{CARD} .lineup-seat.is-you").click()
        popover.wait_for(state="detached")
        page.wait_for_timeout(250)
        assert state["writes"] == []
        assert page.locator(f'{CARD} .lineup-seat[data-key="s2"] .lineup-name-text').inner_text() == "Kai"
        clean(seen)


def test_a_new_lineup_closes_an_open_swap_without_writing(browser):
    """A popover belongs to the lineup it opened on: when the session moves to
    another strategy, the swap closes rather than pinning into the new one."""
    with practice(browser, lineup_session()) as (page, state, seen):
        _seat, popover = open_swap(page, "s0")
        moved = deepcopy(state["session"])
        moved["target"] = {**moved["target"], "strat": "Sideflip"}
        state["session"] = moved
        origin = "{0.scheme}://{0.netloc}".format(urlparse(page.url))
        assert page.request.post(origin + "/api/_fixture/ghost_broadcast").json() == {"published": True}
        popover.wait_for(state="detached")
        assert state["writes"] == []
        clean(seen)


def _paged_search(route, state):
    query = parse_qs(urlparse(route.request.url).query, keep_blank_values=True)
    state["requests"].append(query)
    player = query.get("player", [""])[0].casefold()
    if "cursor" in query:
        try:
            assert json.loads(query["cursor"][0]) == CURSOR
        except (ValueError, AssertionError):
            route.fulfill(status=422, json={"detail": "Invalid fixture cursor encoding"})
            return
        if state["defer"]:
            state["pending"].append(route)
            return
        route.fulfill(json={"records": [record("c" * 64, "Zelda", 318)], "next_cursor": None})
    elif player.startswith("form"):
        route.fulfill(json={"records": SEARCH[:2], "next_cursor": CURSOR})
    elif player.startswith("alice"):
        route.fulfill(json={"records": [SEARCH[2]], "next_cursor": None})
    else:
        route.fulfill(json={"records": [], "next_cursor": None})


def pump_until(page, ready):
    """Route handlers run while the page waits: pump until `ready()` holds (up to 5 s)."""
    for _ in range(100):
        if ready():
            return True
        page.wait_for_timeout(50)
    return ready()


def test_more_players_round_trips_its_cursor_and_an_old_page_cannot_replace_a_new_search(browser):
    with practice(browser, lineup_session()) as (page, state, seen):
        paging = {"requests": [], "pending": [], "defer": False}
        page.route("**/api/ghost/rivals/search?*", lambda route: _paged_search(route, paging))
        _seat, popover = open_swap(page, "s0")
        search = popover.get_by_role("searchbox", name="Find a player")
        search.fill("Form")
        more = popover.get_by_role("button", name="More players", exact=True)
        more.click()
        popover.get_by_text("Zelda", exact=True).wait_for()
        assert json.loads(paging["requests"][-1]["cursor"][0]) == CURSOR
        assert result_names(popover) == ["FormFrame", "Zelda"]
        search.fill("FormF")
        more.wait_for()
        paging["defer"] = True
        more.click()
        assert pump_until(page, lambda: len(paging["pending"]) == 1)
        search.fill("Alice")
        popover.get_by_text("Alice", exact=True).wait_for()
        paging["pending"].pop().fulfill(json={"records": [record("c" * 64, "Zelda", 318)], "next_cursor": None})
        page.wait_for_timeout(150)
        assert result_names(popover) == ["Alice"], "an old page landed on a newer search"
        assert state["writes"] == []
        clean(seen)


# --------------------------------------------------------------------------
# The Rivals section of Ghost settings
# --------------------------------------------------------------------------

def ghost_settings(page):
    page.get_by_role("button", name="Practice log settings", exact=True).click()
    page.get_by_role("dialog", name="Practice log settings").get_by_role("button", name="Ghost settings").click()
    sheet = page.get_by_role("dialog", name="Ghost settings")
    sheet.locator(".rival-options").wait_for()
    return sheet


def rival_count(sheet):
    return sheet.locator(".rival-options").get_by_role("combobox", name=re.compile("^Rivals"))


def edit_rivals(page, sheet):
    rival_count(sheet).select_option("3")
    sheet.get_by_label("Also race my personal best").uncheck()
    sheet.locator("input.ghost-opacity").evaluate(
        "el => { el.value = '0.5'; el.dispatchEvent(new Event('input', {bubbles: true})); }")
    page.wait_for_timeout(220)  # Longer than the row's trailing write: nothing may leave before Done.


@pytest.mark.parametrize("width", WIDTHS)
def test_ghost_settings_saves_rivals_and_appearance_in_one_write_and_cancel_keeps_it_local(browser, tmp_path, width):
    session = lineup_session()
    with practice(browser, session, width=width) as (page, state, seen):
        sheet = ghost_settings(page)
        edit_rivals(page, sheet)
        assert state["writes"] == []
        sheet.get_by_role("button", name="Cancel", exact=True).click()
        sheet.wait_for(state="detached")
        assert state["writes"] == []

        sheet = ghost_settings(page)
        assert rival_count(sheet).input_value() == "4"
        assert sheet.get_by_label("Also race my personal best").is_checked()
        assert float(sheet.locator("input.ghost-opacity").input_value()) == session["opacity"]
        edit_rivals(page, sheet)
        assert state["writes"] == []
        artifact(page, tmp_path, f"ghost-settings-rivals-{width}.png")
        modal = sheet.bounding_box()
        assert modal["x"] >= 0 and modal["x"] + modal["width"] <= width, modal
        assert modal["y"] >= 0 and modal["y"] + modal["height"] <= 1100, modal

        sheet.get_by_role("button", name="Done", exact=True).click()
        sheet.wait_for(state="detached")
        options = {**session["rivals"]["options"], "count": 3, "include_pb": False}
        assert state["writes"] == [{"opacity": 0.5, "rivals": {"scope": "strategy", "options": options},
                                    "target": FIXTURE_TARGET}]
        assert state["session"]["rivals"]["options"] == options
        # The sheet reads its own session; the card hears the change the way
        # the real server announces every write, as a ghost_changed broadcast.
        origin = "{0.scheme}://{0.netloc}".format(urlparse(page.url))
        assert page.request.post(origin + "/api/_fixture/ghost_broadcast").json() == {"published": True}
        # Not racing your PB any more: your seat stays, without a ghost of its own.
        settle(page, "card => card.querySelector('.lineup-seat.is-you.no-ghost')")
        clean(seen)


def test_a_fresh_profile_opens_the_rank_range_without_a_personal_best(browser):
    with practice(browser, lineup_session(pb_frames=None)) as (page, state, seen):
        you = next(s for s in seats(page) if s["you"])
        assert you["time"] == "—" and you["noGhost"], you
        sheet = ghost_settings(page)
        assert sheet.get_by_role("group", name="Minimum rank").count() == 1
        assert sheet.get_by_text("With no time yet, you race people from Capless 5 to Toad 1.",
                                 exact=False).is_visible()
        assert sheet.locator(".rival-rank").evaluate_all("nodes => nodes.map(n => n.dataset.displayedStep)") == ["1", "5"]
        sheet.get_by_role("button", name="Cancel", exact=True).click()
        assert state["writes"] == []
        clean(seen)


def test_rank_change_uses_canonical_animation_and_reduced_motion_snaps(browser):
    with practice(browser, lineup_session()) as (page, state, seen):
        sheet = ghost_settings(page)
        start = int(sheet.locator(".rival-rank").first.get_attribute("data-displayed-step"))
        samples = page.evaluate("""async () => {
          const sheet = document.querySelector('.modal'), samples = [], icon = sheet.querySelector('.rival-rank');
          sheet.querySelector('[aria-label="Increase minimum rank"]').click();
          const until = performance.now() + 1100;
          while (performance.now() < until) {
            samples.push({progress: Number(icon.dataset.progress), step: icon.dataset.displayedStep,
              scale: getComputedStyle(icon.querySelector('.hat') || icon).scale});
            await new Promise(requestAnimationFrame);
          }
          return samples;
        }""")
        assert any(0 < item["progress"] < 1 for item in samples), samples
        assert {item["step"] for item in samples} == {str(start), str(start + 1)}
        assert len({item["scale"] for item in samples}) > 2, "the cap never squashed or popped"
        page.emulate_media(reduced_motion="reduce")
        sheet.get_by_role("button", name="Increase minimum rank").click()
        page.wait_for_function(f"document.querySelector('.modal .rival-rank').dataset.displayedStep === '{start + 2}'")
        assert sheet.locator(".rival-rank").first.get_attribute("data-progress") == "1"
        assert state["writes"] == []
        clean(seen)


def test_rapid_rank_presses_coalesce_then_midflight_reduced_motion_settles(browser):
    with practice(browser, lineup_session()) as (page, state, seen):
        sheet = ghost_settings(page)
        start = int(sheet.locator(".rival-rank").first.get_attribute("data-displayed-step"))
        for _ in range(3):
            sheet.get_by_role("button", name="Increase minimum rank").click()
        icon = "document.querySelector('.modal .rival-rank')"
        page.wait_for_function(f"{icon}.dataset.displayedStep === '{start + 3}' && {icon}.dataset.progress === '1'")
        sheet.get_by_role("button", name="Decrease minimum rank").click()
        page.wait_for_function(f"Number({icon}.dataset.progress) < .9")
        page.emulate_media(reduced_motion="reduce")
        page.wait_for_function(f"{icon}.dataset.displayedStep === '{start + 2}' && {icon}.dataset.progress === '1'")
        assert state["writes"] == []
        clean(seen)


# --------------------------------------------------------------------------
# Strategy / Overall choose the ghost scope
# --------------------------------------------------------------------------

OVERALL_ROW = {**lineup_row(0, "FormFrame", "Gold", "III", 330), "strategy": "Cannonless", "strategy_name": "Cannonless"}


def _scope_answer(payload, scope):
    answer = deepcopy(payload)
    answer["rivals"]["scope"] = scope
    if scope == "overall":
        answer["rivals"]["rows"] = [deepcopy(OVERALL_ROW)]
    return answer


@pytest.fixture
def practice_scopes(browser):
    payload = lineup_session()
    with serve_ui_with_ghost(deepcopy(payload)) as (base, _state):
        context = browser.new_context(viewport={"width": 1500, "height": 1100})
        page = context.new_page()
        page.set_default_timeout(10000)
        seen = watch(page)
        state = {"writes": [], "pending": [], "defer": False, "reject": False, "scope": "strategy", "shared": False}

        def ghost_write(route):
            if route.request.method != "PUT":
                route.continue_()
                return
            body = route.request.post_data_json
            assert body["target"] == payload["target"]
            state["writes"].append(body)
            scope = body["rivals"]["scope"]
            if state["reject"]:
                route.fulfill(status=422, json={"detail": "Choose the current practice scope."})
            elif state["defer"]:
                state["pending"].append((route, scope))
            else:
                state["scope"] = scope
                route.fulfill(json=_scope_answer(payload, scope))

        def search(route):
            query = parse_qs(urlparse(route.request.url).query, keep_blank_values=True)
            assert json.loads(query["target"][0]) == payload["target"]
            assert query["scope"][0] == state["scope"]
            route.fulfill(json={"records": _scope_answer(payload, state["scope"])["rivals"]["rows"],
                                "next_cursor": None})

        def practice_view(route):
            response = route.fetch()
            data = response.json()
            if state["shared"]:
                for star in data["stars"]:
                    if (star["course_id"], star["star_id"]) == (2, 4):
                        star["one_ladder"] = True
                        star["rank"] = deepcopy(star["entity_rank"])
            route.fulfill(response=response, json=data)

        page.route("**/api/ghost", ghost_write)
        page.route("**/api/ghost/rivals/search?*", search)
        page.route("**/api/session?*", practice_view)
        page.route("**/api/replay/available", lambda route: route.fulfill(json={"available": []}))
        try:
            yield page, base, state, seen, payload
        finally:
            context.close()


def seat_named(page, name):
    return page.locator(f"{CARD} .lineup-seat").filter(has=page.locator(".lineup-name-text", has_text=name))


@pytest.mark.parametrize("width,shared", [(1500, False), (850, True)])
def test_practice_tabs_swap_the_ghost_scope_and_back(practice_scopes, tmp_path, width, shared):
    """Overall admits other compatible strategies; both tabs stay on the
    active card even when their rank ladders coincide (`shared`)."""
    page, base, state, seen, payload = practice_scopes
    state["shared"] = shared
    page.set_viewport_size({"width": width, "height": 1100})
    enter_practice(page, base)
    card = page.locator(CARD)
    strategy = card.get_by_role("button", name="Strategy", exact=True)
    overall = card.get_by_role("button", name="Overall", exact=True)
    assert strategy.get_attribute("aria-pressed") == "true" and state["writes"] == []
    overall.click()
    seat_named(page, "FormFrame").wait_for()
    settle(page, "card => card.querySelectorAll('.lineup-seat').length === 2")
    assert state["writes"] == [{"target": payload["target"], "rivals": {"scope": "overall"}}]
    _seat, popover = open_swap(page, "s0")
    popover.get_by_text("FormFrame", exact=True).wait_for()
    assert seen["searches"][-1]["scope"] == ["overall"]
    page.keyboard.press("Escape")
    popover.wait_for(state="detached")
    assert seat_named(page, "You").locator(".lineup-time").inner_text() == '11"43'
    assert card.locator(".rank-banner-empty").count() == 0
    artifact(page, tmp_path, f"lineup-overall-{width}.png")
    strategy.click()
    seat_named(page, "Alexander_Speedruns").wait_for()
    assert state["writes"][-1] == {"target": payload["target"], "rivals": {"scope": "strategy"}}
    assert strategy.get_attribute("aria-pressed") == "true"
    settle(page, "card => card.querySelectorAll('.lineup-seat').length === 5")
    assert seat_named(page, "FormFrame").count() == 0
    clean(seen)


def test_rapid_practice_scope_changes_finish_on_latest_tab(practice_scopes):
    page, base, state, seen, payload = practice_scopes
    enter_practice(page, base)
    card = page.locator(CARD)
    state["defer"] = True
    card.get_by_role("button", name="Overall", exact=True).click()
    # The card says nothing about rivals until the session answers for the new tab.
    page.locator(f"{CARD} .lineup-host.is-waiting").wait_for()
    # The write reaches the server one step after the click: every write first
    # fetches the local request capability (ui/api.js localFetch).
    assert pump_until(page, lambda: state["pending"])
    assert len(state["pending"]) == 1
    card.get_by_role("button", name="Strategy", exact=True).click()
    assert len(state["writes"]) == 1
    pending, scope = state["pending"].pop()
    state["defer"] = False
    state["scope"] = scope
    pending.fulfill(json=_scope_answer(payload, scope))
    seat_named(page, "Alexander_Speedruns").wait_for()
    page.locator(f"{CARD} .lineup-host:not(.is-waiting)").wait_for()
    assert [body["rivals"]["scope"] for body in state["writes"]] == ["overall", "strategy"]
    assert card.get_by_role("button", name="Strategy", exact=True).get_attribute("aria-pressed") == "true"
    clean(seen)


def test_failed_scope_swap_does_not_repeat_without_another_gesture(practice_scopes):
    page, base, state, seen, _payload = practice_scopes
    enter_practice(page, base)
    card = page.locator(CARD)
    state["reject"] = True
    card.get_by_role("button", name="Overall", exact=True).click()
    page.get_by_text("Choose the current practice scope.", exact=True).wait_for()
    page.wait_for_timeout(350)
    assert len(state["writes"]) == 1
    state["reject"] = False
    card.get_by_role("button", name="Strategy", exact=True).click()
    card.get_by_role("button", name="Overall", exact=True).click()
    seat_named(page, "FormFrame").wait_for()
    assert len(state["writes"]) == 2
    assert seen["failed"] == [(422, base + "/api/ghost")]
    assert len(seen["errors"]) == 1 and "422 (Unprocessable Entity)" in seen["errors"][0]


# --------------------------------------------------------------------------
# The reported Wild Blue PB, through the real rival resolver
# --------------------------------------------------------------------------

def _reported_practice_pb(service):
    from dataclasses import replace
    from tests.test_ghost_adaptive import attempt
    run = replace(attempt(frames=321), id=13194, course_id=2, star_id=2, session_id=service.session_id,
                  game_version=None, native_clock_frame=30978, closed_frame=31297, native_clock_kind="star",
                  timed_at="xcam")
    service.db.upsert_attempt(run)
    service.db.insert_pb(2, 2, "Standard", "igt", 321, run.id, "2026-10-06T21:31:41Z", game_version=None)


@pytest.mark.parametrize("width", WIDTHS)
def test_reported_wild_blue_pb_reaches_the_real_lineup_and_wario_range(browser, tmp_path, monkeypatch, width):
    from tools import ui_fixture
    from tools.ghost_fixture import attach_ghost_routes
    from sm64_events.ghost.rivals import Rivals
    from tests.test_ghost_rivals import Native, Population, CONTROLS
    from tests.test_ghost_rival_strategy import reported_wild_blue_standards

    target = {"kind": "star", "entity_key": "2-2", "strat": "Standard"}
    state = {"session": ghost_session(target=target, enabled=True), "writes": []}
    original = ui_fixture.create_app

    def create(*args, **kwargs):
        app = original(*args, **kwargs)
        attach_ghost_routes(app, state)
        return app
    monkeypatch.setattr(ui_fixture, "create_app", create)
    with ui_fixture.serve_ui_live(target=(2, 2)) as (base, service):
        ui_fixture._run_coro(service.set_strat(2, 2, "Standard"))
        ui_fixture._run_coro(service.set_rank_mode("pb"))
        reported_wild_blue_standards(service.ranks)
        _reported_practice_pb(service)
        population, native = Population(), Native()
        population.rows = []
        rivals = Rivals(service, population, native, lambda: "us")
        source = rivals.refresh(target, CONTROLS, enabled=True)
        assert rivals.pb["step"] == 19 and source["available"]
        state["session"].update(source=source, rivals=rivals.payload())
        context = browser.new_context(viewport={"width": width, "height": 1100})
        page = context.new_page()
        page.set_default_timeout(10000)
        seen = watch(page)
        page.route("**/api/replay/available", lambda route: route.fulfill(json={"available": []}))
        try:
            enter_practice(page, base)
            shown = seats(page)
            assert [(s["name"], s["time"]) for s in shown] == [("You", '10"70')], shown
            practice_view = page.request.get(base + "/api/session?clock=igt").json()
            star = next(s for s in practice_view["stars"] if (s["course_id"], s["star_id"]) == (2, 2))
            assert (star["rank"]["rank"], star["rank"]["division"]) == ("Gold", "I")
            sheet = ghost_settings(page)
            assert sheet.get_by_label("Wario 5", exact=True).is_visible()
            assert sheet.get_by_label("Wario 1", exact=True).is_visible()
            assert sheet.get_by_label("Standard only").count() == 0
            assert sheet.locator(".rival-rank").first.get_attribute("data-displayed-step") == "20"
            assert sheet.locator(".rival-rank").last.get_attribute("data-displayed-step") == "24"
            artifact(page, tmp_path, f"wild-blue-settings-{width}.png")
            clean(seen)
        finally:
            context.close()
