# tools/readme_media.py
"""Regenerate the README's app screenshots from the UI fixture.

    uv run python tools/readme_media.py            # every shot
    uv run python tools/readme_media.py hero       # one shot

The README shows the trainer through a few screenshots of the real interface.
They come from the same isolated fixture the layout tests use (uilab Stories in
`tools/uilab_project.py`), never from the live app, so refreshing them after a
redesign is this one command, and a shot whose Story was renamed fails here
rather than going quietly stale. In-game clips (the ghost race, a star grab)
can only be captured from a real Project64 session; they live beside these in
`docs/readme/` and are listed in `HUMAN_CAPTURES`.
"""
from __future__ import annotations

import io
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from find_uilab import find_uilab  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "readme"
WIDTH, HEIGHT = 1440, 900      # a laptop-sized window, the app's common shape
SCALE = 2                      # shot at 2x, saved at SAVED_WIDTH: crisp on any screen
SAVED_WIDTH = 1600


@dataclass(frozen=True)
class Shot:
    name: str           # docs/readme/<name>.png
    story: str          # a Story in tools/uilab_project.py
    selector: str       # the element to crop to; "" = the whole window
    max_height: int     # crop taller elements to this many CSS pixels


SHOTS = (
    Shot("hero", "page", "", HEIGHT),
    Shot("practice", "active-target", ".log-card.log-card-active", 900),
    Shot("ranks", "library-target", ".library-section.open", 398),   # the Capless-to-Mario ladder
    Shot("scorecard", "scorecard", ".scorecard-card", 820),
)

# Captured by hand from a real Project64 session; this tool never writes them.
HUMAN_CAPTURES = ("ghost-race", "star-grab")

_RECT = """(() => { const el = document.querySelector(%s); if (!el) return null;
  el.scrollIntoView({block: 'start'}); const r = el.getBoundingClientRect();
  return JSON.stringify({x: r.x, y: r.y, width: r.width, height: r.height}); })()"""


def _shoot(page, project, url, shot: Shot, stories) -> bytes:
    story = stories[shot.story]
    page.set_viewport(WIDTH, HEIGHT)
    page.goto(url)
    if project.ready_selector:
        page.wait_for(project.ready_selector)
    time.sleep(0.6)
    if story.setup:
        page.evaluate(story.setup)
        time.sleep(0.8)
    if not shot.selector:
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(0.3)
        return page.screenshot()
    raw = page.evaluate(_RECT % json.dumps(shot.selector))
    if not raw:
        raise SystemExit(f"{shot.name}: {shot.selector} is not on the {shot.story} story's page")
    rect = json.loads(raw)
    height = min(rect["height"], shot.max_height)
    page.set_viewport(WIDTH, max(HEIGHT, int(height) + 40))
    time.sleep(0.4)
    rect = json.loads(page.evaluate(_RECT % json.dumps(shot.selector)))
    return page.screenshot(clip={"x": rect["x"], "y": rect["y"],
                                 "width": rect["width"], "height": height})


def _save(png: bytes, path: Path) -> None:
    from PIL import Image
    image = Image.open(io.BytesIO(png)).convert("RGB")
    if image.width > SAVED_WIDTH:
        image = image.resize((SAVED_WIDTH, round(image.height * SAVED_WIDTH / image.width)),
                             Image.LANCZOS)
    image.save(path, optimize=True)


def main(argv: list[str]) -> int:
    missing = find_uilab()
    if missing:
        raise SystemExit(missing)
    from uilab.driver import get_driver
    from uilab_project import PROJECT, STORIES
    stories = {story.name: story for story in STORIES}
    wanted = [shot for shot in SHOTS if not argv or shot.name in argv]
    OUT.mkdir(parents=True, exist_ok=True)
    with PROJECT.open() as url, get_driver().launch(device_scale_factor=SCALE) as page:
        for shot in wanted:
            path = OUT / f"{shot.name}.png"
            _save(_shoot(page, PROJECT, url, shot, stories), path)
            print(f"{path.relative_to(OUT.parents[1])}  {path.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
