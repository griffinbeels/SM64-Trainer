"""The hat sprites: every part caps.js names exists, nothing is orphaned, and
the tintable ones are actually white.

The exports arrive from Photoshop at 236 grey, not 255. Multiply tinting scales
the tier colour by that grey, so shipping them unnormalised silently darkens
all nine caps -- and Toad, whose whole identity is a white cap, most of all.
"""
import re
from pathlib import Path

from PIL import Image

from tests.source_scan import strip_comments

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "sm64_events" / "ui"
HAT = UI / "assets" / "hat"
CAPS_JS = UI / "components" / "caps.js"

# Parts every hat draws regardless of tier, plus the four wing steps -- split
# per side, because a flap rotates the two wings in OPPOSITE directions and one
# image containing both can only rotate as a unit.
BASE_PARTS = ({"cap", "patch"}
              | {f"wing{n}_{side}" for n in range(1, 5) for side in ("l", "r")})


def _named_parts() -> set[str]:
    """Every art stem caps.js can ask for: bases, patterns, and treatments
    that resolve to their own file."""
    source = strip_comments(CAPS_JS.read_text(encoding="utf-8"))
    parts = set(BASE_PARTS)
    parts |= set(re.findall(r'pattern:\s*"([^"]+)"', source))
    if 'treatment: "outline"' in source:
        parts.add("cap_outline")
    return parts


def test_every_named_part_has_its_png():
    missing = {p for p in _named_parts() if not (HAT / f"{p}.png").exists()}
    assert not missing, f"caps.js names art with no file: {sorted(missing)}"


def test_no_orphan_sprites():
    on_disk = {p.stem for p in HAT.glob("*.png")}
    assert on_disk == _named_parts(), (
        f"orphans {sorted(on_disk - _named_parts())} ship in the exe for nothing")


def test_every_sprite_shares_one_canvas():
    """Layers stack at inset:0 with no per-part offsets; a differently sized
    sprite silently shifts."""
    sizes = {p.name: Image.open(p).size for p in HAT.glob("*.png")}
    assert len(set(sizes.values())) == 1, f"sprites disagree on canvas: {sizes}"


def test_tintable_sprites_reach_pure_white():
    """Multiply scales the tier colour by this grey; anything under 250 tints
    dark. The wings keep their own shading, so only their highlight matters."""
    for stem in ("cap", "patch", "spots", "wing1_l", "wing4_r"):
        art = Image.open(HAT / f"{stem}.png").convert("RGBA")
        alpha = art.getchannel("A")
        grey = art.convert("L")
        brightest = max(v for v, a in zip(grey.getdata(), alpha.getdata()) if a > 200)
        assert brightest >= 250, f"{stem}.png peaks at {brightest}, not white"
