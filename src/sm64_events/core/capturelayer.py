"""THE CAPTURE LAYER's installer: the wrapper graphics plugin, into and out
of Project64, under consent (round 32 item 95; the facts live in this
docstring and in .claude/rules/replay-compare.md).

Project64 1.6 keeps its plugin choice in the registry, under
``HKCU\\Software\\N64 Emulation\\Project64 Version 1.6\\Dll``: the value
``Graphics Dll`` is a FILENAME inside ``<pj64>\\Plugin\\`` (``Use Default
Plugin Dir`` = 1; otherwise the ``Plugin Directory`` value names the
folder). Installing the layer is four writes and nothing else:

1. copy ``GLideN64_SM64Trainer.dll`` -- THE RENDERER, the trainer's build
   of LINK's GLideN64 v4.2 with the capture hooks -- into that Plugin folder;
2. copy ``sm64_trainer_gfx.dll`` -- the capture wrapper -- beside it;
3. write ``sm64_trainer_gfx.ini`` with ``wrapped=GLideN64_SM64Trainer.dll``
   so the wrapper loads OUR renderer (a stock renderer has no capture
   export, and every picture would read "input unavailable");
4. set ``Graphics Dll`` to the wrapper's filename, remembering the plugin
   it replaced (``previous_graphics_dll`` in the overlay) for undo.

Undo is step 4 in reverse; the files stay (harmless, and a reinstall is
instant). An app update never re-onboards: a packaged build refreshes the
installed pair by itself (``refresh_if_stale`` at boot and every two
seconds) as soon as Project64 is closed, under the consent already given,
and the setup screen stays shut while that is pending (his rule,
2026-09-16: "No additional onboarding past the original onboarding").
Both refuse while Project64 runs: PJ64 1.6 rewrites its registry
values on exit, so a change made under it can be undone by its own
shutdown, and a running emulator has the old DLL loaded anyway.

Nothing here knows a game address or a pixel; it moves one file and one
registry value, and it remembers what it did in a JSON overlay
(`capture_layer_settings_path()`), resilient like `core/modes.py`: an
absent or corrupt file loads as "never consented" with one warning.

The registry, the process list and the GPU capture observation are
injected (small Protocols below) so every path is testable with fakes.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from sm64_events.core.onboarding import CounterWindow
from sm64_events.core.setup_gpu import GpuSetupEvidence
from sm64_events.core.plugin_installation import sha256 as _sha256, verified_copy

log = logging.getLogger("sm64.capturelayer")

WRAPPER_DLL = "sm64_trainer_gfx.dll"
WRAPPER_INI = "sm64_trainer_gfx.ini"
RENDERER_DLL = "GLideN64_SM64Trainer.dll"
#: the files this trainer puts in the Plugin folder; never "the plugin the
#: user had", so undo can never select one of ours
OWN_FILES = frozenset({WRAPPER_DLL.lower(), RENDERER_DLL.lower()})
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

_OVERLAY_DEFAULTS = {
    "consented_at": None,
    "pj64_dir": None,
    "wrapped": None,
    "uninstalled_at": None,
    "installed_sha256": None,
    "installation_receipt": None,
    "installed_renderer_sha256": None,
    "renderer_receipt": None,
    "previous_graphics_dll": None,
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
    wrapped_name: str | None      # what the ini forwards to (our renderer once installed)
    layer_alive: bool             # recent source/control evidence, bound to plugin_pid
    consented_at: str | None
    problems: list = field(default_factory=list)   # fixable sentences, in order
    state: str = NOT_INSTALLED
    #: "gpu" once the GPU route has delivered an accepted picture, None until then
    pictures_via: str | None = None
    #: THE EXACT STEPS from here to a live layer, in order, each
    #: {id, label, done, action}: the setup screen renders these as its
    #: checklist and ticks them live. His rule (2026-09-05): "onboarding is
    #: the exact set of steps the user needs to follow to set everything up
    #: PERFECTLY" -- including close Project64, wait, start it again.
    steps: list = field(default_factory=list)
    plugin_pid: int | None = None
    pictures_flowing: bool = False
    plugin_dir: str | None = None
    renderer_present: bool = False     # GLideN64_SM64Trainer.dll sits in the Plugin folder
    renderer_current: bool = False     # ...and is the one shipped with this build
    previous_graphics_dll: str | None = None   # what undo selects again
    #: this build refreshes the installed pair by itself once Project64 is
    #: closed (packaged build, our own earlier install); the UI must not
    #: reopen onboarding for a stale layer it will update anyway
    automatic_update: bool = False
    # Internal typed evidence; the public setup response retains its old shape.
    gpu_observation: GpuSetupEvidence | None = field(default=None, repr=False)

    def as_dict(self) -> dict:
        result = asdict(self)
        result.pop("gpu_observation")
        return {**result, "installation_verified": self.installation_verified}

    @property
    def installation_verified(self) -> bool:
        """Current files/configuration, independent of this checkout's history."""
        return (self.wrapper_current and self.renderer_current
                and _configured(self.wrapper_present, self.wrapper_selected, self.wrapped_name,
                                self.renderer_present))


