"""First presented picture must not wait for an experimental response's tail."""
import base64
import json

from playwright.sync_api import sync_playwright

from sm64_events.replay.fragments import FragmentReader
from test_ui_loop_cutoff import fragment_packets
from test_ui_replay_picture_steps import PROJECT, STORY
from test_ui_review_selection import selection_media


def test_review_presents_and_steps_prefix_before_response_finishes(tmp_path):
    original, bounded = tmp_path / "source.mp4", tmp_path / "bounded.mp4"
    replay = selection_media(original, gop=30)
    mime = fragment_packets(original, bounded)
    data = bounded.read_bytes()
    reader = FragmentReader()
    units = reader.feed(data)
    reader.finish()
    assert len([unit for unit in units if unit.kind == "media"]) == 4
    prefix = b"".join(unit.data for unit in units[:3])
    tail = data[len(prefix):]
    assert tail and len(prefix) < len(data)
    replay["review_media"] = {"url": "/streamed-review.mp4", "mime_type": mime,
                              "video_timescale": 90000, "timestamp_offset_s": 0,
                              "visible_start_s": 0, "visible_end_s": 4}
    inputs = {"frames": 120, "attempt_frames": 120, "lead_frames": 0, "fps": 30,
              "stretches": [[0, 1000, 120]], "buttons": [[32768, "A"]], "stick_max": 84,
              "dead_zone": 8, "angle_units": 65536, "actions": [], "markers": [],
              "runs": [{"start": 0, "length": 120, "buttons": 32768, "stick_x": 0,
                        "stick_y": 84, "yaw": 0, "speed": 32}]}
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/attempts/*/replay", lambda route: route.fulfill(json=replay))
            page.route("**/api/attempts/*/inputs*", lambda route: route.fulfill(json=inputs))
            page.route("**/api/attempts/*/replay/review-state", lambda route: route.fulfill(json={
                "loop": None, "zoom": None, "template_offsets": {}}))
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            page.evaluate("""({prefix, tail}) => {
              const bytes = text => Uint8Array.from(atob(text), c => c.charCodeAt(0));
              const originalFetch = window.fetch;
              window.tailSent = false; window.prefixPictures = [];
              window.fetch = (url, options) => {
                if (String(url) !== '/streamed-review.mp4') return originalFetch(url, options);
                window.streamStarted = performance.now();
                return Promise.resolve(new Response(new ReadableStream({start(controller) {
                  controller.enqueue(bytes(prefix));
                  window.releaseTail = () => {
                    window.tailSent = true; controller.enqueue(bytes(tail)); controller.close();
                  };
                }}), {headers:{'Content-Type':'video/mp4'}}));
              };
              const request = HTMLVideoElement.prototype.requestVideoFrameCallback;
              const canvas = document.createElement('canvas'); canvas.width=320; canvas.height=96;
              const context = canvas.getContext('2d', {willReadFrequently:true});
              HTMLVideoElement.prototype.requestVideoFrameCallback = function(callback) {
                return request.call(this, (now, meta) => {
                  context.drawImage(this,0,0,320,96);
                  const number = Array.from({length:8},(_,bit)=>
                    context.getImageData(bit*40+20,48,1,1).data[0]>128 ? 1<<bit : 0).reduce((a,b)=>a+b,0);
                  window.prefixPictures.push({number,time:meta.mediaTime,tailSent:window.tailSent,
                    elapsed:performance.now()-window.streamStarted});
                  callback(now, meta);
                });
              };
            }""", {"prefix": base64.b64encode(prefix).decode(), "tail": base64.b64encode(tail).decode()})
            page.evaluate(STORY.setup)
            page.wait_for_function("window.prefixPictures.length > 0", timeout=5000)
            page.wait_for_selector(".input-inspector")
            video = page.locator(".attempt-drawer video")
            video.focus()
            page.keyboard.press("k")
            result = page.evaluate("({pictures:window.prefixPictures,tailSent:window.tailSent})")
            assert not result["tailSent"]
            assert any(not picture["tailSent"] for picture in result["pictures"])
            assert video.evaluate("video => video.currentSrc.startsWith('blob:')")
            page.get_by_role("button", name="Start", exact=True).click()
            page.get_by_role("button", name="Forward 1", exact=True).click()
            page.wait_for_function("window.prefixPictures.some(p => p.number===1 && Math.abs(p.time-1/30)<.000002)")
            page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('1 /')")
            page.evaluate("window.releaseTail()")
            video.evaluate("video => {video.currentTime=115.5/30;}")
            page.wait_for_function("window.prefixPictures.some(p => p.number===115 && Math.abs(p.time-115/30)<.000002)")
            page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('115 /')")
            (tmp_path / "prefix-readiness.json").write_text(json.dumps({
                **result, "prefix_bytes": len(prefix), "file_bytes": len(data)}), encoding="utf-8")
            page.locator(".attempt-drawer").screenshot(path=str(tmp_path / "prefix-review.png"))
            assert errors == []
        finally:
            browser.close()
