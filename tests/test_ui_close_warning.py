"""The close warning and the recording panel's list of replays being made smaller.

The real app runs offline; only `/api/replay/compression` is scripted, in the
browser, so a test can hold a job mid-way, advance it and finish it. The
shutdown POST is answered in the browser too: the fixture server's own route
would end the fixture.
"""
import sys
import time
from contextlib import contextmanager
from itertools import pairwise
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab_project import PROJECT

LONG = "Tick Tock Clock: The Pit and the Pendulums"
NAMES = [LONG, "Whomp's Fortress: Shoot into the Wild Blue", "Bowser in the Dark World: No Reds",
         "Lethal Lava Land: Boil the Big Bully", None, "Rainbow Ride: Cruiser Crossing the Rainbow",
         "Wet-Dry World: Express Elevator--Hurry Up!", "Tiny-Huge Island: Pluck the Piranha Flower"]
STAGES = [("compressing", 0.41), ("waiting", None), ("waiting", None), ("checking", 0.93),
          ("in_use", 1.0), ("done", 1.0), ("kept", 1.0), ("done", 1.0)]
WARNING = ".close-warning"
REQUEST_CLOSE = "window.dispatchEvent(new CustomEvent('sm64-close-requested'))"


def job(attempt_id, stage, fraction=None, label=LONG, to_bytes=None):
    return {"attempt_id": attempt_id, "label": label, "time_text": "1'02\"36" if attempt_id % 2 else "0'11\"16",
            "stage": stage, "fraction": fraction, "from_bytes": 38102345,
            "to_bytes": 11639193 if stage in ("done", "in_use") and to_bytes is None else to_bytes}


def jobs(count):
    return [job(200 + index, *STAGES[index], label=NAMES[index]) for index in range(count)]


def busy(rows):
    return {"active": any(row["stage"] in ("waiting", "compressing", "checking") for row in rows),
            "jobs": rows}


class Scripted:
    """What the page is told about compression, and what it asked the app to do."""

    def __init__(self, page, body):
        self.body, self.requests, self.shutdowns = body, 0, []
        page.route("**/api/replay/compression", self._compression)
        page.route("**/api/admin/shutdown", self._shutdown)

    def _compression(self, route):
        self.requests += 1
        if self.body is None:    # replay is off: its routes are not mounted
            route.fulfill(status=404, json={"detail": "Not Found"})
        else:
            route.fulfill(json=self.body)

    def _shutdown(self, route):
        self.shutdowns.append((route.request.method, time.monotonic()))
        route.fulfill(json={"shutting_down": True})


@contextmanager
def app(body, width=1500, height=1100):
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            scripted = Scripted(page, body)
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            yield page, scripted
            assert errors == []
        finally:
            browser.close()


def button(page, name):
    return page.locator(".modal-actions").get_by_role("button", name=name, exact=True)


def test_an_idle_app_never_asks_and_the_shell_can_tell_the_warning_is_listening():
    with app(busy(jobs(3))) as (page, scripted):
        # Control: the page reacts to input at all, so a silent modal below
        # would be the feature's fault and not a frozen app's.
        page.locator('button.nav-item[title="Run"]').first.click()
        page.wait_for_function(
            "document.querySelector('button.nav-item[title=\"Run\"]').getAttribute('aria-current') === 'page'")
        page.wait_for_timeout(1200)
        assert scripted.requests == 0, "compression was polled with no surface open"
        assert page.evaluate("window.__sm64CloseWarning") is True
        assert page.locator(WARNING).count() == 0


