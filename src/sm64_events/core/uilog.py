"""What the UI actually SHOWED, recorded so a live report can be read back.

**Why this exists.** The event journal answers what the GAME did. It has never
been able to answer what was on SCREEN, and two live reports on 2026-08-02
turned out to be unreadable for exactly that reason: *"it briefly lingered in
the star/segment selector section at the top... and then was removed"* and
*"after opening the usamune menu / pausing, it accidentally got rid of the
Bowser 1 -> WF segment (it was just there a couple frames ago)"*. Both describe
a CELL APPEARING AND DISAPPEARING. Nothing in the journal records that, so both
could only be read off screenshots — and a screenshot is one frame the human
happened to catch. His ask, verbatim:

    "how about we add some extra logging so you can literally just know what
    was displayed in the star/segment selector... and so you know what the
    active target/segment/star is. That way, I can just play the game and
    interact with the system, and you'll be able to deduce what the state of
    the UI (i.e., what elements existed and when) based on my behavior."

**Why it is NOT journaled events.** `tracking/service.py` is explicit that the
arm/disarm notices "are ephemeral UI state and must never be journaled", and it
is right for a structural reason: the projector RE-DERIVES the armed set from
the journal on every replay, so writing derived notices back into the same
journal makes replay non-idempotent. This is a SEPARATE channel with no
projection semantics at all — an append-only observation log, correlated with
the journal by wall clock and by the game frame the server stamps on receipt.

**And it records the RENDERED DOM, not a parallel model.** `ui/uilog.js` reads
the cells and card lines back out of the page after paint. A model-based
recorder would faithfully log what we BELIEVE is displayed, which is precisely
the belief under suspicion whenever one of these reports arrives.

Read it back with ``tools/what_happened.py``, which interleaves these rows into
the same timeline as the journal's own events.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sm64_events.core.paths import data_root

# Trim policy. The log is a debugging instrument for the LAST session, never an
# archive — a bounded file is what keeps it free to write on every change
# (which is the whole point: a change that lasted three frames must show up).
MAX_RECORDS = 4000
TRIM_ABOVE_BYTES = 2_000_000

# Fields a client may set. Anything else is dropped rather than stored: this
# endpoint takes a free-shaped body from the browser, and an unbounded key set
# would let a page bloat the file that has to stay cheap to append to.
CLIENT_FIELDS = ("surface", "client_utc", "title", "note", "cells", "cards",
                 "logs", "marks", "rows", "total")
# A surface not named here is DROPPED, silently and by design — which is also
# why adding a reader to `ui/uilog.js` is never the whole change: the recorder
# reader posted correctly for a while and this list ate every record, with no
# error anywhere. `tests/test_ui_log_records_the_real_page.py` is what catches
# that, because it asserts the records LAND rather than that they were sent.
SURFACES = ("selector", "target", "log", "recorder")

MAX_TEXT = 200
MAX_CELLS = 40


def log_path() -> Path:
    return data_root() / "data" / "ui_log.jsonl"


def _clip(value):
    """Bound anything that reaches disk. A cell list is bounded in COUNT and
    each name in LENGTH, so one pathological render cannot cost the whole
    budget the trim policy is protecting."""
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if isinstance(value, list):
        return [_clip(item) for item in value[:MAX_CELLS]]
    if isinstance(value, dict):
        return {str(key)[:MAX_TEXT]: _clip(val) for key, val in value.items()}
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_TEXT]


def sanitize(body: dict) -> dict | None:
    """The client's half of a record, or None if it is not a UI observation.

    Pure, so the endpoint has no shaping logic of its own to drift from what
    the tests check."""
    if not isinstance(body, dict):
        return None
    if body.get("surface") not in SURFACES:
        return None
    return {key: _clip(body[key]) for key in CLIENT_FIELDS if key in body}


def record(body: dict, frame: int | None = None,
           now: datetime | None = None) -> dict | None:
    """Append one observation. Returns the stored record, or None if the body
    was not one (an unknown surface is dropped silently — a browser extension
    or a stale tab must not be able to make this raise)."""
    entry = sanitize(body)
    if entry is None:
        return None
    stamped = datetime.now(timezone.utc) if now is None else now
    entry["server_utc"] = stamped.isoformat()
    entry["frame"] = frame
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so Python does not translate to CRLF on Windows — this file is
    # read back line-by-line and diffed by eye.
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if path.stat().st_size > TRIM_ABOVE_BYTES:
        _trim(path)
    return entry


def _trim(path: Path) -> None:
    kept = path.read_text(encoding="utf-8").splitlines()[-MAX_RECORDS:]
    path.write_text("".join(line + "\n" for line in kept),
                    encoding="utf-8", newline="")


def read(path: Path | None = None) -> list[dict]:
    """Every stored observation, oldest first.

    A malformed line is SKIPPED, never raised on: the last line can be a torn
    append if the process died mid-write, and a debugging instrument that
    refuses to open after a crash is useless at the one moment it is wanted."""
    target = log_path() if path is None else path
    if not target.exists():
        return []
    out = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def render(entry: dict) -> str:
    """One line for a timeline, in the same voice as an event payload.

    Lives here rather than in the tool so the reader and the writer cannot
    disagree about what a record means."""
    if entry.get("surface") == "selector":
        cells = [cell for cell in (entry.get("cells") or [])
                 if isinstance(cell, dict)]
        # The ACTIVE cell wears brackets — the one thing a bare name list
        # cannot say, and half of what these reports are about.
        drawn = ", ".join(
            f"[{cell.get('name')}]" if cell.get("active") else str(cell.get("name"))
            for cell in cells)
        head = " / ".join(part for part in (entry.get("title"), entry.get("note"))
                          if part)
        return f"{head or '(no heading)'} -> {drawn or '(NO CELLS)'}"
    if entry.get("surface") == "recorder":
        rows = [row for row in (entry.get("rows") or []) if isinstance(row, dict)]
        drawn = "  |  ".join(
            " ".join(part for part in (row.get("label"), row.get("igt")) if part)
            for row in rows)
        return f"recorder({entry.get('total')}) -> {drawn or '(NO ROWS)'}"
    cards = [card for card in (entry.get("cards") or []) if isinstance(card, dict)]
    if not cards:
        return "(NO CARDS)"
    return "  ||  ".join(_render_card(card) for card in cards)


def _render_card(card: dict) -> str:
    head = " · ".join(part for part in (card.get("label"), card.get("context"),
                                        card.get("name")) if part)
    tail = " · ".join(part for part in (card.get("strat"), card.get("state"),
                                        card.get("step")) if part)
    return head + (f"  |  {tail}" if tail else "")
