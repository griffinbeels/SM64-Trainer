"""Drive the real Practice Log over a scratch service; never attach the recorder.

Only attempt metadata in the session response is decorated. The server, store,
Practice component, star/segment/unassigned cards, stylesheet and settings are
the shipping implementation. A clean entry is the control for each list.
"""
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from tools.ui_fixture import (FIXTURE_COURSE, FIXTURE_SEGMENT, FIXTURE_STAR,
                              serve_ui)

REPO = Path(__file__).resolve().parents[1]
SHOTS = REPO / ".planning/practice-log-settings/shots"


def _rows(sample, start):
    variants = [{"outcome": "success"}, {"outcome": "reset"},
                {"outcome": "hard_reset"}, {"outcome": "death"},
                {"outcome": "success", "state_loads": 1},
                {"outcome": "success", "anchor_type": "state_loaded"},
                {"outcome": "success", "invalid_reason": "save_state"},
                {"outcome": "loaded"}]
    rows = []
    for i, variant in enumerate(variants):
        row = {**sample, "id": start + i, "journal_id": start + i,
               "state_loads": 0, "anchor_type": "practice_reset",
               "invalid_reason": None, "cleared": False, "pb_action": None,
               "pb_blocked": None, "pb_delta_frames": None,
               "ended_utc": f"2026-10-05T12:00:{i:02d}+00:00", **variant}
        rows.append(row)
    return rows


def _watch_failures(page):
    errors, failed_api = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text)
            if message.type == "error" else None)
    page.on("response", lambda response: failed_api.append(
        [response.url, response.status]) if "/api/" in response.url
        and response.status >= 400 else None)
    return errors, failed_api


def _decorate_session(route):
    """Give the star, segment and unassigned lists one row per log bucket."""
    if urlsplit(route.request.url).path != "/api/session":
        route.continue_()
        return
    response = route.fetch()
    value = response.json()
    star = next(sec for sec in value["stars"]
                if sec["course_id"] == FIXTURE_COURSE
                and sec["star_id"] == FIXTURE_STAR)
    segment = next(sec for sec in value["segments"]
                   if sec["segment_id"] == FIXTURE_SEGMENT)
    sample = copy.deepcopy(star["attempts"][0])
    star["attempts"] = _rows(sample, 100000)
    segment["attempts"] = _rows(sample, 200000)
    value["stars"] = [star]
    value["segments"] = [segment]
    value["unassigned"] = _rows(sample, 300000)
    route.fulfill(response=response, body=json.dumps(value),
                  content_type="application/json")


def _expect_rows(cards, n):
    for card in cards:
        expect(card.locator(".attempt-table > tr")).to_have_count(n)


def _open_every_bucket(cards):
    # Existing fold control proves the page can process input before
    # driving the new controls. Open every bucket deliberately.
    for card in cards:
        fold = card.locator(":scope > .log-card-head .log-card-fold")
        expect(fold).to_be_visible()
        if fold.get_attribute("aria-expanded") == "false":
            fold.click()
        expect(card.locator(".attempt-table > tr")).to_have_count(4)


def _choose_every_preference(page, dialog, resets, saved, cards):
    expect(resets).not_to_be_checked()
    expect(saved).to_be_checked()
    expect(resets).to_be_focused()
    # Keyboard uses the native switch input, not a click-only handler.
    resets.press("Space")
    _expect_rows(cards, 1)
    saved.click()
    _expect_rows(cards, 5)
    resets.click()
    _expect_rows(cards, 8)
    saved.click()
    _expect_rows(cards, 4)
    dialog.get_by_role("combobox").select_option("oldest")
    expect(dialog.get_by_role("combobox")).to_have_value("oldest")
    page.screenshot(path=str(SHOTS / "desktop.png"), animations="disabled")
    dialog.screenshot(path=str(SHOTS / "settings.png"), animations="disabled")


