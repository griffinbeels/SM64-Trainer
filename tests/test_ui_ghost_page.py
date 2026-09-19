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
# A constant-rate stand-in for a template: one picture per game frame, the
# counter running from zero. The follower's own VFR and offset-cut cases are
# scored in the trace harness; what this file needs is a clip a browser will
# really play.
VIEW = {"frame_times": None, "picture_igt": list(range(CLIP_SECONDS * CLIP_FPS)),
        "duration_s": float(CLIP_SECONDS), "video_start_s": 0.0,
        "fps": CLIP_FPS, "game_fps": 30, "encode": "cfr"}

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


def _snapshot(igt: int, timer: int, level: int, area: int) -> GameSnapshot:
    """The shape the poll loop hands the feed, so the wire format under test
    is the real one rather than a dict shaped like it."""
    return GameSnapshot(
        wall_time_utc=datetime.now(timezone.utc), global_timer=timer,
        mario_action=0, mario_action_timer=0, num_stars=0,
        last_completed_course=0, last_completed_star=0,
        igt_overall=igt, curr_level=level, curr_area=area)


class _Fixture:
    """The page's server: the real clock router, a source payload, the clip,
    and one door for the test to push a snapshot in at the poll side."""

    def __init__(self, clip: Path):
        self.available = True
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
            return AVAILABLE if self.available else UNAVAILABLE

        @app.post("/test/clock")
        def clock(igt: int, global_timer: int, level: int = 8, area: int = 1):
            self.feed.note(_snapshot(igt, global_timer, level, area))
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
# emulator is paused. So the page samples itself 25 ms after the last one.
_TRACE = """(() => {
  const igts = %s;
  window.__done = false; window.__result = null;
  let k = 0;
  const id = setInterval(() => {
    fetch(`/test/clock?igt=${igts[k]}&global_timer=${9000 + k}`,
          {method: 'POST'});
    k += 1;
    if (k >= igts.length) {
      clearInterval(id);
      setTimeout(() => {
        window.__result = window.ghostStatus();
        window.__done = true;
      }, 120);
    }
  }, 33);
  return true;
})()"""


def _trace(page, igts: list) -> dict:
    """Run one clock trace and answer with what the page was doing at its end."""
    page.evaluate(_TRACE % json.dumps(igts))
    for _ in range(len(igts) * 2 + 80):
        if page.evaluate("window.__done === true"):
            break
        page.wait_ms(50)
    result = page.evaluate("window.__result")
    assert result is not None, "the trace never finished"
    return result


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
        assert page.evaluate(
            "getComputedStyle(document.getElementById('ghost')).visibility"
        ) == "hidden"

        # 2. A real template: the page resolves it and opens the clip.
        fixture.available = True
        page.goto(url)
        page.wait_for('body[data-ghost="ready"]')
        assert page.evaluate("window.ghostStatus().template.pictures") == 600
        # It asked about the star in its OWN address, in the template store's
        # spelling, and asked for nothing else: the nudge is not a page, so it
        # is not in the URL (see ghost/session.py::follower_url).
        assert fixture.source_queries[-1] == {"kind": "star",
                                              "entity_key": "LLL-1"}
        assert page.evaluate("document.getElementById('ghost').muted") is True
        # Full-bleed: the video fills the window, with no chrome beside it.
        box = page.evaluate(
            "(() => { const r = document.getElementById('ghost')"
            ".getBoundingClientRect(); return {w: r.width, h: r.height,"
            " x: r.x, y: r.y}; })()")
        assert box["x"] == 0 and box["y"] == 0
        assert box["w"] == page.evaluate("window.innerWidth")
        assert box["h"] == page.evaluate("window.innerHeight")

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
        #    on the template's own first picture until the timer moves.
        reset = _trace(page, [0])
        assert reset["decision"]["reason"] == "reset", reset
        assert reset["paused"] is True, reset
        assert reset["shown_igt"] == 0, reset

        # 6. His timing hotkey, over the channel the companion window uses.
        _post_message(page, "{type: 'ghost-nudge', nudge_frames: 45}")
        nudged = _trace(page, list(range(100, 131)))
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

        # `problems()` DRAINS, so it is asked once and the answer kept --
        # asking twice (once for the assertion, once for its message) reports
        # a clean page under the failure it just swallowed.
        problems = page.problems()
        assert problems == [], problems

    assert fixture.feed.listeners == 0
    assert fixture.feed.running is False
