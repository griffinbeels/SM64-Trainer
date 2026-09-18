"""The ghost session (interface I5) answered beside the real UI fixture.

Track E builds the server side of `GET|PUT /api/ghost` in parallel with the
practice card's switch, so the card cannot be rendered against the real route
yet. This serves the frozen SHAPE instead, attached to the same offline app
`tools/ui_fixture.py` already boots -- the real `index.html`, the real
stylesheet, the real shell and the real practice log, with one extra router.

Why a wrapper rather than a hand-built harness page: a harness that mounts the
component alone measures a layout that does not exist (no `.app-shell`, no
`.practice-page` container, so every `@container` rule silently never
matches). The rule is in `.claude/rules/ui-core.md` and this is the shape it
prescribes -- serve the real page, fake only the endpoint that is missing.

`ui_fixture.py` itself is untouched: `create_app` is swapped for the duration
of the context and put back, so nothing here is a change another branch has to
merge around.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import ui_fixture  # noqa: E402

# The star the fixture makes the active practice target (FIXTURE_COURSE 2,
# FIXTURE_STAR 4). A template is filed under the dashed key, which is the
# spelling the session's own target carries.
FIXTURE_TARGET = {"kind": "star", "entity_key": "2-4",
                  "strat": ui_fixture.FIXTURE_STRAT}

# Two of the four real refusal sentences from `ghost/source.py::REASONS`,
# copied rather than imported so a fixture cannot silently start passing by
# agreeing with a sentence somebody shortened. The second is the LONGEST of
# the four: a fixture that sits at the easy end of a range measures a layout
# nobody has (`.claude/rules/ui-core.md`), so the disabled state is rendered
# with the worst sentence the server can actually produce.
NO_TEMPLATE_TEXT = ("You have not set a template for this yet, so there is "
                    "no run to play alongside you.")
NO_FRAME_MAP_TEXT = ("The recording behind this template does not say which "
                     "moment of the game each picture shows, so it could not "
                     "stay in step with your play.")


def ghost_session(*, enabled: bool = False, opacity: float = 0.6,
                  nudge_frames: int = 0, available: bool = True,
                  reason: str | None = None, reason_text: str | None = None,
                  target: dict | None = None) -> dict:
    """One I5 payload, in the frozen shape."""
    return {
        "enabled": enabled,
        "opacity": opacity,
        "nudge_frames": nudge_frames,
        "target": FIXTURE_TARGET if target is None else target,
        "source": {"available": available,
                   "reason": reason,
                   "reason_text": reason_text,
                   "template_id": 7 if available else None,
                   "attempt_id": 91 if available else None,
                   "exactness": "frame_map" if available else None},
        "window": {"running": enabled and available,
                   "visible": enabled and available},
    }


def attach_ghost_routes(app, state: dict) -> None:
    """`GET|PUT /api/ghost` over one mutable dict.

    The PUT applies only the keys it was given and answers with the whole
    session, exactly as I5 says, so the card's own write path is exercised
    rather than stubbed. Out-of-range values are the 422 the interface
    promises, which is what lets a test prove the control never sends one.
    """
    from fastapi import HTTPException

    @app.get("/api/ghost")
    def _ghost_get():
        return state["session"]

    @app.put("/api/ghost")
    def _ghost_put(body: dict):
        session = dict(state["session"])
        if "enabled" in body:
            session["enabled"] = bool(body["enabled"])
        if "opacity" in body:
            opacity = float(body["opacity"])
            if not 0 <= opacity <= 1:
                raise HTTPException(422, "The ghost's visibility is a "
                                         "fraction between 0 and 1.")
            session["opacity"] = opacity
        if "nudge_frames" in body:
            frames = int(body["nudge_frames"])
            if abs(frames) > 300:
                raise HTTPException(422, "The ghost can only be nudged ten "
                                         "seconds either way.")
            session["nudge_frames"] = frames
        available = session["source"]["available"]
        session["window"] = {"running": session["enabled"] and available,
                             "visible": session["enabled"] and available}
        state["session"] = session
        state["writes"].append(dict(body))
        return session


@contextlib.contextmanager
def serve_ui_with_ghost(session: dict | None = None, **kwargs):
    """The real offline app, plus the ghost session door.

    Yields `(base_url, state)`; `state["session"]` is the live payload and
    `state["writes"]` every body the page PUT, in order -- which is how a
    test proves that one drag is one write rather than sixty.
    """
    state = {"session": session or ghost_session(), "writes": []}
    real_create_app = ui_fixture.create_app

    def create_app_with_ghost(*args, **kwargs_inner):
        app = real_create_app(*args, **kwargs_inner)
        attach_ghost_routes(app, state)
        return app

    ui_fixture.create_app = create_app_with_ghost
    try:
        with ui_fixture.serve_ui(**kwargs) as base:
            yield base, state
    finally:
        ui_fixture.create_app = real_create_app
