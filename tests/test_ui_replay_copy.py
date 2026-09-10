"""A native packet-copy cut hides decoder pre-roll in the actual browser."""
import base64
from datetime import datetime, timedelta, timezone
import shutil
import subprocess

import av
from playwright.sync_api import sync_playwright

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.extract import ClipExtractor
from sm64_events.replay.media import MediaRun
from sm64_events.replay.ring import SegmentInfo, SegmentRing
from test_replay_picture_identity import read_pictures
from test_ui_review_selection import selection_media
from test_ui_replay_picture_steps import PROJECT, STORY


def copied_media(tmp_path):
    source = tmp_path / "source.mp4"
    selection_media(source, gop=60)
    transport = tmp_path / "source.ts"
    ffmpeg = shutil.which("ffmpeg")
    subprocess.run([ffmpeg, "-v", "error", "-i", str(source), "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo", "-c:v", "copy", "-c:a", "aac",
                    "-t", "4", "-muxdelay", "0", "-f", "mpegts", str(transport)],
                   check=True, capture_output=True, timeout=20, **quiet_spawn_kwargs())
    original = read_pictures(transport)
    origin = datetime(2026, 9, 8, tzinfo=timezone.utc)
    ring = SegmentRing(None, 1 << 30)
    run = MediaRun("browser-copy", origin.timestamp())
    start = origin + timedelta(seconds=original[0][0])
    ring.add(SegmentInfo(transport, "video", start, start + timedelta(seconds=4),
                         transport.stat().st_size, (320, 96), run))
    result = ClipExtractor(ReplayConfig(), "libx264", ffmpeg).extract(
        ring, start + timedelta(seconds=1.24), start + timedelta(seconds=3.4), tmp_path / "cut.mp4")
    decoded = read_pictures(result.path)
    assert decoded[0][1] == 37
    with av.open(str(result.path)) as container:
        assert any(p.pts is not None and p.pts < 0 and p.is_discard
                   for p in container.demux(video=0)), "fixture must contain hidden decoder pre-roll"
    return {"clip_url": "data:video/mp4;base64," + base64.b64encode(result.path.read_bytes()).decode(),
            "duration_s": result.duration_s, "frame_times": result.frame_times,
            "frame_map": [100 + n for _, n in decoded], "picture_ids": [n for _, n in decoded],
            "picture_igt": [n for _, n in decoded], "frame_map_source": "plugin",
            "fps": 30, "game_fps": 30, "source": "buffer"}


def test_native_copy_hides_preroll_and_keeps_browser_picture_identity(tmp_path):
    replay = copied_media(tmp_path)
    with PROJECT.open() as url, sync_playwright() as play:
        browser = play.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1100})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route("**/api/attempts/*/replay", lambda route: route.fulfill(json=replay))
            page.goto(url)
            page.wait_for_selector(PROJECT.ready_selector)
            page.evaluate(STORY.setup)
            page.wait_for_selector(".input-inspector")
            video = page.locator(".attempt-drawer video")
            page.wait_for_function("document.querySelector('.attempt-drawer video').readyState >= 2")
            for slot in [0, 1, 20, 45, 0]:
                target = (replay["frame_times"][slot] + replay["frame_times"][slot + 1]) / 2
                video.evaluate("(v,t) => {v.pause(); v.currentTime=t;}", target)
                expected = replay["picture_ids"][slot]
                page.wait_for_function("""n => {
                    const v=document.querySelector('.attempt-drawer video');
                    if(v.seeking) return false;
                    const c=document.createElement('canvas'); c.width=320; c.height=96;
                    const ctx=c.getContext('2d',{willReadFrequently:true}); ctx.drawImage(v,0,0,320,96);
                    return Array.from({length:8},(_,bit)=>ctx.getImageData(bit*40+20,48,1,1).data[0]>128?1<<bit:0)
                        .reduce((a,b)=>a+b,0)===n;
                }""", arg=expected)
                page.wait_for_function("text => document.querySelector('.input-inspector-frame .meta').textContent.trim()===text",
                                       arg=f'{expected // 30:02d}"{expected % 30 * 100 // 30:02d}')
            page.screenshot(path=str(tmp_path / "native-copy.png"))
            assert errors == [], errors
            print(f"Native packet-copy browser evidence: {tmp_path}")
        finally:
            browser.close()
