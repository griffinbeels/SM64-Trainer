"""The Lineup on the real practice card, at the supported floor and wide.

Every check boots the real `index.html`, stylesheet and practice log through
`tools/ghost_fixture.py` and reads the active card's Ghost Overlay row off the
painted page. The model's rules have their own node-driven test
(tests/test_ui_lineup_model.py); this file holds what only a render can show:

  * each state draws the right people and words, inside the card, with every
    name whole and on at most two lines;
  * the number on the lit track is the difference of the two times you can
    read, not frame arithmetic;
  * your seat is the anchor: switching lineups keeps the SAME element, and
    loading resolving never paints a row without seats;
  * the strategy buttons write this card's strategy, the head toggle writes
    `enabled`, and every endless shimmer is one ui/offscreenloops.js freezes;
  * every runner name the bundled library knows fits a seat at 850px.

Playwright is used directly because two checks must intercept a request
(`page.route`); frame sampling still goes through `uilab.trace.record`.
"""
import contextlib
from copy import deepcopy
import json
import re
import sys
from pathlib import Path
from urllib.request import urlopen

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from playwright.sync_api import sync_playwright  # noqa: E402
from uilab import trace  # noqa: E402

from ghost_fixture import (FIXTURE_TARGET, LINEUP_PLAYERS, ghost_session,  # noqa: E402
                           lineup_rivals, lineup_row, serve_ui_with_ghost)
from sm64_events.core.timefmt import format_igt_short  # noqa: E402
from tests.test_ui_lineup_model import WORDS  # noqa: E402
from tests.browser_faults import no_faults, watch  # noqa: E402

CARD = ".log-card.log-card-active"
ROW = f"{CARD} .ghost-row.ghost-row-lineup"
TOGGLE = f"{CARD} .log-card-actions > .ghost-toggle"
SEAT = f"{ROW} .lineup-row > .lineup-seat"
WIDTHS = (1500, 850)
#: The PB whose gap to the first rival (Alexander_Speedruns, 339 frames) reads
#: differently from frame arithmetic: 344 shows 11"46 and 339 shows 11"30, so
#: the track must say −0.16, while (344 − 339) / 30 rounds to 0.17.
GAP_PB = 344
#: A best recorded run slower than the fixture's 343-frame PB, as it nearly always is.
BEST_RUN = 352
NO_STRAT_TARGET = {**FIXTURE_TARGET, "strat": None}


def lineup_session(*, enabled=True, target=None, **rivals):
    session = {**ghost_session(enabled=enabled), "rivals": lineup_rivals(**rivals)}
    if target is not None:
        session["target"] = target
    return session


def marked_rows():
    """A pinned rival you have beaten, one still downloading, one verified, and
    an unpinned rival you have beaten."""
    rows = [lineup_row(0, "Alexander_Speedruns", "Diamond", "V", 350, pinned=True),
            lineup_row(1, "Quinn", "Diamond", "IV", 335, pending=True),
            lineup_row(2, "Kai", "Diamond", "III", 334),
            lineup_row(3, "Morgan", "Diamond", "I", 360)]
    rows[2]["verified"] = True
    return rows


def unknown_rom():
    session = lineup_session()
    session["source"] = {**session["source"], "available": False, "reason": "unknown_rom"}
    return session


STATES = {
    "ready": lineup_session(pb_frames=GAP_PB),
    "marks": lineup_session(rows=marked_rows()),
    "loading": lineup_session(state="loading"),
    "no_strategy": lineup_session(state="no_strategy", target=NO_STRAT_TARGET),
    # Round 11: "That strategy selector should be visible REGARDLESS of ghosts
    # being on or off."
    "no_strategy_off": lineup_session(enabled=False, state="no_strategy", target=NO_STRAT_TARGET),
    "nobody": lineup_session(rows=[], reason="nobody"),
    "offline": lineup_session(rows=[], reason="offline"),
    "signed_out": lineup_session(rows=[], reason="signed_out"),
    "unknown_rom": unknown_rom(),
    "unreachable": lineup_session(),       # served behind a failing GET
    "off": lineup_session(enabled=False),
    # Your PB has no poses: your best recorded run races in its seat and colour.
    "best_run": lineup_session(best_run_frames=BEST_RUN),
}

