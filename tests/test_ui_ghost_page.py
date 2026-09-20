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
import os
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
#: PER-PICTURE TIMES, because that is what his real clips carry and the
#: difference is not cosmetic: with them the lookup from a presented
#: `mediaTime` to a picture sits exactly ON the boundary -- a float hair below
#: `frame_times[k]` names picture k-1 -- while a constant-rate clip rounds to
#: the nearest and hides it. A clip without them could not catch the off-by-one
#: the page guards against with `PICTURE_EPSILON`.
PICTURES = CLIP_SECONDS * CLIP_FPS
VIEW = {"frame_times": [round(k / CLIP_FPS, 6) for k in range(PICTURES)],
        "picture_igt": [HELD] * PRE_ROLL + list(range(3, LAST_IGT + 1))
        + [LAST_IGT] * TAIL,
        "duration_s": float(CLIP_SECONDS), "video_start_s": 0.0,
        "fps": CLIP_FPS, "game_fps": 30, "encode": "picture_feed"}
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
        #: Mario's rectangle per picture, when the test gives the clip one.
        #: None is every ghost recorded before this existed.
        self.boxes = None
        #: The clip's own view payload, when a test serves REAL footage rather
        #: than the stand-in this module builds.
        self.source_view = None
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
            return {**AVAILABLE,
                    "view": self.source_view or AVAILABLE["view"],
                    "span": self.span,
                    "mario_boxes": self.boxes,
                    "mario_boxes_reason": None if self.boxes else "no_box_list",
                    "mario_boxes_reason_text": None if self.boxes else
                        "This run has not had Mario found in it yet."}

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

        @app.post("/test/view")
        def view(name: str):
            # What `PUT /api/ghost` does: the session hands the feed the new
            # view and nothing else happens. No URL, no navigation.
            self.feed.set_view(name)
            return {"ok": True}

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
    assert abs(over_the_clock["decision"]["drift"]) <= DRIFT_FRAMES, over_the_clock

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
        assert abs(playing["decision"]["drift"]) <= DRIFT_FRAMES, playing
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
        # Long enough for the rate correction to have done its work: it
        # closes the remaining error over `correctionTau` (0.5 s), so a
        # 31-sample trace measured it mid-correction and reported 3 frames on
        # a page that was settling correctly. Four time constants, not a wider
        # tolerance.
        nudged = _trace(page, list(range(100, 161)))
        assert nudged["nudge_frames"] == 45, nudged
        assert nudged["target_igt"] == nudged["sample"]["igt"] + 45, nudged
        assert nudged["decision"]["target"] == nudged["sample"]["igt"] + 45, nudged
        assert abs(nudged["decision"]["drift"]) <= DRIFT_FRAMES, nudged

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


# ---------------------------------------------------------------------------
# ONLY MARIO: the third ghost view, read off the canvas it paints
# ---------------------------------------------------------------------------
#
# His words, 2026-09-20: *"let's do tight rectangles on our existing ghosts,
# let's try it."* Everything below is measured from PAINTED PIXELS rather than
# from the page's own account of itself: the claim is about what lands on his
# screen over Project64, and a status field saying a box was used is exactly
# what a page with a broken draw would still report.

#: HOW FAR OUT THE GHOST MAY BE, in game frames, on a single reading.
#:
#: Measured rather than chosen (probe, 2026-09-20, 168 consecutive readings of
#: steady play on this clip): drift is 0 in 104, 1 in 62, and reaches 3 once,
#: while the rate correction hunts between 0.967 and 1.1. It hunts because the
#: clip carries PER-PICTURE times, so the target is a picture's midpoint while
#: the presented `mediaTime` is its start -- half a picture of standing error
#: for the correction to chase, which a constant-rate fixture never showed.
#:
#: A bound of 2 on one sample of a hunting value is a coin flip (it failed 2
#: runs in 4), and the repo has paid for that shape before: take the step from
#: the recorded values and allow it. It keeps its teeth -- a ghost that had
#: stopped following drifts without bound, and the mutation that reads the
#: requested picture instead of the presented one still fails by name.
DRIFT_FRAMES = 3

#: The clip is 320x240, so a 640x480 window makes the `contain` mapping a
#: plain doubling with no offset -- which is what lets the expected rectangle
#: be computed HERE, by hand, instead of by the module under test.
MARIO_VIEWPORT = (640, 480)
MARIO_SCALE = 2
#: Boxes that JUMP between neighbouring pictures. A list that crept would let
#: a one-picture lag pass unnoticed; 50 clip pixels between consecutive
#: pictures is 100 on screen, and no tolerance hides that.
BOX_STRIDE = 50
BOX_WIDTH, BOX_HEIGHT = 40, 60
BOX_TOP = 30
#: `ui/ghost/mariobox.js::MARGIN_FRACTION` is 0 since 2026-09-20 -- he looked
#: at an 8 % margin on his own screen and asked for none ("as close to mario's
#: extents as possible"), so the cut-out IS the box. Stated here rather than
#: imported: this file is the independent opinion, and if the constant moves
#: again this number has to be moved deliberately rather than following along.
BOX_MARGIN = 0


