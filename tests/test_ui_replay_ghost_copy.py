"""The "Show ghosts" toggle swaps the real drawer's video to its ghost copy in place.

The scratch app supplies the practice workflow; only the replay answer is
replaced, with two small recordings on identical picture times: the clean one
and a ghost copy that paints a green band above the same picture numbers.
Every check reads the decoded picture, never the requested seek.
"""
import base64
from fractions import Fraction
import json

import av
import numpy as np

from frontend_runner import run_frontend
from test_replay_picture_identity import picture, read_pictures
from test_ui_replay_picture_steps import PROJECT, STORY, get_driver


def test_show_ghosts_component_contracts():
    run_frontend("ghostcopy.test.js")


PICTURES = 90
TICKS = [i * 3000 for i in range(PICTURES)]  # 30 per second on the 90 kHz clock


def recording(path, ghosts):
    with av.open(str(path), "w", options={"movie_timescale": "90000"}) as container:
        stream = container.add_stream("libx264", rate=30)
        stream.width, stream.height, stream.pix_fmt = 320, 96, "yuv420p"
        stream.time_base = stream.codec_context.time_base = Fraction(1, 90000)
        stream.options = {"crf": "12", "preset": "ultrafast", "bf": "0", "g": "1"}
        for number, tick in enumerate(TICKS):
            image = picture(number)
            if ghosts:
                image[:24, :, :3] = np.array([0, 255, 0], dtype=np.uint8)  # BGRA green
            frame = av.VideoFrame.from_ndarray(image, format="bgra")
            frame.pts, frame.time_base = tick, Fraction(1, 90000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    decoded = read_pictures(path)
    assert [number for _, number in decoded] == list(range(PICTURES))
    assert [round(t * 90000) for t, _ in decoded] == TICKS
    return [t for t, _ in decoded]


def data_url(path):
    return "data:video/mp4;base64," + base64.b64encode(path.read_bytes()).decode()


def replay_payload(tmp_path, offered):
    clean, copy = tmp_path / "clean.mp4", tmp_path / "ghosts.mp4"
    times = recording(clean, ghosts=False)
    assert recording(copy, ghosts=True) == times
    return {"clip_url": data_url(clean), "ghost_copy_url": data_url(copy) if offered else None,
            "ghost_copy_reason": None if offered else "Ghosts in recording was off",
            "frame_times": [round(t, 6) for t in times], "picture_ids": list(range(PICTURES)),
            "frame_map": [100 + i for i in range(PICTURES)],
            "picture_igt": list(range(PICTURES)), "frame_map_source": "plugin",
            "duration_s": times[-1] + 1 / 30, "fps": 30, "game_fps": 30,
            "anchor_offset_s": 0, "source": "buffer", "truncated": False}


def open_drawer(page, url, replay, remembered=None):
    page.goto(url)
    page.wait_for(PROJECT.ready_selector)
    if remembered is not None:
        page.evaluate(f"localStorage.setItem('sm64.replayShowGhosts', {json.dumps(remembered)})")
    page.evaluate(r"""(() => {
      const original = window.fetch;
      window.ghostReplay = REPLAY;
      window.fetch = (url, options) => /\/api\/attempts\/\d+\/replay$/.test(String(url))
        ? Promise.resolve(new Response(JSON.stringify(REPLAY), {status:200,
            headers:{'Content-Type':'application/json'}})) : original(url, options);
    })()""".replace("REPLAY", json.dumps(replay)))
    page.evaluate(STORY.setup)
    watch_pictures(page)


def reopen_drawer(page):
    """Close the drawer and open it again: a fresh player, as for the next replay."""
    page.evaluate("document.querySelector('.attempt-actions .icon-button[aria-expanded=true]').click()")
    assert settled(page, "!document.querySelector('.attempt-drawer')")
    page.evaluate("document.querySelector('.attempt-actions .icon-button').click()")
    watch_pictures(page)


def watch_pictures(page):
    page.wait_for(".attempt-drawer video")
    page.wait_for(".input-inspector")
    # Every presented picture stamps its decoded number and whether the
    # ghost band is painted, so a wait names the picture actually on screen.
    page.evaluate("""(async () => {
      const video = document.querySelector('.attempt-drawer video');
      if (video.readyState < 2) await new Promise(resolve =>
        video.addEventListener('loadeddata', resolve, {once:true}));
      const canvas = document.createElement('canvas');
      canvas.width=320; canvas.height=96;
      const ctx = canvas.getContext('2d', {willReadFrequently:true});
      const read = () => {
        ctx.drawImage(video,0,0,320,96);
        const number = Array.from({length:8}, (_,bit) =>
          ctx.getImageData(bit*40+20,48,1,1).data[0]>128 ? 1<<bit : 0).reduce((a,b)=>a+b,0);
        const [r, g] = ctx.getImageData(20,8,1,1).data;
        return {number, ghosts: g > 128 && r < 100};
      };
      window.readReplayPicture = read;
      const shown = () => {
        const seen = read();
        video.dataset.picture = String(seen.number);
        video.dataset.ghosts = seen.ghosts ? "1" : "0";
        video.requestVideoFrameCallback(shown);
      };
      video.requestVideoFrameCallback(shown);
    })()""")


def settled(page, condition):
    """True once `condition` holds; the drawer renders after the picture arrives."""
    return page.evaluate("""(async () => {
      for (let i = 0; i < 60; i++) {
        if (CONDITION) return true;
        await new Promise(resolve => setTimeout(resolve, 50));
      }
      return false;
    })()""".replace("CONDITION", condition))


def toggle_state(page):
    return page.evaluate("""(() => {
      const button = [...document.querySelectorAll('.attempt-drawer .replay-playback-options button')]
        .find(b => b.textContent.trim() === 'Show ghosts');
      return button ? {pressed: button.getAttribute('aria-pressed'),
        active: button.classList.contains('is-active'), disabled: button.disabled} : null;
    })()""")


def shoot(page, tmp_path, name):
    for width in [1500, 850]:
        page.set_viewport(width, 1100)
        # The whole player: its notices, the picture and every control.
        page.evaluate("document.querySelector('.attempt-drawer .replay-player').scrollIntoView()")
        (tmp_path / f"{name}-{width}.png").write_bytes(page.screenshot())
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.set_viewport(1500, 1100)


def test_show_ghosts_swaps_in_place_and_is_remembered(tmp_path):
    replay = replay_payload(tmp_path, offered=True)
    video = ".attempt-drawer video"
    with PROJECT.open() as url, get_driver().launch(viewport=(1500, 1100)) as page:
        open_drawer(page, url, replay, remembered="0")
        assert toggle_state(page) == {"pressed": "false", "active": False, "disabled": False}
        # Control: the clean recording steps to a known picture with no band.
        page.click('.attempt-drawer button:text-is("Start")')
        page.wait_for(f'{video}[data-picture="0"][data-ghosts="0"]')
        for expected in [1, 2, 3]:
            page.click('.attempt-drawer button:text-is("Forward 1")')
            page.wait_for(f'{video}[data-picture="{expected}"][data-ghosts="0"]')
        inspector = page.evaluate("document.querySelector('.input-inspector-frame').textContent")
        shoot(page, tmp_path, "offered-clean")

        # Paused on picture 3: the copy shows picture 3, still paused.
        page.click('.attempt-drawer button:text-is("Show ghosts")')
        page.wait_for(f'{video}[data-picture="3"][data-ghosts="1"]')
        assert page.evaluate(f"document.querySelector('{video}').src === window.ghostReplay.ghost_copy_url")
        assert page.evaluate(f"document.querySelector('{video}').paused")
        assert toggle_state(page) == {"pressed": "true", "active": True, "disabled": False}
        assert settled(page, "document.querySelector('.input-inspector-frame').textContent === "
                       + json.dumps(inspector))
        page.wait_ms(300)  # a late seek or play would land inside this window
        assert page.evaluate(f"document.querySelector('{video}').paused")
        assert page.evaluate("window.readReplayPicture()") == {"number": 3, "ghosts": True}
        assert page.evaluate("localStorage.getItem('sm64.replayShowGhosts')") == "1"
        # Frame stepping walks the copy on the same clock.
        page.click('.attempt-drawer button:text-is("Forward 1")')
        page.wait_for(f'{video}[data-picture="4"][data-ghosts="1"]')
        page.click('.attempt-drawer button:text-is("Back 1")')
        page.wait_for(f'{video}[data-picture="3"][data-ghosts="1"]')
        shoot(page, tmp_path, "offered-ghosts")

        # Playing at a quarter speed: the swap back keeps playing, at that speed.
        page.evaluate("""(() => {
          const select = document.querySelector('.attempt-drawer .replay-speed select');
          select.value = '0.25'; select.dispatchEvent(new Event('change', {bubbles:true}));
        })()""")
        page.click('.attempt-drawer button.primary-transport')
        page.wait_for('.attempt-drawer button.primary-transport:has-text("Pause")')
        page.click('.attempt-drawer button:text-is("Show ghosts")')
        page.wait_for(f'{video}[data-ghosts="0"]')
        state = page.evaluate(f"""(() => {{ const v = document.querySelector('{video}');
          return {{paused: v.paused, rate: v.playbackRate, time: v.currentTime}}; }})()""")
        assert state["paused"] is False and state["rate"] == .25
        # Playback resumed near the swap point (pictures 3..10), never from the start.
        assert 3 / 30 <= state["time"] < 11 / 30, state
        page.click('.attempt-drawer button.primary-transport')
        assert page.evaluate("localStorage.getItem('sm64.replayShowGhosts')") == "0"
        assert page.problems() == []

        # The choice carries to the next replay that has a copy.
        page.click('.attempt-drawer button:text-is("Show ghosts")')
        reopen_drawer(page)
        assert page.evaluate(f"document.querySelector('{video}').src === window.ghostReplay.ghost_copy_url")
        page.wait_for(f'{video}[data-ghosts="1"]')
        assert toggle_state(page)["pressed"] == "true"
        assert page.problems() == []
    print(f"Rendered ghost copy evidence: {tmp_path}")


def test_no_ghost_copy_no_toggle(tmp_path):
    replay = replay_payload(tmp_path, offered=False)
    with PROJECT.open() as url, get_driver().launch(viewport=(1500, 1100)) as page:
        # Even with the choice remembered, a replay without a copy plays clean.
        open_drawer(page, url, replay, remembered="1")
        page.wait_for('.attempt-drawer video[data-ghosts="0"]')
        assert toggle_state(page) is None
        assert page.evaluate("document.querySelector('.attempt-drawer video').src === window.ghostReplay.clip_url")
        assert page.evaluate("localStorage.getItem('sm64.replayShowGhosts')") == "1"
        shoot(page, tmp_path, "not-offered")
        assert page.problems() == []
    print(f"Rendered no-copy evidence: {tmp_path}")


def test_a_copy_that_cannot_play_falls_back_to_clean(tmp_path):
    replay = {**replay_payload(tmp_path, offered=True),
              "ghost_copy_url": "data:video/mp4;base64," + base64.b64encode(b"not a video" * 64).decode()}
    with PROJECT.open() as url, get_driver().launch(viewport=(1500, 1100)) as page:
        open_drawer(page, url, replay, remembered="1")
        page.wait_for('.attempt-drawer .replay-notice:has-text("The ghost copy could not be played")')
        page.wait_for('.attempt-drawer video[data-ghosts="0"]')
        assert page.evaluate("document.querySelector('.attempt-drawer video').src === window.ghostReplay.clip_url")
        assert toggle_state(page) == {"pressed": "false", "active": False, "disabled": True}
        shoot(page, tmp_path, "copy-failed")
        assert page.problems() == []
    print(f"Rendered fallback evidence: {tmp_path}")

