"""The Scene viewer's Course/Star/Strategy selector and its ghost panel (M4 round 4, items 2 and 3) on
the real page.

His words (round 4): "We should include our Course/Star/Strategy selector on this screen. By default, it
opens to whatever the user currently has selected in the practice log (or how they got here)" (item 2);
"we should also display on the bottom A PAGINATED LIST OF GHOSTS THAT WE COULD LOAD INTO THIS SCENE!!!
... If I add a ghost, it should immediately appear in the scene without needing to reload" (item 3).

The UI fixture boots the real app with test_ui_attempt_scene.py's scratch lab and fake renderer host
(headless, no GPU). The lab's round 4 routes are stubbed here with their agreed shapes, ahead of the
lab's own (m4-ghostdata builds them, m4-ghostdraw the socket's "ghosts" answer): GET
/api/scene-lab/stage, GET /api/scene-lab/ghosts, the open answer's "title" and "target", and the
scene socket's {"kind": "ghosts"} reply. The page's logic is pinned in tests/frontend/sceneviewer.test.js.
"""
import json
import math
import re

from fastapi import APIRouter
from playwright.sync_api import sync_playwright

import test_ui_attempt_scene as attempt_scene
from sm64_events.server import scene_lab_api

STAGE_ATTEMPT = 4000                     # opens the stub's package (any id the stub does not refuse)
NO_SCENE_STAR = 5                        # Blast Away the Wall (ids count from 0): no scene for its stage
NO_SCENE = "None of your runs of this has a saved scene."
REFUSED = "top-refused"
REFUSAL = "This ghost was recorded on another version of the course."
LONG_STRAT = "Owlless Triple Jump into Backwards Long Jump, Cannonless"
PLAYERS = ["SullyLikesBigChungusTheSecond", "WaterproofTeabag_", "Runner One", "Kz_Speedruns_Backwards_Long",
           "minikori", "Run&Jump:1", "W" * 20, "MarioSpeedrunsWWW", "EightIsGreat", "Kz_Speedruns_BLJ18"]
WHOLE = 18                     # a typical speedrunner name this long shows whole at 1280 (the lead's rule)


def _time(hundredths: int) -> str:
    """A bare time from hundredths: 11"03, or 1'02"40 past a minute."""
    seconds, cents = divmod(hundredths, 100)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}'{seconds:02d}\"{cents:02d}" if minutes else f"{seconds}\"{cents:02d}"


def _ghost(ref: str, player: str, hundredths: int, *, rank=None, more=0, yours=False, attempt=None, at=0,
           strat=None, version=None) -> dict:
    return {"ref": ref, "player": player, "time": _time(hundredths), "frames": hundredths * 30 // 100,
            "rank": rank, "recorded_utc": f"2026-10-{1 + at % 27:02d}T12:00:00Z", "more": more, "yours": yours,
            "attempt": attempt, "strat": strat, "version": version}


def _yours(sort: str) -> list[dict]:
    rows = [_ghost(f"yours-{i}", "You", 1103 + i * 17, yours=True, attempt=STAGE_ATTEMPT if i == 0 else 9000 + i,
                   at=(i * 11) % 23) for i in range(23)]
    return sorted(rows, key=lambda row: row["recorded_utc"], reverse=True) if sort == "newest" else rows


