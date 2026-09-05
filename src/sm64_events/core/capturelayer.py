"""THE CAPTURE LAYER's installer: the wrapper graphics plugin, into and out
of Project64, under consent (round 32 item 95; the facts live in this
docstring and in .claude/rules/replay-compare.md).

Project64 1.6 keeps its plugin choice in the registry, under
``HKCU\\Software\\N64 Emulation\\Project64 Version 1.6\\Dll``: the value
``Graphics Dll`` is a FILENAME inside ``<pj64>\\Plugin\\`` (``Use Default
Plugin Dir`` = 1; otherwise the ``Plugin Directory`` value names the
folder). Installing the layer is three writes and nothing else:

1. copy ``sm64_trainer_gfx.dll`` into that Plugin folder;
2. write ``sm64_trainer_gfx.ini`` beside it with ``wrapped=<the Graphics
   Dll the user had>`` -- never a wrapper name, so installing twice is a
   no-op;
3. set ``Graphics Dll`` to the wrapper's filename.

Undo is step 3 in reverse; the files stay (harmless, and a reinstall is
instant). Both refuse while Project64 runs: PJ64 1.6 rewrites its registry
values on exit, so a change made under it can be undone by its own
shutdown, and a running emulator has the old DLL loaded anyway.

Nothing here knows a game address or a pixel; it moves one file and one
registry value, and it remembers what it did in a JSON overlay
(`capture_layer_settings_path()`), resilient like `core/modes.py`: an
absent or corrupt file loads as "never consented" with one warning.

The registry, the process list and the frame stream's header are
injected (small Protocols below) so every path is testable with fakes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

log = logging.getLogger("sm64.capturelayer")

WRAPPER_DLL = "sm64_trainer_gfx.dll"
WRAPPER_INI = "sm64_trainer_gfx.ini"
REGISTRY_KEY = r"Software\N64 Emulation\Project64 Version 1.6"
REGISTRY_DLL_SUBKEY = REGISTRY_KEY + r"\Dll"
GRAPHICS_DLL_VALUE = "Graphics Dll"
PLUGIN_DIR_VALUE = "Plugin Directory"
USE_DEFAULT_PLUGIN_DIR_VALUE = "Use Default Plugin Dir"

# `LayerStatus.state`
NOT_INSTALLED = "not_installed"   # nothing of ours in PJ64 (consent not given, or undone)
NEEDS_RESTART = "needs_restart"   # installed and selected; PJ64 has not loaded it yet
#: the frame stream's status bits this module reads (mirrored from
#: plugin/gfxwrap/stream.h through replay/framestream.py)
STATUS_GL_CONTEXT = 2
STATUS_READSCREEN = 32
ACTIVE = "active"                 # the frame stream's heartbeat is advancing
REGRESSED = "regressed"           # we installed it, but the registry names another plugin now
UNAVAILABLE = "unavailable"       # no PJ64 folder known / no DLL shipped with this build

_OVERLAY_DEFAULTS = {
    "consented_at": None,
    "pj64_dir": None,
    "wrapped": None,
    "uninstalled_at": None,
}


class LayerRefused(Exception):
    """An install or uninstall that cannot proceed; `str(exc)` is the
    sentence the setup screen shows where the click landed."""


class Registry(Protocol):
    def get(self, subkey: str, name: str) -> str | int | None: ...
    def set(self, subkey: str, name: str, value: str) -> None: ...


class Processes(Protocol):
    def pj64_image_path(self) -> str | None:
        """Full path of the running Project64.exe, or None."""
        ...


@dataclass(frozen=True)
class LayerStatus:
    pj64_dir: str | None
    pj64_running: bool
    registry_graphics_dll: str | None
    wrapper_present: bool         # the DLL file sits in the Plugin folder
    wrapper_current: bool         # ...and equals the one shipped with this build
    wrapper_selected: bool        # the registry names the wrapper
    wrapped_name: str | None      # what the ini forwards to
    layer_alive: bool             # the frame stream's heartbeat advanced
    gl_context: bool              # the layer found a GL context on the emulation thread
    consented_at: str | None
    problems: list = field(default_factory=list)   # fixable sentences, in order
    state: str = NOT_INSTALLED
    #: which capture point the pictures take: "gl" (the layer's own GL_FRONT
    #: read), "readscreen" (the wrapped plugin's own ReadScreen -- his
    #: GLideN64 renders on a thread of its own), None while none has
    pictures_via: str | None = None
    #: THE EXACT STEPS from here to a live layer, in order, each
    #: {id, label, done, action}: the setup screen renders these as its
    #: checklist and ticks them live. His rule (2026-09-05): "onboarding is
    #: the exact set of steps the user needs to follow to set everything up
    #: PERFECTLY" -- including close Project64, wait, start it again.
    steps: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _files_match(path_a: Path, path_b: Path) -> bool:
    """Byte-identical, compared by hash rather than size-and-mtime -- the
    only question that matters here is "would re-copying change anything",
    and a stale-but-same-size DLL must not read as current."""
    try:
        return _sha256(path_a) == _sha256(path_b)
    except OSError:
        return False


class CaptureLayer:
    """Locate Project64, report the layer's state, install and undo it."""

    def __init__(self, registry: Registry, processes: Processes,
                 settings_path: Path, dll_source: Path | None,
                 stream_header: Callable[[], object | None] | None = None):
        self._registry = registry
        self._processes = processes
        self._settings_path = settings_path
        self._dll_source = dll_source
        self._stream_header = stream_header or (lambda: None)
        self._last_alive: int | None = None   # the previous status() call's heartbeat

    # -- overlay -----------------------------------------------------

    def _load_overlay(self) -> dict:
        """The persisted `{consented_at, pj64_dir, wrapped, uninstalled_at}`,
        or all-None defaults on any read failure -- an absent or corrupt
        file must never stop the setup screen from rendering (same
        resilience contract as `core/modes.py::load_mode_config`)."""
        try:
            raw = json.loads(self._settings_path.read_text())
        except FileNotFoundError:
            return dict(_OVERLAY_DEFAULTS)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            log.warning("ignoring unreadable %s; capture layer treated as "
                        "never consented", self._settings_path)
            return dict(_OVERLAY_DEFAULTS)
        if not isinstance(raw, dict):
            log.warning("ignoring non-object %s; capture layer treated as "
                        "never consented", self._settings_path)
            return dict(_OVERLAY_DEFAULTS)
        return {key: raw.get(key, default) for key, default in _OVERLAY_DEFAULTS.items()}

    def _save_overlay(self, overlay: dict) -> None:
        self._settings_path.parent.mkdir(parents=True, exist_ok=True)
        self._settings_path.write_text(json.dumps(overlay, indent=2))

    def _remember_pj64_dir(self, pj64_dir: Path) -> None:
        overlay = self._load_overlay()
        if overlay.get("pj64_dir") == str(pj64_dir):
            return
        overlay["pj64_dir"] = str(pj64_dir)
        self._save_overlay(overlay)

    # -- the plugin folder + its files --------------------------------

    def _plugin_dir(self, pj64_dir: Path) -> Path:
        """PJ64's Plugin folder: the exe folder's own `Plugin` subfolder
        unless `Use Default Plugin Dir` is explicitly 0, in which case
        `Plugin Directory` names it instead."""
        use_default = self._registry.get(REGISTRY_KEY, USE_DEFAULT_PLUGIN_DIR_VALUE)
        if use_default in (0, "0"):
            custom = self._registry.get(REGISTRY_KEY, PLUGIN_DIR_VALUE)
            if custom:
                return Path(custom)
        return pj64_dir / "Plugin"

    def _registry_graphics_dll(self) -> str | None:
        value = self._registry.get(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE)
        return str(value) if value is not None else None

    def _set_registry_graphics_dll(self, name: str) -> None:
        self._registry.set(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE, name)

    def _read_wrapped_name(self, ini_path: Path) -> str | None:
        try:
            text = ini_path.read_text()
        except OSError:
            return None
        for line in text.splitlines():
            if line.startswith("wrapped="):
                return line[len("wrapped="):].strip()
        return None

    def _write_wrapped_name(self, ini_path: Path, name: str | None) -> None:
        ini_path.write_text(f"wrapped={name or ''}\n")

    # -- public surface ------------------------------------------------

    def locate(self) -> Path | None:
        """Project64's folder: from the running process, else remembered."""
        image_path = self._processes.pj64_image_path()
        if image_path:
            pj64_dir = Path(image_path).parent
            self._remember_pj64_dir(pj64_dir)
            return pj64_dir
        remembered = self._load_overlay().get("pj64_dir")
        if remembered and Path(remembered).is_dir():
            return Path(remembered)
        return None

    def status(self) -> LayerStatus:
        pj64_dir = self.locate()
        running = self._processes.pj64_image_path() is not None
        overlay = self._load_overlay()
        consented_at = overlay.get("consented_at")

        registry_graphics_dll = self._registry_graphics_dll()
        wrapper_selected = (registry_graphics_dll is not None
                             and registry_graphics_dll.lower() == WRAPPER_DLL.lower())
        wrapper_present = False
        wrapper_current = False
        wrapped_name = overlay.get("wrapped")

        if pj64_dir is not None:
            plugin_dir = self._plugin_dir(pj64_dir)
            dll_path = plugin_dir / WRAPPER_DLL
            ini_path = plugin_dir / WRAPPER_INI
            wrapper_present = dll_path.exists()
            if wrapper_present and self._dll_source is not None:
                wrapper_current = _files_match(dll_path, self._dll_source)
            if ini_path.exists():
                ini_wrapped = self._read_wrapped_name(ini_path)
                if ini_wrapped is not None:
                    wrapped_name = ini_wrapped

        header = self._stream_header()
        layer_alive = False
        gl_context = False
        pictures_via = None
        if header is not None:
            alive = getattr(header, "alive", None)
            if isinstance(alive, int):
                if self._last_alive is None:
                    # The first read after boot has nothing to compare
                    # against; a second read a few heartbeats later (60/s)
                    # answers it, instead of a "restart Project64" that a
                    # running game did not earn.
                    time.sleep(0.05)
                    later = getattr(self._stream_header(), "alive", alive)
                    layer_alive = isinstance(later, int) and later != alive
                    alive = later if isinstance(later, int) else alive
                else:
                    layer_alive = alive != self._last_alive
                self._last_alive = alive
            status_bits = getattr(header, "status", None)
            if isinstance(status_bits, int):
                gl_context = bool(status_bits & STATUS_GL_CONTEXT)
                if gl_context:
                    pictures_via = "gl"
                elif status_bits & STATUS_READSCREEN:
                    pictures_via = "readscreen"

        # UNAVAILABLE means this BUILD has nothing to install. Not knowing
        # where Project64 lives is a step the setup screen walks the user
        # through, not a reason to hide it (his first launch after the
        # layer shipped: no screen, because PJ64 was not running yet).
        if self._dll_source is None:
            state = UNAVAILABLE
        elif consented_at is None:
            state = NOT_INSTALLED
        elif pj64_dir is None or not wrapper_selected or not wrapper_present:
            state = REGRESSED
        elif layer_alive:
            state = ACTIVE
        else:
            state = NEEDS_RESTART

        problems: list[str] = []
        if state == NOT_INSTALLED and pj64_dir is None:
            problems.append("start Project64 once so the trainer can find it")
        elif state == NOT_INSTALLED and running:
            problems.append("Project64 is running -- close it before installing")
        elif state == REGRESSED and pj64_dir is None:
            problems.append("start Project64 once so the trainer can find it again")
        elif state == REGRESSED and not wrapper_selected:
            problems.append(
                f"another graphics plugin is selected ({registry_graphics_dll}); "
                "re-install to restore the capture layer")
        elif state == REGRESSED:
            problems.append("the capture layer's file is missing from Project64's "
                            "Plugin folder; re-install to restore it")
        elif state == NEEDS_RESTART:
            problems.append("restart Project64 to load the capture layer")
        if header is not None and pictures_via is None and getattr(header, "dropped", 0):
            # The layer presents but no picture reaches it: no GL context on
            # the emulation thread (GLideN64_LINK_4.2 renders on a thread of
            # its own, measured 2026-09-05) AND its ReadScreen answered
            # nothing. The recorder has already fallen back to the desktop
            # grab; this row says so, where the click lands.
            problems.append("the capture layer is loaded but no picture reaches it "
                            "(no OpenGL context on the emulation thread, and the graphics "
                            "plugin's ReadScreen gave nothing); recording uses desktop "
                            "capture until this is fixed")
        if state in (ACTIVE, NEEDS_RESTART) and wrapper_present and not wrapper_current:
            problems.append("this build carries a newer capture layer; "
                            + ("close Project64 and the trainer updates it by itself"
                               if running else "Update to install it"))
        steps = self._steps(state, running, wrapper_present and not wrapper_current,
                            layer_alive)

        return LayerStatus(
            pj64_dir=str(pj64_dir) if pj64_dir is not None else None,
            pj64_running=running,
            registry_graphics_dll=registry_graphics_dll,
            wrapper_present=wrapper_present,
            wrapper_current=wrapper_current,
            wrapper_selected=wrapper_selected,
            wrapped_name=wrapped_name,
            layer_alive=layer_alive,
            gl_context=gl_context,
            pictures_via=pictures_via,
            steps=steps,
            consented_at=consented_at,
            problems=problems,
            state=state,
        )

    def install(self, consent: bool) -> LayerStatus:
        if not consent:
            raise LayerRefused("consent is required before installing the capture layer")
        pj64_dir = self.locate()
        if pj64_dir is None:
            raise LayerRefused("start Project64 once so the trainer can find it")
        if self._processes.pj64_image_path() is not None:
            raise LayerRefused("close Project64, then install")
        if self._dll_source is None or not self._dll_source.exists():
            raise LayerRefused("this build carries no capture layer")

        plugin_dir = self._plugin_dir(pj64_dir)
        plugin_dir.mkdir(parents=True, exist_ok=True)
        dll_path = plugin_dir / WRAPPER_DLL
        ini_path = plugin_dir / WRAPPER_INI

        current_graphics_dll = self._registry_graphics_dll()
        if current_graphics_dll is not None and current_graphics_dll.lower() == WRAPPER_DLL.lower():
            # a re-install: the registry already names OUR wrapper, so the
            # real "what were you wrapping" answer is whatever the ini
            # already says -- never overwrite it with our own name.
            wrapped_name = (self._read_wrapped_name(ini_path) if ini_path.exists()
                             else self._load_overlay().get("wrapped"))
        else:
            wrapped_name = current_graphics_dll

        shutil.copyfile(self._dll_source, dll_path)
        self._write_wrapped_name(ini_path, wrapped_name)
        self._set_registry_graphics_dll(WRAPPER_DLL)

        overlay = self._load_overlay()
        overlay["consented_at"] = _now_iso()
        overlay["pj64_dir"] = str(pj64_dir)
        overlay["wrapped"] = wrapped_name
        overlay["uninstalled_at"] = None
        self._save_overlay(overlay)

        return self.status()

    @staticmethod
    def _steps(state: str, running: bool, stale: bool, layer_alive: bool) -> list:
        """The ordered steps from this state to a live layer. Every step is
        a thing HE does (close, click, start) or a thing the trainer does
        by itself that he can watch tick; a state with nothing left to do
        has no steps."""
        close = {"id": "close", "label": "Close Project64", "done": not running,
                 "action": None}
        start = {"id": "start", "label": "Start Project64 and open the Usamune ROM",
                 "done": layer_alive, "action": None}
        if state == NOT_INSTALLED:
            return [close,
                    {"id": "install", "label": "Install the capture layer",
                     "done": False, "action": "install"},
                    start]
        if state == REGRESSED:
            return [close,
                    {"id": "install", "label": "Re-install the capture layer",
                     "done": False, "action": "install"},
                    start]
        if stale:
            return [dict(close, label="Close Project64 -- the trainer then updates "
                                      "the capture layer by itself"),
                    {"id": "update", "label": "Capture layer updated", "done": False,
                     "action": None},
                    start]
        if state == NEEDS_RESTART:
            return ([close, start] if running else [start])
        return []

    def refresh_if_stale(self) -> bool:
        """Copy this build's DLL over an installed older one while Project64
        is closed. True when a copy happened; False when nothing was
        installed, the file is current, or PJ64 holds it open. Runs at boot
        under the consent already given -- the one update path for a plugin
        fix, so a user never re-consents to what they already chose."""
        if self._dll_source is None or not self._dll_source.exists():
            return False
        if self._load_overlay().get("consented_at") is None:
            return False
        if self._processes.pj64_image_path() is not None:
            return False
        pj64_dir = self.locate()
        if pj64_dir is None:
            return False
        dll_path = self._plugin_dir(pj64_dir) / WRAPPER_DLL
        if not dll_path.exists() or _files_match(dll_path, self._dll_source):
            return False
        shutil.copyfile(self._dll_source, dll_path)
        return True

    def refresh_loop(self, stop, interval_s: float = 2.0, log=None) -> None:
        """`refresh_if_stale` every `interval_s` until `stop` is set -- so a
        newer build's layer lands within a breath of Project64 closing, and
        the setup screen's "updated" step ticks while he watches. His rule
        (2026-09-05): "order shouldn't matter... Open the game first, or
        open the tool first, who cares."""
        while not stop.wait(interval_s):
            try:
                if self.refresh_if_stale() and log is not None:
                    log.info("capture layer refreshed to this build's DLL "
                             "(Project64 was closed)")
            except Exception:
                if log is not None:
                    log.exception("capture layer refresh failed")

    def uninstall(self) -> LayerStatus:
        if self._processes.pj64_image_path() is not None:
            raise LayerRefused("close Project64, then uninstall")

        pj64_dir = self.locate()
        overlay = self._load_overlay()
        wrapped_name = None
        if pj64_dir is not None:
            ini_path = self._plugin_dir(pj64_dir) / WRAPPER_INI
            if ini_path.exists():
                wrapped_name = self._read_wrapped_name(ini_path)
        if wrapped_name is None:
            wrapped_name = overlay.get("wrapped")
        if wrapped_name is not None:
            self._set_registry_graphics_dll(wrapped_name)

        overlay["uninstalled_at"] = _now_iso()
        overlay["consented_at"] = None
        self._save_overlay(overlay)

        return self.status()


# The real Registry/Processes adapters (WinRegistry, WinProcesses) moved to
# capturelayer_win.py once this module passed its size budget -- re-exported
# here so `from sm64_events.core.capturelayer import WinRegistry` still
# works for a future wiring call site.
from sm64_events.core.capturelayer_win import WinProcesses, WinRegistry  # noqa: E402,F401
