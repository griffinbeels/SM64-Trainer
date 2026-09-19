"""The overlay page in a real browser, over the real clock socket.

`tests/test_ui_ghost_follower.py` owns the NUMBERS -- what the rule decides and
how many game frames it is out by. This file owns the half a trace cannot
answer: that the page resolves its clip, that its module graph loads in a
browser at all, that the clock survives the wire, and that a decision reaches
an actual HTMLVideoElement (a seek lands, play runs, hold pauses). The
invisible-feature rule in `.claude/rules/ui-core.md` is exactly this gap --
unit tests plus `node --check` once shipped a feature that rendered nothing.

THE FIXTURE MOUNTS THE REAL ROUTER. An earlier version hand-rolled the
WebSocket handshake inside `http.server`; Chromium refused it as "Incorrect
'Sec-WebSocket-Accept' header value" with an accept value checked by hand
against the RFC and byte-identical for a raw client (measured 2026-09-18). A
test that hand-rolls a protocol is a second implementation to debug, and this
one was answering a question nobody asked -- so it runs `create_ghost_clock_router`
under uvicorn instead, which makes the run an end-to-end proof of I2 and I3
together: a snapshot goes in at the poll side, and the video moves.

Both of the page's own inputs are exercised: the clock over the socket, and
`postMessage` for the nudge and for a clock sample (the door the companion
window uses, and the one the graphics plugin would use if the memory poll ever
proves too slow -- the brainstorm's A+ escalation).
"""
import asyncio
import io
import json
import shutil
import socket
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import STAR_GRAB_ACTIONS

from tests.source_scan import strip_comments
from sm64_events.server.ghost_clock import (GhostClockFeed,
                                            create_ghost_clock_router)

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"

sys.path.insert(0, str(REPO / "tools"))
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg builds the stand-in clip this page needs to play")

CLIP_SECONDS = 20
CLIP_FPS = 30
PRE_ROLL = 44
#: pictures of star cutscene after the grab, holding the final counter value.
#: What a recording keeps around the attempt is the player's setting; the
#: source's `span` is the attempt itself.
TAIL = 30
RUN = CLIP_SECONDS * CLIP_FPS - PRE_ROLL - TAIL
#: The value the counter holds through a reset's reload: the PREVIOUS run's
#: final time. Taken from the payload his own overlay was handed on its first
#: real try (star 6-0, 2026-09-19), reduced to numbers in
#: `tests/fixtures/ghost_preroll_view.json`.
HELD = 3029
# A constant-rate stand-in for a template, SHAPED LIKE A REAL SAVED CLIP: it
# opens before the reset that started the attempt, so its first 44 pictures
# carry a number from a run that is already over, the attempt's own counter
# starts at 3, and 30 pictures of star cutscene follow it. The follower's VFR
# and offset-cut cases are scored in the trace harness; what this file needs is
# a clip a browser will really play, with both ends on it -- an earlier fixture
# started cleanly at zero and stopped at the grab, which is why a rule that
# refused every real clip could pass here.
LAST_IGT = 3 + RUN - 1
VIEW = {"frame_times": None,
        "picture_igt": [HELD] * PRE_ROLL + list(range(3, LAST_IGT + 1))
        + [LAST_IGT] * TAIL,
        "duration_s": float(CLIP_SECONDS), "video_start_s": 0.0,
        "fps": CLIP_FPS, "game_fps": 30, "encode": "cfr"}
#: what the source computes from the attempt's own frames: the attempt is
#: pictures PRE_ROLL..PRE_ROLL+RUN-1, and neither the lead-in nor the cutscene
#: is part of it.
SPAN = {"first_picture": PRE_ROLL, "last_picture": PRE_ROLL + RUN - 1,
        "start_frame": 1000, "end_frame": 1000 + LAST_IGT,
        "basis": "attempt_frames"}

UNAVAILABLE = {
    "available": False, "reason": "template_has_no_attempt",
    "reason_text": "this template was typed in, so it has no footage",
    "template_id": 3, "attempt_id": None, "media_url": None,
    "view": None, "exactness": None}
AVAILABLE = {
    "available": True, "reason": None, "reason_text": None,
    "template_id": 3, "attempt_id": 35076, "media_url": "/media/ghost.mp4",
    "view": VIEW, "exactness": "frame_map"}