def _slug(strat: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", strat.lower()).strip("-")


ROMS = ("us", "jp", None)               # each top ghost in turn: US, JP, a record that names no ROM


def _top(strat: str | None) -> list[dict]:
    """One strategy's top ghosts, or (None) All strategies': each of those names its own, the long one too."""
    rows = [_ghost(f"top-{_slug(strat or 'all')}-{i}", PLAYERS[i % len(PLAYERS)], 1001 + i * 9, rank=i + 1,
                   strat=strat or (LONG_STRAT if i % 2 else "Single Jump"), version=ROMS[i % 3]) for i in range(20)]
    rows[2] = {**rows[2], "ref": REFUSED}
    return rows


def _player(name: str) -> list[dict]:
    return [_ghost(f"player-{name}-{i}", name, 1050 + i * 31) for i in range(3)]


def _page(rows: list[dict], page: int, size: int = 10) -> dict:
    return {"items": rows[(page - 1) * size:page * size], "page": page,
            "pages": max(1, math.ceil(len(rows) / size)), "reason": None}


def _stubs(app, lab, seen) -> None:
    """GET /stage and /ghosts, the open answer's title and target, and the socket's "ghosts" reply."""
    seen.update(stage=[], ghosts=[], sets=[], titles={}, stage_attempts={})
    router = APIRouter()

    @router.get("/api/scene-lab/stage")
    def stage(course: int | None = None, star: int | None = None, segment: int | None = None, strat: str | None = None):
        seen["stage"].append({"course": course, "star": star, "segment": segment, "strat": strat})
        chosen = strat or LONG_STRAT
        target = {"course_id": course, "star_id": star, "segment_id": segment, "strat": chosen,
                  "course_name": "Whomp's Fortress", "star_name": "Shoot into the Wild Blue", "strat_name": chosen}
        if star == NO_SCENE_STAR:
            return {"target": target, "attempt": None, "time": None, "reason": NO_SCENE, "scenes": []}
        # Each strategy's fastest is another attempt: the first asked is STAGE_ATTEMPT.
        attempt = seen["stage_attempts"].setdefault(chosen, STAGE_ATTEMPT + 4 * len(seen["stage_attempts"]))
        seen["titles"][attempt] = (target, f"Whomp's Fortress · Shoot into the Wild Blue · {chosen} · 11\"03")
        return {"target": target, "attempt": attempt, "time": "11\"03", "reason": None,
                "scenes": [{"attempt": attempt, "time": "11\"03", "recorded_utc": "2026-10-27T12:00:00Z"}]}

    @router.post("/api/scene-lab/open")
    async def open_scene(body: dict):
        seen["opened"].append(body)
        if "attempt" not in body:
            return await lab.open(body["package"])
        described = {**await lab.open(attempt_scene.PACKAGE), "attempt": body["attempt"]}
        seen["host_pids"].append(described.get("renderer_pid"))
        if body["attempt"] in seen["titles"]:
            target, title = seen["titles"][body["attempt"]]
            described.update(title=title, target=target)
        return described

    @router.get("/api/scene-lab/ghosts")
    def ghosts(section: str, page: int = 1, sort: str = "fastest", strat: str | None = None, q: str = "",
               player: str = "", course: int | None = None, star: int | None = None, segment: int | None = None,
               strategies: str = "one"):
        seen["ghosts"].append({"section": section, "page": page, "sort": sort, "strat": strat, "q": q,
                               "player": player, "course": course, "star": star, "strategies": strategies})
        if section == "yours":
            return _page(_yours(sort), page)
        if section == "top":
            return _page(_top(None if strategies == "all" else strat or ""), page)
        if section == "search":
            found = [name for name in PLAYERS if q.lower() in name.lower()]
            return _page([{**_player(name)[0], "more": 2} for name in found], page)
        return _page(_player(player), page)

    app.include_router(router)
    for _ in router.routes:
        app.router.routes.insert(0, app.router.routes.pop())


def _answer_ghosts(monkeypatch, seen) -> None:
    """The scene socket answers {"kind": "ghosts"} as interface B says: what it draws, what it refused."""
    built = scene_lab_api._PageSocket.__init__

    def init(self, lab, websocket):
        built(self, lab, websocket)
        receive = websocket.receive

        async def receiving():
            while True:
                event = await receive()
                message = json.loads(event["text"]) if event.get("text") else None
                if not isinstance(message, dict) or message.get("kind") != "ghosts":
                    return event
                seen["sets"].append([ghost["ref"] for ghost in message["set"]])
                await self.send({"id": message["id"], "kind": "ghosts",
                                 "shown": [g["ref"] for g in message["set"] if g["ref"] != REFUSED],
                                 "refused": [{"ref": REFUSED, "reason": REFUSAL}
                                             for g in message["set"] if g["ref"] == REFUSED]})
        websocket.receive = receiving
    monkeypatch.setattr(scene_lab_api._PageSocket, "__init__", init)


# Every picture request on the scene socket, so a test sees the picture asked for again after an Add.
PICTURES = r"""
(() => {
  const sent = window.__pictures = [];
  const Native = window.WebSocket;
  window.WebSocket = class extends Native {
    send(data) {
      try { const message = JSON.parse(data); if (!message.kind) sent.push(message.frame); }
      catch { /* not a request */ }
      return super.send(data);
    }
  };
})()
"""
DRAWN = "!!document.querySelector('.scene-lab-readout')"
SECTION = ".scene-lab-ghost-section[aria-label='{}']"


def _browse(play, base, width=1280):
    browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
    page = browser.new_page(viewport={"width": width, "height": 1000})
    page.add_init_script(PICTURES)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base)
    return browser, page, errors


