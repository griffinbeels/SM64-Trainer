"""An attached but unavailable GPU worker must not paint a recording indicator."""

import json
from types import SimpleNamespace as NS

import pytest
from playwright.sync_api import sync_playwright

from test_gpucapture import GpuCapture, US_LAYOUT, WIN, make_recorder, FakeAudioSource
from test_ui_replay_picture_steps import PROJECT


@pytest.mark.parametrize("width", [850, 1500])
def test_gpu_failure_status_reaches_real_recording_indicator(tmp_path, width):
    video = GpuCapture(WIN.pid, US_LAYOUT, nominal_rate=30)
    recorder = make_recorder(tmp_path, video, FakeAudioSource())
    recorder._video_source = video
    recorder._video_sink = video.create_sink(NS(picture_feed=True), None, None, None)
    recorder._recording = True
    video.report_wait("GPU recording unavailable: fully close and reopen Project64")
    state = recorder.status()
    assert state["recording"] is False and state["publication_error"]
    errors, requests = [], []
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": 1000})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text)
                    if message.type == "error" else None)
            page.on("response", lambda response: requests.append(
                {"url": response.url, "status": response.status}) if "/api/" in response.url else None)
            page.route("**/api/replay/status", lambda route: route.fulfill(json=state))
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            indicator = page.locator(".recording-button.bad:visible")
            indicator.wait_for()
            assert indicator.inner_text().strip() == "no capture"
            assert not page.locator(".recording-button.ok").count()
            indicator.screenshot(path=str(tmp_path / f"capture-unavailable-{width}.png"))
            (tmp_path / "browser.json").write_text(json.dumps(
                {"errors": errors, "requests": requests}), encoding="utf-8")
            assert not errors
            assert requests and all(200 <= row["status"] < 300 for row in requests)
        finally:
            browser.close()
