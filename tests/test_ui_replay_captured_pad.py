"""The actual drawer reads the presented capture, including repeated counters."""
import base64
import json
from fractions import Fraction

import av

from test_ui_replay_picture_steps import PROJECT, STORY, get_driver, picture, read_pictures


def captured_video(path):
    # Ordinary 100ms pictures; the microsecond stepping fixture deliberately
    # exercises timestamp quantization and cannot hold its first slot here.
    with av.open(str(path), "w") as output:
        stream = output.add_stream("libx264", rate=10)
        stream.width, stream.height, stream.pix_fmt = 320, 96, "yuv420p"
        stream.options = {"crf": "12", "preset": "ultrafast", "bf": "0", "g": "1"}
        for number in range(5):
            frame = av.VideoFrame.from_ndarray(picture(number), format="bgra")
            frame.pts, frame.time_base = number, Fraction(1, 10)
            for packet in stream.encode(frame):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    decoded = read_pictures(path)
    assert [number for _, number in decoded] == list(range(5))
    return [t for t, _ in decoded], [number for _, number in decoded]


def test_drawer_uses_captured_pad_and_never_borrows_for_unknown_picture(tmp_path):
    path = tmp_path / "pictures.mp4"
    times, numbers = captured_video(path)
    pads = [{"stick_x": 1, "stick_y": 1, "buttons": 0x2000,
             "yaw": -1991, "action": 0, "speed": 32.113},
            {"stick_x": -2, "stick_y": 3, "buttons": 0x8000,
             "yaw": 100, "action": 0, "speed": 12}, None]
    replay = {"clip_url": "data:video/mp4;base64," + base64.b64encode(path.read_bytes()).decode(),
              "frame_times": times, "picture_ids": numbers,
              "frame_map": [8273] * len(times), "picture_igt": [3016] * len(times),
              "input_span": [5256, 8600],
              "picture_states": pads + [pads[0]] * (len(times) - 3),
              "frame_map_source": "plugin", "duration_s": times[-1] + 1/30,
              "fps": 30, "game_fps": 30, "anchor_offset_s": 0,
              "source": "buffer", "truncated": False,
              "pad_stamp_agreement": {"pictures": 1, "agree": 0, "rows": len(times),
                                      "disagreements": [[0, 8273, [0, 1, 8192], [1, 1, 8192]]]}}
    inputs = {"attempt_id": 42, "fps": 30, "frames": 3345, "attempt_frames": 3284,
              "lead_frames": 0, "stretches": [[0, 5256, 3345]],
              "buttons": [[32768, "A"], [16384, "B"], [8192, "Z"]],
              "stick_max": 84, "dead_zone": 8, "angle_units": 65536,
              "actions": [], "markers": [], "template": None,
              "runs": [{"start": 0, "length": 3345, "buttons": 8192,
                        "stick_x": 0, "stick_y": 1, "yaw": -1991, "speed": 32.113}]}
    with PROJECT.open() as url, get_driver().launch(viewport=(1500, 1100)) as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        page.evaluate(r"""(() => {
          const original = window.fetch;
          window.fetch = (url, options) => {
            const value = /\/api\/attempts\/\d+\/replay$/.test(String(url)) ? REPLAY
              : /\/api\/attempts\/\d+\/inputs(?:\?|$)/.test(String(url)) ? INPUTS : null;
            return value ? Promise.resolve(new Response(JSON.stringify(value), {status:200,
                headers:{'Content-Type':'application/json'}})) : original(url, options);
          };
        })()""".replace("REPLAY", json.dumps(replay)).replace("INPUTS", json.dumps(inputs)))
        page.evaluate(STORY.setup)
        page.wait_for(".input-inspector")
        page.evaluate("""(async () => {
          const video = document.querySelector('.attempt-drawer video');
          if (video.readyState < 2) await new Promise(resolve =>
            video.addEventListener('loadeddata', resolve, {once:true}));
          video.pause();
        })()""")
        page.click('.attempt-drawer button:text-is("Start")')
        page.wait_for('.input-inspector .stick-value:text-is("U1")')
        (tmp_path / "initial-inspector.html").write_text(page.evaluate(
            "document.querySelector('.input-inspector').outerHTML"), encoding="utf-8")
        (tmp_path / "initial.png").write_bytes(page.screenshot())
        page.wait_for('.input-inspector .stick-value:text-is("R1")')
        page.wait_for('.input-inspector .controller-button:text-is("Z")')
        for width in [1500, 850]:
            page.set_viewport(width, 1100)
            page.evaluate("document.querySelector('.input-inspector').scrollIntoView({block:'center'})")
            (tmp_path / f"captured-pad-{width}.png").write_bytes(page.screenshot())
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.click('.attempt-drawer button:text-is("Forward 1")')
        page.wait_for('.input-inspector .stick-value:text-is("U3")')
        page.wait_for('.input-inspector .stick-value:text-is("L2")')
        page.wait_for('.input-inspector .controller-button:text-is("A")')
        page.click('.attempt-drawer button:text-is("Forward 1")')
        page.wait_for('.input-inspector .controller-button:text-is("not captured")')
        assert "U3" not in page.evaluate("document.querySelector('.input-inspector').textContent")
        # Seeking back must restore the stamped value despite the repeated raw counter.
        page.click('.attempt-drawer button:text-is("Back 1")')
        page.wait_for('.input-inspector .stick-value:text-is("U3")')
        page.wait_for('.input-inspector .stick-value:text-is("L2")')
        assert page.problems() == []
    print(f"Captured pad browser evidence: {tmp_path}")
