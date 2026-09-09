"""The requested review gestures use real mouse/key events and encoded pictures."""
import base64
from fractions import Fraction

import av
from playwright.sync_api import sync_playwright

from test_replay_picture_identity import picture, read_pictures
from test_ui_replay_picture_steps import PROJECT, STORY


def selection_media(path, gop=1):
    with av.open(str(path), "w", options={"movie_timescale": "90000"}) as container:
        stream = container.add_stream("libx264", rate=30)
        stream.width, stream.height, stream.pix_fmt = 320, 96, "yuv420p"
        stream.time_base = stream.codec_context.time_base = Fraction(1, 90000)
        stream.options = {"crf": "12", "preset": "ultrafast", "bf": "0", "g": str(gop)}
        for index in range(120):
            frame = av.VideoFrame.from_ndarray(picture(index), format="bgra")
            frame.pts, frame.time_base = index * 3000, Fraction(1, 90000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    decoded = read_pictures(path)
    assert [number for _, number in decoded] == list(range(120))
    return {"clip_url": "data:video/mp4;base64," + base64.b64encode(path.read_bytes()).decode(),
            "frame_times": [round(time, 6) for time, _ in decoded], "picture_ids": list(range(120)),
            "frame_map": list(range(1000, 1120)), "picture_igt": list(range(120)),
            "frame_map_source": "plugin", "fps": 30, "game_fps": 30, "duration_s": 4, "source": "buffer"}


def seek_frame(page, index):
    page.locator(".attempt-drawer video").evaluate("(v, i) => {v.pause(); v.currentTime=(i+.5)/30;}", index)
    page.wait_for_function("i => document.querySelector('.input-inspector-frame strong').textContent.startsWith(i+' /')", arg=index)


def check_loop_selection(page):
    video = page.locator(".attempt-drawer video")
    lanes = page.locator(".input-lanes")
    lanes.scroll_into_view_if_needed()
    box = page.locator(".input-track-column").bounding_box()
    y = lanes.bounding_box()["y"] + 20
    page.mouse.move(box["x"] + box["width"] * 50.5 / 120, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * 100.5 / 120, y, steps=8)
    page.mouse.up()
    page.wait_for_selector('.replay-loop-row button[aria-pressed="true"]')
    assert page.locator(".input-loop-shade.is-enabled").count() == 1
    bounds = page.locator(".replay-loop-row .replay-media-time").all_text_contents()
    assert bounds == ["0:01.67", "0:03.37"], bounds
    seek_frame(page, 10)
    video.click()
    assert video.evaluate("v => !v.paused && v.currentTime >= 50/30 && v.currentTime < 1.9")
    page.keyboard.press("Shift+i")
    assert video.evaluate("v => !v.paused && v.currentTime < 1.9")
    page.keyboard.press("k")
    seek_frame(page, 70)
    video.click()
    assert video.evaluate("v => !v.paused && v.currentTime > 2.3")
    page.keyboard.press("k")
    page.keyboard.press("x")
    assert page.locator(".replay-loop-row .replay-media-time").all_text_contents() == ["—", "—"]
    seek_frame(page, 60)
    lanes.focus()
    page.keyboard.down("k")
    page.keyboard.press("j")
    page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('59 /')")
    page.keyboard.press("l")
    page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('60 /')")
    page.keyboard.up("k")
    page.keyboard.press("i")
    seek_frame(page, 90)
    page.keyboard.press("o")
    assert page.locator(".replay-loop-row .replay-media-time").all_text_contents() == ["0:02.00", "0:03.03"]
    assert page.get_by_role("button", name="Loop", exact=True).get_attribute("aria-pressed") == "true"


def test_drag_chords_and_fullscreen_readout(tmp_path):
    replay = selection_media(tmp_path / "selection.mp4")
    inputs = {"frames": 120, "attempt_frames": 120, "lead_frames": 0, "fps": 30,
              "stretches": [[0, 1000, 120]], "buttons": [[32768, "A"]], "stick_max": 84,
              "dead_zone": 8, "angle_units": 65536, "actions": [], "markers": [],
              "runs": [{"start": 0, "length": 120, "buttons": 32768, "stick_x": 0, "stick_y": 84, "yaw": 0, "speed": 40}]}
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/attempts/*/replay", lambda route: route.fulfill(json=replay))
            page.route("**/api/attempts/*/inputs*", lambda route: route.fulfill(json=inputs))
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            page.evaluate(STORY.setup)
            page.wait_for_selector(".input-inspector")
            seek_frame(page, 30)
            check_loop_selection(page)
            for width, height in [(1500, 1100), (850, 900), (850, 540)]:
                page.set_viewport_size({"width": width, "height": height})
                page.get_by_role("button", name="Fullscreen", exact=True).click()
                page.wait_for_selector(".replay-player > .review-readout.is-docked")
                readout = page.locator(".review-readout.is-docked").bounding_box()
                assert readout["y"] >= 0 and readout["y"] + readout["height"] <= height
                assert page.locator(".review-readout.is-docked").evaluate("e => e.scrollHeight <= e.clientHeight + 1")
                for selector in [".input-inspector", ".review-readout button"]:
                    for element in page.locator(selector).all():
                        box = element.bounding_box()
                        assert box["y"] >= readout["y"] and box["y"] + box["height"] <= readout["y"] + readout["height"] + 1
                assert page.locator(".attempt-drawer-inputs").bounding_box()["height"] >= 100
                assert page.locator(".input-inspector").count() == 1
                page.screenshot(path=str(tmp_path / f"fullscreen-readout-{width}-{height}.png"))
                page.evaluate("document.exitFullscreen()")
                page.wait_for_selector(".review-readout-anchor > .review-readout:not(.is-docked)")
            assert errors == [], errors
            print(f"Loop selection evidence: {tmp_path}")
        finally:
            browser.close()