#: (seats, placeholders, note words) each state paints.
EXPECT = {
    "ready": (5, 0, None), "marks": (5, 0, None), "loading": (5, 4, None),
    "no_strategy": (0, 0, "noStrategy"), "no_strategy_off": (0, 0, "noStrategyOff"),
    "nobody": (1, 0, "nobody"), "offline": (1, 0, "offline"),
    "signed_out": (1, 0, "signedOut"), "unknown_rom": (0, 0, "unknownRom"),
    "unreachable": (0, 0, "unreachable"), "off": (5, 0, None), "best_run": (6, 0, None),
}

READY = {
    "loading": f"{SEAT}.is-placeholder", "no_strategy": f"{ROW} .lineup-note.has-choices",
    "no_strategy_off": f"{ROW} .lineup-note.has-choices",
    "off": f"{ROW} .lineup.is-off", "ready": f"{SEAT}:not(.is-you)", "marks": f"{SEAT}:not(.is-you)",
    "best_run": f"{SEAT}:not(.is-you)",
}

SETTLED = """() => {
  const row = document.querySelector('.log-card.log-card-active .ghost-row');
  if (!row) return false;
  return document.getAnimations().every((a) => {
    const target = a.effect && a.effect.target;
    if (!target || !row.contains(target)) return true;
    return a.effect.getComputedTiming().iterations === Infinity || a.playState !== 'running';
  });
}"""

MEASURE = """() => {
  const card = document.querySelector('.log-card.log-card-active');
  const row = card.querySelector('.ghost-row');
  const lineup = row.querySelector('.lineup');
  const ol = lineup.querySelector('.lineup-row');
  const clip = lineup.querySelector('.lineup-clip');
  const clipBox = clip.getBoundingClientRect();
  const contents = (el) => { const r = document.createRange(); r.selectNodeContents(el);
    return [...r.getClientRects()].filter((b) => b.width > 0); };
  const seats = [...ol.querySelectorAll(':scope > .lineup-seat')].map((s) => {
    const box = s.getBoundingClientRect();
    const name = s.querySelector('.lineup-name:not(.lineup-bone)');
    const text = s.querySelector('.lineup-name-text');
    const time = s.querySelector('.lineup-time:not(.lineup-bone)');
    const rects = name ? contents(name) : [];
    const lineHeight = name ? parseFloat(getComputedStyle(name).lineHeight) : 0;
    return {
      key: s.dataset.key, you: s.classList.contains('is-you'),
      placeholder: s.classList.contains('is-placeholder'), pinned: s.classList.contains('is-pinned'),
      pending: s.classList.contains('is-pending'), beaten: s.classList.contains('is-beaten'),
      next: s.classList.contains('is-next'), gap: s.dataset.gap ?? null,
      gapPaint: getComputedStyle(s, '::after').content,
      name: text ? text.textContent : null, time: time ? time.textContent.trim() : null,
      lines: name ? Math.round(name.getBoundingClientRect().height / lineHeight) : 0,
      inside: rects.every((r) => r.left >= box.left - 0.5 && r.right <= box.right + 0.5
        && r.top >= clipBox.top - 0.5 && r.bottom <= clipBox.bottom + 0.5),
      nameOverflow: name ? name.scrollWidth - name.clientWidth : 0,
      bones: s.querySelectorAll('.lineup-bone').length, lock: Boolean(s.querySelector(':scope > .lineup-lock')),
      swap: Boolean(s.querySelector(':scope > .lineup-swap')), noGhost: s.classList.contains('no-ghost'),
      hue: s.style.getPropertyValue('--hue'),
      opacity: parseFloat(getComputedStyle(s).opacity), left: box.left, right: box.right,
    };
  });
  const notes = [...ol.querySelectorAll(':scope > .lineup-note')].map((n) => {
    const words = n.cloneNode(true);
    words.querySelectorAll('button, .lineup-choices').forEach((b) => b.remove());
    const box = n.getBoundingClientRect();
    return { text: words.textContent.trim(),
      choices: [...n.querySelectorAll('.lineup-choice')].map((b) => b.textContent.trim()),
      chips: [...n.querySelectorAll('.lineup-choice')].map((b) => { const r = b.getBoundingClientRect();
        return { left: r.left, right: r.right, top: Math.round(r.top), width: r.width,
                 clipped: b.scrollWidth - b.clientWidth }; }),
      action: (n.querySelector('.lineup-note-action') || {}).textContent || null,
      inside: box.left >= clipBox.left - 0.5 && box.right <= clipBox.right + 0.5 };
  });
  const toggle = card.querySelector('.log-card-actions > .ghost-toggle');
  const picker = [...card.querySelectorAll('.log-card-strat-picker select option')]
    .map((o) => o.value).filter((v) => v && v !== '__new');
  return {
    seats, notes, picker, off: lineup.classList.contains('is-off'), clipHeight: clipBox.height,
    rowOverflow: ol.scrollWidth - ol.clientWidth, clipOverflow: clip.scrollWidth - clip.clientWidth,
    ghostRowOverflow: row.scrollWidth - row.clientWidth, cardOverflow: card.scrollWidth - card.clientWidth,
    pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    toggle: toggle && { pressed: toggle.getAttribute('aria-pressed'), disabled: toggle.disabled },
  };
}"""