def test_the_warning_shows_each_replay_moving_and_exit_anyway_closes_the_app():
    with app(busy(jobs(3))) as (page, scripted):
        # The first growth of a bar is sampled from the frame its row mounts.
        arrival = page.evaluate("""(async () => {
          %s;
          const widths = [];
          const start = performance.now();
          while (performance.now() - start < 1600) {
            const bar = document.querySelector('%s .compression-job.is-compressing .compression-meter > div');
            if (bar) widths.push(bar.getBoundingClientRect().width
              / bar.parentElement.getBoundingClientRect().width);
            await new Promise(requestAnimationFrame);
          }
          return widths;
        })()""" % (REQUEST_CLOSE, WARNING))
        assert arrival and arrival[0] < 0.15, f"the bar appeared part-full: {arrival[:5]}"
        assert abs(arrival[-1] - 0.41) < 0.01, arrival[-5:]

        rows = page.locator(f"{WARNING} .compression-job")
        assert rows.count() == 3
        first = rows.nth(0).inner_text()
        assert LONG in first and "0'11\"16" in first and "Compressing 41%" in first
        second = rows.nth(1).inner_text()
        assert "Waiting its turn" in second and "1'02\"36" in second
        text = page.locator(".modal").inner_text()
        assert "already saved" in text and "those 3 replays stay full size" in text
        assert "Nothing is lost" in text
        assert page.evaluate("document.activeElement.textContent.trim()") == "Wait"

        # Exit loses nothing, so it must not wear the destructive treatment.
        exit_anyway = button(page, "Exit anyway")
        assert "danger" not in (exit_anyway.get_attribute("class") or "")
        colours = page.evaluate("""(() => {
          const probe = document.createElement('button'); probe.className = 'danger-button';
          document.querySelector('.modal-actions').append(probe);
          const danger = getComputedStyle(probe).color; probe.remove();
          const exit = [...document.querySelectorAll('.modal-actions button')]
            .find((b) => b.textContent.trim() === 'Exit anyway');
          return [danger, getComputedStyle(exit).color];
        })()""")
        assert colours[0] != colours[1], colours

        # One poll later the job is further along; the bar travels there.
        scripted.body = busy([job(200, "compressing", 0.63, label=NAMES[0]), *jobs(3)[1:]])
        travel = page.evaluate("""(async () => {
          const bar = document.querySelector('%s .compression-job.is-compressing .compression-meter > div');
          const widths = [];
          const start = performance.now();
          while (performance.now() - start < 1500) {
            widths.push(bar.getBoundingClientRect().width / bar.parentElement.getBoundingClientRect().width);
            await new Promise(requestAnimationFrame);
          }
          return widths;
        })()""" % WARNING)
        assert "Compressing 63%" in rows.nth(0).inner_text()
        assert abs(travel[0] - 0.41) < 0.01 and abs(travel[-1] - 0.63) < 0.01, (travel[0], travel[-1])
        assert all(later >= earlier - 1e-6 for earlier, later in pairwise(travel)), "the bar moved backwards"
        # A step shows NO width in between; a transition shows several, as
        # many as the machine paints frames. A loaded GitHub runner painted 7
        # over this travel where the desktop paints dozens (2026-09-21), so
        # the bound asks for motion, not for this desktop's frame rate.
        between = {round(width, 4) for width in travel if 0.42 < width < 0.62}
        assert len(between) >= 4, f"the bar stepped instead of moving: {sorted(between)}"

        exit_anyway.click()
        page.wait_for_function("document.querySelector('.modal-actions').textContent.includes('Closing')")
        assert [method for method, _ in scripted.shutdowns] == ["POST"]


def test_wait_closes_the_app_by_itself_after_the_last_replay_is_seen_finishing():
    with app(busy([job(200, "checking", 0.93)])) as (page, scripted):
        page.evaluate(REQUEST_CLOSE)
        page.wait_for_selector(f"{WARNING} .compression-job")
        list_top = f"document.querySelector('{WARNING} .compression-jobs').getBoundingClientRect().top"
        page.wait_for_timeout(500)   # past the arrival, which moves the whole panel
        tops = [page.evaluate(list_top)]
        button(page, "Wait").click()
        page.wait_for_function(
            f"document.querySelector('{WARNING}').textContent.includes('SM64 Trainer will close when these finish.')")
        assert button(page, "Wait").count() == 0 and button(page, "Exit now").count() == 1
        tops.append(page.evaluate(list_top))
        page.wait_for_timeout(1500)
        assert scripted.shutdowns == [], "it exited while a replay was still being worked on"

        scripted.body = busy([job(200, "done", 1.0)])
        page.wait_for_function(
            f"document.querySelector('{WARNING}').textContent.includes('Done — 36.3 MB to 11.1 MB')")
        seen_done = time.monotonic()
        tops.append(page.evaluate(list_top))
        # The list he is watching holds still through the press and the finish.
        assert max(tops) - min(tops) < 0.5, f"the list moved under him: {tops}"
        deadline = seen_done + 6
        while not scripted.shutdowns and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert [method for method, _ in scripted.shutdowns] == ["POST"]
        held = scripted.shutdowns[0][1] - seen_done
        assert 0.8 <= held <= 3, f"the finished row was on screen for {held:.2f}s before the app closed"
        page.wait_for_timeout(1500)
        assert len(scripted.shutdowns) == 1