def mario_box(index):
    """Mario's rectangle in picture `index`, in the clip's own pixels.

    ONE IN FIVE SITS IN THE BOTTOM-RIGHT CORNER, touching both edges. Not
    variety for its own sake: a clear that rounds the wrong way only leaves a
    line where the cut-out reached the canvas's own edge, so a generator that
    kept Mario comfortably inside the picture made that guard TOOTHLESS -- and
    it measurably was, until this row existed.
    """
    if index % 5 == 4:
        return [320 - BOX_WIDTH, 240 - BOX_HEIGHT, BOX_WIDTH, BOX_HEIGHT]
    return [20 + (index % 5) * BOX_STRIDE, BOX_TOP, BOX_WIDTH, BOX_HEIGHT]


def expected_on_canvas(box):
    """Where that rectangle must land on the canvas, with its margin, at a
    viewport of exactly the clip's aspect. Plain arithmetic, deliberately not
    the page's."""
    x, y, w, h = box
    left = max(0, x - BOX_MARGIN)
    top = max(0, y - BOX_MARGIN)
    right = min(320, x + w + BOX_MARGIN)
    bottom = min(240, y + h + BOX_MARGIN)
    return {"left": left * MARIO_SCALE, "top": top * MARIO_SCALE,
            "width": (right - left) * MARIO_SCALE,
            "height": (bottom - top) * MARIO_SCALE}


def box_list(pictures):
    return {"width": 320, "height": 240, "maker": "the test",
            "boxes": [mario_box(index) for index in range(pictures)]}


#: The painted region of the overlay's canvas, in CSS pixels, from its own
#: alpha channel, TOGETHER WITH the status that produced it. Everything the
#: page does not draw is left CLEAR -- which over Project64 is the game
#: showing through -- so the bounding box of what is not clear is exactly what
#: he would see.
#:
#: ONE READ, and that is not tidiness. A `requestVideoFrameCallback` fires
#: between two `evaluate` round trips and repaints; the first run of the
#: cut-out check reported the rectangle exactly one picture (100 screen pixels)
#: away from the box the status named, and the page was right both times.
PAINTED = """
(() => {
  const status = window.ghostStatus();
  const canvas = document.getElementById('mario');
  const ratio = window.devicePixelRatio || 1;
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  const data = ctx.getImageData(0, 0, w, h).data;
  let left = w, top = h, right = -1, bottom = -1, painted = 0;
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      if (data[(y * w + x) * 4 + 3] > 8) {
        painted += 1;
        if (x < left) left = x;
        if (x > right) right = x;
        if (y < top) top = y;
        if (y > bottom) bottom = y;
      }
    }
  }
  // PAINTED PIXELS OUTSIDE THE RECTANGLE THE PAGE SAYS IT DREW. A bounding
  // box cannot see a one-device-pixel line left behind by a clear that
  // rounded inward at 150%: it widens the box by two thirds of a CSS pixel,
  // inside any sane tolerance, while being a visible stripe of stale footage
  // over his game.
  let outside = 0;
  const drawn = status.mario_drawn;
  if (drawn) {
    const l = Math.floor(drawn.dx * ratio) - 1;
    const t = Math.floor(drawn.dy * ratio) - 1;
    const r = Math.ceil((drawn.dx + drawn.dw) * ratio) + 1;
    const b = Math.ceil((drawn.dy + drawn.dh) * ratio) + 1;
    for (let y = 0; y < h; y += 1)
      for (let x = 0; x < w; x += 1)
        if (data[(y * w + x) * 4 + 3] > 8
            && (x < l || x > r || y < t || y > b)) outside += 1;
  }
  const box = {painted, outside,
               picture: status.mario_picture, box: status.mario_box,
               view: status.view, canvas: {width: w / ratio, height: h / ratio}};
  if (!painted) return box;
  return {...box,
          left: left / ratio, top: top / ratio,
          width: (right - left + 1) / ratio,
          height: (bottom - top + 1) / ratio};
})()
"""

#: Two readings the test installs on the page ONCE, both built on the REAL
#: modules rather than on a restatement of what they do.
#:
#: `__slotOfPresented` asks `ui/frame.js` -- the clip's one owner of time ->
#: picture, the same door the follower reads through -- which picture the
#: PRESENTED media time names, and `__slotOfRequested` asks the same question
#: of `currentTime`, which is what the element was told to seek to. While the
#: video plays those two are about a picture apart, and that gap is what makes
#: the lag check able to fail at all.
#:
#: `__compareCutOut` is the colour reading: how much of the painted cut-out is
#: still the footage's own colour, compared against the SAME source rectangle
#: drawn into a scratch canvas the page has never seen.
INSTALL = """
(async () => {
  const frame = await import('/ui/frame.js');
  const answer = await (await fetch(
    '/api/ghost/source?kind=star&entity_key=LLL-1')).json();
  const clock = frame.clipClock(answer.view);
  const video = document.getElementById('ghost');
  const canvas = document.getElementById('mario');
  // Both readings and the page's own answer in ONE tick, for the reason
  // `PAINTED` takes its own: a picture can be presented between two calls.
  window.__followed = () => {
    const status = window.ghostStatus();
    return {presented: frame.slotAtTime(status.video_time, clock),
            requested: frame.slotAtTime(video.currentTime, clock),
            picture: status.mario_picture, box: status.mario_box};
  };
  window.__compareCutOut = () => {
    const drawn = window.ghostStatus().mario_drawn;
    if (!drawn) return {same: null, pixels: 0};
    const ratio = window.devicePixelRatio || 1;
    const ctx = canvas.getContext('2d');
    const painted = ctx.getImageData(
      Math.round(drawn.dx * ratio), Math.round(drawn.dy * ratio),
      Math.max(1, Math.round(drawn.dw * ratio)),
      Math.max(1, Math.round(drawn.dh * ratio)));
    const probe = document.createElement('canvas');
    probe.width = painted.width;
    probe.height = painted.height;
    const pctx = probe.getContext('2d');
    pctx.drawImage(video, drawn.sx, drawn.sy, drawn.sw, drawn.sh,
                   0, 0, probe.width, probe.height);
    const source = pctx.getImageData(0, 0, probe.width, probe.height).data;
    let same = 0, total = 0;
    for (let i = 0; i < painted.data.length; i += 4) {
      total += 1;
      if (Math.abs(painted.data[i] - source[i]) <= 8
          && Math.abs(painted.data[i + 1] - source[i + 1]) <= 8
          && Math.abs(painted.data[i + 2] - source[i + 2]) <= 8) same += 1;
    }
    return {same: total ? same / total : null, pixels: total};
  };
  return true;
})()
"""


