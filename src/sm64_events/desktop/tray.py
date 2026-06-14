# src/sm64_events/desktop/tray.py
"""System tray icon (pystray): Show / Quit. Shell-only — no browser
equivalent, so it never touches ui/. Runs on its own daemon thread."""
import logging
import sys
import threading
from pathlib import Path

import pystray
from PIL import Image

log = logging.getLogger("sm64.desktop")


def _icon_image() -> "Image.Image":
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", "."))
    else:
        # src/sm64_events/desktop/tray.py -> repo root / assets
        # parents[0]=desktop, [1]=sm64_events, [2]=src, [3]=worktree root
        base = Path(__file__).resolve().parents[3] / "assets"
    try:
        return Image.open(base / "ukiki.ico")
    except Exception:
        return Image.new("RGB", (64, 64), (120, 72, 36))


def create(on_show, on_quit) -> "pystray.Icon":
    menu = pystray.Menu(
        pystray.MenuItem("Show", lambda icon, item: on_show()),
        pystray.MenuItem("Quit", lambda icon, item: on_quit()))
    return pystray.Icon("sm64_tracker", _icon_image(), "sm64_tracker", menu)


def start(icon) -> None:
    threading.Thread(target=icon.run, name="tray", daemon=True).start()


def stop(icon) -> None:
    try:
        icon.stop()
    except Exception:
        log.debug("tray stop failed", exc_info=True)
