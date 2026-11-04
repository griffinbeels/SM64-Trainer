"""The real storage panel exposes count/session/time without writing live data."""
import json

import pytest
from playwright.sync_api import sync_playwright

from test_ui_replay_picture_steps import PROJECT


def assert_keep_footage_sentence(panel):
    sentence = panel.locator(".replay-retention-row small")
    assert "more than 7 attempts or more than 15 minutes of attempts" in sentence.inner_text()
    assert sentence.evaluate("(s) => s.scrollWidth <= s.closest('.popover').clientWidth"), \
        "the Keep footage sentence runs off the panel instead of wrapping"


def test_retention_panel_persists_selected_policy_at_supported_widths(tmp_path):
    settings = {"retention_attempts": 10, "retention_s": None, "max_buffer_bytes": 2 * 1024**3,
                "retention_footage_s": 600,
                "pre_pad_s": 3, "post_pad_s": 2, "saved_bytes": 10000000, "save_root": "fixture"}
    updates = []

    def route_settings(route):
        if route.request.method == "PUT":
            update = route.request.post_data_json
            updates.append(update)
            settings.update(update)
        route.fulfill(json=settings)

    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/replay/status", lambda route: route.fulfill(json={
                **settings, "recording": True, "enabled": True, "disk_bytes": 1000000,
                "encoder": "fixture", "audio_mode": "process"}))
            page.route("**/api/replay/settings", route_settings)
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            page.locator(".recording-button").first.click()
            panel = page.locator(".replay-settings-popover")
            panel.get_by_role("spinbutton", name="Attempts to retain").fill("7")
            panel.get_by_role("spinbutton", name="Footage limit in minutes").fill("15")
            assert_keep_footage_sentence(panel)
            panel.get_by_role("button", name="Apply", exact=True).click()
            page.wait_for_function("document.querySelector('.popover-actions').textContent.includes('saved')")
            assert updates[-1]["retention_attempts"] == 7 and updates[-1]["retention_s"] is None
            assert updates[-1]["retention_footage_s"] == 900
            for width in (1500, 850):
                page.set_viewport_size({"width": width, "height": 1100})
                panel.screenshot(path=str(tmp_path / f"retention-{width}.png"))
                box = panel.bounding_box()
                assert box["x"] >= 0 and box["x"] + box["width"] <= width
                label = panel.locator(".replay-retention-row > span").bounding_box()
                controls = panel.locator(".retention-options").bounding_box()
                assert controls["y"] >= label["y"] + label["height"], "retention choices overlap their explanation"
            panel.get_by_role("radio", name="Minutes", exact=True).check()
            panel.get_by_role("spinbutton", name="Minutes to retain").fill("12")
            panel.get_by_role("button", name="Apply", exact=True).click()
            page.wait_for_timeout(100)
            assert updates[-1]["retention_attempts"] is None and updates[-1]["retention_s"] == 720
            assert "retention_footage_s" not in updates[-1]  # the limit waits for attempts mode
            assert panel.get_by_role("spinbutton", name="Footage limit in minutes").count() == 0
            panel.get_by_role("radio", name="Session", exact=True).check()
            panel.get_by_role("button", name="Apply", exact=True).click()
            page.wait_for_timeout(100)
            assert updates[-1]["retention_attempts"] is None and updates[-1]["retention_s"] is None
            assert errors == []
            (tmp_path / "settings-evidence.json").write_text(json.dumps(updates), encoding="utf-8")
        finally:
            browser.close()


SETTINGS = {"retention_attempts": 5, "retention_footage_s": 600, "retention_s": None,
            "max_buffer_bytes": 2 * 1024**3, "pre_pad_s": 0.5, "post_pad_s": 2,
            "saved_bytes": 0, "save_root": "fixture"}
LOW_DISK = {"enabled": True, "recording": False, "recording_enabled": True, "capture_state": "waiting",
            "storage_pressure": True, "disk_bytes": 0, "max_buffer_bytes": 2 * 1024**3,
            "disk_free_bytes": int(1.3 * 1024**3), "disk_reserve_bytes": 2 * 1024**3,
            "pose_source_owned": True, "audio_mode": "process", "encoder": "fixture"}


def panel_for(page, status, url):
    page.route("**/api/replay/status", lambda route: route.fulfill(json=status))
    page.route("**/api/replay/settings", lambda route: route.fulfill(json=SETTINGS))
    page.goto(url)
    page.wait_for_selector(PROJECT.ready_selector)
    button = page.locator(".recording-button").first
    button.wait_for()
    return button


@pytest.mark.parametrize("status, label, promise", [
    (LOW_DISK, "Low disk: video stopped", True),
    ({**LOW_DISK, "pose_source_owned": False}, "Low disk: video stopped", False),
])
def test_low_disk_tells_the_player_and_only_promises_ghosts_it_is_recording(tmp_path, status, label, promise):
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            button = panel_for(page, status, url)
            assert label in button.inner_text()
            fits = button.evaluate("""(b) => {
                const text = document.createRange();
                text.selectNodeContents(b);
                return text.getBoundingClientRect().width <= b.parentElement.getBoundingClientRect().width + 1;
            }""")
            assert fits, "the sidebar cut off the low-disk label"
            ghosts = "ghosts are still being recorded"
            assert (ghosts in button.get_attribute("title")) is promise
            button.click()
            alert = page.locator(".recording-low-disk")
            words = alert.inner_text()
            assert "because your disk is almost full" in words
            assert (ghosts in words) is promise
            assert "1.3 GB free" in words and "2.0 GB free" in words
            assert "\\" not in words and ":/" not in words  # numbers, never a path
            for width in (1500, 850):
                page.set_viewport_size({"width": width, "height": 1100})
                page.locator(".replay-settings-popover").screenshot(path=str(tmp_path / f"low-disk-{width}.png"))
                box = alert.bounding_box()
                assert box["x"] >= 0 and box["x"] + box["width"] <= width
            assert errors == []
        finally:
            browser.close()


@pytest.mark.parametrize("change", [{"storage_pressure": False}, {"recording_enabled": False, "capture_state": "off"},
                                    {"capture_gated": True}])
def test_low_disk_sentence_stays_away_when_disk_is_not_why_video_stopped(change):
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            button = panel_for(page, {**LOW_DISK, **change}, url)
            assert "disk" not in button.get_attribute("title").split(" — ")[0]
            button.click()
            page.locator(".replay-settings-popover").wait_for()
            assert page.locator(".recording-low-disk").count() == 0
        finally:
            browser.close()