def _build_clip(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=size=320x240:rate={CLIP_FPS}",
         "-t", str(CLIP_SECONDS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-g", "15", "-movflags", "+faststart", str(path)],
        check=True, capture_output=True, timeout=180)


def _snapshot(igt: int, timer: int, level: int, area: int,
              action: int = 0) -> GameSnapshot:
    """The shape the poll loop hands the feed, so the wire format under test
    is the real one rather than a dict shaped like it."""
    return GameSnapshot(
        wall_time_utc=datetime.now(timezone.utc), global_timer=timer,
        mario_action=action, mario_action_timer=0, num_stars=0,
        last_completed_course=0, last_completed_star=0,
        igt_overall=igt, curr_level=level, curr_area=area)


#: An action a star grab actually puts Mario in, taken from the project's one
#: registry rather than typed: the point of driving the REAL `clock_message`
#: here is that the page is answering the server's own reading of the game.
GRAB_ACTION = min(STAR_GRAB_ACTIONS)


class _Fixture:
    """The page's server: the real clock router, a source payload, the clip,
    and one door for the test to push a snapshot in at the poll side."""

    def __init__(self, clip: Path):
        self.available = True
        #: the attempt's exact span, when the source can compute one. None is
        #: the older server (and the clip whose frames cannot be resolved).
        self.span = None
        self.feed = GhostClockFeed()
        #: every source query the page actually sent, so the test can check
        #: which star the page ASKED about rather than trusting that it read
        #: its own URL correctly. The three spellings of a star's key are
        #: walked end to end in `tests/test_ghost_key_forms.py`; this is the
        #: one hop that needs a real browser, because it is the page reading
        #: its own address.
        self.source_queries: list[dict] = []
        app = FastAPI()
        app.include_router(create_ghost_clock_router(self.feed))

        @app.get("/api/ghost/source")
        def source(request: Request):
            self.source_queries.append(dict(request.query_params))
            if not self.available:
                return UNAVAILABLE
            return {**AVAILABLE, "span": self.span}

        @app.post("/test/clock")
        def clock(igt: int, global_timer: int, level: int = 8, area: int = 1,
                  action: int = 0):
            self.feed.note(_snapshot(igt, global_timer, level, area, action))
            return {"ok": True}

        @app.post("/test/clock-run")
        async def clock_run(run: dict):
            """A WHOLE TRACE, paced on the server's own loop.

            The page used to post one sample per game frame. That put the
            harness on the critical path twice over: the requests are issued in
            order and complete out of it, so the page could be handed a counter
            that went BACKWARDS (a reset, to any follower), and on a loaded
            machine a round trip could exceed the follower's 200 ms starvation
            window between two samples of a steady trace. Both faults belong to
            the rig -- the live poll loop calls `note()` in order on one thread,
            at the game's own rate -- so the rig owes them rather than the page
            owing tolerance for an emulator that cannot do either.
            """
            period = run.get("period_ms", 33) / 1000
            loop = asyncio.get_running_loop()
            opened = loop.time()
            for index, igt in enumerate(run["igts"]):
                self.feed.note(_snapshot(int(igt), 9000 + index,
                                         run.get("level", 8),
                                         run.get("area", 1),
                                         run.get("action", 0)))
                # Against a DEADLINE, not `sleep(period)`. Windows' timer wakes
                # on ~15.6 ms boundaries, so a plain 33 ms sleep is really 47 --
                # the counter would advance at 21 fps while the clip plays at
                # 30, and the follower would honestly report a drift that was
                # the rig's own. A deadline lets each overshoot be absorbed by
                # the next wait instead of accumulating.
                delay = opened + (index + 1) * period - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
            return {"sent": len(run["igts"])}

        @app.post("/test/tint")
        def tint(enabled: bool, hue_degrees: int):
            # What `PUT /api/ghost` does: the session hands the feed the new
            # colour and nothing else happens. No URL, no navigation.
            self.feed.set_tint({"enabled": enabled, "hue_degrees": hue_degrees})
            return {"ok": True}

        @app.post("/test/nudge")
        def nudge(frames: int):
            # What `PUT /api/ghost` does on the real server: the session hands
            # the feed its new nudge and nothing else happens. No URL, no
            # navigation — which is the property this fixture exists to let
            # the test observe.
            self.feed.set_nudge(frames)
            return {"ok": True}

        @app.get("/media/ghost.mp4")
        def media():
            # FileResponse answers Range requests, which is not optional:
            # Chromium will not seek inside a video served as a flat 200, and
            # the whole page is seeks.
            return FileResponse(clip, media_type="video/mp4")

        app.mount("/ui", StaticFiles(directory=str(UI)), name="ui")
        self.server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=0, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.url = ""

    def __enter__(self):
        self.thread.start()
        deadline = threading.Event()
        for _ in range(200):
            if self.server.started and self.server.servers:
                break
            deadline.wait(0.05)
        assert self.server.started, "the fixture server never came up"
        host, port = self.server.servers[0].sockets[0].getsockname()[:2]
        self.url = f"http://{host}:{port}"
        # It answers before the test asks anything of the page.
        with socket.create_connection((host, port), timeout=5):
            pass
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)


