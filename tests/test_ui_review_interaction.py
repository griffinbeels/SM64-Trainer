"""Real input events must stay in review, including actual browser fullscreen."""
import base64
import pytest

from playwright.sync_api import sync_playwright
from test_ui_replay_picture_steps import PROJECT, STORY
from test_ui_review_selection import selection_media


def check_playback(page, video):
    video.evaluate("v => { v.pause(); v.currentTime=0; v.playbackRate=.1; }")
    video.click()
    assert video.evaluate("v => !v.paused")
    video.click()
    assert video.evaluate("v => v.paused")
    page.keyboard.press("l")
    assert video.evaluate("v => v.playbackRate") == 1
    page.keyboard.press("l")
    assert video.evaluate("v => v.playbackRate") == 2
    page.keyboard.press("k")
    assert video.evaluate("v => v.paused")
    video.evaluate("v => { v.currentTime=.35; }")
    page.wait_for_function("!document.querySelector('.attempt-drawer video').seeking")
    page.keyboard.press("j")
    page.wait_for_function("document.querySelector('.attempt-drawer video').currentTime < .3")
    page.keyboard.press("k")


def check_navigation(page, video):
    lanes = page.locator(".input-lanes")
    lanes.scroll_into_view_if_needed()
    lanes.hover()
    before = page.evaluate("scrollY")
    page.mouse.wheel(0, -400)
    page.wait_for_function("Number(document.querySelector('.timeline-scroll').getAttribute('aria-valuemax')) > 0")
    assert page.evaluate("scrollY") == before
    bar = page.locator(".timeline-scroll")
    assert abs(bar.bounding_box()["width"] - lanes.bounding_box()["width"]) < 3
    bar.focus()
    page.keyboard.press("End")
    page.wait_for_function("Number(document.querySelector('.timeline-scroll').getAttribute('aria-valuenow')) > 0")
    thumb = page.locator(".timeline-scroll-thumb").bounding_box()
    track = bar.bounding_box()
    assert 0 < thumb["width"] < track["width"]
    page.mouse.move(thumb["x"] + thumb["width"] / 2, thumb["y"] + thumb["height"] / 2)
    page.mouse.down()
    page.mouse.move(track["x"] + thumb["width"] / 2, thumb["y"] + thumb["height"] / 2, steps=5)
    page.mouse.up()
    page.wait_for_function("document.querySelector('.timeline-scroll').getAttribute('aria-valuenow') === '0'")
    volume = page.locator(".replay-volume")
    page.get_by_role("button", name="Mute", exact=True).focus()
    volume.focus()
    page.keyboard.press("End")
    assert video.evaluate("v => v.volume") == 1
    page.keyboard.press("Home")
    assert video.evaluate("v => v.volume") == 0
    assert volume.evaluate("e => getComputedStyle(e).paddingLeft") == "0px"
    return lanes


def check_short_fullscreen(page, output):
    page.set_viewport_size({"width": 850, "height": 540})
    page.get_by_role("button", name="Fullscreen", exact=True).click()
    page.wait_for_function("!!document.fullscreenElement")
    split = page.get_by_role("separator", name="Resize gameplay and timeline")
    split.focus()
    page.keyboard.press("Home")
    controls = page.locator(".replay-controls").bounding_box()
    assert controls["y"] + controls["height"] <= split.bounding_box()["y"]
    assert page.locator(".attempt-drawer-inputs").bounding_box()["height"] >= 100
    page.screenshot(path=str(output / "fullscreen-short.png"))
    page.evaluate("document.exitFullscreen()")
    page.wait_for_function("!document.fullscreenElement")


def configure_media(page, replay, downloaded, clip):
    page.route("**/api/attempts/*/replay", lambda route: route.fulfill(json={"clip_url": None} if downloaded else replay))
    if downloaded:
        page.route("**/api/attempts/*/recording", lambda route: route.fulfill(json={"url": "https://example.com/clip.mp4"}))
        page.route("**/api/media?*", lambda route: route.fulfill(json={"state": "ready", "clip_url": replay["clip_url"], "fps": 30}))
        page.route("**/api/media", lambda route: route.fulfill(json={"state": "ready", "clip_url": replay["clip_url"], "fps": 30}))
        page.route("**/api/media/preview?*", lambda route: route.fulfill(json={"title": "Downloaded fixture"}))
        page.route("https://example.com/**", lambda route: route.fulfill(path=str(clip), content_type="video/mp4"))


@pytest.mark.parametrize("downloaded", [False, True])
def test_review_gestures_fullscreen_and_volume(tmp_path, downloaded):
    clip = tmp_path / "controls.mp4"
    media = selection_media(clip)
    times, identities = media["frame_times"], media["picture_ids"]
    replay = {"clip_url": "data:video/mp4;base64," + base64.b64encode(clip.read_bytes()).decode(),
              "frame_times": [round(t, 6) for t in times], "picture_ids": identities, "frame_map": list(range(100, 108)),
              "frame_map_source": "plugin", "fps": 30, "game_fps": 30,
              "duration_s": times[-1] + 1 / 30, "source": "buffer"}
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            configure_media(page, replay, downloaded, clip)
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            assert page.locator(".review-latest-button").count() == 0
            page.evaluate(STORY.setup)
            if downloaded:
                page.get_by_role("button", name="Download", exact=True).click()
                page.wait_for_selector(".external-video-local")
            video = page.locator(".attempt-drawer video")
            page.wait_for_selector(".input-inspector")
            page.wait_for_function("document.querySelector('.attempt-drawer video').readyState >= 2")
            check_playback(page, video)
            lanes = check_navigation(page, video)
            page.get_by_role("button", name="Fullscreen", exact=True).click()
            page.wait_for_function("document.fullscreenElement?.classList.contains('attempt-drawer')")
            assert page.locator(".attempt-drawer-inputs").is_visible()
            split = page.get_by_role("separator", name="Resize gameplay and timeline")
            old = split.bounding_box()["y"]
            split.focus()
            page.keyboard.press("ArrowUp")
            assert split.bounding_box()["y"] < old
            video.click()
            page.keyboard.press("k")
            assert video.evaluate("v => v.paused")
            page.screenshot(path=str(tmp_path / "fullscreen.png"))
            page.evaluate("document.exitFullscreen()")
            page.wait_for_function("!document.fullscreenElement")
            check_short_fullscreen(page, tmp_path)
            for width, height in [(1500, 1100), (850, 900)]:
                page.set_viewport_size({"width": width, "height": height})
                lanes.scroll_into_view_if_needed()
                page.screenshot(path=str(tmp_path / f"review-{width}.png"))
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert errors == [], errors
            print(f"Review interaction screenshots: {tmp_path}")
        finally:
            browser.close()
