"""Real keyboard intent survives a replay loop restarting at the clip's end."""
import json

import pytest
from playwright.sync_api import sync_playwright

from test_ui_replay_picture_steps import PROJECT, STORY
from test_ui_review_selection import selection_media


@pytest.fixture
def looping_page(tmp_path):
    replay = selection_media(tmp_path / "source.mp4", gop=30)
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/attempts/*/replay", lambda route: route.fulfill(json=replay))
            # Out is the 4 s clip's own end, so every wrap passes through the
            # element's end of stream and its `ended` restart.
            page.route("**/api/attempts/*/replay/review-state", lambda route: route.fulfill(json={
                "loop": {"start": 3.7, "end": 4, "enabled": True},
                "zoom": None, "template_offsets": {}}))
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            page.evaluate(STORY.setup)
            page.wait_for_selector(".input-inspector")
            page.wait_for_function("document.querySelector('.attempt-drawer video').duration > 3.99")
            video = page.locator(".attempt-drawer video")
            video.focus()
            page.keyboard.press("k")
            yield page, video, errors
        finally:
            browser.close()


def test_real_forward_shuttle_keeps_rate_across_eos(looping_page, tmp_path):
    page, video, errors = looping_page
    video.evaluate("""video => {
      window.wraps = [];
      video.addEventListener('ended', () => window.wraps.push({
        time: video.currentTime, rate: video.playbackRate, shuttle: video.dataset.reviewShuttle}));
    }""")
    ladder = page.evaluate("import('/ui/replayshuttle.js').then(m => m.REPLAY_SPEEDS.filter(r => r >= 1))")
    for _ in range(4):
        page.keyboard.press("l")
    page.wait_for_function("window.wraps.length >= 3")
    result = video.evaluate("video => ({wraps:window.wraps,rate:video.playbackRate,shuttle:video.dataset.reviewShuttle})")
    (tmp_path / "shuttle-eos.json").write_text(json.dumps(result), encoding="utf-8")
    # Four presses climb four steps of the shared ladder, and every wrap
    # through EOS keeps that rate rather than dropping back to 1x.
    expected = ladder[min(3, len(ladder) - 1)]
    assert expected > 1 and result["rate"] == expected, result
    assert all(wrap["rate"] == expected for wrap in result["wraps"]), result
    assert result["shuttle"] == f"Forward {expected:g}×", result
    page.keyboard.press("k")
    page.wait_for_timeout(150)
    assert video.evaluate("video => video.paused && video.playbackRate === 1")
    assert errors == []


def test_k_at_decoder_end_cancels_queued_loop_restart(looping_page, tmp_path):
    page, video, errors = looping_page
    # Native pause precedes ended. Deliver the actual K binding at that boundary,
    # when video.pause() alone is a no-op because the element is already paused.
    video.evaluate("""video => {
      window.boundaryPause = false;
      video.addEventListener('pause', () => {
        if (!video.ended || window.boundaryPause) return;
        window.boundaryPause = true;
        for (const type of ['keydown', 'keyup']) video.dispatchEvent(new KeyboardEvent(
          type, {key:'k',code:'KeyK',bubbles:true,cancelable:true}));
      });
    }""")
    page.keyboard.press("l")
    page.wait_for_function("window.boundaryPause")
    page.wait_for_timeout(250)
    result = video.evaluate("video => ({paused:video.paused,time:video.currentTime,shuttle:video.dataset.reviewShuttle})")
    (tmp_path / "pause-at-eos.json").write_text(json.dumps(result), encoding="utf-8")
    assert result["paused"], result
    assert result["shuttle"] == "", result
    assert errors == []