# Run a clock trace from INSIDE the page, at the game's own rate, through the
# real socket -- and read the page's own status back at the end of it.
#
# Both halves matter. Driving 120 samples one CDP round trip at a time would
# measure the harness rather than the page; and READING the status over a round
# trip afterwards would race the follower's own starvation hold, which stops
# the ghost after `starveMs` without a sample exactly as it must when the
# emulator is paused. So the page samples itself.
#
# IT READS WHEN THE CLOCK GOES QUIET, not after a fixed delay, and that is a
# fix rather than a preference. A fixed 120 ms assumed the round trip from this
# `fetch` through the server, the feed's pump and the socket was quicker than
# that; on a loaded machine it is not, so the status was captured BEFORE the
# last sample landed and the page had legitimately starved -- `holding:
# no_clock`, which reads exactly like a ghost that stopped following. It cost
# three runs, on sections written today and on one that had been green for a
# day.
#
# ONE REQUEST STARTS THE WHOLE TRACE and the server paces it (see
# `/test/clock-run`), so the browser is not between the samples at all.
#
# The wait that ENDS a trace is "no new sample for ~60 ms": quiet enough to
# mean it is over, and far enough inside `starveMs` (200) that the page cannot
# have started holding.
_TRACE = """(() => {
  const run = %s;
  window.__done = false; window.__result = null;
  (async () => {
    await fetch('/test/clock-run', {method: 'POST',
      headers: {'content-type': 'application/json'}, body: JSON.stringify(run)});
    const deadline = performance.now() + 4000;
    let seen = -1;
    let quiet = 0;
    while (quiet < 4 && performance.now() < deadline) {
      const samples = window.ghostStatus().samples;
      if (samples === seen) quiet += 1; else { seen = samples; quiet = 0; }
      await new Promise((go) => setTimeout(go, 15));
    }
    window.__result = window.ghostStatus();
    window.__done = true;
  })();
  return true;
})()"""


def _trace(page, igts: list, **fields) -> dict:
    """Run one clock trace and answer with what the page was doing at its end.

    `fields` ride every sample: the live grab flag is a field of the clock
    message like any other, so it belongs on the same trace rather than in a
    second mechanism the page never sees in real use.
    """
    page.evaluate(_TRACE % json.dumps({"igts": list(igts), **fields}))
    for _ in range(len(igts) * 2 + 80):
        if page.evaluate("window.__done === true"):
            break
        page.wait_ms(50)
    result = page.evaluate("window.__result")
    assert result is not None, "the trace never finished"
    return result


def _opens_on_the_attempt_not_the_pre_roll(page) -> None:
    """THE DEFECT THIS CLOSES, at the page's own level.

    A saved clip begins before the reset that started the attempt, and through
    that reload the counter still holds the previous run's final value. The
    follower threw the whole template away for it (`counter_restarts`) and the
    page had nothing to play -- his first real try was a black rectangle over
    Project64 with no footage in it (2026-09-19).

    Two claims here, and they are different: the pre-roll is not part of the
    template, and the element does not appear until a picture has actually been
    presented -- because the picture it would show first is the pre-roll.
    """
    # 600 pictures: 44 from the run before this one, then the attempt and its
    # cutscene tail. With no span from the source the counter decides the start
    # and the clip's own end decides the end.
    # Fields, not the whole payload -- other tracks add their own.
    template = page.evaluate("window.ghostStatus().template")
    assert template["ok"] is True, template
    assert template["reason"] is None, template
    assert template["basis"] == "counter_restart", template
    assert template["pictures"] == RUN + TAIL, template
    assert template["preroll_pictures"] == PRE_ROLL, template
    assert template["first_igt"] == 3, template
    assert template["last_igt"] == LAST_IGT, template

    page.wait_for('body[data-ghost="ready"][data-shown="yes"]')
    assert page.evaluate(
        "getComputedStyle(document.getElementById('ghost')).visibility"
    ) == "visible"
    opened = page.evaluate("window.ghostStatus()")
    assert opened["shown"] is True, opened
    assert opened["shown_igt"] == 3, opened
    # The picture it presented is the attempt's own first one. The pre-roll
    # ends at 44/30 s, so a clip still sitting at its start has not been placed
    # and the first thing the player would see is the previous run.
    assert opened["video_time"] >= PRE_ROLL / CLIP_FPS - 0.05, opened

    # The gate is the attribute, not the timing: take it away and the element
    # goes back to painting nothing. Restored immediately -- a driven test that
    # leaves the page in a state it invented measures a page only it has seen.
    assert page.evaluate(
        "(() => { delete document.body.dataset.shown;"
        " const v = getComputedStyle(document.getElementById('ghost')).visibility;"
        " document.body.dataset.shown = 'yes'; return v; })()") == "hidden"


