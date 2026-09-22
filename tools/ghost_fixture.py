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


#: The server's own sentence for a ghost whose recording has never had Mario
#: found in it, copied rather than imported so a fixture cannot start passing
#: by agreeing with a sentence somebody shortened (`ghost/mariobox.py`).
NO_BOXES_TEXT = ("This run has not had Mario found in it yet, so only the "
                 "whole picture can be shown.")


def ghost_session(*, enabled: bool = False, opacity: float = 0.6,
                  nudge_frames: int = 0, available: bool = True,
                  reason: str | None = None, reason_text: str | None = None,
                  target: dict | None = None,
                  tint: dict | None = None, view: str = "whole",
                  mario_boxes: bool = False) -> dict:
    """One I5 payload, in the frozen shape.

    The colour treatment defaults to ON here because that is what the shipped
    session defaults to, and a fixture sitting at the easy end of a range
    measures a card nobody has: the tuning line is at its WIDEST with the hue
    slider out, so that is the one worth rendering.
    """
    return {
        "enabled": enabled,
        "opacity": opacity,
        "tint": {"enabled": True, "hue_degrees": 120} if tint is None else tint,
        "shadow": True,
        "view": view,
        "nudge_frames": nudge_frames,
        "target": FIXTURE_TARGET if target is None else target,
        "source": {"available": available,
                   "reason": reason,
                   "reason_text": reason_text,
                   "template_id": 7 if available else None,
                   "attempt_id": 91 if available else None,
                   "exactness": "frame_map" if available else None,
                   # Whether this ghost can be drawn as Mario alone, and the
                   # server's own sentence when it cannot. One bit and one
                   # sentence: the boxes themselves never ride the broadcast.
                   "mario_boxes_available": bool(mario_boxes and available),
                   "mario_boxes_reason":
                       None if (mario_boxes and available) else "no_box_list",
                   "mario_boxes_reason_text":
                       None if (mario_boxes and available) else NO_BOXES_TEXT},
        "window": {"running": enabled and available,
                   "visible": enabled and available},
    }


def _select_ghosts(session: dict, body: dict, input_source: dict) -> None:
    """The real session's rules, over fixture rows: PB first, ticked saved
    recordings after it, the source is the first of them, and each playable
    one wears the hue the native command would give it."""
    from fastapi import HTTPException

    from sm64_events.ghost.native_session import ghost_hue
    from sm64_events.ghost.selection import checked_choice

    if body.get("target") != session["target"]:
        raise HTTPException(422, "The practice target changed.")
    try:
        choice = checked_choice(body["ghosts"])
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    selection = dict(session["selection"])
    rows = {candidate["attempt_id"]: candidate for candidate in selection["candidates"]}
    if any(number not in rows for number in choice["attempts"]):
        raise HTTPException(422, "That ghost is not saved for this star or segment. Choose another ghost.")
    pb = selection.get("pb")
    chosen = ([("pb", {**pb, "template_id": -pb["attempt_id"]})] if choice["pb"] and pb else [])
    chosen += [(number, {**rows[number], "template_id": -number}) for number in choice["attempts"]]
    selection["ghosts"] = choice
    native = [(key, row) for key, row in chosen if row["native_available"]]
    tint = session["tint"]
    selection["playing"] = [] if session["view"] != "mario" else [
        {"key": key, "attempt_id": row["attempt_id"],
         "hue_degrees": ghost_hue(tint["hue_degrees"], index) if tint["enabled"] else None}
        for index, (key, row) in enumerate(native)]
    session["selection"] = selection
    session["source"] = dict(input_source)
    if chosen:
        first = chosen[0][1]
        for key in ("template_id", "attempt_id", "native_available",
                    "native_reason_text", "whole_available", "whole_reason_text"):
            session["source"][key] = first[key]


def _set_shadow(session: dict, body: dict) -> None:
    from fastapi import HTTPException

    if "shadow" in body:
        if type(body["shadow"]) is not bool:
            raise HTTPException(422, "The ghost's shadow is either on or off.")
        session["shadow"] = body["shadow"]


def attach_ghost_routes(app, state: dict) -> None:
    """`GET|PUT /api/ghost` over one mutable dict.

    The PUT applies only the keys it was given and answers with the whole
    session, exactly as I5 says, so the card's own write path is exercised
    rather than stubbed. Out-of-range values are the 422 the interface
    promises, which is what lets a test prove the control never sends one.

    THE REAL ROUTES ARE TAKEN OUT FIRST, and they have to be. The app factory
    now mounts `/api/ghost` itself, with no session behind it in an offline
    fixture, so it answers 503 — and a duplicate FastAPI path shadows
    silently, first registration winning (`.claude/rules/server.md`). Adding
    this door beside that one left the switch reading "unreachable" and
    rendering NOTHING, which looks exactly like a component that is broken:
    28 rendered switch checks errored on a control that was never drawn
    (measured 2026-09-18, on the merge of tracks E and F).
    """
    from fastapi import HTTPException

    app.router.routes = [route for route in app.router.routes
                         if getattr(route, "path", None) != "/api/ghost"]
    input_source = dict(state["session"]["source"])

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
        if "tint" in body:
            # MERGED, as the real session merges it: the control sends only the
            # half he touched, and a fixture that replaced the whole object
            # would let a control that drops the other half look correct.
            asked = dict(body["tint"])
            if "hue_degrees" in asked and not 0 <= int(asked["hue_degrees"]) <= 359:
                raise HTTPException(422, "The ghost's colour goes from 0 to "
                                         "359 degrees around the colour wheel.")
            session["tint"] = {**(session.get("tint") or {}), **asked}
        _set_shadow(session, body)
        if "view" in body:
            if body["view"] not in ("whole", "mario"):
                raise HTTPException(422, "The ghost shows either the whole "
                                         "picture or only Mario.")
            session["view"] = body["view"]
        if "proximity" in body:
            from sm64_events.ghost.proximity import checked_proximity
            session["proximity"] = {**session.get("proximity", {}),
                                    **checked_proximity(body["proximity"])}
        if "ghosts" in body:
            _select_ghosts(session, body, input_source)
        if "native_available" in session["source"]:
            source = dict(session["source"])
            native = session["view"] == "mario"
            source["available"] = source["native_available" if native else "whole_available"]
            source["reason_text"] = source["native_reason_text" if native else "whole_reason_text"]
            session["source"] = source
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