def centiseconds(text):
    """A displayed time (11"46, 1'02"33) as the centiseconds a player reads."""
    match = re.fullmatch(r"(?:(\d+)')?(\d+)\"(\d{2})", text)
    assert match, f"not a displayed time: {text!r}"
    minutes, seconds, hundredths = (int(part or 0) for part in match.groups())
    return (minutes * 60 + seconds) * 100 + hundredths


def shown_gap(you_text, next_text):
    return f"−{(centiseconds(you_text) - centiseconds(next_text)) / 100:.2f}"


@pytest.fixture(scope="module")
def served():
    with serve_ui_with_ghost(deepcopy(STATES["ready"])) as (base, state):
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
            try:
                yield base, state, browser
            finally:
                browser.close()


@contextlib.contextmanager
def opened(served, width, height=1100):
    _base, _state, browser = served
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    page.set_default_timeout(15000)
    seen = watch(page)
    # The offline fixture has no replay service; its real availability is empty.
    page.route("**/api/replay/available", lambda route: route.fulfill(json={"available": []}))
    try:
        yield page, seen
    finally:
        page._retire_observed_requests()
        context.close()


def settle(page):
    page.wait_for_function(SETTLED)
    page.wait_for_timeout(60)
    page.wait_for_function(SETTLED)


def show(page, served, name, session=None):
    base, state, _browser = served
    state["session"] = deepcopy(session or STATES[name])
    state["read_error"] = name == "unreachable"
    state["writes"].clear()
    page._retire_observed_requests()
    page.goto(base)
    page.locator(READY.get(name, f"{ROW} .lineup-note")).first.wait_for(
        state="attached" if name == "off" else "visible")
    settle(page)


def broadcast(page, served, session):
    base, state, _browser = served
    state["session"] = deepcopy(session)
    assert page.request.post(base + "/api/_fixture/ghost_broadcast").json() == {"published": True}


@pytest.mark.parametrize("endpoint,method,retired_allowed", [
    ("/api/uilog", "POST", True),
    ("/api/scorecard/entry_goals?scope=overall&keys=star%3A2%3A4", "GET", True),
    ("/ui/assets/star_icons/wf6.png", "GET", True),
    ("/api/scorecard/entry_goals", "POST", False),
])
def test_navigation_cancellation_is_distinguished_from_a_telemetry_failure(served, endpoint, method, retired_allowed):
    with opened(served, 1500) as (page, seen):
        show(page, served, "ready")
        held = []
        pattern = "**" + endpoint.split("?")[0] + "*"
        probe_endpoint = endpoint + ("&" if "?" in endpoint else "?") + "probe=lineup-cancel"
        options = {"method": method, **({"body": "{}"} if method == "POST" else {})}
        fetch = f"void fetch({json.dumps(probe_endpoint)}, {json.dumps(options)}).catch(() => {{}})"
        page.route(pattern, lambda route: held.append(route.request)
                   if route.request.url.endswith(probe_endpoint) else route.continue_())
        with page.expect_request(lambda request: request.url.endswith(probe_endpoint)):
            page.evaluate(fetch)
        show(page, served, "ready")
        assert held and held[0].failure == "net::ERR_ABORTED"
        if retired_allowed:
            no_faults(seen)
        else:
            assert seen["failed"] == [("failed", held[0].url, "net::ERR_ABORTED")]
        for code, failure in (("connectionrefused", "net::ERR_CONNECTION_REFUSED"),
                              ("aborted", "net::ERR_ABORTED")):
            page.unroute(pattern)
            page.route(pattern, lambda route, *, code=code: route.abort(error_code=code)
                       if route.request.url.endswith(probe_endpoint) else route.continue_())
            with page.expect_event("requestfailed", predicate=lambda request: request.url.endswith(probe_endpoint)) as refused:
                page.evaluate(fetch)
            assert refused.value.failure == failure
            assert ("failed", refused.value.url, failure) in seen["failed"]