def test_the_ghost_can_be_drawn_as_only_mario(tmp_path):
    """The whole round at the page's own level: the box list arrives, the view
    flips on the clock without a reload, only the box region is painted, it is
    painted where the footage has it, it follows the PRESENTED picture, and
    the colour lands on the box alone."""
    clip = tmp_path / "ghost.mp4"
    _build_clip(clip)

    with _Fixture(clip) as fixture, get_driver().launch() as page:
        fixture.boxes = box_list(len(VIEW["picture_igt"]))
        url = f"{fixture.url}/ui/ghost/index.html?kind=star&entity_key=LLL-1"
        page.set_viewport(*MARIO_VIEWPORT)
        page.goto(url)
        page.wait_for('body[data-ghost="ready"][data-shown="yes"]')
        assert page.evaluate(INSTALL) is True
        _the_page_took_the_box_list(page)
        _only_the_box_region_is_painted(page)
        _the_cut_out_is_where_the_footage_has_it(page)
        _the_box_follows_the_presented_picture(page)
        _the_colour_lands_on_the_cut_out(page)
        _a_still_ghost_still_takes_a_new_colour(page)
        _the_ghost_still_follows_in_the_mario_view(page)
        _the_whole_picture_view_is_the_base_behaviour(page)
        _a_ghost_with_no_boxes_never_offers_the_view(page, fixture, url)
        _a_list_that_appears_later_is_picked_up(page, fixture)


def test_native_mario_packets_carry_the_pixels_the_real_page_just_drew(tmp_path):
    """Exercise the native entry point; canvas transparency alone was false proof."""
    from sm64_events.ghost.window.presentation import read_frame

    clip = tmp_path / "ghost-native.mp4"
    _build_clip(clip)
    with _Fixture(clip) as fixture, get_driver().launch() as page:
        fixture.boxes = box_list(len(VIEW["picture_igt"]))
        page.set_viewport(*MARIO_VIEWPORT)
        page.goto(f"{fixture.url}/ui/ghost/index.html?kind=star&entity_key=LLL-1"
                  "&native_session=test-native")
        page.wait_for('body[data-ghost="ready"][data-shown="yes"]')
        page.evaluate("""(() => {
          window.__native = {count: 0, mismatches: 0};
          window.pywebview = {api: {present: async (packet) => {
            window.__native.last = packet;
            if (packet.mode === 'mario' && packet.visible) {
              const bytes = atob(packet.pixels);
              const actual = document.getElementById('mario').getContext('2d')
                .getImageData(packet.x, packet.y, packet.width, packet.height).data;
              if (bytes.length !== actual.length || [...actual].some(
                    (value, index) => value !== bytes.charCodeAt(index)))
                window.__native.mismatches++;
              window.__native.count++;
              window.__native.drawn = packet;
            }
            return {accepted: true, viewport: {width: 961, height: 721}};
          }}};
          dispatchEvent(new Event('pywebviewready'));
          return true;
        })()""")
        fixture.feed.set_view("mario")
        state = _trace(page, list(range(3, 65)))
        assert state["native_bridge"] and state["view"] == "mario", state
        delivered = page.evaluate("window.__native")
        assert delivered["count"] > 10, delivered["count"]
        assert delivered["mismatches"] == 0
        frame = read_frame(delivered["drawn"])
        assert frame.mode == "mario" and frame.visible
        assert (frame.viewport_width, frame.viewport_height) == (961, 721)
        assert frame.width * frame.height < frame.viewport_width * frame.viewport_height / 10
        assert len(frame.rgba) == frame.width * frame.height * 4
        assert delivered["drawn"]["session"] == "test-native"
        assert page.evaluate("getComputedStyle(document.getElementById('mario')).visibility") == "hidden"
        # A star grab must clear the native crop through the same entry point.
        _trace(page, [65] * 4, action=GRAB_ACTION)
        assert page.evaluate("window.__native.last.visible") is False
        fixture.feed.set_view("whole")
        _trace(page, [0, 1, 2, 3, 4, 5])
        whole = page.evaluate("window.__native.last")
        assert whole["mode"] == "whole" and whole["pixels"] is None
        problems = page.problems()
        assert problems == [], problems


