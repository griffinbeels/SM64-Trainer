"""Every displayed time is bare: `11"43`, never `0'11"43`.

His rule, 2026-09-24: "We should always prefer "bare "11"43" whenever
possible. If the time includes the minute digit, then we include it if it's
>=1 (e.g., 0'11"43 should be 11"43, while 1'11"43 is correct)."

The canonical formatters (`core/timefmt.py::format_igt`, `ui/format.js::fmtIgt`)
keep their `0'` because some strings are IDENTIFIERS: a clip's filename, and
the time a detector journals inside its event. Everything a person reads goes
through the display form (`format_igt_short` / `fmtIgtShort`). This scan holds
the complete list of identifier uses, so a new display site that reaches for
the canonical formatter fails here instead of showing `0'` on screen.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "sm64_events"

# path (relative to src/sm64_events) -> (calls, why it is an identifier)
PYTHON_IDENTIFIERS = {
    "core/timefmt.py": (1, "format_igt_short is built on it"),
    "detectors/area.py": (1, "journaled event payload"),
    "detectors/caused.py": (1, "journaled event payload"),
    "detectors/key.py": (1, "journaled event payload"),
    "detectors/level.py": (1, "journaled event payload"),
    "detectors/moment.py": (1, "journaled event payload"),
    "detectors/spawn.py": (1, "journaled event payload"),
    "detectors/star_grab.py": (2, "journaled event payload"),
    "detectors/warp.py": (1, "journaled event payload"),
    "replay/service.py": (2, "clip filename (igt and rta)"),
}
JS_IDENTIFIERS = {
    "ui/format.js": (1, "fmtIgtShort is built on it"),
}
PY_CALL = re.compile(r"(?<!def )\bformat_igt\(")
JS_CALL = re.compile(r"(?<!function )\bfmtIgt\(")


def _calls(pattern, suffixes):
    found = {}
    for path in SRC.rglob("*"):
        if path.suffix not in suffixes or "vendor" in path.parts:
            continue
        count = len(pattern.findall(path.read_text(encoding="utf-8")))
        if count:
            found[path.relative_to(SRC).as_posix()] = count
    return found


def test_only_identifiers_use_the_canonical_python_formatter():
    found = _calls(PY_CALL, {".py"})
    expected = {path: count for path, (count, _) in PYTHON_IDENTIFIERS.items()}
    assert found == expected, (
        "format_igt keeps its 0' and is for identifiers only; a time a person "
        "reads goes through format_igt_short (his rule, 2026-09-24). "
        f"Found {found}, allowed {expected}.")


def test_only_identifiers_use_the_canonical_js_formatter():
    found = _calls(JS_CALL, {".js", ".html", ".mjs"})
    expected = {path: count for path, (count, _) in JS_IDENTIFIERS.items()}
    assert found == expected, (
        "fmtIgt keeps its 0' and is for identifiers only; a displayed time "
        "goes through fmtIgtShort (his rule, 2026-09-24). "
        f"Found {found}, allowed {expected}.")


def test_the_display_forms_drop_only_an_empty_minute():
    from sm64_events.core.timefmt import drop_empty_minutes, format_igt_short

    assert format_igt_short(343) == '11"43'
    assert format_igt_short(1800 + 343) == "1'11\"43"
    assert drop_empty_minutes("10'00\"00") == "10'00\"00"
