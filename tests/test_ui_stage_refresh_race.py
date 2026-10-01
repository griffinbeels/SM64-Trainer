"""A session response captured before a warp cannot move the selector back."""
import json

from test_ui_special_stages import (
    BANNER, _Play, _clean, _control, _read, _target, _wait,
    get_driver, serve_ui_live,
)
from uilab.trace import record


def test_delayed_session_response_preserves_the_new_websocket_stage(tmp_path):
    with serve_ui_live(tmp_path / "stage-race.db", seed=False,
                       bundled_library=False) as (base, service), \
            get_driver().launch(headless=True, viewport=(850, 1000)) as page:
        play = _Play(service)
        _control(page, base, play)  # A real BoB click and settled browser writes.
        catalog = _read(base)["catalog"]["courses"]
        heading = next(course["name"] for course in catalog if course["id"] == 20)
        page.evaluate("""
        (() => {
          const original = window.fetch;
          const probe = window.stageRefreshRace = {
            old: null, later: [], pending: 0, oldDelivered: false
          };
          const oldGate = new Promise(resolve => {
            window.releaseOldStageView = resolve;
          });
          const laterGate = new Promise(resolve => {
            window.releaseLaterStageViews = resolve;
          });
          window.fetch = async (...args) => {
            probe.pending++;
            try {
              const response = await original(...args);
              if (new URL(String(args[0]), location.href).pathname === '/api/session') {
                const body = await response.clone().json();
                const captured = {stage: body.stage, target: body.target};
                if (probe.old === null) {
                  probe.old = captured;
                  await oldGate;  // The real old Response has already returned.
                  probe.oldDelivered = true;
                } else {
                  probe.later.push(captured);
                  // Keep a later refresh from concealing the stale-stage flash.
                  await laterGate;
                }
              }
              return response;
            } finally { probe.pending--; }
          };
          window.restoreStageRaceFetch = () => { window.fetch = original; };
        })()
        """)
        trace = None
        try:
            play.publish("attempts_invalidated", {})  # Shipping refresh trigger.
            _wait(page, "window.stageRefreshRace.old !== null")
            old = page.evaluate("window.stageRefreshRace.old")
            assert old["stage"]["level"] == 9
            assert old["target"]["course_id"] == 1

            play.enter(28)  # Real level/stage detectors and WebSocket broadcast.
            _wait(page, "document.querySelector('.stagebanner .shead b')"
                  f"?.textContent === {json.dumps(heading)}")

            def release_old(_page):
                page.evaluate("releaseOldStageView()")
                _wait(page, "window.stageRefreshRace.oldDelivered && "
                      "window.stageRefreshRace.later.length > 0")

            trace = record(page, watch={"heading": BANNER + " .shead b"},
                           trigger=release_old, ms=650)
            later = page.evaluate("window.stageRefreshRace.later")
            assert any(view["stage"]["level"] == 28 for view in later), later
        finally:
            page.evaluate("releaseOldStageView(); releaseLaterStageViews();")
            _wait(page, "window.stageRefreshRace.pending === 0")
            page.evaluate("restoreStageRaceFetch()")

        frames = trace.of("heading").frames
        assert len(frames) >= 8, "Too few frames to observe the response delivery"
        assert all(frame.get("text") == heading for frame in frames), frames
        _target(page, base, 20, 0)
        view = _read(base)
        assert view["stage"]["level"] == 28
        assert view["target"]["course_id"] == 20
        assert view["target"]["star_id"] == 0
        _clean(page, play)  # Includes every HTTP/console fault, including 409.