def _paints_nothing_at_all(page) -> None:
    """NOTHING IS PAINTED, which is not the same claim as "the video is
    hidden". This page sits over the running game, so an opaque page is a black
    rectangle over Project64 -- what his first real try saw, and what he saw
    again as a translucent dark sheet once whole-window opacity was turned
    down. Read the COMPUTED colour: a `background: #000` anywhere in the chain
    is invisible to a class-name check and is exactly the defect."""
    assert page.evaluate(
        "getComputedStyle(document.getElementById('ghost')).visibility"
    ) == "hidden"
    assert page.evaluate(
        "[document.documentElement, document.body,"
        " document.getElementById('ghost')]"
        ".map((el) => getComputedStyle(el).backgroundColor)"
    ) == ["rgba(0, 0, 0, 0)"] * 3
    # And the window that owns this page is told there is nothing to show.
    assert page.evaluate("window.ghostStatus().presentable") is False


def _the_page_hands_the_span_to_the_follower(page, fixture, url: str) -> None:
    """HIS RULING, through the page: *"It should always be clamped to exactly
    the span of the attempt, regardless of the user's recording settings."*

    The page's own job here is one hop -- take the span off the source payload
    and hand it to the follower -- and it is exactly the hop a trace harness
    cannot see. Everything the span then MEANS is scored in
    `tests/test_ui_ghost_follower.py`.
    """
    fixture.span = SPAN
    page.goto(url)
    page.wait_for('body[data-ghost="ready"][data-shown="yes"]')
    template = page.evaluate("window.ghostStatus().template")
    assert template["basis"] == "attempt_frames", template
    # The cutscene tail is gone; the attempt's own pictures are all that is left.
    assert template["pictures"] == RUN, template
    assert template["first_igt"] == 3, template
    assert template["last_igt"] == LAST_IGT, template
    # Past the grab it parks on the last picture of the ATTEMPT and keeps
    # showing it, rather than playing the cutscene he has not earned.
    over = _trace(page, [LAST_IGT + 200] * 10)
    assert over["decision"]["reason"] == "past_template", over
    assert over["shown_igt"] == LAST_IGT, over
    # The attempt is over, so the window comes down rather than freezing a
    # grab frame over his game -- see `_presentable_tracks_the_footage`.
    assert over["presentable"] is False, over
    fixture.span = None


def _presentable_tracks_the_footage(page) -> None:
    """`presentable` is the WINDOW's signal, so it is tested where the window
    reads it: through `window.ghostStatus()` on the real page.

    The companion window shows itself only while this is true, which is what
    makes an overlay with nothing to draw cost the player nothing. He saw the
    other arrangement on the first try: at 80% opacity a translucent dark sheet
    over the game, at 100% an opaque black one, with no footage in either.

    Four states here, and the two that must be TRUE are the point -- a signal
    that only ever says "no" would hide the feature just as thoroughly.
    """
    # Following: a decoded picture of the one the rule names is on screen.
    playing = _trace(page, list(range(3, 90)))
    assert playing["presentable"] is True, playing
    assert playing["shown"] is True, playing

    # Past the template's last picture -- he is slower than the ghost was.
    # The ghost's run is over, so the window goes away: a frozen star-grab
    # frame over his game while he is still trying to finish tells him nothing
    # and covers what he is doing. It still PARKS on that picture (nothing
    # outside the attempt is ever shown); only the window is taken down.
    over = _trace(page, [700] * 10)
    assert over["decision"]["reason"] == "past_template", over
    assert over["shown_igt"] == LAST_IGT, over
    assert over["presentable"] is False, over

    # ...and his next reset brings it straight back. The reload is the OTHER
    # end of the span and it stays presentable: the ghost is parked on the
    # frame it is about to run from, and taking the window away for the three
    # game frames the counter spends below the template's start would be a
    # blink he reads as a fault.
    reloading = _trace(page, [0, 1, 2])
    assert reloading["decision"]["reason"] == "before_template", reloading
    assert reloading["shown_igt"] == 3, reloading
    assert reloading["presentable"] is True, reloading

    back = _trace(page, list(range(3, 40)))
    assert back["presentable"] is True, back

    # The clock stops arriving (the emulator is paused, or the server went
    # away): the ghost holds, and there is no longer anything true on screen.
    page.wait_ms(400)
    starved = page.evaluate("window.ghostStatus()")
    assert starved["holding"] == "no_clock", starved
    assert starved["presentable"] is False, starved


