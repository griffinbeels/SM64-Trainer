"""Rapid input bursts and real pointer drags keep the final decoded picture."""
import hashlib
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from test_ui_replay_picture_steps import PROJECT, STORY
from test_ui_review_selection import selection_media


def test_drag_flood_settles_and_continues_playing(tmp_path):
    width = 1500
    original = tmp_path / "scrub-source.mp4"
    replay = selection_media(original, gop=30)
    replay["clip_url"] = "/scrub-source.mp4"
    media = {"/scrub-source.mp4": original.read_bytes()}
    inputs = {"frames": 120, "attempt_frames": 120, "lead_frames": 0, "fps": 30,
              "stretches": [[0, 1000, 120]], "buttons": [[32768, "A"]], "stick_max": 84,
              "dead_zone": 8, "angle_units": 65536, "actions": [], "markers": [],
              "runs": [{"start": 0, "length": 120, "buttons": 32768, "stick_x": 0,
                        "stick_y": 84, "yaw": 0, "speed": 32}]}
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": 1100})
            page.set_default_timeout(5000)
            errors, requests, statuses, source_hashes = [], [], [], {}
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.on("response", lambda response: statuses.append({"url": response.url, "status": response.status}))
            _route_media(page, replay, media, inputs, requests, source_hashes)
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            page.evaluate(STORY.setup)
            page.wait_for_selector(".input-inspector")
            video = page.locator(".attempt-drawer video")
            video.evaluate("video => video.pause()")
            page.wait_for_function("document.querySelector('.attempt-drawer video').readyState >= 2")
            page.wait_for_function("document.querySelector('input[aria-label=\"Seek recording\"]').max === '4'")
            burst, expected = _exercise_scrubber(page, video)
            evidence = page.evaluate("window.scrubEvidence")
            evidence.update({"viewport_width": width, "burst_writes": burst,
                             "requests": requests, "responses": statuses, "errors": errors,
                             "source_sha256": source_hashes, "pointer_final_picture": expected})
            (tmp_path / "scrub-evidence.json").write_bytes(json.dumps(evidence, indent=2).encode())
            page.locator(".attempt-drawer").screenshot(path=str(tmp_path / "scrub-drawer.png"))
            assert requests and len(requests) <= 3, requests
            assert errors == []
            assert all(response["status"] < 400 for response in statuses)
        finally:
            debug = page.evaluate("""() => {const v=document.querySelector('.attempt-drawer video');
              return {evidence:window.scrubEvidence,video:v && {src:v.currentSrc,time:v.currentTime,
                duration:v.duration,paused:v.paused,seeking:v.seeking,ready:v.readyState,
                picture:window.readScrubPicture?.()},slider:document.querySelector('input[aria-label="Seek recording"]')?.outerHTML};} """)
            debug.update({"errors": errors, "requests": requests, "source_hashes": source_hashes})
            (tmp_path / "scrub-debug.json").write_bytes(json.dumps(debug, indent=2).encode())
            browser.close()


def _route_media(page, replay, media, inputs, requests, source_hashes):
    def source(route):
        name = route.request.url.split("/ui/")[-1]
        path = Path(__file__).resolve().parents[1] / "src/sm64_events/ui" / name
        if path.is_file():
            data = path.read_bytes()
            source_hashes[name] = hashlib.sha256(data).hexdigest()
            route.fulfill(body=data, content_type="text/javascript")
        else:
            route.continue_()
    page.route("**/ui/*.js", source)
    page.route("**/ui/**/*.js", source)
    def recording(route):
        path = "/" + route.request.url.rsplit("/", 1)[-1]
        requested = route.request.headers.get("range")
        requests.append({"path": path, "range": requested})
        data = media[path]
        if requested:
            first, last = requested.removeprefix("bytes=").split("-", 1)
            start, end = int(first), int(last) if last else len(data) - 1
            route.fulfill(status=206, body=data[start:end + 1], content_type="video/mp4",
                          headers={"Content-Range": f"bytes {start}-{end}/{len(data)}",
                                   "Accept-Ranges": "bytes"})
        else:
            route.fulfill(body=data, content_type="video/mp4", headers={"Accept-Ranges": "bytes"})
    page.route("**/scrub-*.mp4", recording)
    page.route("**/api/attempts/*/replay", lambda route: route.fulfill(json=replay))
    page.route("**/api/attempts/*/inputs*", lambda route: route.fulfill(json=inputs))
    page.route("**/api/attempts/*/replay/review-state", lambda route: route.fulfill(json={
        "loop": None, "zoom": None, "template_offsets": {}}))