# --------------------------------------------------------------------------
# Each state, painted
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rendered(served):
    out = {}
    for width in WIDTHS:
        with opened(served, width) as (page, seen):
            for name in STATES:
                show(page, served, name)
                out[(name, width)] = page.evaluate(MEASURE)
            out[("faults", width)] = seen
    served[1]["read_error"] = False
    return out


@pytest.mark.parametrize("name", sorted(STATES))
@pytest.mark.parametrize("width", WIDTHS)
def test_each_state_paints_its_people_and_words_inside_the_card(rendered, name, width):
    shown = rendered[(name, width)]
    seats, holes, words = EXPECT[name]
    assert len(shown["seats"]) == seats, shown["seats"]
    assert sum(seat["placeholder"] for seat in shown["seats"]) == holes
    if seats:
        # A seat you have beaten (a pin, your best recorded run) sits to your left.
        assert shown["seats"][0]["you"] or name in ("marks", "best_run"), "your seat is not first"
    assert [note["text"] for note in shown["notes"]] == ([WORDS[words]] if words else [])
    assert all(note["inside"] for note in shown["notes"]), shown["notes"]
    for key in ("rowOverflow", "clipOverflow", "ghostRowOverflow", "cardOverflow", "pageOverflow"):
        assert shown[key] <= 1, f"{key} {shown[key]}px at {width}px in {name}"
    # Folded away on purpose: the row's own collapse hides every name.
    for seat in [] if shown["off"] else shown["seats"]:
        if seat["placeholder"]:
            # Time, name and the strategy line each wait as a bone.
            assert seat["bones"] == 3 and seat["name"] is None, seat
            continue
        assert seat["inside"] and seat["nameOverflow"] <= 0, f"a name is cut off: {seat}"
        assert 1 <= seat["lines"] <= 2, f"{seat['name']!r} takes {seat['lines']} lines"
    assert shown["toggle"]["disabled"] is (name == "unreachable"), shown["toggle"]


@pytest.mark.parametrize("width", WIDTHS)
def test_ready_seats_sit_slowest_to_fastest_with_their_times(rendered, width):
    seats = rendered[("ready", width)]["seats"]
    pb = STATES["ready"]["rivals"]["pb"]["frames"]
    expected = [("You", format_igt_short(pb))] + [
        (name, format_igt_short(frames)) for name, *_rank, frames in sorted(LINEUP_PLAYERS, key=lambda p: -p[3])]
    assert [(seat["name"], seat["time"]) for seat in seats] == expected
    lefts = [seat["left"] for seat in seats]
    assert lefts == sorted(lefts), "seats are not drawn left to right in time order"


@pytest.mark.parametrize("width", WIDTHS)
def test_the_marks_say_pinned_downloading_and_beaten(rendered, width):
    by_name = {seat["name"]: seat for seat in rendered[("marks", width)]["seats"]}
    pinned, pending, beaten = by_name["Alexander_Speedruns"], by_name["Quinn"], by_name["Morgan"]
    assert pinned["pinned"] and pinned["lock"] and not pinned["beaten"] and pinned["opacity"] == 1
    assert pending["pending"] and not pending["lock"]
    assert beaten["beaten"] and beaten["opacity"] < 1


@pytest.mark.parametrize("width", WIDTHS)
def test_your_best_recorded_run_races_in_your_colour_beside_your_pb(rendered, width):
    """His decision, 2026-10-25: the card labels it "Best recorded run" and
    keeps the real PB as the rank you're chasing."""
    seats = rendered[("best_run", width)]["seats"]
    best, you = seats[0], seats[1]
    assert (you["you"], you["time"], you["noGhost"]) == (True, format_igt_short(343), True)
    assert (best["name"], best["time"]) == (WORDS["bestRun"], format_igt_short(BEST_RUN))
    assert best["hue"] == you["hue"] and not best["noGhost"]
    # Yours, not a rival: no swap, never dimmed as beaten by your PB.
    assert not best["swap"] and not best["beaten"] and best["opacity"] == 1
    assert all(seat["swap"] for seat in seats[2:])
    lit, mine = next_after_you(seats)
    assert lit == [mine[0] + 1] and seats[lit[0]]["name"] == "Alexander_Speedruns"


