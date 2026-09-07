"""Opening and restarting the real drawer target the attempt, retaining buffers."""
import base64
import json

from test_ui_replay_picture_steps import tiny_video, PROJECT, STORY, get_driver
from sm64_events.replay.navigation import captured_input_span, attempt_start_slot


def test_drawer_opens_and_restarts_at_the_attempt_with_buffer_access(tmp_path):
    path = tmp_path / "start.mp4"
    times, numbers = tiny_video(path)
    replay = {"clip_url": "data:video/mp4;base64," + base64.b64encode(path.read_bytes()).decode(),
              "frame_times": [round(t, 6) for t in times], "picture_ids": numbers,
              "frame_map": [97,98,99,100,100,101,102,103],
              "picture_igt": [97,98,99,0,0,1,2,3], "frame_map_source": "plugin",
              "duration_s": times[-1] + 1/30, "fps": 30, "game_fps": 30,
              "anchor_offset_s": .09, "source": "buffer", "truncated": False}
    replay.update(input_span=captured_input_span(replay),
                  attempt_start_slot=attempt_start_slot(replay, 100))
    inputs = {"attempt_id":42, "fps":30, "frames":7, "attempt_frames":3,
              "lead_frames":3, "stretches":[[0,97,7]], "buttons":[[32768,"A"]],
              "stick_max":84, "dead_zone":8, "angle_units":65536,
              "actions":[], "markers":[], "template":None,
              "runs":[{"start":0,"length":7,"buttons":0,"stick_x":0,
                       "stick_y":0,"yaw":0,"speed":0}]}
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
          window.initialPlayPositions=[];
          HTMLMediaElement.prototype.play=function() {
            window.initialPlayPositions.push(this.currentTime);
            // Let the real decoder present the initial picture, then hold it
            // for inspection. The test never supplies an initial seek itself.
            return play.call(this).then(()=>this.pause());
          };
        })()""".replace("REPLAY",json.dumps(replay)).replace("INPUTS",json.dumps(inputs)))
        page.evaluate(STORY.setup)
        page.wait_for('.input-inspector-frame .is-stamped:text-is(\'00"00\')')
        positions = page.evaluate("window.initialPlayPositions")
        assert len(positions) == 1 and times[3] < positions[0] < times[4], positions
        page.evaluate("""(() => {
          const video=document.querySelector('.attempt-drawer video');
          window.readPicture=()=>{
            const canvas=document.createElement('canvas'); canvas.width=320; canvas.height=96;
            const ctx=canvas.getContext('2d',{willReadFrequently:true});
            ctx.drawImage(video,0,0,320,96);
            return Array.from({length:8},(_,bit)=>ctx.getImageData(bit*40+20,48,1,1).data[0]>128?1<<bit:0)
              .reduce((a,b)=>a+b,0);
          };
        })()""")
        assert page.evaluate("window.readPicture()") == 3
        page.click('.attempt-drawer button:text-is("Forward 1")')
        page.wait_for('.input-inspector-frame .is-stamped:text-is(\'00"03\')')
        page.click('.attempt-drawer button:text-is("Start")')
        page.wait_for('.input-inspector-frame .is-stamped:text-is(\'00"00\')')
        assert page.evaluate("window.readPicture()") == 3
        page.click('.attempt-drawer button:text-is("Forward 1")')
        page.wait_for('.input-inspector-frame .is-stamped:text-is(\'00"03\')')
        # Typing controls and modified shortcuts must not restart the video.
        for tag in ["input", "textarea", "select", "div"]:
            prevented = page.evaluate("""(() => {
              const node=document.createElement(TAG);
              if(TAG==='div') node.contentEditable='true';
              document.body.append(node);
              const event=new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true,cancelable:true});
              node.dispatchEvent(event); node.remove(); return event.defaultPrevented;
            })()""".replace("TAG",json.dumps(tag)))
            assert prevented is False
        assert page.evaluate("window.readPicture()") == 4
        for modifier in ["ctrlKey", "metaKey", "altKey"]:
            assert page.evaluate("""(() => {
              const event=new KeyboardEvent('keydown',{key:'ArrowDown',MODIFIER:true,cancelable:true});
              window.dispatchEvent(event);return event.defaultPrevented;
            })()""".replace("MODIFIER",modifier)) is False
        page.evaluate("window.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown'}))")
        page.wait_for('.input-inspector-frame .is-stamped:text-is(\'00"00\')')
        assert page.evaluate("window.readPicture()") == 3
        page.click('.attempt-drawer button:text-is("Back 1")')
        page.wait_for('.input-inspector-frame .is-stamped:text-is(\'03"30\')')
        assert page.evaluate("window.readPicture()") == 2
        assert page.evaluate("document.querySelector('.attempt-drawer video').paused")
        assert len(page.evaluate("window.initialPlayPositions")) == 1
        for width in [1500,850]:
            page.set_viewport(width,1100)
            page.evaluate("document.querySelector('.replay-transport').scrollIntoView({block:'center'})")
            (tmp_path/f"restart-{width}.png").write_bytes(page.screenshot())
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.problems() == []
    print(f"Replay navigation browser evidence: {tmp_path}")