def _exercise_scrubber(page, video):
    page.evaluate("""() => {
      const video = document.querySelector('.attempt-drawer video');
      const slider = document.querySelector('input[aria-label="Seek recording"]');
      const time = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime');
      window.scrubEvidence = {writes:[], inputs:[], pictures:[]};
      Object.defineProperty(video, 'currentTime', {configurable:true,
        get() {return time.get.call(this);},
        set(value) {window.scrubEvidence.writes.push(value);time.set.call(this,value);}});
      slider.addEventListener('input', () => window.scrubEvidence.inputs.push(Number(slider.value)), true);
      const canvas = document.createElement('canvas'); canvas.width=320; canvas.height=96;
      const context = canvas.getContext('2d', {willReadFrequently:true});
      window.readScrubPicture = () => {
        context.drawImage(video,0,0,320,96);
        return Array.from({length:8},(_,bit)=>context.getImageData(bit*40+20,48,1,1).data[0]>128?1<<bit:0)
          .reduce((a,b)=>a+b,0);
      };
      const shown = (_now, meta) => {
        window.scrubEvidence.pictures.push({number:window.readScrubPicture(),time:meta.mediaTime});
        video.requestVideoFrameCallback(shown);
      };
      video.requestVideoFrameCallback(shown);
      for (let i=0;i<240;i++) {
        slider.value=String(i===239 ? 87.5/30 : ((i*43)%119+.5)/30);
        slider.dispatchEvent(new Event('input',{bubbles:true}));
      }
    }""")
    page.wait_for_function("window.scrubEvidence.pictures.some(p=>p.number===87)")
    page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('87 /')")
    assert page.evaluate("window.readScrubPicture()") == 87
    burst = page.evaluate("window.scrubEvidence.writes.length")
    assert burst <= 3, f"240 scrub inputs issued {burst} decoder seeks"
    assert video.evaluate("v => v.paused")
    slider = page.get_by_role("slider", name="Seek recording")
    slider.scroll_into_view_if_needed()
    box = slider.bounding_box()
    page.mouse.move(box["x"] + box["width"] * .2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * .7, box["y"] + box["height"] / 2, steps=80)
    page.mouse.up()
    target = page.evaluate("window.scrubEvidence.inputs.at(-1)")
    expected = min(119, int(target * 30))
    page.wait_for_function("n => window.readScrubPicture()===n", arg=expected)
    page.wait_for_function("n => document.querySelector('.input-inspector-frame strong').textContent.startsWith(n+' /')", arg=expected)
    page.get_by_role("button", name="Play", exact=True).click()
    page.wait_for_function("n => window.scrubEvidence.pictures.some(p=>p.number>n)", arg=expected)
    assert video.evaluate("v => !v.paused")
    page.mouse.move(box["x"] + box["width"] * .3, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * .5, box["y"] + box["height"] / 2, steps=20)
    page.mouse.up()
    playing_target = page.evaluate("window.scrubEvidence.inputs.at(-1)")
    page.wait_for_function("time => document.querySelector('.attempt-drawer video').currentTime >= time", arg=playing_target)
    assert video.evaluate("v => !v.paused")
    video.focus(); page.keyboard.press("k")
    page.get_by_role("button", name="Start", exact=True).click()
    page.wait_for_function("window.readScrubPicture()===0")
    page.get_by_role("button", name="Forward 1", exact=True).click()
    page.wait_for_function("window.readScrubPicture()===1")
    page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('1 /')")
    return burst, expected