def _shoot(page, folder, name):
    for width in (850, 1280):
        page.set_viewport_size({"width": width, "height": 1000})
        page.wait_for_timeout(150)
        page.locator(".scene-lab").screenshot(path=str(folder / f"{name}-{width}.png"))
    page.set_viewport_size({"width": 1280, "height": 1000})


def _selector(page) -> dict:
    return page.evaluate("""(() => {
      const root = document.querySelector('.scene-lab-stage-picker');
      return { name: root.querySelector('.scene-lab-target-words strong')?.textContent,
        course: root.querySelector('.scene-lab-target-words small')?.textContent,
        strat: root.querySelector('select')?.value ?? null,
        note: root.querySelector('.scene-lab-stage-note')?.textContent ?? null };
    })()""")


# Each ghost list's box and its controls', left to right; the first row's top.
LISTS = """[...document.querySelectorAll('.scene-lab-ghost-section')].map((section) => {
  const box = section.getBoundingClientRect(), row = section.querySelector('.scene-lab-ghost-list > li');
  return { name: section.getAttribute('aria-label'), left: box.left, right: box.right,
    controls: [...section.querySelectorAll('header > select, header > input')].map((c) => c.getBoundingClientRect().right),
    row: row ? Math.round(row.getBoundingClientRect().top) : null };
})"""


def _lists_hold_their_columns(page) -> None:
    """A long strategy name ellipsizes in its list's dropdown: no list grows over its neighbour, no control
    pokes out of its list, and side-by-side lists start their rows level (measured 2026-10-27: Top ghosts'
    dropdown widened its column over Find a player)."""
    lists = page.evaluate(LISTS)
    for one, after in zip(lists, lists[1:], strict=False):
        assert one["right"] <= after["left"] + 1 or after["left"] < one["left"], lists     # side by side, or wrapped
    for one in lists:
        assert all(right <= one["right"] + 1 for right in one["controls"]), one
    level = {one["row"] for one in lists[:2] if one["row"] is not None}
    assert len(level) == 1, lists


def _pick(page, course: str, star: str) -> None:
    page.locator(".scene-lab-target").click()
    page.locator("[role=dialog] button", has_text=course).first.click()
    page.locator("[role=dialog] .starcell", has_text=star).first.click()
    page.locator("[role=dialog]").wait_for(state="detached")


