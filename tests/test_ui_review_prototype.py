"""Exercise the actual review entry and transport with independently decoded pictures."""
import base64
import json

import pytest

from frontend_runner import run_frontend
from test_ui_replay_picture_steps import tiny_video, PROJECT, STORY, get_driver


def test_review_component_contracts():
    run_frontend("reviewmedia.test.js", "reviewstate.test.js", "latestreview.test.js", "replaykeys.test.js", "replayfocus.test.js")


@pytest.fixture
def review_page(tmp_path):
    path = tmp_path / "review.mp4"
    times, numbers = tiny_video(path)
    # Match frame_times_of's six-decimal wire format. Extra fixture precision
    # would place a 90 kHz slot just after Chromium's delivered mediaTime.
    replay = {"clip_url": "data:video/mp4;base64," + base64.b64encode(path.read_bytes()).decode(),
              "frame_times": [round(t, 6) for t in times], "picture_ids": numbers,
              "frame_map": list(range(100, 108)), "picture_igt": list(range(8)),
              "frame_map_source": "plugin", "duration_s": times[-1] + 1/30,
              "fps": 30, "game_fps": 30, "anchor_offset_s": .1,
              "attempt_start_slot": 3, "source": "buffer", "truncated": False}
    run = {"start": 0, "length": 8, "buttons": 32768, "stick_x": 0,
           "stick_y": 0, "yaw": 0, "speed": 0}
    inputs = {"fps": 30, "frames": 8, "attempt_frames": 8, "lead_frames": 0,
              "stretches": [[0,100,8]], "buttons": [[32768,"A"]],
              "stick_max": 84, "dead_zone": 8, "angle_units": 65536,
              "actions": [], "markers": [], "runs": [run],
              "template": {"id": 1, "name": "Later sequence from a longer example",
                           "frames": 40, "runs": [], "actions": [],
                           "source": {"frames": 40, "revision": "a" * 64,
                                      "runs": [{**run,"start":20,"length":8}], "actions": []}}}
    with PROJECT.open() as url, get_driver().launch(viewport=(1500,1100)) as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        page.evaluate(r"""(() => {
          const original=window.fetch, replay=REPLAY, inputs=INPUTS;
          window.fetch=(url, options) => {
            const path=String(url);
            const payload=/\/api\/attempts\/\d+\/replay$/.test(path) ? replay
              : /\/api\/attempts\/\d+\/inputs(?:\?|$)/.test(path) ? inputs : null;
            return payload ? Promise.resolve(new Response(JSON.stringify(payload),
              {status:200,headers:{'Content-Type':'application/json'}})) : original(url,options);
          };
          const play=HTMLMediaElement.prototype.play;
          HTMLMediaElement.prototype.play=function() {
            return play.call(this).then(()=>{if(!window.allowLoopPlayback)this.pause();});
          };
          const request=HTMLVideoElement.prototype.requestVideoFrameCallback;
          HTMLVideoElement.prototype.requestVideoFrameCallback=function(callback) {
            return request.call(this,(now,meta)=>{
              window.loopObserver?.(now,meta);
              callback(now,meta);
            });
          };
        })()""".replace("REPLAY",json.dumps(replay)).replace("INPUTS",json.dumps(inputs)))
        page.evaluate(STORY.setup)
        page.wait_for(".attempt-drawer .input-inspector")
        page.wait_for('.replay-loop-row button:text-is("Set In"):not([disabled])')
        page.click('.replay-transport button:text-is("Start")')
        page.wait_for('.input-inspector-frame .is-stamped:text-is(\'00"10\')')
        assert page.evaluate("document.querySelector('.attempt-drawer video').controls") is False
        page.click('.replay-loop-row button:text-is("Set In")')
        page.click('.replay-transport button:text-is("Forward 1")')
        page.wait_for('.input-inspector-frame .is-stamped:text-is(\'00"16\')')
        page.click('.replay-loop-row button:text-is("Set Out")')
        page.wait_for('.replay-loop-row button:text-is("Loop"):not([disabled])')
        # Completing In/Out enables looping automatically.
        page.wait_for('.replay-loop-row button[aria-pressed="true"]:text-is("Loop")')
        (tmp_path / "loop-bounds.json").write_text(json.dumps(page.evaluate(
            "Array.from(document.querySelectorAll('.replay-loop-row .replay-media-time'), e=>e.textContent)")))
        yield page


def test_native_loop_never_presents_a_picture_beyond_out(review_page, tmp_path):
    page = review_page
    loop_frames = page.evaluate("""(async () => {
      const video=document.querySelector('.attempt-drawer video');
      const frames=[], seen=new Set();
      window.loopEvents=[];
      for (const name of ['play','playing','pause','seeking','seeked','waiting','timeupdate','ended'])
        video.addEventListener(name,()=>window.loopEvents.push({name,time:video.currentTime,
          wall:performance.now(),paused:video.paused,seeking:video.seeking}));
      const canvas=document.createElement('canvas');canvas.width=320;canvas.height=96;
      const ctx=canvas.getContext('2d',{willReadFrequently:true});
      const read=(_now,meta)=>{
        if(seen.has(meta.presentedFrames))return;
        seen.add(meta.presentedFrames);
        ctx.drawImage(video,0,0,320,96);
        const number=Array.from({length:8},(_,bit)=>
          ctx.getImageData(bit*40+20,48,1,1).data[0]>128 ? 1<<bit : 0).reduce((a,b)=>a+b,0);
        frames.push({time:meta.mediaTime,number,current:video.currentTime,wall:performance.now(),seeking:video.seeking});
      };
      // Read before the application callback can seek away from this picture.
      window.loopObserver=read;
      window.allowLoopPlayback=true;video.playbackRate=.25;
      await video.play();await new Promise(resolve=>setTimeout(resolve,2100));video.pause();
      window.loopObserver=null;return frames;
    })()""")
    (tmp_path / "loop-pictures.json").write_text(json.dumps(loop_frames))
    (tmp_path / "loop-events.json").write_text(json.dumps(page.evaluate("window.loopEvents")))
    assert sum(frame["number"] == 3 for frame in loop_frames) >= 2, loop_frames
    assert all(frame["number"] in (3,4) for frame in loop_frames), loop_frames


def test_drawer_review_controls_and_reopening(review_page, tmp_path):
    page = review_page
    page.click('.input-zoom-controls button:text-is("Zoom to loop")')
    page.click('button[aria-label="Shift template earlier one frame"]')
    page.wait_for('.input-template-offset:text-is("-1f")')
    # Controls sit below the whole picture, including its bottom HUD.
    assert page.evaluate("""(() => {
      const video=document.querySelector('.attempt-drawer video').getBoundingClientRect();
      const controls=document.querySelector('.attempt-drawer .replay-controls').getBoundingClientRect();
      return controls.top >= video.bottom;
    })()""")
    for width,height in [(1500,1100),(850,900),(850,540)]:
        page.set_viewport(width,height)
        (tmp_path/f"review-{width}-{height}.png").write_bytes(page.screenshot())
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate("""Array.from(document.querySelectorAll('.attempt-drawer button'))
          .every(button => {const r=button.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth;})""")
    page.click('.attempt-actions button[aria-label="View replay"][aria-expanded="true"]')
    assert page.count('.attempt-drawer') == 0
    page.evaluate(STORY.setup)
    page.wait_for('.input-template-offset:text-is("-1f")')
    page.wait_for('.replay-loop-row button[aria-pressed="true"]:text-is("Loop")')
    assert page.problems() == []
    print(f"Review prototype browser evidence: {tmp_path}")