def _configured(present: bool, selected: bool, wrapped: str | None, renderer_present: bool) -> bool:
    """Both files in place, the wrapper selected, and the ini pointing the
    wrapper at OUR renderer. An ini naming any other plugin (an older
    install that wrapped the user's own renderer) is not set up."""
    return bool(present and selected and renderer_present and wrapped
                and wrapped.lower() == RENDERER_DLL.lower())


def _not_ours(name: str | None) -> str | None:
    """A plugin file name that is not one of the trainer's own, else None."""
    return name if name and name.lower() not in OWN_FILES else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Every native build id ends in its role (-gpu-runtime, -gpu-encoder,
# -renderer). The suffix is required: the renderer DLL carries hundreds of
# 64-digit decimal tables that a bare hex pattern also matches.
_BUILD_ID = re.compile(rb"[0-9a-f]{64}-[a-z][a-z-]*")


_identity_cache: dict = {}


def _build_identity(path: Path) -> bytes | None:
    """A native file's embedded build id: a digest of the sources it was
    compiled from, plus its role suffix. None for a file without one.
    Cached by (path, size, mtime): the refresh loop and the setup screen's
    polling would otherwise scan the 12 MB renderer every two seconds."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _identity_cache:
        ids = set(_BUILD_ID.findall(path.read_bytes()))
        _identity_cache[key] = ids.pop() if len(ids) == 1 else None
    return _identity_cache[key]


def _files_match(path_a: Path, path_b: Path) -> bool:
    """Same capture layer. Two wrappers built from the same native sources
    carry the same embedded build id but differ in bytes (link timestamps),
    so a rebuild of an unchanged tree must not tell him his installed
    candidate "differs from this build" (his report, 2026-09-15). Files
    without an id fall back to the byte hash; a stale-but-same-size DLL
    still never reads as current."""
    try:
        own, other = _build_identity(path_a), _build_identity(path_b)
        if own is not None and other is not None:
            return own == other
        return _sha256(path_a) == _sha256(path_b)
    except OSError:
        return False


def _problems(state: str, pj64_dir, running: bool, wrapper_selected: bool, registry_graphics_dll,
              wrapper_present: bool, wrapped_name, renderer_present: bool, layer_current: bool,
              automatic: bool) -> list[str]:
    """Fixable sentences, in order, for the setup screen."""
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
    elif state == REGRESSED and wrapper_present and (
            not renderer_present or (wrapped_name or "").lower() != RENDERER_DLL.lower()):
        problems.append(
            f"the capture layer wraps {wrapped_name or 'no plugin'} instead of the trainer's "
            "renderer, so it cannot capture; re-install to add the renderer")
    elif state == REGRESSED:
        problems.append("the capture layer's files or configuration are missing from Project64's "
                        "Plugin folder; re-install to restore them")
    elif state == NEEDS_RESTART:
        problems.append(("restart" if running else "start")
                        + " Project64 to load the capture layer")
    if state in (ACTIVE, NEEDS_RESTART) and wrapper_present and not layer_current:
        problems.append("the installed capture layer differs from this build; "
                        + _update_guidance(running, automatic))
    return problems


def _update_guidance(running: bool, automatic: bool) -> str:
    if automatic:
        return ("close Project64 and the trainer updates it by itself"
                if running else "the trainer will update it automatically")
    return ("close Project64, then choose Install to use this build's version"
            if running else "choose Install to use this build's version")


class CaptureLayer:
    """Locate Project64, report the layer's state, install and undo it."""

    def __init__(self, registry: Registry, processes: Processes,
                 settings_path: Path, dll_source: Path | None | Callable[[], Path | None],
                 *,
                 renderer_source: Path | None | Callable[[], Path | None] = None,
                 gpu_observation: Callable[[], GpuSetupEvidence | None] | None = None,
                 auto_refresh: bool = False):
        self._registry = registry
        self._processes = processes
        self._settings_path = settings_path
        self._dll_source = dll_source
        self._renderer_source = renderer_source
        self._gpu_observation = gpu_observation or (lambda: None)
        # A worktree's bundled bytes may be older than a manually installed
        # candidate. Hash inequality proves difference, never update ordering.
        self.auto_refresh = auto_refresh
        self._activity = CounterWindow()
        self._activity_lock = threading.Lock()
        # Install, the packaged refresh tick and Remove all copy the same
        # files and rewrite the same overlay; one at a time, or one side's
        # rollback snapshot can undo the other's finished copy.
        self._install_lock = threading.RLock()
        self._refresh_stop = threading.Event()
        self._refresh_thread = None

    def start_refresh(self, logger=None):
        if not self.auto_refresh or self._refresh_stop.is_set() or self._refresh_thread is not None:
            return
        worker = threading.Thread(target=self.refresh_loop, args=(self._refresh_stop,),
                                  kwargs={"log": logger}, name="capture-layer-refresh", daemon=True)
        worker.start()
        self._refresh_thread = worker

    def close(self):
        """Stop refresh before retiring the passive GPU observer; retry retained owners."""
        self._refresh_stop.set()
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=2.0)
            if self._refresh_thread.is_alive():
                raise RuntimeError("capture-layer refresh cleanup is still pending")
            self._refresh_thread = None
        close = getattr(self._gpu_observation, "close", None)
        if close is not None:
            close()

    def _source_dll(self) -> Path | None:
        # Resolve on use: copying/restoring a developer build after server
        # startup must not cache "unavailable" for the rest of the session.
        source = self._dll_source() if callable(self._dll_source) else self._dll_source
        return source if source is not None and source.is_file() else None

    def _renderer_dll(self) -> Path | None:
        source = self._renderer_source() if callable(self._renderer_source) else self._renderer_source
        return source if source is not None and source.is_file() else None

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

    def _installed_files(self, pj64_dir: Path | None, source: Path | None,
                         renderer: Path | None) -> tuple[bool, bool, str | None, bool, bool]:
        """(wrapper present, wrapper current, ini wrapped name, renderer
        present, renderer current); "current" compares build ids."""
        if pj64_dir is None:
            return False, False, None, False, False
        plugin_dir = self._plugin_dir(pj64_dir)
        dll_path = plugin_dir / WRAPPER_DLL
        present = dll_path.is_file()
        current = present and source is not None and _files_match(dll_path, source)
        renderer_path = plugin_dir / RENDERER_DLL
        renderer_present = renderer_path.is_file()
        renderer_current = renderer_present and renderer is not None and _files_match(renderer_path, renderer)
        return present, current, self._read_wrapped_name(plugin_dir / WRAPPER_INI), renderer_present, renderer_current

    def _previous_graphics_dll(self, overlay: dict, ini_wrapped: str | None) -> str | None:
        """The plugin undo selects: what the registry names now if that is
        not ours, else what we remembered, else what an older install's ini
        wrapped (that scheme wrapped the user's own plugin)."""
        return (_not_ours(self._registry_graphics_dll())
                or _not_ours(overlay.get("previous_graphics_dll"))
                or _not_ours(ini_wrapped)
                or _not_ours(overlay.get("wrapped")))

    def status(self) -> LayerStatus:
        source = self._source_dll()
        renderer = self._renderer_dll()
        pj64_dir = self.locate()
        running = self._processes.pj64_image_path() is not None
        overlay = self._load_overlay()
        automatic = self._automatic_refresh(pj64_dir)

        registry_graphics_dll = self._registry_graphics_dll()
        wrapper_selected = (registry_graphics_dll is not None
                             and registry_graphics_dll.lower() == WRAPPER_DLL.lower())
        wrapper_present, wrapper_current, wrapped_name, renderer_present, renderer_current = (
            self._installed_files(pj64_dir, source, renderer))
        configured = _configured(wrapper_present, wrapper_selected, wrapped_name, renderer_present)
        layer_current = wrapper_current and renderer_current

        # The only liveness evidence is the GPU route's control page and its
        # accepted-picture count (core/setup_gpu.py); no observation means the
        # wrapper has not published a page this server can see.
        gpu = self._gpu_observation()
        if gpu is None:
            activity = {"pictures": False}
            layer_alive, plugin_pid, pictures_via = False, None, None
        else:
            activity = {"pictures": gpu.pictures}
            layer_alive, plugin_pid = gpu.alive, gpu.producer_pid
            pictures_via = "gpu" if gpu.pictures else None

        # UNAVAILABLE means this BUILD has nothing to install. Not knowing
        # where Project64 lives is a step the setup screen walks the user
        # through, not a reason to hide it (his first launch after the
        # layer shipped: no screen, because PJ64 was not running yet).
        if source is None or renderer is None:
            state = UNAVAILABLE
        elif overlay.get("consented_at") is None and not configured:
            state = NOT_INSTALLED
        elif pj64_dir is None or not configured:
            state = REGRESSED
        elif layer_alive:
            state = ACTIVE
        else:
            state = NEEDS_RESTART

        problems = _problems(state, pj64_dir, running, wrapper_selected, registry_graphics_dll,
                             wrapper_present, wrapped_name, renderer_present, layer_current, automatic)
        steps = self._steps(state, running, source is not None and wrapper_present and not layer_current,
                            layer_alive, automatic)

        return LayerStatus(
            pj64_dir=str(pj64_dir) if pj64_dir is not None else None,
            pj64_running=running,
            registry_graphics_dll=registry_graphics_dll,
            wrapper_present=wrapper_present,
            wrapper_current=wrapper_current,
            wrapper_selected=wrapper_selected,
            wrapped_name=wrapped_name,
            layer_alive=layer_alive,
            pictures_via=pictures_via,
            steps=steps,
            consented_at=overlay.get("consented_at"),
            problems=problems,
            state=state,
            plugin_pid=plugin_pid,
            pictures_flowing=activity.get("pictures", False),
            plugin_dir=str(self._plugin_dir(pj64_dir)) if pj64_dir else None,
            renderer_present=renderer_present,
            renderer_current=renderer_current,
            previous_graphics_dll=self._previous_graphics_dll(overlay, wrapped_name),
            automatic_update=automatic,
            gpu_observation=gpu,
        )

    def install(self, consent: bool) -> LayerStatus:
        with self._install_lock:
            return self._install(consent)

    def _install(self, consent: bool) -> LayerStatus:
        if not consent:
            raise LayerRefused("consent is required before installing the capture layer")
        pj64_dir = self.locate()
        if pj64_dir is None:
            raise LayerRefused("start Project64 once so the trainer can find it")
        if self._processes.pj64_image_path() is not None:
            raise LayerRefused("close Project64, then install")
        source, renderer = self._source_dll(), self._renderer_dll()
        if source is None or renderer is None:
            raise LayerRefused("this build carries no capture layer")
        validate = getattr(self._processes, "check_folder", None)
        if validate is not None:
            target = validate(str(pj64_dir))
            if target["state"] != "ready":
                raise LayerRefused(target["message"])

        plugin_dir = self._plugin_dir(pj64_dir)
        plugin_dir.mkdir(parents=True, exist_ok=True)
        dll_path = plugin_dir / WRAPPER_DLL
        ini_path = plugin_dir / WRAPPER_INI
        renderer_path = plugin_dir / RENDERER_DLL

        overlay = self._load_overlay()
        # The plugin undo returns to. A re-install finds OUR wrapper in the
        # registry, so the answer is what we remembered (or what an older
        # install's ini wrapped), never one of our own files.
        previous = self._previous_graphics_dll(
            overlay, self._read_wrapped_name(ini_path) if ini_path.exists() else None)
        overlay["consented_at"] = _now_iso()
        overlay["pj64_dir"] = str(pj64_dir)
        overlay["wrapped"] = RENDERER_DLL
        overlay["previous_graphics_dll"] = previous
        overlay["uninstalled_at"] = None
        with verified_copy(renderer, renderer_path, reason="explicit_install",
                           rollback=(dll_path, ini_path, self._settings_path)) as renderer_receipt:
            overlay["installed_renderer_sha256"] = renderer_receipt["after"]["sha256"]
            overlay["renderer_receipt"] = renderer_receipt
            with verified_copy(source, dll_path, reason="explicit_install",
                               rollback=(ini_path, self._settings_path)) as receipt:
                overlay["installed_sha256"] = receipt["after"]["sha256"]
                overlay["installation_receipt"] = receipt
                self._write_wrapped_name(ini_path, RENDERER_DLL)
                self._save_overlay(overlay)
                # Last write: if copying or persistence fails, Project64 keeps
                # its previous graphics selection and the files are restored.
                self._set_registry_graphics_dll(WRAPPER_DLL)

        return self.status()

    def _steps(self, state: str, running: bool, stale: bool, layer_alive: bool, automatic: bool) -> list:
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
            if not automatic:
                return [close,
                        {"id": "install", "label": "Install this build's capture layer",
                         "done": False, "action": "install"},
                        start]
            return [dict(close, label="Close Project64 -- the trainer then updates "
                                      "the capture layer by itself"),
                    {"id": "update", "label": "Capture layer updated", "done": False,
                     "action": None},
                    start]
        if state == NEEDS_RESTART:
            return ([close, start] if running else [start])
        return []

    def _automatic_refresh(self, pj64_dir: Path | None) -> bool:
        """External/manual replacements do not transfer update ownership."""
        if not self.auto_refresh or pj64_dir is None:
            return False
        overlay = self._load_overlay()
        expected = overlay.get("installed_sha256")
        if not expected or not overlay.get("consented_at"):
            return False
        try:
            return _sha256(self._plugin_dir(pj64_dir) / WRAPPER_DLL) == expected
        except OSError:
            return False

    def refresh_if_stale(self) -> bool:
        with self._install_lock:
            return self._refresh_if_stale()

    def _refresh_if_stale(self) -> bool:
        """Copy this build's DLL over a differing one when explicitly enabled.

        Source checkouts leave a shared, manually installed candidate alone.
        Packaged composition opts in to the existing release update behavior.
        Project64 must be closed. True when a copy happened; False when nothing was
        installed, the file is current, or PJ64 holds it open. Runs at boot
        under the consent already given -- the one update path for a plugin
        fix, so a user never re-consents to what they already chose."""
        if not self.auto_refresh:
            return False
        source, renderer = self._source_dll(), self._renderer_dll()
        if source is None or renderer is None:
            return False
        if self._load_overlay().get("consented_at") is None:
            return False
        if self._processes.pj64_image_path() is not None:
            return False
        pj64_dir = self.locate()
        if not self._automatic_refresh(pj64_dir):
            return False
        validate = getattr(self._processes, "check_folder", None)
        if validate is not None and validate(str(pj64_dir))["state"] != "ready":
            return False
        plugin_dir = self._plugin_dir(pj64_dir)
        dll_path, renderer_path, ini_path = (plugin_dir / WRAPPER_DLL, plugin_dir / RENDERER_DLL,
                                             plugin_dir / WRAPPER_INI)
        ini_wrapped = self._read_wrapped_name(ini_path)
        wraps_ours = (ini_wrapped or "").lower() == RENDERER_DLL.lower()
        if not dll_path.exists():
            return False
        wrapper_stale = not _files_match(dll_path, source)
        renderer_stale = not renderer_path.exists() or not _files_match(renderer_path, renderer)
        if not (wrapper_stale or renderer_stale or not wraps_ours):
            return False
        overlay = self._load_overlay()
        if not wraps_ours:
            # An older install wrapped the user's own plugin: that name is
            # what undo must return to once the ini names our renderer.
            overlay["previous_graphics_dll"] = self._previous_graphics_dll(overlay, ini_wrapped)
            overlay["wrapped"] = RENDERER_DLL
        # Only the file that differs is copied; each copy is verified and
        # rolls the whole install back on failure.
        with ExitStack() as copies:
            if renderer_stale:
                renderer_receipt = copies.enter_context(verified_copy(
                    renderer, renderer_path, reason="automatic_refresh",
                    rollback=(dll_path, ini_path, self._settings_path)))
                overlay["installed_renderer_sha256"] = renderer_receipt["after"]["sha256"]
                overlay["renderer_receipt"] = renderer_receipt
            if wrapper_stale:
                receipt = copies.enter_context(verified_copy(
                    source, dll_path, reason="automatic_refresh", rollback=(ini_path, self._settings_path)))
                overlay["installed_sha256"] = receipt["after"]["sha256"]
                overlay["installation_receipt"] = receipt
            self._write_wrapped_name(ini_path, RENDERER_DLL)
            self._save_overlay(overlay)
        return True

    def refresh_loop(self, stop, interval_s: float = 2.0, log=None) -> None:
        """`refresh_if_stale` every `interval_s` until `stop` is set -- so a
        newer build's layer lands within a breath of Project64 closing, and
        the setup screen's "updated" step ticks while he watches. His rule
        (2026-09-05): "order shouldn't matter... Open the game first, or
        open the tool first, who cares."""
        if not self.auto_refresh:
            return
        while not stop.wait(interval_s):
            try:
                if self.refresh_if_stale() and log is not None:
                    log.info("capture layer refreshed to this build's DLL "
                             "(Project64 was closed)")
            except Exception:
                if log is not None:
                    log.exception("capture layer refresh failed")

    def uninstall(self) -> LayerStatus:
        """Select the previous graphics plugin again. When none is known
        (the registry named one of our own files when we installed), the
        wrapper stays selected: it still draws the game, and the setup
        page says so instead of promising a restore."""
        with self._install_lock:
            return self._uninstall()

    def _uninstall(self) -> LayerStatus:
        if self._processes.pj64_image_path() is not None:
            raise LayerRefused("close Project64, then uninstall")

        pj64_dir = self.locate()
        overlay = self._load_overlay()
        ini_wrapped = None
        if pj64_dir is not None:
            ini_path = self._plugin_dir(pj64_dir) / WRAPPER_INI
            if ini_path.exists():
                ini_wrapped = self._read_wrapped_name(ini_path)
        previous = self._previous_graphics_dll(overlay, ini_wrapped)
        if previous is not None:
            self._set_registry_graphics_dll(previous)

        overlay["uninstalled_at"] = _now_iso()
        overlay["consented_at"] = None
        self._save_overlay(overlay)

        return self.status()


# The real Registry/Processes adapters (WinRegistry, WinProcesses) moved to
# capturelayer_win.py once this module passed its size budget -- re-exported
# here so `from sm64_events.core.capturelayer import WinRegistry` still
# works for a future wiring call site.
from sm64_events.core.capturelayer_win import WinProcesses, WinRegistry  # noqa: E402,F401
