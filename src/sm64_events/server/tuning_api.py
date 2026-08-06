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

A fourth thing, added 2026-08-03 (the practice-log tuning round, spec
practice-log-entity-cards): for the `log` surface, EVERY numeric tunable also
ships a second appearance -- `var(--log-<kebab-key>, <fallback>)` in
index.html, read by CSS before any JS has ever run (the "how the app looks
with an empty tuning slot" state `logtuning.js`'s own `test_
logtuning_vars_reproduces_the_shipped_defaults` pins). Rewriting the registry
alone leaves that fallback stale -- exactly the "a value two surfaces show
gets ONE DOOR" bug CLAUDE.md's dev-process rules name, aimed at a rewrite
endpoint instead of a component. `sync_css_fallbacks` below is SAVE's second
door: it runs in the SAME request, against the SAME `written` set
`rewrite_defaults` just produced, and fails the WHOLE save (writing neither
file) rather than leave the registry and the stylesheet disagreeing.
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
    "marelo": _UI / "marelotuning.js",
    "log": _UI / "logtuning.js",
    "selector": _UI / "selectortuning.js",
    "feed": _UI / "feedtuning.js",
}

# The original single-surface name, kept as the climb's own path so nothing
# reading it has to learn the registry map. New surfaces use /api/tuning/<name>.
TUNING_JS = TUNING_REGISTRIES["climb"]

# Registries whose tunables ALSO drive a CSS custom property with a fallback
# literal in index.html -- `{registry: (stylesheet, cssVarPrefix)}`. Nothing
# in climbtuning.js/marelotuning.js has this shape: both feed a JS animation
# loop directly (`useRankClimb`'s per-frame values), so there is no "how it
# looks before JS ran" state for a fallback to encode, and no second door for
# SAVE to keep shut. `log` is the one surface today because its tunables are
# consumed entirely by CSS (`ui/logtuning.js`'s own header comment) -- a
# second entry here is the whole cost of a future surface gaining the same
# shape; nothing else in this module would need to change.
CSS_FALLBACK_TARGETS: dict[str, tuple[Path, str]] = {
    "log": (_UI / "index.html", "log"),
}

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


def _format_number(wanted: float) -> str:
    """The one place a Python float becomes the text a `value:` field (or,
    via `_css_literal` below, a CSS fallback) carries.

    json.dumps rather than repr: Python would write `1e-05` and `True`,
    neither of which is what either destination's own style uses. A whole
    number is written WITHOUT a decimal point -- `520.0` sitting where `520`
    was would make a no-op save report itself as a change, in EITHER file.
    """
    rounded = round(float(wanted), 6)
    return json.dumps(int(rounded) if rounded == int(rounded) else rounded)


# camelCase tunable key -> the CSS custom property name it drives, e.g.
# iconSize -> --log-icon-size. Mirrors `ui/logtuning.js::cssVarName` EXACTLY
# (`key.replace(/([A-Z])/g, "-$1").toLowerCase()`) -- a second door, in the
# "when one door is impossible, the duplicate gets a test that COMPARES the
# two" sense CLAUDE.md's dev-process rules describe: Python cannot import a
# JS module, so `tests/test_tuning_api.py` runs the real logtuning.js under
# node and asserts every key's var name agrees with this function, mutation-
# proved. A prefix is threaded in rather than hardcoded because the prefix
# IS the registry's own choice (`--log-`, `--climb-` if one ever needed this),
# not a fact this file gets to assert on its own.
_KEBAB = re.compile(r"([A-Z])")


def css_var_name(prefix: str, key: str) -> str:
    return f"--{prefix}-{_KEBAB.sub(r'-\1', key).lower()}"


_UNIT = re.compile(r"\bunit:\s*\"([^\"]*)\"")


def _row_unit(row_body: str) -> str | None:
    match = _UNIT.search(row_body)
    return match.group(1) if match else None


def _css_literal(formatted_number: str, unit: str) -> str:
    """Mirrors `logTuningVars`' own three-way switch EXACTLY ("x"-unit rows
    written bare, "%"-unit rows written as a bare `fr` value, everything else
    a "px" suffix) -- there is no fourth unit among today's rows on either
    side. "%" rows are the practice-log card's three SHARE tunables
    (identityPercent/ranksPercent/pbPercent, spec practice-log-entity-cards):
    written as `fr` rather than a literal percentage so three shares that
    don't sum to 100 still normalize instead of needing clamping/normalizing
    arithmetic duplicated here AND in logTuningVars (exactly the "two
    independent number-to-text roads" risk this file's own module docstring
    already names for the numeric case).

    Takes the ALREADY-FORMATTED `value:` text rather than reformatting
    `wanted` a second time, so a `value:` and its own fallback can never
    round or stringify differently -- one number, formatted once, read
    twice."""
    if unit == "x":
        return formatted_number
    if unit == "%":
        return f"{formatted_number}fr"
    return f"{formatted_number}px"


def rewrite_defaults(source: str, values: dict[str, object],
                     where: str = "the tuning registry") -> tuple[str, dict]:
    """Return `(new_source, written)` with each named row's `value:` replaced.

    Raises ValueError with a sentence naming the offending key -- the inspector
    puts it straight on screen, so it has to read as an explanation rather than
    a traceback.

    `where` is the file being edited, named in those sentences. It used to be
    the hardcoded string "climbtuning.js", which was right for the only surface
    that existed when this was written and became a lie the moment a second one
    was registered: a failed save from the OVERALL RANK-UP inspector sent the
    user to a file it had never touched (2026-07-28).
    """
    written: dict[str, object] = {}
    for key, wanted in values.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", key):
            raise ValueError(f"{key!r} is not a tunable name")
        pattern = _row_pattern(key)
        matches = list(pattern.finditer(source))
        if len(matches) != 1:
            raise ValueError(
                f"{key!r} matched {len(matches)} rows in {where}; "
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
            replacement = _format_number(wanted)
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


def _css_fallback_pattern(var: str) -> re.Pattern[str]:
    # Three groups so the replacement can keep the surrounding `var(...,` and
    # `)` byte-identical and touch only the literal between them -- the same
    # offset-splice discipline `rewrite_defaults` uses on `value:`, aimed at
    # `var(--log-x, <fallback>)` instead.
    return re.compile(
        r"(var\(\s*" + re.escape(var) + r"\s*,\s*)([^)]+?)(\s*\))")


def sync_css_fallbacks(css_source: str, registry_source: str, prefix: str,
                       written: dict[str, object], where: str) -> tuple[str, dict]:
    """After `rewrite_defaults` has changed a registry's `value:` fields,
    bring index.html's own `var(--log-x, <fallback>)` literals for the SAME
    keys into agreement -- the fallback is what CSS reads before any JS has
    run (`ui/logtuning.js::logTuningVars`'s golden test), so it is a second,
    independent appearance of "the shipped default" and SAVE moving only the
    registry is exactly the bug this closes.

    Only numeric TUNABLES apply: a CHOICE (a string in `written`) drives a
    modifier class via `logTuningClasses`, which index.html's selectors read
    fresh on every render -- there is no pre-JS literal for a class to fall
    out of step with, so a choice is skipped rather than special-cased.

    Strict on purpose, because `css_source` is index.html rather than a small
    known registry file: a key with ZERO fallbacks in it, or with several that
    already DISAGREE, is refused outright. A silent partial rewrite -- three
    of four occurrences moved, one left stale -- would be worse than the bug
    this function exists to fix, so nothing is written on that path; the
    caller is expected to fail the WHOLE save rather than write the registry
    half alone.
    """
    written_css: dict[str, str] = {}
    for key, wanted in written.items():
        if isinstance(wanted, str):
            continue  # a CHOICE: no var() fallback exists for it
        row_match = _row_pattern(key).search(registry_source)
        if row_match is None:  # pragma: no cover -- rewrite_defaults already
            raise ValueError(   # guarantees the row exists; belt and suspenders
                f"{key!r} has no row in the registry to read its unit from")
        unit = _row_unit(row_match.group("row"))
        if unit is None:
            raise ValueError(
                f"{key!r} declares no unit: -- cannot format a CSS fallback")
        literal = _css_literal(_format_number(wanted), unit)
        var = css_var_name(prefix, key)
        pattern = _css_fallback_pattern(var)
        matches = list(pattern.finditer(css_source))
        if not matches:
            raise ValueError(
                f"{key!r} ({var}) has no var(...) fallback anywhere in "
                f"{where} -- SAVE would leave the pre-JS render silently "
                "disagreeing with the registry")
        seen = {match.group(2).strip() for match in matches}
        if len(seen) > 1:
            raise ValueError(
                f"{var} has {len(seen)} disagreeing fallbacks in {where} "
                f"({', '.join(sorted(seen))}) -- fix them by hand so SAVE "
                "knows which one is right before it can replace them")
        if literal not in seen:
            css_source = pattern.sub(rf"\g<1>{literal}\g<3>", css_source)
            written_css[key] = literal
    return css_source, written_css


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
            updated, written = rewrite_defaults(
                source, dict(body.values), where=target.name)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

        # Second door: for a surface with a stylesheet half, resolve BOTH
        # rewrites before writing EITHER file. A CSS problem must fail the
        # whole save -- writing the registry alone would recreate the exact
        # "one door moved, the other didn't" bug this exists to close, just
        # with the two files swapped.
        css_file: Path | None = None
        css_updated: str | None = None
        written_css: dict[str, str] = {}
        css_stylesheet = CSS_FALLBACK_TARGETS.get(registry)
        if css_stylesheet and written:
            css_file, prefix = css_stylesheet
            if not css_file.is_file():
                raise HTTPException(503, f"{css_file} is missing")
            css_source = css_file.read_text(encoding="utf-8")
            try:
                css_updated, written_css = sync_css_fallbacks(
                    css_source, updated, prefix, written, where=css_file.name)
            except ValueError as error:
                raise HTTPException(409, str(error)) from error

        if written:
            # newline="" so the file keeps the LF endings git recorded; Python's
            # default would rewrite every line as CRLF on Windows and turn a
            # three-value tuning change into a whole-file diff.
            target.write_text(updated, encoding="utf-8", newline="")
            if css_file is not None and written_css:
                css_file.write_text(css_updated, encoding="utf-8", newline="")
        return {"written": len(written), "values": written, "path": str(target),
                "written_css": len(written_css),
                "css_path": str(css_file) if css_file else None}

    return router