@pytest.mark.parametrize("state", ["no_strategy", "no_strategy_off"])
@pytest.mark.parametrize("width", WIDTHS)
def test_no_strategy_offers_every_strategy_in_balanced_rows(rendered, width, state):
    """Round 10: "all of the strategies here fit without it being difficult
    to understand / messy" and "for the buttons, we should also sort them in
    rank standard order". Every strategy the card's picker lists, in its
    order, on buttons of one width, in rows that differ by at most one
    button, the longer rows first, nothing clipped and nothing overflowing."""
    shown = rendered[(state, width)]
    # Standing open with ghosts on or off: the row is not folded away.
    assert not shown["off"] and shown["clipHeight"] > 40, (shown["off"], shown["clipHeight"])
    note, = shown["notes"]
    assert len(shown["picker"]) > 3 and note["choices"] == shown["picker"], (note["choices"], shown["picker"])
    chips = note["chips"]
    assert max(c["width"] for c in chips) - min(c["width"] for c in chips) < 0.5, chips
    assert all(c["clipped"] <= 0 for c in chips), chips
    rows = [sum(1 for c in chips if c["top"] == top) for top in sorted({c["top"] for c in chips})]
    assert max(rows) - min(rows) <= 1 and rows == sorted(rows, reverse=True), rows
    assert note["inside"], note


@pytest.mark.parametrize("width", WIDTHS)
def test_unreachable_offers_retry_and_off_folds_the_row(rendered, width):
    assert rendered[("unreachable", width)]["notes"][0]["action"] == "Retry"
    off = rendered[("off", width)]
    assert off["off"] and off["clipHeight"] < 1, off["clipHeight"]
    assert off["toggle"]["pressed"] == "false"
    assert rendered[("ready", width)]["clipHeight"] > 40


@pytest.mark.parametrize("width", WIDTHS)
def test_rendering_every_state_raised_no_page_errors(rendered, width):
    # The unreachable state is served behind a deliberate 503 on GET /api/ghost.
    seen = rendered[("faults", width)]
    assert [error for error in seen["errors"] if "503" not in error] == [], seen["errors"]
    assert [fault for fault in seen["failed"] if not (fault[0] == 503 and fault[1].endswith("/api/ghost"))] == []


# --------------------------------------------------------------------------
# The lit track's number
# --------------------------------------------------------------------------

def next_after_you(seats):
    lit = [index for index, seat in enumerate(seats) if seat["next"]]
    you = [index for index, seat in enumerate(seats) if seat["you"]]
    return lit, you


@pytest.mark.parametrize("name", ["ready", "marks"])
@pytest.mark.parametrize("width", WIDTHS)
def test_the_gap_is_the_difference_of_the_times_you_can_read(rendered, name, width):
    seats = rendered[(name, width)]["seats"]
    lit, you = next_after_you(seats)
    assert lit == [you[0] + 1], f"the lit seat is not the one right after yours: {seats}"
    mine, rival = seats[you[0]], seats[lit[0]]
    expected = shown_gap(mine["time"], rival["time"])
    assert rival["gap"] == expected
    assert rival["gapPaint"] == f'"{expected}"', "the number is in the DOM but not painted"


def test_the_gap_example_discriminates_displayed_from_frame_arithmetic():
    """Calibration for the guard above: on the "ready" pair, a gap computed
    from frames would read 0.17, so the rendered check could not pass by
    accident on an implementation that skipped the displayed times."""
    rival = max(frames for *_head, frames in LINEUP_PLAYERS)
    displayed = shown_gap(format_igt_short(GAP_PB), format_igt_short(rival))
    assert displayed == "−0.16"
    assert f"−{(GAP_PB - rival) / 30:.2f}" != displayed


@pytest.mark.parametrize("width", WIDTHS)
def test_nothing_is_lit_while_rivals_load(rendered, width):
    assert not any(seat["next"] for seat in rendered[("loading", width)]["seats"])


