"""Deliver one browser crop as one native composition update.

The browser remains the decoder and clock owner. Its full-size window is kept
at alpha zero in Mario mode; only the separate crop surface paints. A packet
contains both the pixels and their position, so they cannot slip by a frame.
The renderer owns its HWND on one DPI-aware thread and never queries Project64.
"""
import base64
import binascii
import threading
import time
import uuid
from dataclasses import dataclass

from sm64_events.ghost.window import diag, native, style
from sm64_events.ghost.window.geometry import Rect

MAX_PIXELS = 4_000_000
STALE_SECONDS = 1.0


@dataclass(frozen=True)
class CropFrame:
    sequence: int
    mode: str
    visible: bool
    x: int
    y: int
    width: int
    height: int
    viewport_width: int
    viewport_height: int
    rgba: bytes


def read_frame(packet: dict) -> CropFrame:
    """Validate the page boundary before allocating a native bitmap."""
    if not isinstance(packet, dict) or packet.get("mode") not in ("whole", "mario"):
        raise ValueError("unknown crop mode")
    names = ("sequence", "x", "y", "width", "height", "viewport_width", "viewport_height")
    if any(type(packet.get(key)) is not int or packet[key] < 0 for key in names):
        raise ValueError("crop coordinates must be nonnegative integers")
    if type(packet.get("visible")) is not bool:
        raise ValueError("crop visibility must be boolean")
    width, height = packet["width"], packet["height"]
    rgba = b""
    if packet["mode"] == "mario" and packet["visible"]:
        if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= MAX_PIXELS):
            raise ValueError("crop is too large or empty")
        if (packet["x"] + width > packet["viewport_width"]
                or packet["y"] + height > packet["viewport_height"]):
            raise ValueError("crop leaves the viewport")
        pixels = packet.get("pixels")
        if not isinstance(pixels, str) or len(pixels) != ((width * height * 4 + 2) // 3) * 4:
            raise ValueError("crop byte count does not match its rectangle")
        try:
            rgba = base64.b64decode(pixels, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("invalid crop pixels") from error
        if len(rgba) != width * height * 4:
            raise ValueError("crop byte count does not match its rectangle")
    return CropFrame(**{key: packet[key] for key in names},
                     mode=packet["mode"], visible=packet["visible"], rgba=rgba)


@dataclass(frozen=True)
class Scene:
    rect: Rect | None = None
    alpha: int = 0
    browser_hwnd: int = 0
    ready: bool = False


class PageApi:
    """Expose only frame delivery, never the renderer's lifecycle methods."""

    def __init__(self, bridge):
        self._bridge = bridge

    def present(self, packet):
        return self._bridge.present(packet)


class CropBridge:
    """One latest-frame mailbox, also the sole API exposed to the page.

    Navigation changes the token. An old page's late asynchronous delivery
    cannot put its last Mario over the new target. Heartbeats carry the cached
    frame, letting a paused picture stay up while a hung page expires.
    """

    def __init__(self, *, clock=time.monotonic, wake=None, surface_factory=None):
        self._clock = clock
        self._wake_follower = wake
        self._factory = surface_factory
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._frame = None
        self._frame_at = 0.0
        self._scene = Scene()
        self._scene_at = 0.0
        self._session = uuid.uuid4().hex
        self._hidden = True
        self._drawn = None
        self._uploads = 0
        self._error = None

    @property
    def session(self):
        with self._lock:
            return self._session

    @property
    def mode(self):
        with self._lock:
            return self._frame.mode if self._frame else "pending"

    @property
    def browser_ready(self):
        with self._lock:
            return bool(self._frame and self._frame.mode == "whole"
                        and self._frame.visible and self._hidden
                        and self._clock() - self._frame_at < STALE_SECONDS)

    @property
    def hidden(self):
        with self._lock:
            return self._hidden

    def reset(self):
        with self._lock:
            self._session = uuid.uuid4().hex
            self._frame = None
            self._scene = Scene()
        self._wake.set()

    def set_wake(self, wake):
        self._wake_follower = wake

    def diagnostics(self):
        """Small readback for the existing page log; never log pixel payloads."""
        with self._lock:
            return {"mode": self.mode, "hidden": self._hidden,
                    "sequence": self._frame.sequence if self._frame else None,
                    "uploads": self._uploads, "drawn": self._drawn,
                    "error": self._error}

    def present(self, packet):
        """Called by pywebview on a bridge thread; never calls a window API."""
        frame = read_frame(packet)
        with self._lock:
            if packet.get("session") != self._session:
                return {"accepted": False, "reason": "old_page"}
            if self._frame and frame.sequence <= self._frame.sequence:
                return {"accepted": False, "reason": "old_frame"}
            changed_mode = (self._frame is None or frame.mode != self._frame.mode
                            or frame.visible != self._frame.visible)
            self._frame, self._frame_at = frame, self._clock()
            rect = self._scene.rect
        self._wake.set()
        if changed_mode and self._wake_follower:
            self._wake_follower.set()
        return {"accepted": True, "sequence": frame.sequence,
                "viewport": {"width": rect.width, "height": rect.height} if rect else None}

    def configure(self, scene):
        with self._lock:
            self._scene, self._scene_at = scene, self._clock()
        self._wake.set()

    def follow(self, want, hwnd, ready, applied_alpha):
        """Use only a proven browser placement, in the follower's DPI space."""
        placed = ready and want.rect is not None and native.get_window_rect(hwnd) == want.rect
        self.configure(Scene(rect=want.rect, alpha=want.alpha, browser_hwnd=hwnd,
                             ready=bool(placed and applied_alpha == 0)))

    def render_once(self, surface):
        """One snapshot; window writes are exclusive to the renderer thread."""
        with self._lock:
            frame, scene = self._frame, self._scene
            session = self._session
            fresh = (self._clock() - self._frame_at < STALE_SECONDS
                     and self._clock() - self._scene_at < STALE_SECONDS)
        ready = (fresh and frame and frame.mode == "mario" and frame.visible
                 and scene.ready and scene.rect and scene.alpha > 0
                 and frame.viewport_width == scene.rect.width
                 and frame.viewport_height == scene.rect.height)
        if not ready:
            surface.hide()
            self._mark_hidden(True)
            return False
        rect = Rect(x=scene.rect.x + frame.x, y=scene.rect.y + frame.y,
                    width=frame.width, height=frame.height)
        # The decoder is already directly above the game. Put the crop directly
        # above the decoder without asking the emulator's potentially busy UI.
        above = native.window_above(scene.browser_hwnd) if scene.browser_hwnd else None
        if above == surface.hwnd:
            above = None
        if not surface.present(rect, frame.rgba, frame.width, frame.height,
                               scene.alpha, insert_after=above):
            with self._lock:
                self._error = getattr(surface, "last_error", "upload refused")
            surface.hide()
            self._mark_hidden(True)
            return False
        with self._lock:
            # Navigation or a clear can arrive during the bitmap conversion.
            # Do not resurrect that old snapshot after its page was retired.
            current = self._frame
            if (session != self._session or not current or not current.visible
                    or current.mode != "mario" or self._scene != scene):
                surface.hide()
                self._mark_hidden(True)
                return False
            shown = surface.show()
            self._uploads += 1
            self._drawn = {"rect": rect.as_list(), "alpha": scene.alpha}
            self._error = None
            self._mark_hidden(not shown)
            return shown

    def _mark_hidden(self, hidden):
        with self._lock:
            changed = self._hidden != hidden
            self._hidden = hidden
        if changed and self._wake_follower:
            self._wake_follower.set()

    def run(self, stop):
        from sm64_events.ghost.window.surface import LayeredSurface

        native.set_thread_dpi_context(style.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        surface = (self._factory or LayeredSurface)()
        errors = diag.Collapsing()
        try:
            while not stop.is_set():
                self._wake.clear()
                try:
                    self.render_once(surface)
                    surface.pump()
                except Exception as error:  # noqa: BLE001 - fail closed, retry next frame
                    surface.hide()
                    self._mark_hidden(True)
                    with self._lock:
                        self._error = str(error)
                    errors("crop_failed", level="error", detail=str(error))
                self._wake.wait(0.05)
        finally:
            surface.close()
            self._mark_hidden(True)
