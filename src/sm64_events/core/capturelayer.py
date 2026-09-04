"""THE CAPTURE LAYER's installer: the wrapper graphics plugin, into and out
of Project64, under consent (round 32 item 95; spec
docs/superpowers/specs is local -- the facts live in this docstring).

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

import logging
from dataclasses import asdict, dataclass, field
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
ACTIVE = "active"                 # the frame stream's heartbeat is advancing
REGRESSED = "regressed"           # we installed it, but the registry names another plugin now
UNAVAILABLE = "unavailable"       # no PJ64 folder known / no DLL shipped with this build


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
    gl_context: bool              # the layer found a GL context to read from
    consented_at: str | None
    problems: list = field(default_factory=list)   # fixable sentences, in order
    state: str = NOT_INSTALLED

    def as_dict(self) -> dict:
        return asdict(self)


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

    def locate(self) -> Path | None:
        """Project64's folder: from the running process, else remembered."""
        raise NotImplementedError

    def status(self) -> LayerStatus:
        raise NotImplementedError

    def install(self, consent: bool) -> LayerStatus:
        raise NotImplementedError

    def uninstall(self) -> LayerStatus:
        raise NotImplementedError
