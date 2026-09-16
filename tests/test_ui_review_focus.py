"""Delayed replay readiness and real mouse/keyboard transport ownership."""
import base64

import pytest
from playwright.sync_api import sync_playwright

from test_ui_replay_picture_steps import PROJECT, STORY
from test_ui_review_selection import selection_media


@pytest.mark.parametrize("leave", [False, True])
def test_ready_focus_respects_last_click_and_compact_controls(tmp_path, leave):
    clip = tmp_path / "focus.mp4"
    replay = selection_media(clip)
    replay["clip_url"] = "data:video/mp4;base64," + base64.b64encode(clip.read_bytes()).decode()
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            errors, pending = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.route("**/api/attempts/*/replay", lambda route: pending.append(route))
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            setup = STORY.setup.replace("await waitFor(() => !!document.querySelector('.input-lanes'));", "")
            page.evaluate(setup)
            page.wait_for_selector(".replay-state")
            page.evaluate("""() => {
                const input = document.createElement('input'); input.id='outside-field';
                input.setAttribute('aria-label','Outside field'); document.body.prepend(input);
            }""")
            if leave:
                page.get_by_label("Outside field").click()
            assert len(pending) == 1
            pending.pop().fulfill(json=replay)
            page.wait_for_selector(".input-lanes")
            video = page.locator(".attempt-drawer video")
            page.wait_for_function("document.querySelector('.attempt-drawer video').readyState >= 2")
            if leave:
                assert page.get_by_label("Outside field").evaluate("e => e === document.activeElement")
                page.keyboard.type("k")
                assert page.get_by_label("Outside field").input_value() == "k"
                # Even a click on inert replay padding restores shortcut ownership.
                page.locator(".replay-controls").click(position={"x": 2, "y": 2})
            page.wait_for_function("document.activeElement?.classList.contains('input-lanes')")
            page.keyboard.press("k")
            assert video.evaluate("v => v.paused")
            check_compact_controls(page, video, tmp_path)
            assert errors == [], errors
        finally:
            browser.close()


def check_compact_controls(page, video, tmp_path):
    seek = page.get_by_label("Seek recording")
    seek.click(position={"x": 80, "y": 15})
    assert not seek.evaluate("e => e === document.activeElement")
    page.keyboard.press("l")
    assert video.evaluate("v => !v.paused")
    page.keyboard.press("k")
    assert video.evaluate("v => v.paused")
    # Keyboard users still get a focused, adjustable seek slider.
    seek.focus()
    page.keyboard.press("Home")
    # A slider seek is queued to the next presented picture (reviewseek.js);
    # read its outcome, not the instant after the key.
    page.wait_for_function("document.querySelector('.attempt-drawer video').currentTime === 0", timeout=3000)
    assert seek.evaluate("e => e.matches(':focus-visible')")
    pair = page.locator(".replay-time-pair")
    assert pair.inner_text().endswith(" / 0:04.00")
    assert pair.bounding_box()["x"] > seek.bounding_box()["x"] + seek.bounding_box()["width"]
    mute = page.get_by_role("button", name="Mute", exact=True)
    page.mouse.move(0, 0)
    assert not page.get_by_label("Volume", exact=True).is_visible()
    mute.hover()
    assert page.get_by_label("Volume", exact=True).is_visible()
    mute.click()
    assert video.evaluate("v => v.muted")
    page.locator(".replay-mute-cross").wait_for(state="visible", timeout=3000)
    page.get_by_role("button", name="Unmute", exact=True).click()
    assert not video.evaluate("v => v.muted")
    page.get_by_label("Playback speed").select_option("2")
    assert video.evaluate("v => v.playbackRate") == 2
    assert page.locator(".replay-speed svg").is_visible()
    page.mouse.move(0, 0)
    assert not page.get_by_label("Volume", exact=True).is_visible()
    mute.focus()
    page.keyboard.press("Tab")
    assert page.get_by_label("Volume", exact=True).evaluate("e => e === document.activeElement")
    page.keyboard.press("End")
    assert video.evaluate("v => v.volume") == 1
    page.locator(".replay-controls").screenshot(path=str(tmp_path / "compact-controls.png"))