def test_keeping_the_app_cancels_the_pending_exit_and_stops_asking():
    with app(busy([job(200, "compressing", 0.5)])) as (page, scripted):
        for leave in (lambda: button(page, "Keep using the app").click(),
                      lambda: page.keyboard.press("Escape")):
            scripted.body = busy([job(200, "compressing", 0.5)])
            page.evaluate(REQUEST_CLOSE)
            # Every arrival starts by asking: a wait is never carried over.
            button(page, "Wait").click()
            page.wait_for_function(
                f"document.querySelector('{WARNING}').textContent.includes('will close when these finish')")
            leave()
            page.wait_for_selector(WARNING, state="detached")
            scripted.body = busy([job(200, "done", 1.0)])
            page.wait_for_timeout(400)
            asked = scripted.requests
            page.wait_for_timeout(3600)
            assert scripted.shutdowns == [], "the app closed after he chose to keep using it"
            assert scripted.requests == asked, "it kept polling with the warning dismissed"

        # The narrowest window: the work is over and the exit is already
        # counting down when he changes his mind.
        scripted.body = busy([job(200, "compressing", 0.5)])
        page.evaluate(REQUEST_CLOSE)
        button(page, "Wait").click()
        scripted.body = busy([job(200, "done", 1.0)])
        page.wait_for_function(
            f"document.querySelector('{WARNING}').textContent.includes('All finished. Closing SM64 Trainer')")
        page.keyboard.press("Escape")
        page.wait_for_selector(WARNING, state="detached")
        page.wait_for_timeout(2500)
        assert scripted.shutdowns == [], "an exit already counting down survived Keep using the app"


SETTINGS = {"retention_attempts": 10, "retention_s": None, "max_buffer_bytes": 2 * 1024**3,
            "pre_pad_s": 3, "post_pad_s": 2, "saved_bytes": 10000000, "save_root": "fixture"}


def show_the_recording_dot(page):
    """The offline fixture has no replay service, so the dot is hidden until told otherwise."""
    page.route("**/api/replay/status", lambda route: route.fulfill(json={
        **SETTINGS, "recording": True, "enabled": True, "disk_bytes": 1000000,
        "encoder": "fixture", "audio_mode": "process"}))
    page.route("**/api/replay/settings", lambda route: route.fulfill(json=SETTINGS))
    page.reload()
    page.wait_for_selector(PROJECT.ready_selector)


def test_a_route_that_is_not_there_reads_as_nothing_waiting_and_says_nothing():
    with app(None) as (page, scripted):
        show_the_recording_dot(page)
        page.locator(".recording-button").first.click()
        panel = page.locator(".replay-settings-popover")
        panel.locator(".compression-section-empty").wait_for()
        words = panel.inner_text()
        assert "Nothing is waiting right now." in words
        assert not any(alarm in words for alarm in ("404", "Not Found", "rror", "Could not")), words
        panel.get_by_role("button", name="Close", exact=True).click()

        # A server that goes away mid-Wait: one failed look must not close the
        # app under a job, and looks that keep failing must not strand him.
        scripted.body = busy([job(200, "compressing", 0.5)])
        page.evaluate(REQUEST_CLOSE)
        button(page, "Wait").click()
        page.wait_for_function(
            f"document.querySelector('{WARNING}').textContent.includes('will close when these finish')")
        scripted.body = None
        went_away = time.monotonic()
        deadline = went_away + 8
        while not scripted.shutdowns and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert [method for method, _ in scripted.shutdowns] == ["POST"], "he was left waiting on a dead route"
        released = scripted.shutdowns[0][1] - went_away
        # One failed look would release it within 0.5 s + the 1.2 s hold; three
        # cannot before 1.0 s + the hold.
        assert released >= 1.95, f"one or two failed looks closed the app after {released:.2f}s"
        assert "rror" not in page.locator(".modal").inner_text()