def _the_element_covers_the_whole_window(page) -> None:
    """The ELEMENT is full-bleed even though the PICTURE may not be.

    What it does not paint it leaves see-through, which over a running game is
    the point; a smaller element would give the window edges nothing at all.
    """
    box = page.evaluate(
        "(() => { const r = document.getElementById('ghost')"
        ".getBoundingClientRect(); return {w: r.width, h: r.height,"
        " x: r.x, y: r.y}; })()")
    assert box["x"] == 0 and box["y"] == 0
    assert box["w"] == page.evaluate("window.innerWidth")
    assert box["h"] == page.evaluate("window.innerHeight")


#: Two backdrops, because a pixel of the FOOTAGE could legitimately be either
#: one of them -- the stand-in clip is a colour-bar pattern and magenta is one
#: of its bars. A pixel that matches both is impossible, so the union of the
#: two differences is the picture and nothing else. Same two-pass trick a
#: transparent export needs.
BACKDROPS = ("#ff00ff", "#0a2f10")


def _painted_picture_box(page) -> tuple[tuple[int, int, int, int], int, int]:
    """WHERE THE PICTURE ACTUALLY IS, measured from painted pixels.

    Not derived from the element box and the clip's aspect -- that is the same
    arithmetic `object-fit` does, so it would agree with any value of it. The
    element covers the window and leaves what it does not paint see-through, so
    a temporary backdrop makes the picture's own box readable in a screenshot.

    The backdrop is restored immediately: a driven test that leaves the page in
    a state it invented measures a page only it has ever seen.
    """
    from PIL import Image, ImageChops, ImageColor

    shots = []
    for colour in BACKDROPS:
        page.evaluate("(() => { document.documentElement.style.background ="
                      f" {colour!r}; return true; }})()")
        page.wait_ms(150)
        shots.append((colour, page.screenshot()))
    page.evaluate("(() => { document.documentElement.style.background = '';"
                  " return true; })()")
    painted = None
    size = None
    for colour, shot in shots:
        image = Image.open(io.BytesIO(shot)).convert("RGB")
        size = image.size
        backdrop = Image.new("RGB", size, ImageColor.getrgb(colour))
        difference = ImageChops.difference(image, backdrop)
        painted = (difference if painted is None
                   else ImageChops.lighter(painted, difference))
    box = painted.getbbox()
    assert box is not None, "nothing at all was painted over the backdrop"
    return box, size[0], size[1]


