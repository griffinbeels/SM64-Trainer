"""Manual recording control reports completion, rather than just desired state."""
import json

from playwright.sync_api import sync_playwright

from test_ui_replay_picture_steps import PROJECT


def open_recording_page(browser, state, settings, control, errors, responses):
    """The real page over faked replay endpoints, recording every failure."""
    page = browser.new_page(viewport={"width": 1500, "height": 1100})
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("response", lambda response: responses.append({"url": response.url, "status": response.status})
            if "/api/" in response.url else None)
    page.route("**/api/replay/status", lambda route: route.fulfill(json=state))
    page.route("**/api/replay/settings", lambda route: route.fulfill(json=settings))
    page.route("**/api/replay/recording", control)
    page.route("**/api/replay/compression", lambda route: route.fulfill(json={"active": False, "jobs": []}))
    return page


def button_label(page):
    """The recording button's own label, without the one-word form the narrow
    sidebar shows beside it (round 8)."""
    return page.locator(".recording-button:visible").evaluate(
        "b => [...b.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim()")


def narrow_rec_is(page, on):
    """The narrow sidebar's one word: REC, red (`is-on`) only while capturing."""
    page.wait_for_function(
        "(() => { const s = document.querySelector('.sidebar-status .recording-short');"
        f" return !!s && s.textContent === 'REC' && s.classList.contains('is-on') === {str(on).lower()}; }})()")


def assert_off_panel_fits(page, panel, switch, tmp_path, width):
    page.set_viewport_size({"width": width, "height": 1100})
    # Enter through the real recording button at each supported
    # shell layout; a resize screenshot can retain stale paint.
    page.reload()
    page.wait_for_selector(PROJECT.ready_selector)
    page.locator(".recording-button:visible").click()
    switch.get_by_role("button", name="Start recording").wait_for()
    panel.screenshot(path=str(tmp_path / f"recording-off-{width}.png"), animations="disabled")
    page.screenshot(path=str(tmp_path / f"recording-page-{width}.png"), animations="disabled")
    box = panel.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert switch.evaluate("el => el.scrollWidth <= el.clientWidth")
    title = switch.locator("b").bounding_box()
    assert title["x"] >= box["x"] and title["x"] + title["width"] <= box["x"] + box["width"]


def test_manual_recording_control_waits_for_actual_stop_and_resume(tmp_path):
    state = {"enabled": True, "recording_enabled": True, "capture_state": "recording",
             "recording": True, "idle": False, "disk_bytes": 1000000,
             "max_buffer_bytes": 2 * 1024**3, "encoder": "fixture", "audio_mode": "process"}
    settings = {"retention_attempts": 10, "retention_s": None, "max_buffer_bytes": 2 * 1024**3,
                "pre_pad_s": 0.5, "post_pad_s": 2, "saved_bytes": 10000000, "save_root": "fixture"}
    writes, errors, responses = [], [], []

    def control(route):
        body = route.request.post_data_json
        writes.append(body)
        state.update(recording_enabled=body["enabled"], recording=False,
                     capture_state="starting" if body["enabled"] else "stopping")
        route.fulfill(json=state)

    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = open_recording_page(browser, state, settings, control, errors, responses)
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            # Control: existing buffer settings are reachable before the new action.
            page.locator(".recording-button").first.click()
            panel = page.locator(".replay-settings-popover")
            panel.get_by_role("spinbutton", name="Attempts to retain").wait_for()
            panel.get_by_role("button", name="Stop recording", exact=True).click()
            switch = panel.locator(".recording-switch")
            page.wait_for_function("document.querySelector('.recording-switch [role=status]').textContent === 'Stopping recording…'")
            assert writes == [{"enabled": False}]
            assert not switch.get_by_role("button", name="Start recording").is_enabled()
            assert "Practice tracking continues." in switch.inner_text()
            assert "Only Mario ghosts" in switch.inner_text()
            assert button_label(page) == "Stopping recording…"
            state.update(capture_state="off")
            page.wait_for_function("document.querySelector('.recording-switch [role=status]').textContent === 'Recording off'")
            assert switch.get_by_role("button", name="Start recording").is_enabled()
            for width in (1500, 850):
                assert_off_panel_fits(page, panel, switch, tmp_path, width)
            state.update(capture_state="stopping", recovery={"state": "cleanup_pending", "error": "Audio capture is still closing"})
            page.wait_for_function("document.querySelector('.recording-switch [role=alert]')?.textContent === 'Audio capture is still closing'")
            assert not switch.get_by_role("button", name="Start recording").is_enabled()
            state.update(capture_state="off", recovery={"state": "ready", "error": None})
            page.wait_for_function("document.querySelector('.recording-switch [role=status]').textContent === 'Recording off'")
            switch.get_by_role("button", name="Start recording", exact=True).click()
            page.wait_for_function("document.querySelector('.recording-switch [role=status]').textContent === 'Starting recording…'")
            assert writes == [{"enabled": False}, {"enabled": True}]
            # A startup can be cancelled; on is not painted as already recording.
            assert switch.get_by_role("button", name="Stop recording").is_enabled()
            assert button_label(page) == "Starting recording…"
            # At 850 the sidebar is narrow and says only REC: grey until the
            # recorder is actually capturing, red once it is (round 8).
            narrow_rec_is(page, on=False)
            state.update(capture_state="recording", recording=True)
            page.wait_for_function("[...document.querySelectorAll('.recording-button')].filter(el => el.getClientRects().length).every(el => el.textContent.includes('rec ·'))")
            narrow_rec_is(page, on=True)
            panel.screenshot(path=str(tmp_path / "recording-on-850.png"), animations="disabled")
            assert errors == []
            assert responses and all(200 <= row["status"] < 300 for row in responses)
            (tmp_path / "browser.json").write_text(json.dumps({"errors": errors, "responses": responses,
                "writes": writes}, indent=2), encoding="utf-8")
        finally:
            browser.close()