# --------------------------------------------------------------------------
# Your seat is the anchor
# --------------------------------------------------------------------------

def other_strategy():
    rows = [lineup_row(slot, name, "Gold", "II", frames)
            for slot, (name, frames) in enumerate((("Rowan", 341), ("Sasha", 338), ("Ines", 336)))]
    return lineup_session(rows=rows, target={**FIXTURE_TARGET, "strat": "Sideflip"})


SAME_YOU = """() => document.querySelector('.log-card.log-card-active .lineup-seat.is-you') === window.__uitYou"""
YOU_LEFT = """() => document.querySelector('.log-card.log-card-active .lineup-seat.is-you').getBoundingClientRect().left"""
TAG_YOU = """() => { window.__uitYou = document.querySelector('.log-card.log-card-active .lineup-seat.is-you');
  return Boolean(window.__uitYou); }"""


def seat_watch(count):
    return {f"seat{index}": f"{SEAT}:nth-child({index})" for index in range(1, count + 1)} | {
        "you": f"{SEAT}.is-you"}


@pytest.mark.parametrize("width", WIDTHS)
def test_switching_lineups_keeps_the_same_seat_of_yours(served, width):
    """Another strategy, then a note: your seat is the same element across
    both exchanges, and it is on screen at full strength on every frame."""
    with opened(served, width) as (page, seen):
        show(page, served, "ready")
        assert page.evaluate(TAG_YOU)
        swapped = trace.record(page, watch={"you": f"{SEAT}.is-you"}, ms=900,
                               trigger=lambda _page: broadcast(page, served, other_strategy()))
        page.locator(f"{SEAT}", has_text="Rowan").wait_for()
        settle(page)
        assert page.evaluate(SAME_YOU), "the other strategy rebuilt your seat"
        names = [seat["name"] for seat in page.evaluate(MEASURE)["seats"]]
        assert names == ["You", "Rowan", "Sasha", "Ines"], names
        frames = swapped.of("you").frames
        assert frames and all(frame.get("effectiveOpacity", 0) > 0.99 for frame in frames), (
            "your seat left the screen during the exchange")
        broadcast(page, served, {**other_strategy(), "rivals": lineup_rivals(rows=[], reason="nobody")})
        page.locator(f"{ROW} .lineup-note").wait_for()
        settle(page)
        assert page.evaluate(SAME_YOU), "the note rebuilt your seat"
        no_faults(seen)


@pytest.mark.parametrize("width", WIDTHS)
def test_loading_resolving_never_paints_a_row_without_seats(served, width):
    with opened(served, width) as (page, seen):
        show(page, served, "loading")
        assert page.evaluate(TAG_YOU)
        left_before = page.evaluate(YOU_LEFT)
        recorded = trace.record(page, watch=seat_watch(5), ms=1400,
                                trigger=lambda _page: broadcast(page, served, STATES["ready"]))
        settle(page)
        left_after = page.evaluate(YOU_LEFT)
        shown = page.evaluate(MEASURE)["seats"]
        assert [seat["placeholder"] for seat in shown] == [False] * 5
        assert page.evaluate(SAME_YOU)
        tracks = [recorded.of(f"seat{index}").frames for index in range(1, 6)]
        assert len(tracks[0]) > 10, "the trace recorded too few frames to mean anything"
        empty = [frame["t"] for frame in tracks[0] if "x" not in frame]
        assert not empty, f"frames with no seat at all: {empty}"
        short = [tracks[0][i]["t"] for i in range(len(tracks[0])) if any("x" not in track[i] for track in tracks)]
        assert not short, f"frames with fewer than five seats: {short}"
        faded = [frame["t"] for frame in recorded.of("you").frames if frame.get("effectiveOpacity", 0) < 0.99]
        assert not faded, f"your seat dimmed while rivals arrived: {faded}"
        # As many places as rivals arrived, so nothing re-centres: your seat
        # holds still. Two halves: where it sits before and after (a jump),
        # and its slide on every frame between (a trace's `x` is the
        # transform's translation, not the box: uilab's sample lets the
        # transform's x replace the box's).
        assert abs(left_after - left_before) <= 1, (
            f"your seat moved from x={left_before:.0f} to x={left_after:.0f} as the rivals arrived")
        slide = recorded.of("you").values("x")
        assert max(slide) - min(slide) <= 1, f"your seat slid {max(slide) - min(slide):.0f}px as the rivals arrived"
        no_faults(seen)