def _the_picture_is_centred_and_undistorted(page) -> None:
    """ROUND 3 ITEM 3, as the window track's live measurements corrected it.

    His report was *"it's ALMOST overlaid properly"*, and the cause was the
    WINDOW covering Project64's whole client area, status-bar strip included --
    about 1.9% of vertical stretch. The window now sits on the rectangle the
    renderer draws into.

    What this page owes is the MAPPING. The project's GLideN64 build forces 4:3
    (`frameBufferEmulation\\aspect=1`), so the game image is inscribed 4:3 and
    centred inside that rectangle and the black bands in his footage are the
    game's own borders. The clip's 4:3 box therefore maps onto the live 4:3
    box: identical to stretching while the two aspects agree, and undistorted
    when he has resized Project64 to another shape.

    So this is measured in a window that is deliberately NOT the clip's shape
    -- 2:1 against a 4:3 clip -- because that is the only shape in which
    `contain` and `fill` paint different pictures.
    """
    was = page.evaluate("(() => ({w: window.innerWidth,"
                        " h: window.innerHeight}))()")
    page.set_viewport(800, 400)
    page.wait_ms(200)
    _the_element_covers_the_whole_window(page)
    intrinsic = page.evaluate(
        "(() => { const v = document.getElementById('ghost');"
        " return {w: v.videoWidth, h: v.videoHeight}; })()")
    assert intrinsic["w"] and intrinsic["h"], intrinsic
    (left, top, right, bottom), width, height = _painted_picture_box(page)
    # Put the window back: the sections after this one measure a page they did
    # not resize, and a rig that leaves its own shape behind measures a page
    # only it has ever seen.
    page.set_viewport(was["w"], was["h"])
    page.wait_ms(200)

    # It fills the SHORT axis and is centred on the long one, with the margins
    # equal to within a pixel of rounding.
    assert top <= 1 and bottom >= height - 1, (left, top, right, bottom, height)
    margins = (left, width - right)
    assert abs(margins[0] - margins[1]) <= 2, margins
    assert margins[0] > 10, (
        f"the picture reaches the window edges at 2:1 -- it was stretched "
        f"rather than mapped: {margins}")
    # ...and it is the clip's own shape, not the window's.
    painted_aspect = (right - left) / (bottom - top)
    clip_aspect = intrinsic["w"] / intrinsic["h"]
    assert abs(painted_aspect - clip_aspect) / clip_aspect < 0.02, (
        f"the picture is distorted: painted {painted_aspect:.4f} against the "
        f"clip's own {clip_aspect:.4f}")


def _his_own_grab_takes_the_window_down(page) -> None:
    """ROUND 3, ITEM 1, through the real socket and the real message.

    His words: *"The ghost should close when I grab the star (sometimes I might
    be faster than the ghost!). Upon star grab for the player's actual live
    session."* The flag is computed on the server from Mario's action against
    `memory/addresses.py::STAR_GRAB_ACTIONS`, so this drives a real grab action
    through `clock_message` rather than hand-posting a boolean.

    Both halves are here because only one of them is obvious: the ghost goes
    down at the touch, and it STAYS down once the dance action has cleared and
    he is walking around with the counter still running.
    """
    following = _trace(page, list(range(3, 60)))
    assert following["presentable"] is True, following

    touched = _trace(page, list(range(60, 70)), action=GRAB_ACTION)
    assert touched["sample"]["star_grab"] is True, touched
    assert touched["decision"]["reason"] == "grabbed", touched
    assert touched["grabbed"] is True, touched
    assert touched["presentable"] is False, touched
    assert touched["paused"] is True, touched

    # The dance ends and he walks on: the action is gone, the counter is still
    # climbing, and the ghost must not come back for a star he already has.
    walking = _trace(page, list(range(70, 100)))
    assert walking["sample"]["star_grab"] is False, walking
    assert walking["decision"]["reason"] == "grabbed", walking
    assert walking["presentable"] is False, walking

    # His reset brings it back, by the same route a finished ghost already
    # uses: the counter returns to near zero and the follower re-places it.
    _trace(page, [0, 1, 2])
    back = _trace(page, list(range(3, 40)))
    assert back["grabbed"] is False, back
    assert back["presentable"] is True, back


def _the_ghost_is_coloured_over_the_clock(page) -> None:
    """ROUND 3, ITEM 2, at the page's own level.

    His words: *"apply a color filter to the entire picture."* The filter is
    read off the ELEMENT rather than off the status: a filter string that the
    engine rejected leaves the property empty with every field still saying it
    was applied. And the page ships no default for it -- nothing is painted
    until the server has said, which is safe because nothing is presentable
    before the first sample either.

    `__alive` is how "the page was never navigated for this" is measured; a
    load of any kind wipes it.
    """
    filter_of = ("getComputedStyle(document.getElementById('ghost')).filter")
    page.evaluate("(() => { window.__alive = 'yes'; return true; })()")
    page.evaluate("(() => { fetch('/test/tint?enabled=true&hue_degrees=200',"
                  " {method: 'POST'}); return true; })()")
    coloured = _trace(page, list(range(3, 20)))
    assert page.evaluate("window.__alive") == "yes", "the page was reloaded"
    assert coloured["tint"] == {"enabled": True, "hue_degrees": 200}, coloured
    assert page.evaluate(filter_of) == "hue-rotate(200deg)"
    assert coloured["tint_filter"] == "hue-rotate(200deg)", coloured

    # Switched off: the picture goes back to the game's own colours, live.
    page.evaluate("(() => { fetch('/test/tint?enabled=false&hue_degrees=200',"
                  " {method: 'POST'}); return true; })()")
    plain = _trace(page, list(range(20, 35)))
    assert page.evaluate("window.__alive") == "yes", "the page was reloaded"
    assert plain["tint"] == {"enabled": False, "hue_degrees": 200}, plain
    assert page.evaluate(filter_of) == "none"

    # And the parent window can push it too, the way it can push the nudge.
    _post_message(page, "{type: 'ghost-tint',"
                        " tint: {enabled: true, hue_degrees: 90}}")
    assert page.evaluate(filter_of) == "hue-rotate(90deg)"
    page.evaluate("(() => { fetch('/test/tint?enabled=true&hue_degrees=120',"
                  " {method: 'POST'}); return true; })()")
    _trace(page, list(range(35, 45)))