def test_arriving_opens_the_practice_targets_stage_and_a_pick_opens_another_or_says_why(tmp_path, monkeypatch):
    import ui_fixture
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_stubs) as (base, seen), \
            sync_playwright() as play:
        _answer_ghosts(monkeypatch, seen)
        browser, page, errors = _browse(play, base)
        try:
            page.locator('.nav-item[title="Scene viewer"]:visible').click()          # no Ghost button
            page.wait_for_function(DRAWN, timeout=20000)
            assert seen["stage"] == [{"course": ui_fixture.FIXTURE_COURSE, "star": ui_fixture.FIXTURE_STAR,
                                      "segment": None, "strat": ui_fixture.FIXTURE_STRAT}]
            assert seen["opened"] == [{"attempt": STAGE_ATTEMPT}]
            assert _selector(page) == {"name": "Shoot into the Wild Blue", "course": "Whomp's Fortress",
                                       "strat": ui_fixture.FIXTURE_STRAT, "note": None}
            title = page.locator(".scene-lab-title").inner_text()
            assert title == f"Whomp's Fortress · Shoot into the Wild Blue · {ui_fixture.FIXTURE_STRAT} · 11\"03"

            _pick(page, "Whomp's Fortress", "Blast Away the Wall")                    # a star with no scene
            page.wait_for_function(f"document.querySelector('.scene-lab-stage-note')?.textContent === {json.dumps(NO_SCENE)}")
            assert seen["stage"][-1] == {"course": ui_fixture.FIXTURE_COURSE, "star": NO_SCENE_STAR, "segment": None,
                                         "strat": None}
            page.wait_for_function("!document.querySelector('.scene-lab-player')")     # the other star's scene closed
            assert _selector(page)["name"] == "Shoot into the Wild Blue"               # the stub names every star so
            _shoot(page, tmp_path, "scene-stage-none")

            _pick(page, "Whomp's Fortress", "Shoot into the Wild Blue")
            page.wait_for_function(DRAWN, timeout=20000)
            assert seen["stage"][-1]["star"] == 2
            assert seen["opened"][-1] == {"attempt": seen["stage_attempts"][LONG_STRAT]}
            assert _selector(page)["strat"] == LONG_STRAT                               # no strategy asked: the lab's
            page.locator(f"{SECTION.format('Top ghosts')} li").first.wait_for()
            _shoot(page, tmp_path, "scene-stage-long")
            for width in (850, 1280):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(100)
                _lists_hold_their_columns(page)
            page.set_viewport_size({"width": 1280, "height": 1000})
            strategy = page.locator(".scene-lab-stage-picker select")
            other = [value for value in strategy.evaluate("(s) => [...s.options].map((o) => o.value)")
                     if value and value != LONG_STRAT][0]
            strategy.select_option(other)
            page.wait_for_function(f"document.querySelector('.scene-lab-title').textContent.includes({json.dumps(other)})")
            assert seen["stage"][-1]["strat"] == other
            _shoot(page, tmp_path, "scene-stage-picked")
            assert errors == []
        finally:
            browser.close()


def _rows(page, section: str) -> list[dict]:
    return page.evaluate(f"""[...document.querySelectorAll("{SECTION.format(section)} .scene-lab-ghost-list > li")]
      .map((li) => ({{ ref: li.dataset.ref, shown: li.classList.contains('is-shown'),
        action: li.querySelector(':scope > button:not(.scene-lab-ghost-more)')?.textContent.trim() ?? null,
        stage: !!li.querySelector('.scene-lab-ghost-stage'),
        reason: li.querySelector('.scene-lab-ghost-reason')?.textContent ?? null }}))""")


def _row(page, section: str, ref: str):
    return page.locator(f"{SECTION.format(section)} li[data-ref='{ref}']")


def _names_show_whole(page) -> None:
    """Every player name up to WHOLE characters shows whole; every name, cut or not, is its own tooltip."""
    names = page.evaluate("""[...document.querySelectorAll('.scene-lab-ghost-player')].map((name) => ({
      text: name.firstChild.textContent, title: name.title, cut: name.scrollWidth > name.clientWidth }))""")
    assert names and all(name["title"] == name["text"] for name in names), names
    cut = [name["text"] for name in names if name["cut"] and len(name["text"]) <= WHOLE]
    assert not cut, cut