def _the_page_took_the_box_list(page):
    """It indexes like the clip's own per-picture arrays, and flipping the
    view is a message on the clock rather than a navigation."""
    status = page.evaluate("window.ghostStatus()")
    assert status["boxes_available"] is True, status
    assert status["boxes_pictures"] == len(VIEW["picture_igt"]), status
    assert status["view"] == "whole", "the ghost starts as the one he knows"

    page.evaluate("(() => { window.__alive = 'yes'; return true; })()")
    page.evaluate("(() => { fetch('/test/view?name=mario', {method: 'POST'});"
                  " return true; })()")
    _trace(page, list(range(3, 30)))
    assert page.evaluate("window.__alive") == "yes", "the page was reloaded"
    after = page.evaluate("window.ghostStatus()")
    assert after["view"] == "mario" and after["view_requested"] == "mario"
    assert page.evaluate(
        "getComputedStyle(document.getElementById('ghost')).visibility"
    ) == "hidden", "the whole picture is still on screen under the cut-out"
    assert page.evaluate(
        "getComputedStyle(document.getElementById('mario')).visibility"
    ) == "visible"


def _only_the_box_region_is_painted(page):
    """(a) Everything outside the box is left CLEAR, which over Project64 is
    the game showing through. Measured on the alpha channel of the real
    canvas, never on a style."""
    painted = page.evaluate(PAINTED)
    assert painted["painted"] > 0, "nothing was painted at all"
    assert painted["canvas"] == {"width": MARIO_VIEWPORT[0],
                                 "height": MARIO_VIEWPORT[1]}, painted
    # A cut-out, not a picture: 50 x 70 clip pixels of 320 x 240 is under 5%.
    whole = MARIO_VIEWPORT[0] * MARIO_VIEWPORT[1]
    assert painted["painted"] < whole * 0.10, painted


def _the_cut_out_is_where_the_footage_has_it(page):
    """(b) At a viewport of the clip's own aspect the mapping is a doubling,
    so the expected rectangle is arithmetic this file does itself."""
    painted = page.evaluate(PAINTED)
    box = painted["box"]
    assert box is not None, painted
    assert box == mario_box(painted["picture"]), painted
    want = expected_on_canvas(box)
    for edge in ("left", "top", "width", "height"):
        assert abs(painted[edge] - want[edge]) <= 2, (
            f"{edge}: painted {painted[edge]} against {want[edge]} for box "
            f"{box} on picture {painted['picture']}")
    assert painted["outside"] == 0, (
        f"{painted['outside']} pixels are painted outside the cut-out -- a "
        "previous one was not fully cleared")


def _the_box_follows_the_presented_picture(page):
    """(c) THE ONE A STATUS FIELD CANNOT SETTLE. The box drawn has to be the
    one for the picture Chromium PRESENTED, not for the one the element was
    asked to seek to -- those are about a picture apart while it plays, and a
    Mario a picture behind his own footage visibly swims.

    The boxes jump 50 clip pixels between neighbours, so a one-picture slip is
    100 screen pixels out and no tolerance hides it. Both readings come from
    `ui/frame.js` itself rather than from a restatement of what it does.
    """
    seen = []
    for start in (40, 70, 100, 130):
        _trace(page, list(range(start, start + 12)))
        seen.append(page.evaluate("(() => window.__followed())()"))
    # ...and MID-TRACE, while the element is really playing. A trace that has
    # settled leaves the two clocks equal, and comparing them there compares a
    # function with itself: the assertion below can only fail if they can
    # differ, so at least one sample must catch them apart.
    seen += _followed_while_playing(page, list(range(160, 240)))
    for row in seen:
        assert row["picture"] == row["presented"], seen
        assert row["box"] == mario_box(row["presented"]), seen
    # ...and the pictures really moved, or every line above is vacuous.
    assert len({row["picture"] for row in seen}) > 1, seen
    apart = [row for row in seen if row["requested"] != row["presented"]]
    assert apart, (
        "the requested and presented pictures never differed, so this check "
        f"could not have failed: {seen}")


def _followed_while_playing(page, igts):
    """Samples taken DURING a trace rather than after it settles.

    The page's own trace door returns when the clock goes quiet, and by then
    the element has caught up -- `currentTime` and the presented picture agree,
    and the difference this whole check is about has gone.
    """
    page.evaluate(_TRACE % json.dumps({"igts": list(igts)}))
    rows = []
    for _ in range(12):
        page.wait_ms(60)
        if page.evaluate("window.__done === true"):
            break
        row = page.evaluate("(() => window.__followed())()")
        if row["picture"] is not None:
            rows.append(row)
    for _ in range(80):
        if page.evaluate("window.__done === true"):
            break
        page.wait_ms(50)
    return rows


