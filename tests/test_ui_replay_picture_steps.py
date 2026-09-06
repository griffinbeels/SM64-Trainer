"""Real drawer controls must land on independently decoded video pictures.

The scratch app supplies its normal practice workflow. Only the replay/input
responses are replaced with this small, known video; no recorder is started.
"""
import base64
from fractions import Fraction
import json
from pathlib import Path
import sys

import av
import pytest

from test_replay_picture_identity import picture, read_pictures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver
from uilab_project import PROJECT, STORIES

STORY = next(story for story in STORIES if story.name == "input-timeline")


def tiny_video(path):
    numbers = [0, 1, 2, 3, 3, 4, 5, 6]
    ticks = [0, 1, 2, 9000, 18000, 18001, 27000, 36000]
    with av.open(str(path), "w", options={"movie_timescale": "90000"}) as container:
        stream = container.add_stream("libx264", rate=30)
        stream.width, stream.height, stream.pix_fmt = 320, 96, "yuv420p"
        stream.time_base = stream.codec_context.time_base = Fraction(1, 90000)
        stream.options = {"crf": "12", "preset": "ultrafast", "bf": "0", "g": "1"}
        for number, tick in zip(numbers, ticks, strict=True):
            frame = av.VideoFrame.from_ndarray(picture(number), format="bgra")
            frame.pts, frame.time_base = tick, Fraction(1, 90000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    decoded = read_pictures(path)
    assert [number for _, number in decoded] == numbers
    assert [round(t * 90000) for t, _ in decoded] == ticks
    return [t for t, _ in decoded], numbers


def test_drawer_steps_decoded_pictures_and_reads_the_presented_timer(tmp_path):
    path = tmp_path / "pictures.mp4"
    times, numbers = tiny_video(path)
    replay = {"clip_url": "data:video/mp4;base64," + base64.b64encode(path.read_bytes()).decode(),
              "frame_times": [round(t, 6) for t in times], "picture_ids": numbers,
              "frame_map": [100,101,99,100,100,101,102,103],
              "picture_igt": [50,51,2,3,3,4,5,6], "frame_map_source": "plugin",
              "duration_s": times[-1] + 1/30, "fps": 30, "game_fps": 30,
              "anchor_offset_s": 0, "source": "buffer", "truncated": False}
    with PROJECT.open() as url, get_driver().launch(viewport=(1500,1100)) as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        # Retain the real input API and every app workflow. This boundary's
        # test varies encoded picture timing independently of the input store.
        page.evaluate(r"""(() => {
          const original = window.fetch;
          window.fetch = (url, options) => /\/api\/attempts\/\d+\/replay$/.test(String(url))
            ? Promise.resolve(new Response(JSON.stringify(REPLAY), {status:200,
                headers:{'Content-Type':'application/json'}})) : original(url, options);
        })()""".replace("REPLAY", json.dumps(replay)))
        page.evaluate(STORY.setup)
        page.wait_for(".attempt-drawer video")
        page.wait_for(".input-inspector")
        page.evaluate("""(async () => {
          const video = document.querySelector('.attempt-drawer video');
          if (video.readyState < 2) await new Promise(resolve =>
            video.addEventListener('loadeddata', resolve, {once:true}));
          video.pause();
          const canvas = document.createElement('canvas');
          canvas.width=320; canvas.height=96;
          const ctx = canvas.getContext('2d', {willReadFrequently:true});
          const read = () => {
            ctx.drawImage(video,0,0,320,96);
            return Array.from({length:8}, (_,bit) =>
              ctx.getImageData(bit*40+20,48,1,1).data[0]>128 ? 1<<bit : 0)
              .reduce((a,b)=>a+b,0);
          };
          window.readReplayPicture = read;
          const shown = (_now, meta) => {
            video.dataset.picture = String(read());
            video.dataset.presented = String(meta.mediaTime);
            video.requestVideoFrameCallback(shown);
          };
          video.requestVideoFrameCallback(shown);
          await new Promise(resolve => {
            video.addEventListener('seeked', resolve, {once:true});
            video.currentTime=.000005;
          });
          video.dataset.picture = String(read());
        })()""")
        page.wait_for('.attempt-drawer video[data-picture="0"]')
        recorded = []
        expected_times = ['01"66', '01"70', '00"06', '00"10', '00"13', '00"16', '00"20']
        for direction, expected in [(1,n) for n in range(1,7)] + [(1,6)] + [(-1,n) for n in range(5,-1,-1)] + [(-1,0)]:
            label = "Forward 1" if direction > 0 else "Back 1"
            page.click(f'.attempt-drawer button:text-is("{label}")')
            page.wait_for(f'.attempt-drawer video[data-picture="{expected}"]', timeout_ms=3000)
            actual = page.evaluate("window.readReplayPicture()")
            assert actual == expected
            page.wait_for(f'.input-inspector-frame .is-stamped:text-is(\'{expected_times[expected]}\')')
            recorded.append(actual)
        # The repeated raw 100 must display this picture's timer, never the
        # earlier occurrence's 50 frames. Exercise the actual control again.
        for expected in [1,2,3]:
            page.click('.attempt-drawer button:text-is("Forward 1")')
            page.wait_for(f'.attempt-drawer video[data-picture="{expected}"]')
        page.wait_for('.input-inspector-frame .is-stamped:text-is(\'00"10\')')
        for width in [1500,850]:
            page.set_viewport(width,1100)
            page.evaluate("document.querySelector('.attempt-drawer').scrollIntoView()")
            (tmp_path / f"drawer-{width}.png").write_bytes(page.screenshot())
            page.evaluate("document.querySelector('.input-inspector').scrollIntoView({block:'center'})")
            (tmp_path / f"inspector-{width}.png").write_bytes(page.screenshot())
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.problems() == []
        (tmp_path / "browser-pictures.json").write_text(json.dumps(recorded), encoding="utf-8")
    print(f"Rendered replay evidence: {tmp_path}")