def _nudge_over_the_clock(page, url: str) -> None:
    """THE DEFECT THIS CLOSES, at the page's own level.

    The nudge used to ride the overlay page's URL, so every press of his timing
    key re-pointed the window: a full page LOAD, which blinks the ghost off and
    re-seeks it from scratch — many times over for a key held down. It now
    arrives on the clock feed, so the page adopts it on its next game frame and
    is never navigated.

    `__alive` is how "never navigated" is MEASURED rather than asserted: it is
    set once, and a load of any kind wipes it. Watching for a flicker would be
    the weaker claim, since a reload can finish between two samples.
    """
    page.evaluate("(() => { window.__alive = 'yes'; return true; })()")
    page.evaluate("(() => { fetch('/test/nudge?frames=-20', {method: 'POST'});"
                  " return true; })()")
    page.wait_ms(100)
    over_the_clock = _trace(page, list(range(400, 431)))
    assert page.evaluate("window.__alive") == "yes", "the page was reloaded"
    assert over_the_clock["nudge_frames"] == -20, over_the_clock
    assert over_the_clock["target_igt"] == over_the_clock["sample"]["igt"] - 20
    assert abs(over_the_clock["decision"]["drift"]) <= 2, over_the_clock

    # A page that starts FRESH — a reload, a new window, a child the session
    # restarted — takes the nudge in force NOW on its first sample, rather than
    # sitting at zero until he presses the key again.
    page.goto(url)
    page.wait_for('body[data-ghost="ready"]')
    assert page.evaluate("window.__alive") is None
    assert page.evaluate("window.ghostStatus().nudge_frames") == 0
    fresh = _trace(page, list(range(400, 411)))
    assert fresh["nudge_frames"] == -20, fresh


def _post_message(page, body: str) -> None:
    page.evaluate(f"(() => {{ window.postMessage({body}, '*'); return true; }})()")
    page.wait_ms(200)


def test_the_picture_is_mapped_rather_than_stretched():
    """ROUND 3, ITEM 3, the half a rendered check cannot see.

    `fill` is the CSS initial value, so a browser reporting it cannot tell a
    declaration from its absence -- and the declaration is what a future edit
    would change. Read the page's own stylesheet instead, with comments
    stripped so the paragraph EXPLAINING why `fill` was rejected cannot fail
    the guard it is about.
    """
    css = strip_comments((UI / "ghost" / "index.html").read_text(encoding="utf-8"))
    assert "object-fit: contain" in css, (
        "the overlay does not map the clip's picture onto the live one")
    assert "object-position: center" in css, (
        "the overlay does not say where the picture sits in its window")
    assert "object-fit: fill" not in css, (
        "the overlay stretches the clip to the window, which distorts it "
        "whenever Project64 is not the shape the clip was recorded at")