def _the_colour_lands_on_the_cut_out(page):
    """(d) His ruling, 2026-09-20: *"color correction should only apply to the
    Mario box now (it shouldn't impact anything other than the cutout
    mario)."*

    In the MARIO view the cut-out is the only thing on screen, so colouring it
    is colouring the ghost. Compared against the SAME pixels cut out of the
    video by this test: the cut-out matches the footage's own colours while the
    shift is off and stops matching them while it is on.

    READ ON A HELD GHOST -- a repeated counter value, so the follower pauses
    the element. The canvas was painted on the last presented picture and this
    comparison draws the video as it is NOW; while it is playing those can be
    one picture apart, and the reading is then about two different moments of
    the footage rather than about the colour. Measured at 0.75 under a loaded
    run (three browser files in one process), and it is the same class of
    mistake as reading the status and the canvas in two round trips.
    """
    page.evaluate("(() => { fetch('/test/tint?enabled=false&hue_degrees=120',"
                  " {method: 'POST'}); return true; })()")
    _trace(page, [170] * 12)
    plain = page.evaluate("(() => window.__compareCutOut())()")
    assert plain["pixels"] > 1000, plain
    assert plain["same"] > 0.9, (
        f"the cut-out does not match the footage it came from: {plain}")

    page.evaluate("(() => { fetch('/test/tint?enabled=true&hue_degrees=120',"
                  " {method: 'POST'}); return true; })()")
    _trace(page, [170] * 12)
    shifted = page.evaluate("(() => window.__compareCutOut())()")
    assert shifted["same"] < plain["same"] - 0.3, (
        f"the colour shift changed nothing inside the box: {shifted} "
        f"against {plain}")
    status = page.evaluate("window.ghostStatus()")
    assert status["box_filter"] == "hue-rotate(120deg)", status
    # BOTH surfaces wear the same string: flipping the view must change what is
    # under the colour, never the colour.
    assert status["tint_filter"] == "hue-rotate(120deg)", status


def _a_still_ghost_still_takes_a_new_colour(page):
    """F1. The canvas only paints when a picture is presented, so with the
    game paused, the Usamune menu open, or Project64 auto-paused because he
    clicked the trainer, dragging the colour slider changed NOTHING -- and
    those are exactly the moments he would be tuning it.

    Driven with no new picture at all: the clock is left alone, so the element
    is holding, and the only thing that happens is the colour arriving.
    """
    _trace(page, [220] * 8)          # a stalled timer: the ghost holds
    before = page.evaluate(PAINTED)
    assert before["painted"] > 0, before
    held = page.evaluate("window.ghostStatus()")
    assert held["paused"] is True, held

    page.evaluate("(() => { fetch('/test/tint?enabled=false&hue_degrees=120',"
                  " {method: 'POST'}); return true; })()")
    # ONE sample, the same picture again: the tint rides it, nothing new is
    # presented, and the cut-out still has to change.
    _trace(page, [220])
    after = page.evaluate("(() => window.__compareCutOut())()")
    assert after["same"] > 0.9, (
        f"a still ghost kept its old colour: {after}")
    page.evaluate("(() => { fetch('/test/tint?enabled=true&hue_degrees=120',"
                  " {method: 'POST'}); return true; })()")
    _trace(page, [220])
    back = page.evaluate("(() => window.__compareCutOut())()")
    assert back["same"] < 0.2, f"the colour did not come back: {back}"


def _the_whole_picture_view_is_the_base_behaviour(page):
    """THE DECISION CHANGE. The canvas paints in the Mario view and nowhere
    else: it and the element reach the screen by two different paths, so a
    cut-out laid over the footage can land a refresh after the picture under
    it -- on a pan, a tinted rectangle of scenery sliding against the game.
    The whole view is therefore exactly what it was before this round."""
    page.evaluate("(() => { fetch('/test/view?name=whole', {method: 'POST'});"
                  " return true; })()")
    _trace(page, list(range(200, 215)))
    status = page.evaluate("window.ghostStatus()")
    assert status["view"] == "whole", status
    assert status["boxes_available"] is True, "the boxes are still there"
    assert page.evaluate(PAINTED)["painted"] == 0, (
        "the cut-out is painted over the whole picture")
    assert status["tint_filter"] == "hue-rotate(120deg)", status
    assert page.evaluate(
        "getComputedStyle(document.getElementById('ghost')).visibility"
    ) == "visible"
    assert page.evaluate(
        "getComputedStyle(document.getElementById('mario')).visibility"
    ) == "hidden"
    page.evaluate("(() => { fetch('/test/view?name=mario', {method: 'POST'});"
                  " return true; })()")
    _trace(page, list(range(215, 230)))
    assert page.evaluate(PAINTED)["painted"] > 0, "the cut-out did not return"


def _the_ghost_still_follows_in_the_mario_view(page):
    """F6(d). The element is HIDDEN in this view, and a hidden element is the
    kind of thing a browser is allowed to stop feeding. If it quietly stopped
    following, every cut-out would be correct and every one of them would be
    from the wrong moment -- which no pixel check here would notice."""
    following = _trace(page, list(range(240, 290)))
    assert following["presentable"] is True, following
    assert following["decision"]["reason"] == "playing", following
    # The DECISION's own drift, not `shown_igt` against `target_igt`: the
    # status is captured after the last sample and the element keeps PLAYING
    # through it, so the difference read afterwards is the video's progress
    # since, not the follower's error. This file already paid for that reading
    # once (2026-09-18) and it cost this check a false red.
    assert following["decision"]["drift"] is not None, following
    assert abs(following["decision"]["drift"]) <= DRIFT_FRAMES, following


def _a_ghost_with_no_boxes_never_offers_the_view(page, fixture, url):
    """Every recording made before this round. Asking for Mario is NOT an
    error -- the setting is app-wide and he walks between stars -- so the page
    draws the whole picture and says why."""
    fixture.boxes = None
    page.goto(url)
    page.wait_for('body[data-ghost="ready"][data-shown="yes"]')
    page.evaluate("(() => { window.__alive = 'yes'; return true; })()")
    _trace(page, list(range(3, 20)))
    status = page.evaluate("window.ghostStatus()")
    assert status["boxes_available"] is False, status
    assert status["boxes_reason"] == "no_box_list", status
    assert status["view_requested"] == "mario", "the session still asks"
    assert status["view"] == "whole", "but the page draws what it has"
    assert page.evaluate(
        "getComputedStyle(document.getElementById('ghost')).visibility"
    ) == "visible"
    assert page.evaluate(PAINTED)["painted"] == 0
    # ...and the whole picture wears the colour, as it always has.
    assert status["tint_filter"] == "hue-rotate(120deg)", status


