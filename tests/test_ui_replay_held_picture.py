"""The real drawer can seek throughout a cut containing one held picture."""
import base64
from datetime import datetime, timezone
import json

import pytest

from sm64_events.replay.extract import ClipExtractor
from test_replay_held_picture import held_capture as held_capture
from test_replay_picture_identity import encoder as encoder
from test_replay_service import attempt, make_service
from test_ui_replay_picture_steps import PROJECT, STORY, get_driver


@pytest.mark.parametrize("encoder", ["libx264"], indirect=True)
def test_drawer_keeps_the_held_picture_visible_through_the_cut(tmp_path, encoder, held_capture):
    ffmpeg, codec = encoder
    cfg, ring, ledger, repeats, _, _ = held_capture
    start, end = repeats[1]["at"] + .2, repeats[1]["at"] + .7
    result = ClipExtractor(cfg, codec, ffmpeg).extract(ring,
        datetime.fromtimestamp(start, timezone.utc), datetime.fromtimestamp(end, timezone.utc),
        tmp_path / "held.mp4")
    service = make_service(tmp_path / "service", [attempt()])
    service.recorder.ledger = ledger
    meta = {"start_utc": result.start_utc.isoformat(), "frame_times": result.frame_times}
    service._map_from_feeds(meta, result)
    replay = {**service._validated_meta(meta, attempt()),
              "clip_url": "data:video/mp4;base64," + base64.b64encode(result.path.read_bytes()).decode(),
              "duration_s": result.duration_s, "fps": 60, "game_fps": 30,
              "anchor_offset_s": 0, "source": "buffer", "truncated": False}
    with PROJECT.open() as url, get_driver().launch(viewport=(1500,1100)) as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        page.evaluate(r"""(() => {
          const original = window.fetch;
          window.fetch = (url, options) => /\/api\/attempts\/\d+\/replay$/.test(String(url))
            ? Promise.resolve(new Response(JSON.stringify(REPLAY), {status:200,
                headers:{'Content-Type':'application/json'}})) : original(url, options);
        })()""".replace("REPLAY", json.dumps(replay)))
        page.evaluate(STORY.setup)
        page.wait_for(".attempt-drawer video")
        page.wait_for(".input-inspector")
        samples = page.evaluate("""(async () => {
          const video = document.querySelector('.attempt-drawer video');
          if (video.readyState < 2) await new Promise(resolve =>
            video.addEventListener('loadeddata', resolve, {once:true}));
          video.pause();
          const canvas = document.createElement('canvas');
          canvas.width=320; canvas.height=96;
          const ctx = canvas.getContext('2d', {willReadFrequently:true});
          const pictures=[];
          for (const fraction of [.01,.5,.99]) {
            await new Promise(resolve => {
              video.addEventListener('seeked', resolve, {once:true});
              video.currentTime=video.duration*fraction;
            });
            ctx.drawImage(video,0,0,320,96);
            pictures.push(Array.from({length:8}, (_,bit) =>
              ctx.getImageData(bit*40+20,48,1,1).data[0]>128 ? 1<<bit : 0)
              .reduce((a,b)=>a+b,0));
          }
          return {duration:video.duration,pictures};
        })()""")
        assert samples["pictures"] == [1,1,1]
        assert abs(samples["duration"] - result.duration_s) < .03
        for width in [1500,850]:
            page.set_viewport(width,1100)
            page.evaluate("document.querySelector('.attempt-drawer').scrollIntoView()")
            (tmp_path / f"held-drawer-{width}.png").write_bytes(page.screenshot())
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.problems() == []
        (tmp_path / "held-browser.json").write_text(json.dumps(samples), encoding="utf-8")
    print(f"Rendered held-picture evidence: {tmp_path}")