def test_the_overlay_page_follows_a_clock_in_a_real_browser(tmp_path):
    clip = tmp_path / "ghost.mp4"
    _build_clip(clip)

    with _Fixture(clip) as fixture, get_driver().launch() as page:
        url = f"{fixture.url}/ui/ghost/index.html?kind=star&entity_key=LLL-1"

        # 1. Nothing to show says so, and paints nothing.
        fixture.available = False
        page.goto(url)
        page.wait_for('body[data-ghost="unavailable"]')
        assert page.evaluate("document.body.dataset.reason") == "template_has_no_attempt"
        _paints_nothing_at_all(page)

        # 2. A real template: the page resolves it and opens the clip.
        fixture.available = True
        page.goto(url)
        page.wait_for('body[data-ghost="ready"]')
        _opens_on_the_attempt_not_the_pre_roll(page)
        # It asked about the star in its OWN address, in the template store's
        # spelling, and asked for nothing else: the nudge is not a page, so it
        # is not in the URL (see ghost/session.py::follower_url).
        assert fixture.source_queries[-1] == {"kind": "star",
                                              "entity_key": "LLL-1"}
        assert page.evaluate("document.getElementById('ghost').muted") is True
        _the_element_covers_the_whole_window(page)

        # 3. Steady play over the real socket: the element runs, on the
        #    picture the rule names.
        #
        #    The error read is the DECISION's own `drift` -- how far out the
        #    picture on screen was at the moment the follower last decided.
        #    Comparing `shown_igt` against `target_igt` after the fact reported
        #    4 frames on a page that was fine: the status is captured 120 ms
        #    after the last sample and the video keeps PLAYING through it,
        #    exactly as it should, so the reading was the video's progress and
        #    not the follower's error (measured 2026-09-18). The tolerance is 2
        #    rather than the trace harness's 1 because this one includes a
        #    decoder and a wire.
        playing = _trace(page, list(range(0, 121)))
        assert playing["samples"] >= 100, playing
        assert playing["paused"] is False, playing
        assert playing["decision"]["drift"] is not None, playing
        assert abs(playing["decision"]["drift"]) <= 2, playing
        assert fixture.feed.listeners == 1

        # 4. The timer stalls: the element pauses, with no pause detector.
        stalled = _trace(page, [120] * 20)
        assert stalled["paused"] is True, stalled
        assert stalled["decision"]["reason"] == "stalled", stalled

        # 5. A reset: the counter returns to zero and so does the ghost, held
        #    on the template's own first picture until the timer moves -- which
        #    for a real clip is the first picture AFTER its pre-roll, never the
        #    previous run's last frame that the clip opens on.
        reset = _trace(page, [0])
        assert reset["decision"]["reason"] == "reset", reset
        assert reset["paused"] is True, reset
        assert reset["shown_igt"] == 3, reset

        # 5b. What the companion window polls to decide whether to show itself
        #     at all. Before the nudge tests, which leave one in force.
        _presentable_tracks_the_footage(page)

        # 5c. HIS grab ends the ghost, whoever got there first (round 3, item
        #     1), and 5d the colour that tells the two pictures apart (item 2).
        #     Both before the nudge tests for the same reason.
        _his_own_grab_takes_the_window_down(page)
        _the_ghost_is_coloured_over_the_clock(page)

        # 5e. The picture is MAPPED onto the window rather than stretched to
        #     it (item 3), measured in a window that is not the clip's shape.
        #     Last of the rendered checks: it resizes the viewport.
        _the_picture_is_centred_and_undistorted(page)

        # 6. His timing hotkey, over the channel the companion window uses.
        _post_message(page, "{type: 'ghost-nudge', nudge_frames: 45}")
        # Long enough for the rate correction to have done its work: it closes
        # the remaining error over `correctionTau` (0.5 s), so a 31-sample
        # trace measured it mid-correction and reported 3 frames on a page that
        # was settling correctly. Four time constants, not a wider tolerance.
        nudged = _trace(page, list(range(100, 161)))
        assert nudged["nudge_frames"] == 45, nudged
        assert nudged["target_igt"] == nudged["sample"]["igt"] + 45, nudged
        assert nudged["decision"]["target"] == nudged["sample"]["igt"] + 45, nudged
        assert abs(nudged["decision"]["drift"]) <= 2, nudged

        # 7. The same clock, pushed by the parent instead of the server --
        #    the escalation path, proved rather than assumed.
        _post_message(
            page, "{type: 'ghost-clock', igt: 300, global_timer: 50000,"
                  " level: 8, area: 1}")
        pushed = page.evaluate("window.ghostStatus()")
        assert pushed["sample"]["igt"] == 300, pushed
        assert pushed["target_igt"] == 345, pushed

        # 8 and 9. His timing key, the way the running app sends it.
        _nudge_over_the_clock(page, url)

        # 10. The source's exact span reaches the follower through the page.
        _the_page_hands_the_span_to_the_follower(page, fixture, url)

        # `problems()` DRAINS, so it is asked once and the answer kept --
        # asking twice (once for the assertion, once for its message) reports
        # a clean page under the failure it just swallowed.
        problems = page.problems()
        assert problems == [], problems

    assert fixture.feed.listeners == 0
    assert fixture.feed.running is False