def _a_list_that_appears_later_is_picked_up(page, fixture):
    """F7. The boxes are made outside the app, one clip at a time, and land
    beside the clip whenever that finishes. The practice card notices within a
    couple of seconds because the session re-resolves on a timer; until this
    the PAGE did not, so the control offered the Mario view while the overlay
    kept drawing the whole picture until he switched it off and on.

    Entered with no boxes and the Mario view already asked for -- which is
    exactly the state he would be in, having turned it on for the star whose
    boxes are still being made.
    """
    assert page.evaluate("window.ghostStatus()")["boxes_available"] is False
    fixture.boxes = box_list(len(VIEW["picture_igt"]))
    # The page asks again on its own timer; nothing here touches it.
    for _ in range(30):
        page.wait_ms(500)
        if page.evaluate("window.ghostStatus()")["boxes_available"]:
            break
    status = page.evaluate("window.ghostStatus()")
    assert status["boxes_available"] is True, (
        "a box list that appeared while the ghost was up never arrived")
    assert status["view"] == "mario", status
    _trace(page, list(range(300, 320)))
    assert page.evaluate(PAINTED)["painted"] > 0, "no cut-out was drawn"
    assert page.evaluate("window.__alive") == "yes", (
        "the page reloaded to pick up a box list")


# ---------------------------------------------------------------------------
# REGISTRATION: the cut-out lands on the whole picture's own box
# ---------------------------------------------------------------------------
#
# Every geometry check above runs at a viewport of the clip's own aspect, where
# the mapping is a doubling and the arithmetic is trivial. That is the easy end
# of the range, and it is not the shape his window is: Project64 resized to
# anything else letterboxes the picture inside the overlay, and his machine
# runs at 150%.
#
# Measured against the VIDEO'S OWN PAINTED BOX rather than against a restated
# `containRect`: the whole picture is drawn by the browser from `object-fit:
# contain`, and where it lands is the thing the cut-out has to agree with. Two
# backdrops, because the stand-in clip is a colour-bar pattern and a pixel of
# it can legitimately match one of them -- a pixel matching BOTH is impossible.

REGISTRATION_BACKDROPS = ("#ff00ff", "#0a2f10")


def _painted_video_box(page):
    """Where the whole picture actually lands, in CSS pixels, from a
    screenshot of the composed page with the cut-out switched off."""
    from PIL import Image, ImageChops, ImageColor

    shots = []
    for colour in REGISTRATION_BACKDROPS:
        page.evaluate("(() => { document.documentElement.style.background ="
                      f" {colour!r}; return true; }})()")
        page.wait_ms(150)
        shots.append((colour, page.screenshot()))
    page.evaluate("(() => { document.documentElement.style.background = '';"
                  " return true; })()")
    painted, size = None, None
    for colour, shot in shots:
        image = Image.open(io.BytesIO(shot)).convert("RGB")
        size = image.size
        backdrop = Image.new("RGB", size, ImageColor.getrgb(colour))
        difference = ImageChops.difference(image, backdrop)
        painted = (difference if painted is None
                   else ImageChops.lighter(painted, difference))
    box = painted.getbbox()
    assert box is not None, "the whole picture painted nothing at all"
    ratio = page.evaluate("window.devicePixelRatio || 1")
    left, top, right, bottom = box
    return {"left": left / ratio, "top": top / ratio,
            "width": (right - left) / ratio, "height": (bottom - top) / ratio,
            "ratio": ratio, "image": size}


def _registers_on_the_whole_picture(page, fixture, url, viewport):
    """The cut-out's painted rectangle, against the same rectangle derived
    from the picture's OWN painted box and the box's place inside the clip."""
    page.set_viewport(*viewport)
    page.goto(url)
    page.wait_for('body[data-ghost="ready"][data-shown="yes"]')
    page.evaluate("(() => { fetch('/test/view?name=whole', {method: 'POST'});"
                  " return true; })()")
    _trace(page, list(range(60, 80)))
    whole = _painted_video_box(page)

    page.evaluate("(() => { fetch('/test/view?name=mario', {method: 'POST'});"
                  " return true; })()")
    _trace(page, list(range(80, 100)))
    cut = page.evaluate(PAINTED)
    assert cut["painted"] > 0, (viewport, cut)
    box = cut["box"]
    assert box == mario_box(cut["picture"]), cut

    # The grown box as a FRACTION of the clip's own picture, then that
    # fraction of where the whole picture really landed. Nothing here restates
    # the page's mapping; it reads the browser's.
    x, y, w, h = box
    left = max(0, x - BOX_MARGIN) / 320
    top = max(0, y - BOX_MARGIN) / 240
    right = min(320, x + w + BOX_MARGIN) / 320
    bottom = min(240, y + h + BOX_MARGIN) / 240
    want = {"left": whole["left"] + left * whole["width"],
            "top": whole["top"] + top * whole["height"],
            "width": (right - left) * whole["width"],
            "height": (bottom - top) * whole["height"]}
    # Two CSS pixels: the whole picture's box is read off a screenshot, so its
    # own edges carry a pixel of rounding at either end.
    for edge in ("left", "top", "width", "height"):
        assert abs(cut[edge] - want[edge]) <= 2, (
            f"{viewport} at {whole['ratio']}x -- {edge}: cut-out "
            f"{cut[edge]:.2f} against the picture's own {want[edge]:.2f} "
            f"(picture box {whole})")
    assert cut["outside"] == 0, (
        f"{viewport} at {whole['ratio']}x: {cut['outside']} pixels painted "
        "outside the cut-out")
    _nothing_is_left_behind_at_the_edge(page, viewport, whole["ratio"])
    return {"viewport": viewport, "ratio": whole["ratio"],
            "picture": whole, "cut": {k: cut[k] for k in
                                      ("left", "top", "width", "height")}}