# --------------------------------------------------------------------------
# The gestures
# --------------------------------------------------------------------------

def test_a_strategy_button_writes_this_cards_strategy(served):
    posted = []

    def strat(route):
        posted.append(route.request.post_data_json)
        route.fulfill(json={})

    with opened(served, 850) as (page, seen):
        page.route("**/api/strat", strat)
        show(page, served, "no_strategy")
        choices = page.locator(f"{ROW} .lineup-choice")
        first = choices.first.text_content().strip()
        choices.first.click()
        for _ in range(100):
            if posted:
                break
            page.wait_for_timeout(50)
        assert len(posted) == 1, posted
        body = posted[0]
        assert {key: body[key] for key in ("course_id", "star_id", "strat_tag")} == {
            "course_id": 2, "star_id": 4, "strat_tag": first}
        assert body["timer_mode"] in ("igt", "rta")
        assert served[1]["writes"] == [], "a strategy pick wrote the ghost session"
        no_faults(seen)


@pytest.mark.parametrize("width", WIDTHS)
def test_the_head_toggle_writes_enabled_and_folds_the_row(served, width):
    _base, state, _browser = served
    with opened(served, width) as (page, seen):
        show(page, served, "ready")
        assert page.evaluate(TAG_YOU)
        page.click(TOGGLE)
        page.locator(f"{ROW} .lineup.is-off").wait_for(state="attached")
        settle(page)
        shown = page.evaluate(MEASURE)
        assert state["writes"] == [{"enabled": False}], state["writes"]
        assert shown["toggle"]["pressed"] == "false" and shown["clipHeight"] < 1, shown
        page.click(TOGGLE)
        page.locator(f"{ROW} .lineup:not(.is-off)").wait_for()
        settle(page)
        shown = page.evaluate(MEASURE)
        assert state["writes"] == [{"enabled": False}, {"enabled": True}], state["writes"]
        assert shown["toggle"]["pressed"] == "true" and shown["clipHeight"] > 40, shown
        assert len(shown["seats"]) == 5 and page.evaluate(SAME_YOU), "unfolding rebuilt the row"
        no_faults(seen)


# --------------------------------------------------------------------------
# Endless loops freeze out of sight
# --------------------------------------------------------------------------

LOOPING = """async () => {
  const { LOOPS } = await import('/ui/offscreenloops.js');
  const host = document.querySelector('.log-card.log-card-active .ghost-row');
  const targets = new Set();
  for (const a of document.getAnimations()) {
    const t = a.effect && a.effect.target;
    if (t && host.contains(t) && a.effect.getComputedTiming().iterations === Infinity) targets.add(t);
  }
  return [...targets].map((t) => ({ cls: t.className, covered: t.matches(LOOPS),
    frozen: t.hasAttribute('data-offscreen') }));
}"""


def looping_out_of_sight(page):
    page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
    page.wait_for_function("""() => {
      const row = document.querySelector('.log-card.log-card-active .ghost-row');
      return row.getBoundingClientRect().bottom < -200;
    }""")
    page.wait_for_timeout(150)
    return page.evaluate(LOOPING)


@pytest.mark.parametrize("arrival", ["page_load", "same_lineup"])
def test_every_endless_shimmer_is_one_the_offscreen_watcher_freezes(served, arrival):
    """The bones shimmer and the empty rings glow forever while rivals load.
    Each is in ui/offscreenloops.js LOOPS and wears `data-offscreen` once the
    row scrolls away -- whether the places arrived with the page or a ready
    row turned back into loading in place (same lineup, same seat elements)."""
    with opened(served, 850, height=500) as (page, seen):
        if arrival == "page_load":
            show(page, served, "loading")
        else:
            show(page, served, "ready")
            broadcast(page, served, STATES["loading"])
            page.locator(f"{SEAT}.is-placeholder").first.wait_for()
            settle(page)
        visible = page.evaluate(LOOPING)
        assert len(visible) == 16, f"four rings and twelve bones should loop: {visible}"
        assert all(loop["covered"] for loop in visible), [loop for loop in visible if not loop["covered"]]
        hidden = looping_out_of_sight(page)
        assert len(hidden) == 16
        assert all(loop["frozen"] for loop in hidden), [loop for loop in hidden if not loop["frozen"]]
        no_faults(seen)


