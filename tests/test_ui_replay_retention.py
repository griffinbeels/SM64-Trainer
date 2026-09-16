"""The real storage panel exposes count/session/time without writing live data."""
import json

from playwright.sync_api import sync_playwright

from test_ui_replay_picture_steps import PROJECT


def test_retention_panel_persists_selected_policy_at_supported_widths(tmp_path):
    settings = {"retention_attempts": 10, "retention_s": None, "max_buffer_bytes": 2 * 1024**3,
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
            panel.get_by_role("button", name="Apply", exact=True).click()
            page.wait_for_function("document.querySelector('.popover-actions').textContent.includes('saved')")
            assert updates[-1]["retention_attempts"] == 7 and updates[-1]["retention_s"] is None
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
            panel.get_by_role("radio", name="Session", exact=True).check()
            panel.get_by_role("button", name="Apply", exact=True).click()
            page.wait_for_timeout(100)
            assert updates[-1]["retention_attempts"] is None and updates[-1]["retention_s"] is None
            assert errors == []
            (tmp_path / "settings-evidence.json").write_text(json.dumps(updates), encoding="utf-8")
        finally:
            browser.close()
