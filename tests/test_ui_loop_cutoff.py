"""Independent picture witnesses for native replay loop cutoff experiments."""
import json

import av
import pytest
from playwright.sync_api import sync_playwright

from test_ui_replay_picture_steps import PROJECT, STORY
from test_ui_review_selection import selection_media
from test_replay_picture_identity import read_pictures


def fragment_packets(source, output):
    """Preserve every original packet; the actual UI must enforce the loop."""
    with av.open(str(source)) as src, av.open(str(output), "w", options={
            "movflags": "frag_keyframe+empty_moov+default_base_moof"}) as dst:
        stream = dst.add_stream_from_template(src.streams.video[0])
        codec = "avc1." + src.streams.video[0].codec_context.extradata[1:4].hex()
        for packet in src.demux(video=0):
            if packet.pts is not None:
                packet.stream = stream
                dst.mux(packet)
    assert read_pictures(output) == read_pictures(source)
    return f'video/mp4; codecs="{codec}"'


@pytest.mark.parametrize("rate,gop", [(.25, 1), (1, 30), (8, 30)])
def test_bounded_source_loop_probe(tmp_path, rate, gop):
    replay = selection_media(tmp_path / "fragment.mp4", gop=gop)
    mime = fragment_packets(tmp_path / "fragment.mp4", tmp_path / "bounded.mp4")
    replay["review_media"] = {"url": "/review-fragments.mp4", "mime_type": mime,
                              "video_timescale": 90000,
                              "timestamp_offset_s": 0, "visible_start_s": 0, "visible_end_s": 4}
    inputs = {"frames": 120, "attempt_frames": 120, "lead_frames": 0, "fps": 30,
              "stretches": [[0, 1000, 120]], "buttons": [], "stick_max": 84,
              "dead_zone": 8, "angle_units": 65536, "actions": [], "markers": [],
              "runs": [{"start": 0, "length": 120, "buttons": 0, "stick_x": 0, "stick_y": 0,
                        "yaw": 0, "speed": 0}]}
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/review-fragments.mp4", lambda route: route.fulfill(
                content_type="video/mp4", body=(tmp_path / "bounded.mp4").read_bytes()))
            page.route("**/api/attempts/*/replay", lambda route: route.fulfill(json=replay))
            page.route("**/api/attempts/*/inputs*", lambda route: route.fulfill(json=inputs))
            page.route("**/api/attempts/*/replay/review-state", lambda route: route.fulfill(json={
                "loop": {"start": .3, "end": .6, "enabled": True}, "zoom": None, "template_offsets": {}}))
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            page.evaluate(STORY.setup)
            page.wait_for_selector(".input-inspector")
            page.wait_for_selector('.replay-loop-row button[aria-pressed="true"]')
            # Refuse an accidentally served sibling checkout or native fallback.
            page.wait_for_function("document.querySelector('.attempt-drawer video').duration < .601")
            result = page.locator(".attempt-drawer video").evaluate("""async (video, rate) => {
              video.pause();
              video.currentTime = .3;
              await Promise.race([
                new Promise(resolve => video.addEventListener('seeked', resolve, {once:true})),
                new Promise((_, reject) => setTimeout(() => reject(Error('seek timed out')), 3000))]);
              const canvas = document.createElement('canvas'); canvas.width = 320; canvas.height = 96;
              const ctx = canvas.getContext('2d', {willReadFrequently:true});
              const frames = []; let handle;
              const read = (_now, meta) => {
                ctx.drawImage(video, 0, 0, 320, 96);
                const id = Array.from({length:8}, (_,bit) =>
                  ctx.getImageData(bit*40+20,48,1,1).data[0] > 128 ? 1<<bit : 0).reduce((a,b)=>a+b,0);
                frames.push({id, time:meta.mediaTime, current:video.currentTime});
                handle = video.requestVideoFrameCallback(read);
              };
              handle = video.requestVideoFrameCallback(read);
              video.playbackRate = rate;
              await video.play();
              // At least the original window, then until two wraps are seen: a
              // fixed wall-clock window under-counts at 8x on a loaded machine
              // (one wrap observed during the full gate, and on main).
              const wraps = () => frames.slice(1).filter((b, i) => b.time < frames[i].time).length;
              const minimum = performance.now() + 1000 / rate + 300, deadline = minimum + 3000;
              while ((performance.now() < minimum || wraps() < 2) && performance.now() < deadline)
                await new Promise(resolve => setTimeout(resolve, 50));
              const result = {frames, paused:video.paused, time:video.currentTime, duration:video.duration,
                source:video.currentSrc.slice(0,80), notice:document.querySelector('.replay-control-error')?.textContent,
                bounded:(await import('/ui/reviewsource.js')).hasBoundedReview(video)};
              video.pause(); video.cancelVideoFrameCallback(handle);
              return result;
            }""", rate)
            (tmp_path / "bounded-probe.json").write_text(json.dumps(result), encoding="utf-8")
            print(f"Bounded source rate={rate}: {result}; evidence {tmp_path}")
            assert errors == []
            assert result["bounded"] and result["source"].startswith("blob:")
            assert result["duration"] == pytest.approx(.6, abs=.000002)
            assert result["frames"]
            assert all(9 <= frame["id"] < 18 for frame in result["frames"])
            assert sum(b["time"] < a["time"] for a, b in zip(result["frames"], result["frames"][1:], strict=False)) >= 2
            if rate == 1:
                check_paused_controls(page, tmp_path)
        finally:
            browser.close()


def check_paused_controls(page, tmp_path):
    video = page.locator('.attempt-drawer video')
    slider = page.get_by_role('slider', name='Seek recording')
    assert slider.get_attribute('max') == '4'
    # Place the paused review on Out's last picture, then drive real controls.
    video.evaluate("""async video => {
      const source = await import('/ui/reviewsource.js');
      source.pauseReviewSource(video); source.seekReviewSource(video, 17.5 / 30);
    }""")
    page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('17 /')")
    page.get_by_role('button', name='Forward 1', exact=True).click()
    page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('18 /')")
    assert video.evaluate('video => video.paused')
    assert video.evaluate('video => video.duration') == pytest.approx(4, abs=.000002)
    assert video.evaluate("""video => {
      const canvas = document.createElement('canvas'); canvas.width=320; canvas.height=96;
      const ctx = canvas.getContext('2d'); ctx.drawImage(video,0,0,320,96);
      return Array.from({length:8},(_,bit)=>ctx.getImageData(bit*40+20,48,1,1).data[0]>128?1<<bit:0)
        .reduce((a,b)=>a+b,0);
    }""") == 18
    page.get_by_role('button', name='Back 1', exact=True).click()
    page.wait_for_function("document.querySelector('.input-inspector-frame strong').textContent.startsWith('17 /')")
    video.click()
    page.wait_for_function("!document.querySelector('.attempt-drawer video').paused && document.querySelector('.attempt-drawer video').duration < .601")
    video.focus()
    page.keyboard.press('k')
    page.keyboard.press('x')
    page.wait_for_function("document.querySelector('.attempt-drawer video').duration > 3.99")
    assert video.evaluate('video => video.paused')
    assert page.locator('.replay-loop-row .replay-media-time').all_text_contents() == ['—', '—']
    for width in [1500, 850]:
        page.set_viewport_size({'width':width, 'height':1100})
        page.locator('.attempt-drawer').screenshot(path=str(tmp_path / f'bounded-controls-{width}.png'))
    assert video.evaluate('video => video.paused')
    assert page.get_by_role('button', name='Play', exact=True).count() == 1
