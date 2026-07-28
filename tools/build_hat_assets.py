"""Turn the maintainer's Photoshop hat exports (`assets/hat_raw/`) into the tintable
sprites the cap renderer composes (`src/sm64_events/ui/assets/hat/`).

Re-run this any time the exports are replaced -- it is the only place the
derivation happens, so a re-export is one command instead of an hour of
hand-desaturating in Photoshop.

The cap arrives RED. Its HSV *value* channel is exactly the shading a white
cap needs -- a lit face reads 255 whatever the hue -- so V, normalised so the
99th percentile of opaque pixels hits pure white, IS the white master.

Side effect: also copies the SuperMario256 font from this machine's installed
copy into the repo (`FONT_SRC` -> `assets/fonts/SuperMario256.ttf`), if it can
find it -- the sprites are the only output every machine can reproduce; the
font copy is a convenience for the machine that has the font installed, not a
required step (final review M8, 2026-07-25: this used to hard-fail here on
any OTHER machine, after every sprite had already been written).

Run: uv run python tools/build_hat_assets.py
"""
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "assets" / "hat_raw"
OUT = ROOT / "src" / "sm64_events" / "ui" / "assets" / "hat"
CAPS_JS = ROOT / "src" / "sm64_events" / "ui" / "components" / "caps.js"
FONT_SRC = Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts" / "SuperMario256.ttf"
FONT_OUT = ROOT / "src" / "sm64_events" / "ui" / "assets" / "fonts" / "SuperMario256.ttf"

WING_COUNT = 4
DOWNSCALE_WIDTH = 512  # largest on-screen use is a 96px cap; 1283px sprites cost ~120KB each

_BOX_PATTERN = (
    r"export const {name} = \{{\s*"
    r"left:\s*([\d.]+),\s*top:\s*([\d.]+),\s*"
    r"width:\s*([\d.]+),\s*height:\s*([\d.]+)\s*\}};"
)


def require(path: Path) -> Path:
    """Hard-fail on a missing input rather than skip it silently."""
    if not path.exists():
        raise SystemExit(f"missing input: {path}")
    return path


def js_box(name: str) -> dict:
    """Pull an {left,top,width,height} literal out of caps.js by name -- the
    geometry lives there once; this script never keeps its own copy to drift
    from it."""
    source = CAPS_JS.read_text(encoding="utf-8")
    match = re.search(_BOX_PATTERN.format(name=name), source)
    if not match:
        raise SystemExit(f"caps.js has no {name} literal in the expected shape")
    left, top, width, height = (float(value) for value in match.groups())
    return {"left": left, "top": top, "width": width, "height": height}


def normalised_white(channel, alpha, percentile=0.99):
    """Greyscale scaled so the given percentile of opaque pixels reads 255."""
    values = sorted(v for v, a in zip(channel.getdata(), alpha.getdata()) if a > 200)
    ceiling = values[int(len(values) * percentile)] or 255
    return channel.point(lambda v: min(255, round(v * 255 / ceiling)))


def alpha_bbox_fraction(alpha, size, threshold=24):
    """The opaque bounding box as a fraction of the canvas -- what a renderer
    positioned by caps.js's CAP_BOX/PATCH_BOX actually needs."""
    solid = alpha.point(lambda v: 255 if v > threshold else 0)
    box = solid.getbbox()
    if box is None:
        raise SystemExit("alpha channel is fully transparent -- nothing to measure")
    left, top, right, bottom = box
    width, height = size
    return {
        "left": left / width,
        "top": top / height,
        "width": (right - left) / width,
        "height": (bottom - top) / height,
    }


def assert_box_matches(label: str, measured: dict, expected: dict):
    """Stop rather than loosen the check -- a disagreement means the art
    moved and a digit would render outside the sign field."""
    for key in ("left", "top", "width", "height"):
        if round(measured[key], 4) != round(expected[key], 4):
            raise SystemExit(
                f"{label} {key} measured {measured[key]:.6f} but caps.js says "
                f"{expected[key]:.6f} -- the art moved, stop and report it"
            )


def build_cap():
    cap = Image.open(require(RAW / "cap.png")).convert("RGBA")
    cap_alpha = cap.getchannel("A")
    value = cap.convert("RGB").convert("HSV").getchannel("V")
    master = normalised_white(value, cap_alpha)
    image = Image.merge("RGBA", (master, master, master, cap_alpha))

    measured = alpha_bbox_fraction(cap_alpha, cap.size)
    print(f"cap bounding box (fraction of canvas): {measured}")
    assert_box_matches("cap", measured, js_box("CAP_BOX"))
    return image, cap_alpha, cap.size


