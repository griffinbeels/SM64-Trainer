# src/sm64_events/desktop/tray.py
"""System tray icon (pystray): Show / Quit. Shell-only — no browser
equivalent, so it never touches ui/. Runs on its own daemon thread."""
import logging
import sys
import threading
from pathlib import Path

import pystray
from PIL import Image

from sm64_events.core.paths import APP_DISPLAY_NAME

log = logging.getLogger("sm64.desktop")

# Single source of truth for the asset base directory, used by tray and window.
def _asset_path(filename: str) -> Path:
    """Resolve an asset filename for both source runs and frozen (PyInstaller) exes."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", "."))
    else:
        # src/sm64_events/desktop/tray.py -> repo root / assets
        # parents[0]=desktop, [1]=sm64_events, [2]=src, [3]=worktree root
        base = Path(__file__).resolve().parents[3] / "assets"
    return base / filename


def _icon_image() -> "Image.Image":
    """Load the Ukiki .ico and return a 32x32 RGBA image ready for pystray.

    pystray (Windows) serialises the PIL image back to ICO via LoadImage.
    Feeding a raw multi-res .ico (256×256 default frame) can cause Windows to
    render a blurry/generic icon.  Loading the 32×32 frame explicitly and
    converting to RGBA gives pystray a clean, correctly-sized source.
    Falls back to a solid brown 64x64 placeholder on any load failure.
    """
    try:
        ico = Image.open(_asset_path("ukiki.ico"))
        # Extract the 32x32 frame directly from the multi-res ICO container so
        # pystray's serialise→LoadImage round-trip receives a sharp tray-sized image.
        frame = ico.ico.getimage((32, 32))
        return frame.convert("RGBA")
    except Exception:
        return Image.new("RGB", (64, 64), (120, 72, 36))


def create(on_show, on_quit) -> "pystray.Icon":
    menu = pystray.Menu(
        pystray.MenuItem("Show", lambda icon, item: on_show()),
        pystray.MenuItem("Quit", lambda icon, item: on_quit()))
    return pystray.Icon(APP_DISPLAY_NAME, _icon_image(), APP_DISPLAY_NAME, menu)


def start(icon) -> None:
    threading.Thread(target=icon.run, name="tray", daemon=True).start()


def stop(icon) -> None:
    try:
        icon.stop()
    except Exception:
        log.debug("tray stop failed", exc_info=True)