def _nothing_is_left_behind_at_the_edge(page, viewport, ratio):
    """A cut-out that reached the canvas's own edge, then one that did not.

    The clear runs in DEVICE pixels before the ratio transform. In CSS pixels
    it rounds inward, and at 150% the backing store is up to half a device
    pixel wider than the rectangle a CSS-pixel clear covers -- so a box against
    the right or bottom edge leaves a line of the previous cut-out behind: a
    stripe of stale footage over his game, from a picture that has moved on.
    Invisible to a bounding box, which it widens by two thirds of a CSS pixel.
    """
    # FOUND rather than computed: which picture a counter value lands on runs
    # through the template, the seek and the decoder, and a test that assumed
    # the arithmetic asked for the wrong one.
    corner = None
    for igt in range(60, 76):
        corner = _trace(page, [igt] * 5)
        if corner["mario_picture"] is not None and corner["mario_picture"] % 5 == 4:
            break
    assert corner and corner["mario_picture"] % 5 == 4, (
        f"no corner box was reached: {corner}")
    assert page.evaluate(PAINTED)["painted"] > 0, corner

    # Stepped until the PICTURE changes, not the counter: neighbouring counter
    # values can land on the same picture, and moving the clock is not the
    # same as moving the cut-out.
    away = None
    for step in range(1, 8):
        away = _trace(page, [corner["sample"]["igt"] + step] * 5)
        if away["mario_picture"] != corner["mario_picture"]:
            break
    assert away and away["mario_picture"] != corner["mario_picture"], (
        f"the ghost never left the corner box: {away}")
    painted = page.evaluate(PAINTED)
    assert painted["outside"] == 0, (
        f"{viewport} at {ratio}x: {painted['outside']} pixels of the corner "
        "cut-out survived the clear")


@pytest.mark.parametrize("ratio", [1.0, 1.5])
def test_the_cut_out_registers_on_the_whole_picture(tmp_path, ratio):
    """A LETTERBOXED window and his own 150%: the two shapes where a mapping
    error is visible and the easy 4:3 case hides one.

    At 150% the canvas's backing store is 1.5 device pixels per CSS pixel, so
    a clear that rounds the wrong way leaves a line of the previous cut-out on
    his screen, and a size taken from a rounded `clientWidth` puts the cut-out
    up to half a pixel off the picture it came from.
    """
    clip = tmp_path / "ghost.mp4"
    _build_clip(clip)
    with _Fixture(clip) as fixture, \
            get_driver().launch(device_scale_factor=ratio) as page:
        fixture.boxes = box_list(len(VIEW["picture_igt"]))
        url = f"{fixture.url}/ui/ghost/index.html?kind=star&entity_key=LLL-1"
        measured = [
            _registers_on_the_whole_picture(page, fixture, url, (800, 400)),
            _registers_on_the_whole_picture(page, fixture, url, (500, 700)),
        ]
    # The rig has to have MEASURED a letterbox, or it proved nothing about one.
    wide, tall = measured
    assert wide["picture"]["left"] > 10, wide
    assert tall["picture"]["top"] > 10, tall
    assert abs(measured[0]["ratio"] - ratio) < 0.01, measured


# ---------------------------------------------------------------------------
# HIS OWN FOOTAGE, and the box list a hand-run session really produced
# ---------------------------------------------------------------------------
#
# Everything above runs on a 320x240 colour-bar stand-in with a box list this
# file invented: every picture has a box, every box is the same size, and the
# clip is a tenth the size of a real one. A real list is none of those things
# -- 1600x1200, several hundred pictures, and a good fraction of them with NO
# box. The nulls arrive BOTH ways: a long run of menu and fade at the head of
# a recording, and single-picture dropouts where a fast flip lost him. Nothing
# interpolates them; "null means null" is the boxes worker's deliberate call.
#
# Skipped rather than failed when the footage is not on this machine, the way
# `tests/test_ui_ghost_follower.py` treats his clips: it is HIS recording, not
# a fixture, and a fresh clone has neither the clip nor the boxes.


