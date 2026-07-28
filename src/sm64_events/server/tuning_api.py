# src/sm64_events/server/tuning_api.py
"""Write the climb tuning inspector's values back into ui/climbtuning.js.

User, 2026-07-27: "what if I can access that page at any time, mess with the
settings, SAVE, and then it automatically applies to my repo immediately?
Basically we're just exposing a specialized settings tool that allows us to
interact with certain elements just like we would in, say, Godot."

So SAVE is not an export step and there is no runtime overlay to load: the
endpoint edits the `value:` fields of the registry itself, which ARE the
shipped defaults, and the change shows up in `git diff` ready to commit.

Three things keep a source-rewriting endpoint from being a liability:

  * It only runs from SOURCE. A frozen exe has no repo to write to and is
    somebody's installed app, so it refuses outright.
  * It only ever replaces a `value:` that already exists, for a key that
    already exists. It cannot add a key, cannot reach any other field, and
    cannot append text.
  * It validates against the FILE, not against a copy of the registry kept
    here -- min/max and the allowed options are read out of the same row being
    edited. A second copy of those bounds in Python is exactly the kind of
    second door tests/test_single_source.py exists to stop.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sm64_events.core.paths import is_frozen

_UI = Path(__file__).resolve().parents[1] / "ui"

# EVERY tunable surface, by name. Adding a second inspector -- a card, a
# transition, anything judged by feel -- is one row here plus its own registry
# module and page; see `.claude/skills/tuning-demo`. An allowlist rather than a
# path parameter because the endpoint WRITES: a caller must never be able to
# name the file it lands in.
TUNING_REGISTRIES = {
    "climb": _UI / "climbtuning.js",
}

# The original single-surface name, kept as the climb's own path so nothing
# reading it has to learn the registry map. New surfaces use /api/tuning/<name>.
TUNING_JS = TUNING_REGISTRIES["climb"]

# A row runs from `\n  <key>: {` to the `\n  },` that closes it — the file's
# own two-space layout, which is what lets this stay a regex over a small,
# known file rather than a JavaScript parser. The WHOLE row is captured
# because `min`/`max`/`options` sit AFTER `value:` and are what validates it.
def _row_pattern(key: str) -> re.Pattern[str]:
    return re.compile(
        r"\n  " + re.escape(key) + r":\s*\{(?P<row>.*?)\n  \},", re.S)


_VALUE = re.compile(r"\bvalue:\s*(?P<value>[^,\n]+)")


class TuningBody(BaseModel):
    values: dict[str, float | str]


def _numeric_bounds(body: str) -> tuple[float, float] | None:
    low = re.search(r"\bmin:\s*(-?[\d.]+)", body)
    high = re.search(r"\bmax:\s*(-?[\d.]+)", body)
    if not low or not high:
        return None
    return float(low.group(1)), float(high.group(1))


def rewrite_defaults(source: str, values: dict[str, object]) -> tuple[str, dict]:
    """Return `(new_source, written)` with each named row's `value:` replaced.

    Raises ValueError with a sentence naming the offending key -- the inspector
    puts it straight on screen, so it has to read as an explanation rather than
    a traceback.
    """
    written: dict[str, object] = {}
    for key, wanted in values.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
            raise ValueError(f"{key!r} is not a tunable name")
        pattern = _row_pattern(key)
        matches = list(pattern.finditer(source))
        if len(matches) != 1:
            raise ValueError(
                f"{key!r} matched {len(matches)} rows in climbtuning.js; "
                "it must match exactly one")
        match = matches[0]
        row_body = match.group("row")
        inner = _VALUE.search(row_body)
        if inner is None:
            raise ValueError(f"{key!r} has no value: field to write")
        # Offsets into the WHOLE file, so the splice below cannot drift.
        value_start = match.start("row") + inner.start("value")
        value_end = match.start("row") + inner.end("value")
        current = inner.group("value").strip()

        if isinstance(wanted, bool):
            raise ValueError(f"{key!r} is not a boolean tunable")
        if isinstance(wanted, (int, float)):
            bounds = _numeric_bounds(row_body)
            if bounds is None:
                raise ValueError(f"{key!r} has no min/max, so it takes no number")
            low, high = bounds
            if not low <= float(wanted) <= high:
                raise ValueError(
                    f"{key} must be between {low} and {high}, got {wanted}")
            # json.dumps rather than repr: Python would write `1e-05` and
            # `True`, neither of which is what the file's own style uses. And a
            # whole number is written WITHOUT a decimal point, matching the
            # registry: `520.0` sitting where `520` was also made a no-op save
            # report itself as a change.
            rounded = round(float(wanted), 6)
            replacement = json.dumps(
                int(rounded) if rounded == int(rounded) else rounded)
        else:
            options = re.search(r"\boptions:\s*\{([^}]*)\}", row_body)
            if not options:
                raise ValueError(f"{key!r} takes a number, not {wanted!r}")
            allowed = re.findall(r"(\w+):", options.group(1))
            if wanted not in allowed:
                raise ValueError(
                    f"{key} must be one of {', '.join(allowed)}, got {wanted!r}")
            replacement = json.dumps(wanted)

        if replacement != current:
            written[key] = wanted
        source = source[:value_start] + replacement + source[value_end:]
    return source, written


def create_tuning_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/tuning/{registry}")
    def save_tuning(registry: str, body: TuningBody) -> dict:
        if is_frozen():
            raise HTTPException(
                409, "The tuning inspector can only write to a source checkout; "
                     "this is the packaged app.")
        target = TUNING_REGISTRIES.get(registry)
        if target is None:
            raise HTTPException(
                404, f"no tunable surface called {registry!r} -- known: "
                     f"{', '.join(sorted(TUNING_REGISTRIES))}")
        if not target.is_file():
            raise HTTPException(503, f"{target} is missing")
        source = target.read_text(encoding="utf-8")
        try:
            updated, written = rewrite_defaults(source, dict(body.values))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        if written:
            # newline="" so the file keeps the LF endings git recorded; Python's
            # default would rewrite every line as CRLF on Windows and turn a
            # three-value tuning change into a whole-file diff.
            target.write_text(updated, encoding="utf-8", newline="")
        return {"written": len(written), "values": written, "path": str(target)}

    return router
