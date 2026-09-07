"""Two real drawers retain independent keyboard and discrepancy navigation."""
import base64
import json
from pathlib import Path

from test_ui_replay_picture_steps import PROJECT, STORY, get_driver, tiny_video


def test_two_drawers_navigate_the_reported_picture_and_show_attempt_times(tmp_path):
    import sm64_events
    root = Path(__file__).resolve().parents[1]
    assert Path(sm64_events.__file__).resolve().is_relative_to(root / "src")
    path = tmp_path / "pictures.mp4"
    times, numbers = tiny_video(path)
    replay = {"clip_url": "data:video/mp4;base64," + base64.b64encode(path.read_bytes()).decode(),
              "frame_times": [round(t, 6) for t in times], "picture_ids": numbers,
              "frame_map": [1000,1001,1002,50,50,51,52,53],
              "picture_igt": [0,0,0,0,0,1,2,3], "frame_map_source": "plugin",
              "duration_s": times[-1] + 1/30, "fps": 30, "game_fps": 30,
              "anchor_offset_s": 0, "attempt_start_slot": 3,
              "source": "buffer", "truncated": False,
              "pad_stamp_agreement": {"pictures": 7,"agree": 6,
                  "disagreements": [[5,51,[0,0,32768],[0,0,16384]]]}}
    inputs = {"fps":30,"frames":7,"lead_frames":3,"attempt_frames":4,
              "kind":"star","entity_key":"2-2","target":"star 2 2",
              "stretches":[[0,1000,3],[3,50,4]],
              "buttons":[[32768,"A"],[16384,"B"]],"stick_max":84,"dead_zone":8,
              "angle_units":65536,
              "runs":[{"start":n,"length":1,"buttons":32768 if n != 4 else 16384,
                       "stick_x":40,"stick_y":80,"yaw":0,"speed":5} for n in range(7)],
              "actions":[{"start":4,"length":1,"action":1,"label":"Jump","group":"airborne"}],
              "markers":[{"frame":4,"label":"Grabbed the pole beside the haunted balcony","type":"pole"}],
              "template":None}
    evidence = root / ".iteration" / "input-wrap-ui"
    evidence.mkdir(parents=True, exist_ok=True)
    with PROJECT.open() as url, get_driver().launch(headless=True, viewport=(1500,1100)) as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        page.evaluate(r"""(() => {
          const original = window.fetch;
          window.fetch = (url, options) => {
            const input = /\/api\/attempts\/(\d+)\/inputs(?:\?|$)/.exec(String(url));
            const replay = /\/api\/attempts\/\d+\/replay$/.test(String(url));
            const data = input ? {...INPUTS, attempt_id:Number(input[1])} : replay ? REPLAY : null;
            return data ? Promise.resolve(new Response(JSON.stringify(data), {status:200,
              headers:{'Content-Type':'application/json'}})) : original(url,options);
          };
        })()""".replace("INPUTS",json.dumps(inputs)).replace("REPLAY",json.dumps(replay)))
        page.evaluate(STORY.setup)
        page.evaluate("document.querySelectorAll('.attempt-actions button[aria-label=\"View replay\"]')[1].click()")
        page.wait_for("body:has(.attempt-drawer:nth-of-type(1))")
        assert page.evaluate("""(async () => {
          for (let n=0;n<100;n++) {
            if (document.querySelectorAll('.attempt-drawer .input-inspector').length===2) return true;
            await new Promise(resolve=>setTimeout(resolve,30));
          } return false;
        })()""")
        page.evaluate("""(async () => {
          const videos=[...document.querySelectorAll('.attempt-drawer video')];
          window.drawers=[...document.querySelectorAll('.attempt-drawer')];
          drawers.forEach((drawer,n)=>drawer.dataset.testPlayer=String(n));
          const {watchVideoPicture}=await import('/ui/videopicture.js');
          for (const video of videos) {
            if (video.readyState<2) await new Promise(resolve=>video.addEventListener('loadeddata',resolve,{once:true}));
            video.pause();
            const canvas=document.createElement('canvas'); canvas.width=320;canvas.height=96;
            const context=canvas.getContext('2d',{willReadFrequently:true});
            watchVideoPicture(video,time=>{
              if (time==null) return;
              context.drawImage(video,0,0,320,96);
              video.dataset.picture=String(Array.from({length:8},(_,bit)=>
                context.getImageData(bit*40+20,48,1,1).data[0]>128?1<<bit:0).reduce((a,b)=>a+b,0));
            });
            video.currentTime=.15;
          }
          window.arrow=(key)=>{
            window.dispatchEvent(new KeyboardEvent('keydown',{key,bubbles:true,cancelable:true}));
            window.dispatchEvent(new KeyboardEvent('keyup',{key,bubbles:true}));
          };
        })()""")
        first = '.attempt-drawer[data-test-player="0"]'
        second = '.attempt-drawer[data-test-player="1"]'
        page.wait_for(f'{first} video[data-picture="3"]')
        page.wait_for(f'{second} video[data-picture="3"]')
        page.evaluate("drawers[0].querySelector('.replay-transport button').focus(); arrow('ArrowRight')")
        page.wait_for(f'{first} video[data-picture="4"]')
        assert page.count(f'{second} video[data-picture="3"]') == 1
        page.evaluate("drawers[1].querySelector('.replay-transport button').focus(); arrow('ArrowLeft')")
        page.wait_for(f'{second} video[data-picture="2"]')
        assert page.count(f'{first} video[data-picture="4"]') == 1
        page.evaluate("arrow('ArrowDown')")
        page.wait_for(f'{second} video[data-picture="3"]')
        # The input bar after the reset must find raw 51, not the earlier 1000.
        page.click(f'{first} button:text-is("Back 1")')
        page.wait_for(f'{first} video[data-picture="3"]')
        page.click(f'{first} button.input-bar[title^=\'B 00"03\']')
        page.wait_for(f'{first} video[data-picture="4"]')
        # Move away, then follow the discrepancy's decoded picture slot.
        page.click(f'{first} button:text-is("Back 1")')
        page.wait_for(f'{first} video[data-picture="3"]')
        page.click(f'{first} .input-screen-check')
        page.click(f'{first} .input-screen-check-row')
        page.wait_for(f'{first} video[data-picture="4"]')
        assert "game neutral · B" in page.evaluate(f"document.querySelector({json.dumps(first+' .input-screen-check-row')}).textContent")
        assert "timeline neutral · A" in page.evaluate(f"document.querySelector({json.dumps(first+' .input-screen-check-row')}).textContent")
        assert "frame 1" in page.evaluate(f"document.querySelector({json.dumps(first+' .input-screen-check-row')}).textContent")
        assert 'at 00"03' in page.evaluate(f"document.querySelector({json.dumps(first+' .input-inspector-moment')}).textContent")
        for width in (1500,850):
            page.set_viewport(width,1100)
            page.evaluate("drawers[0].scrollIntoView({block:'start'})")
            page.wait_ms(100)
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            (evidence/f"replay-navigation-{width}.png").write_bytes(page.screenshot())
            page.evaluate("drawers[0].querySelector('.input-timeline').scrollIntoView({block:'center'})")
            (evidence/f"replay-inspector-{width}.png").write_bytes(page.screenshot())
        assert page.problems() == []
    print(f"Replay navigation browser evidence: {evidence}")