def _ghost_rows_align(page) -> None:
    """Every row's time and action button share one column down a list (his weight rule)."""
    for section in ("Your ghosts", "Top ghosts"):
        edges = page.evaluate(f"""[...document.querySelectorAll("{SECTION.format(section)} .scene-lab-ghost-list > li")]
          .map((li) => [Math.round(li.querySelector('.scene-lab-ghost-time').getBoundingClientRect().right),
                        Math.round(li.querySelector(':scope > button, :scope > .scene-lab-ghost-stage')
                          .getBoundingClientRect().right)])""")
        assert len({edge[0] for edge in edges}) == 1 and len({edge[1] for edge in edges}) == 1, (section, edges)


def _shown(page, ref: str, shown: bool = True) -> None:
    """Waits until the row for `ref` looks selected (or not)."""
    page.wait_for_function("([ref, shown]) => document.querySelector(`li[data-ref='${ref}']`)?.classList"
                           ".contains('is-shown') === shown", arg=[ref, shown])


def _first_row(page, section: str, ref: str) -> None:
    page.wait_for_function("([section, ref]) => document.querySelector(`${section} li`)?.dataset.ref === ref",
                           arg=[SECTION.format(section), ref])


def _ghosts_added_removed_and_refused(page, seen, first: str) -> None:
    before = page.evaluate("window.__pictures.length")
    _row(page, "Top ghosts", first).get_by_role("button", name="Add").click()
    _shown(page, first)
    assert seen["sets"] == [[first]]
    page.wait_for_function(f"window.__pictures.length > {before}")               # drawn again, no reload
    _row(page, "Top ghosts", REFUSED).get_by_role("button", name="Add").click()
    page.wait_for_function("(ref) => document.querySelector(`li[data-ref='${ref}'] .scene-lab-ghost-reason`)",
                           arg=REFUSED)
    assert seen["sets"][-1] == [first, REFUSED]
    refused = {row["ref"]: row for row in _rows(page, "Top ghosts")}[REFUSED]
    assert refused == {**refused, "shown": False, "action": "Add", "reason": REFUSAL}
    _row(page, "Your ghosts", "yours-3").get_by_role("button", name="Add").click()
    _shown(page, "yours-3")
    assert seen["sets"][-1] == [first, "yours-3"]


def _a_player_and_their_slower_ghosts(page, seen) -> str:
    page.get_by_role("searchbox", name="Find a player by name").fill("sully")
    page.locator(f"{SECTION.format('Find a player')} li").first.wait_for()
    asked = [ask for ask in seen["ghosts"] if ask["section"] == "search"]
    assert [ask["q"] for ask in asked] == ["sully"]                               # one ask, once typing stopped
    name = "SullyLikesBigChungusTheSecond"
    page.locator(SECTION.format("Find a player")).get_by_role("button", name="2 more").click()
    page.locator(f"{SECTION.format('Find a player')} .scene-lab-ghost-more-list li").nth(2).wait_for()
    assert seen["ghosts"][-1] == {**seen["ghosts"][-1], "section": "player", "player": name}
    _row(page, "Find a player", f"player-{name}-2").get_by_role("button", name="Add").click()
    _shown(page, f"player-{name}-2")
    return name


# Top ghosts' first rows: each one's flag (its alt, the region's word) and its strategy line.
TOP_MARKS = f"""[...document.querySelectorAll("{SECTION.format('Top ghosts')} .scene-lab-ghost-list > li")]
  .map((li) => [li.querySelector('.region-flag')?.alt ?? null, li.querySelector('.scene-lab-ghost-strat')?.textContent ?? null])"""
# Every strategy line and flag stays inside its row (a long strategy is cut, never pushes the row wider).
MARKS_INSIDE = f"""[...document.querySelectorAll("{SECTION.format('Top ghosts')} .scene-lab-ghost-list > li")]
  .every((li) => {{ const row = li.getBoundingClientRect();
    return [...li.querySelectorAll('.scene-lab-ghost-strat, .region-flag')]
      .every((mark) => {{ const box = mark.getBoundingClientRect(); return box.left >= row.left - 1 && box.right <= row.right + 1; }}); }})"""


