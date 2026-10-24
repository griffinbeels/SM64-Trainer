"""The Credits list keeps up with what the trainer ships.

His ask (task 0003, 2026-07-25): "make sure to maintain this crediting system
... maintain it any time we need to add credit later". `ui/credits.js` ties
each row to the shipped thing it credits through `covers`; this file derives
what ships from the repo itself and fails when something has no row, or when
a row credits something that no longer ships. The full run on GitHub gates
every release, so nothing reaches players uncredited.

What no test can see -- a new tester, a new community source, a lesson step
whose story changed -- is the release review in docs/release-pipeline.md.
"""
import functools
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "sm64_events" / "ui"
CREDITS_JS = UI / "credits.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")

FONT_SUFFIXES = {".ttf", ".otf", ".woff", ".woff2"}


@functools.cache
def registry() -> dict:
    script = (f"import * as m from {CREDITS_JS.as_uri()!r};\n"
              "console.log(JSON.stringify({groups: m.CREDIT_GROUPS, testers: m.TESTERS}));")
    result = subprocess.run(["node", "--input-type=module", "-"], input=script,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def items() -> list[dict]:
    return [item for group in registry()["groups"] for item in group["items"]]


def _dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def shipped() -> list[str]:
    """Everything the packaged app carries that someone else made."""
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    found = [f"python:{_dist(re.split(r'[<>=!~\[; ]', spec, maxsplit=1)[0])}"
             for spec in deps["project"]["dependencies"]]
    found += [f"vendor:{p.name}" for p in sorted((UI / "vendor").glob("*.js"))]
    found += [f"font:{p.name}" for p in sorted((UI / "assets").rglob("*"))
              if p.suffix.lower() in FONT_SUFFIXES]
    natives = (ROOT / "tools" / "build_natives.py").read_text(encoding="utf-8")
    found += [f"native:{dll}" for dll in re.findall(r'Native\("([^"]+\.dll)"', natives)]
    return found


def covers() -> set[str]:
    out = set()
    for item in items():
        for cover in item.get("covers", []):
            kind, _, name = cover.partition(":")
            out.add(f"python:{_dist(name)}" if kind == "python" else cover)
    return out


def test_the_inventory_is_not_empty():
    """Control: every check below passes vacuously on an empty parse."""
    kinds = {entry.split(":")[0] for entry in shipped()}
    assert kinds == {"python", "vendor", "font", "native"}, shipped()
    assert len(shipped()) >= 20
    assert len(items()) >= 20 and registry()["testers"]


@pytest.mark.parametrize("thing", shipped())
def test_everything_shipped_has_a_credit(thing):
    assert thing in covers(), (
        f"{thing} ships with the trainer but no row in ui/credits.js covers it. "
        f"Add a row (name, by, url, what, covers: [\"{thing}\"]) naming who made "
        f"it, from its own README or package metadata.")


def test_no_credit_covers_something_that_no_longer_ships():
    stale = covers() - set(shipped())
    assert not stale, (
        f"ui/credits.js covers {sorted(stale)}, which the trainer no longer "
        f"ships. Remove the row, or the stale covers entry.")


def test_testers_link_real_twitch_handles():
    handles = [tester["twitch"] for tester in registry()["testers"]]
    assert len(handles) == len(set(handles)), handles
    for tester in registry()["testers"]:
        assert tester["name"].strip(), tester
        # Twitch logins: 4-25 characters, lowercase letters, digits, underscore.
        assert re.fullmatch(r"[a-z0-9_]{4,25}", tester["twitch"]), tester


def test_every_credit_has_words_and_a_safe_link():
    for item in items():
        assert item["name"].strip() and item.get("what", "").strip(), item
        if "url" in item:
            assert item["url"].startswith("https://"), item
