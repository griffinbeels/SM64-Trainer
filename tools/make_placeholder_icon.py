# tools/make_placeholder_icon.py
"""Generate assets/ukiki.ico — a simple stylized monkey-head placeholder
(original art, not ripped game assets). Replace with nicer art later; the
build only needs a valid multi-res .ico to exist here."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "assets" / "ukiki.ico"


def main() -> None:
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((20, 20, 236, 236), fill=(120, 72, 36, 255))      # head
    d.ellipse((40, 30, 95, 95), fill=(120, 72, 36, 255))        # ears
    d.ellipse((161, 30, 216, 95), fill=(120, 72, 36, 255))
    d.ellipse((70, 92, 186, 208), fill=(228, 200, 152, 255))    # face
    d.ellipse((98, 120, 122, 150), fill=(35, 25, 18, 255))      # eyes
    d.ellipse((134, 120, 158, 150), fill=(35, 25, 18, 255))
    d.ellipse((116, 156, 140, 178), fill=(80, 52, 30, 255))     # snout
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, sizes=[(16, 16), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