def _top_lists_every_strategy_with_flags(page, seen) -> None:
    """Round 9: Top ghosts asks All strategies first (item 7), each row names its strategy, and every row
    wears the flag of its recording's ROM, none where the record names none (item 11)."""
    asked = [ask for ask in seen["ghosts"] if ask["section"] == "top"]
    assert (asked[0]["strategies"], asked[0]["strat"]) == ("all", None), asked
    picker = page.locator(SECTION.format("Top ghosts")).get_by_role("combobox", name="Strategy for top ghosts")
    assert picker.evaluate("(s) => s.options[s.selectedIndex].textContent") == "All strategies"
    assert page.evaluate(TOP_MARKS)[:4] == [["US", "Single Jump"], ["JP", LONG_STRAT], [None, "Single Jump"],
                                            ["US", LONG_STRAT]]


def test_the_ghost_panel_pages_sorts_searches_and_adds_ghosts_into_the_scene(tmp_path, monkeypatch):
    import ui_fixture
    first = "top-all-0"
    with attempt_scene._served(tmp_path, monkeypatch, lab_on=True, extra=_stubs) as (base, seen), \
            sync_playwright() as play:
        _answer_ghosts(monkeypatch, seen)
        browser, page, errors = _browse(play, base)
        try:
            page.locator('.nav-item[title="Scene viewer"]:visible').click()
            page.wait_for_function(DRAWN, timeout=20000)
            page.locator(f"{SECTION.format('Top ghosts')} li").first.wait_for()
            yours = _rows(page, "Your ghosts")
            assert [row["ref"] for row in yours] == [f"yours-{i}" for i in range(10)]
            assert yours[0]["stage"] and yours[0]["action"] is None                      # the stage is not addable
            assert all(row["action"] == "Add" for row in yours[1:])
            assert page.locator(SECTION.format("Your ghosts")).get_by_text("Page 1", exact=True).is_visible()
            top = _rows(page, "Top ghosts")
            assert len(top) == 10 and top[0]["ref"] == first and top[2]["ref"] == REFUSED
            _top_lists_every_strategy_with_flags(page, seen)
            for width in (850, 1200, 1280, 1440, 1500, 1600):
                page.set_viewport_size({"width": width, "height": 1000})
                page.wait_for_timeout(100)
                _ghost_rows_align(page)
                assert page.evaluate(MARKS_INSIDE), width
                if width >= 1280:
                    _names_show_whole(page)
            page.set_viewport_size({"width": 1280, "height": 1000})

            _ghosts_added_removed_and_refused(page, seen, first)
            name = _a_player_and_their_slower_ghosts(page, seen)
            _shoot(page, tmp_path, "scene-ghosts")
            _row(page, "Top ghosts", first).get_by_role("button", name="Remove").click()
            _shown(page, first, False)
            assert seen["sets"][-1] == ["yours-3", f"player-{name}-2"]

            section = page.locator(SECTION.format("Your ghosts"))
            section.get_by_role("button", name="Next").click()
            _first_row(page, "Your ghosts", "yours-10")
            section.get_by_role("combobox", name="Sort your ghosts").select_option("newest")
            newest = sorted(range(23), key=lambda i: ((i * 11) % 23, -i))[-1]
            _first_row(page, "Your ghosts", f"yours-{newest}")                            # page 1 again, newest first
            assert seen["ghosts"][-1] == {**seen["ghosts"][-1], "section": "yours", "sort": "newest", "page": 1}
            top_strategy = page.locator(SECTION.format("Top ghosts")).get_by_role("combobox", name="Strategy for top ghosts")
            other = [value for value in top_strategy.evaluate("(s) => [...s.options].map((o) => o.value)")
                     if value and value != ui_fixture.FIXTURE_STRAT][0]
            top_strategy.select_option(other)
            _first_row(page, "Top ghosts", f"top-{_slug(other)}-0")
            assert seen["ghosts"][-1] == {**seen["ghosts"][-1], "section": "top", "strat": other}
            assert _selector(page)["strat"] == ui_fixture.FIXTURE_STRAT                  # the stage keeps its own
            assert errors == []
        finally:
            browser.close()