def _his_clips():
    """His own saved clips, READ-ONLY, wherever this checkout can see them.

    The feature worktree is where he was recording and where the boxes worker
    wrote the lists, so that is the interesting tree even from a child
    worktree. `SM64_GHOST_CLIP_ROOTS` aims this elsewhere without copying
    anything.
    """
    override = os.environ.get("SM64_GHOST_CLIP_ROOTS", "").strip()
    if override:
        roots = [Path(part) for part in override.split(os.pathsep) if part]
    else:
        # EVERY CHECKOUT ABOVE THIS ONE, not just the main one. `replays/` is
        # gitignored, so it belongs to whichever tree he was recording in --
        # and a child worktree's git common dir points at the MAIN checkout,
        # which is not where a feature worktree's footage sits. Walking the
        # ancestors finds the feature worktree this one is nested inside.
        roots = [REPO / "replays"]
        roots += [parent / "replays" for parent in REPO.parents]
        try:
            common = subprocess.run(
                ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute",
                 "--git-common-dir"],
                capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            common = ""
        if common:
            roots.append(Path(common).parent / "replays")
    found = []
    for root in roots:
        if not root.exists():
            continue
        for clip in sorted(root.rglob("attempt_*.mp4")):
            boxes = clip.with_name(clip.stem + ".mario.json")
            side = clip.with_suffix(".json")
            if boxes.exists() and side.exists():
                found.append((clip, side, boxes))
    return found


def _real_ghosts():
    """Every clip of his with a box list that indexes it, newest first."""
    for clip, side, listed in _his_clips():
        view = json.loads(side.read_text(encoding="utf-8"))
        boxes = json.loads(listed.read_text(encoding="utf-8-sig"))
        if len(boxes.get("boxes", ())) == len(view.get("picture_igt") or ()):
            yield clip, view, boxes


def test_the_cut_out_holds_on_his_own_footage():
    """A REAL clip, a REAL box list, and the two states an empty overlay can
    mean told apart.

    The one thing the synthetic list cannot exercise: a picture Mario is not
    in. Nothing bridges those, so the honest answer on them is an empty
    screen -- and `ghostStatus()` has to say WHICH empty screen it is, or a
    flicker he reports cannot be read off the log.
    """
    playable = None
    with get_driver().launch() as page:
        page.set_viewport(800, 600)
        for clip, view, boxes in _real_ghosts():
            # NOT EVERY CLIP OF HIS CAN BE A GHOST, and that is the follower
            # working: a star that crosses into a subarea restarts Usamune's
            # counter mid-run, one counter value then names two pictures, and
            # the template refuses (`counter_restarts`) rather than picking
            # one. His Hazy Maze Cave clip is exactly that. The span the real
            # server computes from the attempt row is what narrows those; this
            # fixture has no database, so it takes the first clip that stands
            # on its own.
            fixture = _Fixture(clip)
            fixture.boxes = boxes
            fixture.source_view = view
            with fixture:
                page.goto(f"{fixture.url}/ui/ghost/index.html"
                          "?kind=star&entity_key=WF-1")
                page.wait_ms(1500)
                if page.evaluate("window.ghostStatus().state") == "ready":
                    playable = (clip, view, boxes)
                    break
    if playable is None:
        pytest.skip("no saved clip with a usable Mario box list on this machine")

    clip, view, boxes = playable
    listed = boxes["boxes"]
    with_mario = [i for i, box in enumerate(listed) if box]
    without = [i for i, box in enumerate(listed) if not box]
    assert with_mario and without, f"{clip.name} exercises neither state"

    fixture = _Fixture(clip)
    fixture.boxes = boxes
    with fixture, get_driver().launch() as page:
        fixture.source_view = view
        url = f"{fixture.url}/ui/ghost/index.html?kind=star&entity_key=WF-1"
        page.set_viewport(800, 600)
        page.goto(url)
        page.wait_for('body[data-ghost="ready"][data-shown="yes"]')
        page.evaluate("(() => { fetch('/test/view?name=mario', "
                      "{method: 'POST'}); return true; })()")

        state = page.evaluate("window.ghostStatus()")
        assert state["boxes_available"] is True, state
        assert state["boxes_pictures"] == len(listed), state
        assert (boxes["width"], boxes["height"]) == (
            state["picture_size"]["width"], state["picture_size"]["height"]), (
            "the boxes were measured on a different decode of this clip")

        drawn = _drive_to_a_picture(page, view, with_mario)
        assert drawn is not None, "no picture with a box was ever presented"
        assert drawn["mario_state"] == "drawn", drawn
        assert drawn["mario_box"] == listed[drawn["mario_picture"]], drawn
        painted = page.evaluate(PAINTED)
        assert painted["painted"] > 0, drawn
        assert painted["outside"] == 0, painted

        empty = _drive_to_a_picture(page, view, without)
        assert empty is not None, "no picture without a box was presented"
        assert empty["mario_state"] == "no_mario", empty
        assert empty["mario_box"] is None, empty
        assert page.evaluate(PAINTED)["painted"] == 0, (
            "a picture Mario is not in painted something")
        # ...and the two empty screens are told apart, which is the whole
        # point of the field: no list at all says something else.
        assert empty["boxes_available"] is True, empty
        assert empty["pictures_without_mario"] >= 1, empty


def _drive_to_a_picture(page, view, wanted):
    """Hold the clock on a counter value until a picture from `wanted` is the
    one on screen.

    Driven by the CLOCK, like everything else here: there is no door that says
    "show picture 200", and inventing one would test a path the ghost never
    takes. Several counter values can land on one picture, so this walks.
    """
    stamps = view["picture_igt"]
    targets = set(wanted)
    for index in wanted[:: max(1, len(wanted) // 24)]:
        igt = stamps[index]
        if igt is None:
            continue
        status = _trace(page, [igt] * 6)
        if status["mario_picture"] in targets:
            return status
    return None