# --------------------------------------------------------------------------
# Every runner name fits a seat
# --------------------------------------------------------------------------

FIT = """async ({ names, keys, nowrap }) => {
  const { breakName } = await import('/ui/lineup.js');
  const row = document.querySelector('.log-card.log-card-active .lineup-row');
  const clip = row.closest('.lineup-clip');
  const style = document.createElement('style');
  if (nowrap) {
    style.textContent = '.lineup-name { white-space: nowrap !important; overflow-wrap: normal !important; }';
    document.body.append(style);
  }
  const misfits = [];
  let measured = 0;
  for (const key of keys) {
    const seat = row.querySelector(`:scope > .lineup-seat[data-key="${key}"]`);
    const name = seat.querySelector('.lineup-name');
    const text = name.querySelector('.lineup-name-text');
    const original = [...text.childNodes];
    const lineHeight = parseFloat(getComputedStyle(name).lineHeight);
    for (const candidate of names) {
      text.replaceChildren(breakName(candidate));
      const box = seat.getBoundingClientRect();
      const clipBox = clip.getBoundingClientRect();
      const range = document.createRange();
      range.selectNodeContents(name);
      const rects = [...range.getClientRects()].filter((r) => r.width > 0);
      const lines = Math.round(name.getBoundingClientRect().height / lineHeight);
      const inside = rects.every((r) => r.left >= box.left - 0.5 && r.right <= box.right + 0.5
        && r.bottom <= clipBox.bottom + 0.5);
      measured += 1;
      if (lines > 2 || !inside || name.scrollWidth > name.clientWidth) {
        misfits.push({ name: candidate, seat: key, lines, inside, width: box.width });
      }
    }
    text.replaceChildren(...original);
  }
  style.remove();
  return { measured, misfits, seatWidth: row.querySelector(':scope > .lineup-seat').getBoundingClientRect().width };
}"""


@pytest.fixture(scope="module")
def runner_names(served):
    """Every runner the bundled library lists, through the route the app serves them on."""
    with urlopen(served[0] + "/api/library/runners", timeout=30) as answer:
        names = json.load(answer)["runners"]
    assert len(names) > 400, f"the bundled library lists only {len(names)} runners"
    return names


@pytest.fixture(scope="module")
def fitted(served, runner_names):
    """One page at the 850px floor, a full row of five, and every name measured
    in the narrowest seats: a plain one and the verified one, which also
    carries the tick after the name."""
    with opened(served, 850) as (page, seen):
        show(page, served, "marks")
        keys = ["s1", "s2"]   # Quinn (plain) and Kai (verified)
        assert page.evaluate("() => !!document.querySelector('.log-card.log-card-active "
                             ".lineup-seat[data-key=\"s2\"] .lineup-tick')"), "the verified seat has no tick"
        result = page.evaluate(FIT, {"names": runner_names, "keys": keys, "nowrap": False})
        # Calibration inputs: a name no seat can hold in two lines, and a long
        # name forced onto one line so it spills past the seat. The second has
        # no place breakName would split it, because Chromium still breaks at
        # a <wbr> under `white-space: nowrap`.
        broken = {"lines": page.evaluate(FIT, {"names": ["W" * 60], "keys": keys, "nowrap": False}),
                  "spill": page.evaluate(FIT, {"names": ["M" * 30], "keys": keys, "nowrap": True})}
        no_faults(seen)
    return result, broken


def test_every_bundled_runner_name_fits_a_seat_in_two_lines(fitted, runner_names):
    result, _broken = fitted
    assert result["measured"] == 2 * len(runner_names)
    assert result["misfits"] == [], (
        f"{len(result['misfits'])} names do not fit a {result['seatWidth']:.0f}px seat: {result['misfits'][:10]}")


def test_the_name_fit_measure_can_fail(fitted):
    """Calibration: the same measure reports a name that wraps past two lines
    and one that spills out of its seat."""
    _result, broken = fitted
    assert [misfit["lines"] > 2 for misfit in broken["lines"]["misfits"]] == [True, True], broken["lines"]
    assert [misfit["inside"] for misfit in broken["spill"]["misfits"]] == [False, False], broken["spill"]