def build_outline(cap_alpha, size):
    """The Capless tier: dilate the cap's thresholded alpha and subtract the
    original, leaving a ring that tints like everything else."""
    solid = cap_alpha.point(lambda v: 255 if v > 128 else 0)
    ring = ImageChops.subtract(solid.filter(ImageFilter.MaxFilter(9)), solid)
    white = Image.new("L", size, 255)
    return Image.merge("RGBA", (white, white, white, ring))


def build_flat_white(raw_name: str, check_box: bool):
    """Patch and spots exported at 236 grey; multiply would darken every tint
    by 7%, so push them to pure white."""
    art = Image.open(require(RAW / f"{raw_name}.png")).convert("RGBA")
    alpha = art.getchannel("A")
    if check_box:
        measured = alpha_bbox_fraction(alpha, art.size)
        print(f"patch bounding box (fraction of canvas): {measured}")
        assert_box_matches("patch", measured, js_box("PATCH_BOX"))
    flat = Image.new("L", art.size, 255)
    return Image.merge("RGBA", (flat, flat, flat, alpha))


def build_wing_halves(index: int, seam_x: int):
    """Wings keep their own greyscale shading; only the highlight is
    normalised. Each wing is then split at the canvas midpoint into a left
    and right half, each on the FULL canvas so it still stacks at inset:0 --
    a flap rotates the two wings in OPPOSITE directions, and one image
    holding both can only turn as a unit.

    Normalising happens PER HALF, after the split, not once on the whole
    wing: the two sides are not equally lit, so a single shared ceiling
    leaves the dimmer side's own highlight short of white."""
    wing = Image.open(require(RAW / f"wing{index}.png")).convert("RGBA")
    wing_alpha = wing.getchannel("A")
    grey = wing.convert("L")

    left_mask = Image.new("L", wing.size, 0)
    left_mask.paste(255, (0, 0, seam_x, wing.size[1]))
    right_mask = ImageChops.invert(left_mask)

    left_alpha = ImageChops.multiply(wing_alpha, left_mask)
    right_alpha = ImageChops.multiply(wing_alpha, right_mask)

    left_grey = normalised_white(grey, left_alpha)
    right_grey = normalised_white(grey, right_alpha)

    left_half = Image.merge("RGBA", (left_grey, left_grey, left_grey, left_alpha))
    right_half = Image.merge("RGBA", (right_grey, right_grey, right_grey, right_alpha))

    if left_half.getchannel("A").getbbox() is None:
        raise SystemExit(f"wing{index}_l is empty -- the seam split away all its art")
    if right_half.getchannel("A").getbbox() is None:
        raise SystemExit(f"wing{index}_r is empty -- the seam split away all its art")

    return left_half, right_half


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    outputs = {}

    cap_image, cap_alpha, canvas_size = build_cap()
    outputs["cap"] = cap_image
    outputs["cap_outline"] = build_outline(cap_alpha, canvas_size)
    outputs["patch"] = build_flat_white("patch", check_box=True)
    # spots_toad.png ships as spots.png -- one pattern serves both spotted
    # tiers (Silver/Toadsworth, Bronze/Toad), distinguished only by patternColor.
    outputs["spots"] = build_flat_white("spots_toad", check_box=False)

    seam_x = canvas_size[0] // 2  # the cap is centred to the pixel, so the midpoint is the seam
    for index in range(1, WING_COUNT + 1):
        left_half, right_half = build_wing_halves(index, seam_x)
        outputs[f"wing{index}_l"] = left_half
        outputs[f"wing{index}_r"] = right_half

    # Downscale LAST, after every transform above ran at full resolution --
    # the bounding-box maths must run against the exports' real pixels.
    for name, image in outputs.items():
        width, height = image.size
        scaled_height = round(height * DOWNSCALE_WIDTH / width)
        scaled = image.resize((DOWNSCALE_WIDTH, scaled_height), Image.LANCZOS)
        scaled.save(OUT / f"{name}.png")

    print("wrote:", ", ".join(sorted(f"{name}.png" for name in outputs)))

    # Tolerant, unlike require(): FONT_SRC is a per-user installed-font path
    # that exists only on the machine the font was installed on, not a repo asset -- failing here
    # would stop a reproducible re-run of the sprite derivation above dead on
    # any other machine, after every sprite had already been written. The
    # font already committed at FONT_OUT (or a manual copy) is sufficient.
    if FONT_SRC.exists():
        FONT_OUT.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FONT_SRC, FONT_OUT)
        print(f"font: {FONT_OUT}")
    else:
        print(f"font: skipped -- {FONT_SRC} not found on this machine; "
              f"copy SuperMario256.ttf to {FONT_OUT} by hand if it's missing there")


if __name__ == "__main__":
    main()