def _open_stats_from_settings(page, dialog, gear):
    # Stats is still reachable and its fetched checklist fits on screen.
    dialog.get_by_role("button", name="Stats", exact=True).click()
    stats = page.locator(".stats-popover .popover")
    expect(stats).to_be_visible()
    expect(stats.get_by_role("button", name="Apply", exact=True)).to_be_visible()
    expect(stats.locator('input[type="checkbox"]').first).to_be_visible()
    page.wait_for_function("""() => {
        const box = document.querySelector('.stats-popover').getBoundingClientRect();
        return box.top >= 0 && box.bottom <= innerHeight;
    }""")
    box = stats.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= 1000
    page.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(gear).to_be_focused()


def _narrow_choices_survive_reload(page, gear, dialog, resets, saved, cards):
    page.set_viewport_size({"width": 850, "height": 900})
    gear.click()
    saved.click()
    _expect_rows(cards, 8)
    page.screenshot(path=str(SHOTS / "850px.png"), animations="disabled")
    box = dialog.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 850
    page.get_by_role("heading", name="Recent activity", exact=True).click()
    expect(dialog).not_to_be_visible()
    page.reload()
    gear.click()
    expect(saved).not_to_be_checked()
    expect(resets).not_to_be_checked()
    expect(dialog.get_by_role("combobox")).to_have_value("oldest")


def _reduced_motion_panel_opens_on_screen(page, gear, dialog):
    page.emulate_media(reduced_motion="reduce")
    # The app-wide reduced-motion rule uses 0.01ms with !important.
    assert page.locator(".log-preference-track").first.evaluate(
        "el => Math.max(...getComputedStyle(el).transitionDuration.split(',').map(parseFloat))") <= 0.00001
    page.set_viewport_size({"width": 850, "height": 450})
    expect(dialog).not_to_be_visible()
    page.evaluate("""() => {
        window.__placementFrames = new Promise(resolve => {
            const frames = []; let ticks = 0;
            const sample = () => {
                const el = document.querySelector('.log-settings-panel');
                if (el && getComputedStyle(el).visibility === 'visible') {
                    const box = el.getBoundingClientRect();
                    if (box.height > 0) frames.push({top: box.top, bottom: box.bottom});
                }
                if (frames.length >= 5 || ++ticks >= 60) resolve(frames);
                else requestAnimationFrame(sample);
            };
            requestAnimationFrame(sample);
        });
    }""")
    gear.click()
    page.wait_for_function("""() => {
        const box = document.querySelector('.log-settings-panel').getBoundingClientRect();
        return box.top >= 0 && box.bottom <= innerHeight;
    }""", timeout=5000)
    box = dialog.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= 450
    frames = page.evaluate("window.__placementFrames")
    assert len(frames) == 5, frames
    assert all(frame["top"] >= 0 and frame["bottom"] <= 450 for frame in frames), frames


def test_preferences_drive_every_log_bucket_and_remember_choices():
    SHOTS.mkdir(parents=True, exist_ok=True)
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as base:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1500, "height": 1000})
                errors, failed_api = _watch_failures(page)
                page.route("**/api/session?*", _decorate_session)
                page.goto(base)
                star_card = page.locator(f'.log-card[data-feed-key="star:{FIXTURE_COURSE}:{FIXTURE_STAR}"]')
                segment_card = page.locator(f'.log-card[data-feed-key="segment:{FIXTURE_SEGMENT}"]')
                unassigned = page.locator('.log-card[data-feed-key="unassigned"]')
                cards = (star_card, segment_card, unassigned)
                expect(star_card).to_be_visible()
                _open_every_bucket(cards)
                gear = page.get_by_role("button", name="Practice log settings", exact=True)
                gear.click()
                dialog = page.get_by_role("dialog", name="Practice log settings", exact=True)
                resets = dialog.get_by_role("switch", name="Hide resets and deaths")
                saved = dialog.get_by_role("switch", name="Hide savestated entries")
                _choose_every_preference(page, dialog, resets, saved, cards)
                _open_stats_from_settings(page, dialog, gear)
                _narrow_choices_survive_reload(page, gear, dialog, resets, saved, cards)
                _reduced_motion_panel_opens_on_screen(page, gear, dialog)
                assert not errors, errors
                assert not failed_api, failed_api
            finally:
                browser.close()
