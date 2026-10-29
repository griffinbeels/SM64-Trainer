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
                  target: dict | None = None, tint: dict | None = None,
                  hotkey: str = "dpad_up") -> dict:
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
        "hotkey": hotkey,
    }


LINEUP_PLAYERS = (("Alexander_Speedruns", "Diamond", "V", 339), ("Quinn", "Diamond", "IV", 335),
                  ("Kai", "Diamond", "III", 334), ("Morgan", "Diamond", "I", 331))


def lineup_row(slot: int, name: str, tier: str, division: str, frames: int, *,
               pinned: bool = False, pending: bool = False, player: str | None = None) -> dict:
    """One rival seat as `session.rivals.rows` carries it, wearing the slot's real hue."""
    from sm64_events.ghost.adaptive import PB_HUE_DEGREES
    from sm64_events.ghost.native_session import ghost_hue

    return {"id": f"{slot:x}{name.casefold()}".ljust(64, "0")[:64], "player_id": player or name.casefold(),
            "player_name": name, "strategy": FIXTURE_TARGET["strat"], "frames": frames,
            "tier": tier, "division": division, "slot": slot, "pinned": pinned, "pending": pending,
            "hue_degrees": ghost_hue(PB_HUE_DEGREES, slot + 1)}


def lineup_rivals(*, state: str = "ready", reason: str | None = None, pb_frames: int | None = 343,
                  rows: list | None = None, include_pb: bool = True, count: int = 4,
                  scope: str = "overall", best_run_frames: int | None = None) -> dict:
    """`session.rivals` in the Lineup contract (engineering-plan.md): state,
    reason, your PB and the rival seats, with the shipped options and limits.
    `best_run_frames`: your PB has no poses and your best recorded run races
    in its seat (`ghost/rivals.py::Rivals._pb_seat`)."""
    from sm64_events.ghost.adaptive import PB_HUE_DEGREES
    from sm64_events.ghost.rivals import BEST_RUN_FIELDS

    if rows is None:
        rows = [lineup_row(slot, *player) for slot, player in enumerate(LINEUP_PLAYERS)]
    pb = None if pb_frames is None else {"frames": pb_frames, "tier": "Platinum", "division": "II", "step": 23,
                                          "hue_degrees": PB_HUE_DEGREES, "available": True}
    best = None
    if best_run_frames is not None and include_pb:
        pb = {**pb, "available": False, "reason_text": "Mario poses were not recorded for this attempt."}
        values = {"id": "b357".ljust(64, "0"), "frames": best_run_frames, "tier": "Platinum", "division": "IV",
                  "step": 21, "strategy": FIXTURE_TARGET["strat"], "game_version": "us"}
        best = {**{key: values[key] for key in BEST_RUN_FIELDS}, "hue_degrees": PB_HUE_DEGREES,
                "available": True, "reason_text": None}
    return {"state": state, "reason": reason, "scope": scope, "rows": rows if state == "ready" else [],
            "pb": pb, "best_run": best, "status": None, "simulation": None, "strategy_name": FIXTURE_TARGET["strat"],
            "options": {"mode": "adaptive", "count": count, "include_pb": include_pb,
                        "min_ahead": 1, "max_ahead": 5, "min_step": 0, "max_step": 44},
            "limits": {"last_step": 44, "max_ahead": 10, "counts": [1, 2, 3, 4]},
            "generation": 1, "manual": False}


def _apply_rivals(session: dict, body: dict, search: list) -> None:
    """The rival writes the Lineup sends: unpin a seat, pin a run into a seat,
    or save options. Enough of the real session's answer to drive the card."""
    rivals = dict(session.get("rivals") or {})
    change = body.get("rivals") or {}
    rows = [dict(row) for row in rivals.get("rows", [])]
    if change.get("action") == "reset":
        for row in rows:
            if row["slot"] == change.get("slot"):
                row["pinned"] = False
    elif change.get("action") == "add":
        found = next((row for row in search if row["id"] == change.get("id")), None)
        if found is not None:
            slot = change.get("slot")
            rows = [row for row in rows if row["slot"] != slot]
            rows.append(lineup_row(slot, found["player_name"], found["tier"], found["division"], found["frames"],
                                   pinned=True))
    if "options" in change:
        rivals["options"] = {**rivals.get("options", {}), **change["options"]}
    rivals["rows"] = sorted(rows, key=lambda row: row["slot"])
    session["rivals"] = rivals


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


def attach_ghost_routes(app, state: dict, broadcaster=None) -> None:
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
                         if getattr(route, "path", None) not in ("/api/ghost", "/api/ghost/rivals/search")]
    input_source = dict(state["session"]["source"])

    @app.get("/api/ghost")
    def _ghost_get():
        state["reads"] = state.get("reads", 0) + 1
        if state.get("read_error"):
            from fastapi import HTTPException
            raise HTTPException(503, "Ghost controls are warming up.")
        return state["session"]

    @app.post("/api/_fixture/ghost_broadcast")
    async def _ghost_broadcast():
        """Fixture-only: publish the current session as ghost_changed, the way
        the real session announces every change, so a test can drive the card
        from one state to the next and watch the transition."""
        from datetime import datetime, timezone

        from sm64_events.core.events import Event
        from sm64_events.ghost.session import EVENT_TYPE
        if broadcaster is None:
            return {"published": False}
        await broadcaster.publish(Event(type=EVENT_TYPE, frame=0, timestamp_utc=datetime.now(timezone.utc),
                                        payload=state["session"]))
        return {"published": True}

    @app.get("/api/ghost/rivals/search")
    def _rival_search(target: str, player: str = "", scope: str = "strategy", cursor: str | None = None):
        wanted = player.strip().casefold()
        records = [row for row in state.get("search", []) if wanted in row["player_name"].casefold()]
        return {"records": records, "next_cursor": None}

    @app.put("/api/ghost")
    def _ghost_put(body: dict):
        session = dict(state["session"])
        # MERGED where the real session merges (the tint, the overlap fade):
        # the control sends only the half he touched, and a fixture that
        # replaced the whole object would let a control that drops the other
        # half look correct.
        _set_settings(session, body)
        if "rivals" in body and session.get("rivals") is not None:
            _apply_rivals(session, body, state.get("search", []))
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
        broadcaster = args[1] if len(args) > 1 else kwargs_inner.get("broadcaster")
        attach_ghost_routes(app, state, broadcaster)
        return app

    ui_fixture.create_app = create_app_with_ghost
    try:
        with ui_fixture.serve_ui(**kwargs) as base:
            yield base, state
    finally:
        ui_fixture.create_app = real_create_app
