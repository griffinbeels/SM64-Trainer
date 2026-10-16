"""The ghost session (interface I5) answered beside the real UI fixture.

This serves the native ghost session shape, attached to the same offline app
`tools/ui_fixture.py` already boots -- the real `index.html`, the real
stylesheet, the real shell and the real practice log, with one extra router.

Why a wrapper rather than a hand-built harness page: a harness that mounts the
component alone measures a layout that does not exist (no `.app-shell`, no
`.practice-page` container, so every `@container` rule silently never
matches). The rule is in `.claude/rules/ui-core.md` and this is the shape it
prescribes -- serve the real page and provide an isolated ghost session.

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

# Representative native refusal sentences,
# copied rather than imported so a fixture cannot silently start passing by
# agreeing with a sentence somebody shortened. The disabled state uses a
# complete native refusal rather than a short placeholder.
NO_TEMPLATE_TEXT = "There is no personal best to race here yet."
NO_POSES_TEXT = ("This Mario recording uses an older pose format. "
                 "Record a new attempt to use it.")


def ghost_session(*, enabled: bool | None = None, opacity: float | None = None,
                  nudge_frames: int = 0, available: bool = True,
                  reason: str | None = None, reason_text: str | None = None,
                  target: dict | None = None, tint: dict | None = None) -> dict:
    """One I5 payload, in the frozen shape.

    Every setting is the registry's own default and the ranges are the ones
    the server publishes (`ghost/settings.py`), so a new setting reaches the
    fixture without an edit here. The colour treatment's shipped default is
    ON, which is also the widest tuning line (the hue slider out), so that is
    the one worth rendering.
    """
    from sm64_events.ghost import settings

    values = settings.defaults()
    values["nudge_frames"] = nudge_frames
    if enabled is not None:
        values["enabled"] = enabled
    if opacity is not None:
        values["opacity"] = opacity
    if tint is not None:
        values["tint"] = tint
    return {
        **values,
        "limits": settings.limits(),
        "target": FIXTURE_TARGET if target is None else target,
        "source": {"available": available,
                   "reason": reason,
                   "reason_text": reason_text,
                   "attempt_id": 91 if available else None,
                   "ghosts": []},
        "window": {"running": values["enabled"] and available,
                   "visible": values["enabled"] and available, "backend": "native"},
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
    rows = {candidate["attempt_id"]: candidate for candidate in selection["candidates"]
            if not candidate.get("import_id")}
    imports = {candidate["import_id"]: candidate for candidate in selection["candidates"]
               if candidate.get("import_id")}
    if any(number not in rows for number in choice["attempts"]):
        raise HTTPException(422, "That ghost is not saved for this star or segment. Choose another ghost.")
    if any(identity not in imports for identity in choice.get("imports", [])):
        raise HTTPException(422, "That imported ghost is not available for this star or segment.")
    pb = selection.get("pb")
    chosen = ([("pb", {**pb, "template_id": -pb["attempt_id"]})] if choice["pb"] and pb else [])
    chosen += [(number, {**rows[number], "template_id": -number}) for number in choice["attempts"]]
    chosen += [(identity, {**imports[identity], "template_id": identity})
               for identity in choice.get("imports", [])]
    selection["ghosts"] = choice
    native = [(key, row) for key, row in chosen if row["native_available"]]
    tint = session["tint"]
    selection["playing"] = [
        {"key": key, "attempt_id": row["attempt_id"],
         **({"import_id": row["import_id"]} if row.get("import_id") else {}),
         "hue_degrees": ghost_hue(tint["hue_degrees"], index) if tint["enabled"] else None}
        for index, (key, row) in enumerate(native)]
    session["selection"] = selection
    session["source"] = dict(input_source)
    source = session["source"]
    source["available"] = bool(native)
    source["ghosts"] = [row["attempt_id"] for _key, row in native]
    first = (native or chosen)[0][1] if native or chosen else None
    source["attempt_id"] = first["attempt_id"] if first else None
    source["reason_text"] = (None if native else first["native_reason_text"] if first
                             else "Tick a ghost to race it here.")


def _set_settings(session: dict, body: dict) -> None:
    """Every registry setting in the body, checked and applied (merged for the
    partial ones) exactly as the real session does it."""
    from fastapi import HTTPException

    from sm64_events.ghost import settings

    try:
        checked = settings.validated(body)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    for name, value in checked.items():
        session[name] = settings.applied(session.get(name) or {}, settings.BY_NAME[name], value)


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
    app.router.routes = [route for route in app.router.routes
                         if getattr(route, "path", None) != "/api/ghost"]
    input_source = dict(state["session"]["source"])

    @app.get("/api/ghost")
    def _ghost_get():
        state["reads"] = state.get("reads", 0) + 1
        if state.get("read_error"):
            from fastapi import HTTPException
            raise HTTPException(503, "Ghost controls are warming up.")
        return state["session"]

    @app.put("/api/ghost")
    def _ghost_put(body: dict):
        session = dict(state["session"])
        # MERGED where the real session merges (the tint, the overlap fade):
        # the control sends only the half he touched, and a fixture that
        # replaced the whole object would let a control that drops the other
        # half look correct.
        _set_settings(session, body)
        if "ghosts" in body:
            _select_ghosts(session, body, input_source)
        available = session["source"]["available"]
        session["window"] = {"running": session["enabled"] and available,
                             "visible": session["enabled"] and available, "backend": "native"}
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