def test_the_recording_panel_lists_the_same_jobs_and_asks_only_while_open(tmp_path):
    with app({"active": False, "jobs": []}) as (page, scripted):
        show_the_recording_dot(page)
        page.wait_for_timeout(800)
        assert scripted.requests == 0
        page.locator(".recording-button").first.click()
        panel = page.locator(".replay-settings-popover")
        panel.locator(".compression-section-empty").wait_for()
        assert "Nothing is waiting right now." in panel.inner_text()

        # A save made while the panel sits open on an idle list still turns up.
        scripted.body = busy(jobs(8))
        page.wait_for_function(
            "document.querySelectorAll('.replay-settings-popover .compression-job').length === 8", timeout=6000)
        assert "Compressing 41%" in panel.inner_text() and "Kept at full size" in panel.inner_text()
        assert "Saved replay" in panel.inner_text(), "a job with no label lost its name"
        for width in (1500, 850):
            page.set_viewport_size({"width": width, "height": 1100})
            page.wait_for_timeout(300)
            panel.screenshot(path=str(tmp_path / f"panel-{width}.png"))
            box = panel.bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= width
            overflow = page.evaluate("""[...document.querySelectorAll('.replay-settings-popover .compression-job')]
              .filter((row) => row.scrollWidth > row.clientWidth + 1
                || row.getBoundingClientRect().right
                   > row.closest('.replay-settings-popover').getBoundingClientRect().right).length""")
            assert overflow == 0, f"{overflow} rows spill out of the panel at {width}px"

        panel.get_by_role("button", name="Close", exact=True).click()
        page.wait_for_selector(".replay-settings-popover", state="detached")
        page.wait_for_timeout(400)
        asked = scripted.requests
        page.wait_for_timeout(3600)
        assert scripted.requests == asked, "it kept polling with the panel shut"


@pytest.mark.parametrize("count", [1, 3, 8])
def test_the_warning_fits_at_the_supported_widths(count, tmp_path):
    with app(busy(jobs(count))) as (page, _scripted):
        page.evaluate(REQUEST_CLOSE)
        page.wait_for_function(
            f"document.querySelectorAll('{WARNING} .compression-job').length === {count}")
        for width, height in ((1500, 1100), (850, 1100), (850, 560)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(700)
            page.locator(".modal").screenshot(path=str(tmp_path / f"warning-{count}-{width}x{height}.png"))
            facts = page.evaluate("""(() => {
              const modal = document.querySelector('.modal').getBoundingClientRect();
              const body = document.querySelector('.modal-body');
              const buttons = [...document.querySelectorAll('.modal-actions button')]
                .map((b) => b.getBoundingClientRect());
              const names = [...document.querySelectorAll('.close-warning .compression-job-name')];
              return {
                inside: modal.left >= 0 && modal.right <= innerWidth && modal.top >= 0 && modal.bottom <= innerHeight,
                sideways: body.scrollWidth > body.clientWidth + 1,
                tops: [...new Set(buttons.map((b) => Math.round(b.top)))],
                buttonsInside: buttons.every((b) => b.left >= modal.left && b.right <= modal.right
                  && b.bottom <= modal.bottom),
                clipped: names.filter((n) => n.scrollWidth > n.clientWidth + 1).length,
              };
            })()""")
            where = f"{count} rows at {width}x{height}"
            assert facts["inside"], f"the warning leaves the window: {where}"
            assert not facts["sideways"], f"the list scrolls sideways: {where}"
            assert len(facts["tops"]) == 1 and facts["buttonsInside"], f"the actions wrapped or escaped: {where}"
            assert facts["clipped"] == 0, f"a replay's name is cut off: {where}"
