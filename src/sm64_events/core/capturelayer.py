"""THE CAPTURE LAYER's installer: the trainer's graphics plugin, into and out
of Project64, under consent (round 32 item 95; the facts live in this
docstring and in .claude/rules/chain-setup-readiness.md).

Project64 1.6 keeps its plugin choice in the registry, under
``HKCU\\Software\\N64 Emulation\\Project64 Version 1.6\\Dll``: the value
``Graphics Dll`` is a FILENAME inside ``<pj64>\\Plugin\\`` (``Use Default
Plugin Dir`` = 1; otherwise the ``Plugin Directory`` value names the
folder). The graphics plugin is ONE file, ``sm64_trainer_gfx.dll``: the
trainer's build of LINK's GLideN64 v4.2 (the renderer) with the capture
layer inside it, which Project64 loads itself (2026-11-07: one file, nothing
loaded by name, after Defender flagged the separate renderer). Installing it
is three writes and nothing else:

1. copy the bundled ``sm64_trainer_gfx.dll`` into that Plugin folder under
   the same name;
2. set ``Graphics Dll`` to that filename, remembering the plugin it replaced
   (``previous_graphics_dll`` in the overlay) for undo;
3. turn off frame buffer emulation in the renderer's own ``GLideN64.ini``
   beside it (``core/renderer_settings.py``): the real file, created when
   missing, and an existing per-user VirtualStore copy, never created. With
   it on, ghosts do not draw and the game lags (his rule, 2026-10-30). The
   overlay's ``renderer_settings`` remembers each file's earlier value, which
   undo puts back. A status read only checks it; install and refresh write it.

Older installs put a wrapper under that name, which loaded the renderer
named by ``sm64_trainer_gfx.ini``: ``sm64_trainer_gfx.renderer`` or, before
2026-10-24, ``GLideN64_SM64Trainer.dll``. Such an install reads as stale, not
broken. Install and the automatic refresh copy the one plugin over the
wrapper, then delete those leftovers (`plugin_installation.our_leftovers`):
only files of ours (a renderer by its build id, the ini by its contents),
never while Project64 runs, and never the file ``Graphics Dll`` names, so
Project64 is never left selecting a missing file. Since the plugin keeps the
wrapper's name, Project64's choice stays valid and nobody picks a plugin again.

Undo is steps 2 and 3 in reverse; the plugin file stays (harmless, and a
reinstall is instant). An app update never re-onboards: a trainer refreshes
the installed plugin by itself (``refresh_if_stale`` at boot and every two
seconds) as soon as Project64 is closed, under the consent already given, and
the setup screen stays shut while that is pending (his rule, 2026-09-16: "No
additional onboarding past the original onboarding"). It refreshes only what
an installer like it put there -- the frozen app a frozen app's install, a
source checkout only its own -- and never with an older build
(``core/plugin_ownership.py``).
Both refuse while Project64 runs: PJ64 1.6 rewrites its registry
values on exit, so a change made under it can be undone by its own
shutdown, and a running emulator has the old DLL loaded anyway.

Nothing here knows a game address or a pixel; it moves files and one
registry value, and it remembers what it did in a JSON overlay
(`capture_layer_settings_path()`), resilient like `core/modes.py`: an
absent or corrupt file loads as "never consented" with one warning.

The registry, the process list and the GPU capture observation are
injected (small Protocols below) so every path is testable with fakes.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from sm64_events.core import renderer_settings
from sm64_events.core.renderer_settings import RENDERER_INI
from sm64_events.core.setup_gpu import GpuSetupEvidence
from sm64_events.core.plugin_installation import (
    LEFTOVERS, LEGACY_RENDERER_DLL, LEGACY_RENDERER_FILE, LEGACY_WRAPPER_INI, PLUGIN_DLL,
    DestinationInUse, PluginInstallError, file_operation, in_use, our_leftovers, remove_files,
    sha256 as _sha256, verified_copy,
)
from sm64_events.core.plugin_ownership import FROZEN, build_stamp, not_older, owns

log = logging.getLogger("sm64.capturelayer")

#: the files this trainer puts (or once put) in the Plugin folder; never
#: "the plugin the user had", so undo can never select one of ours
OWN_FILES = frozenset(name.lower() for name in (PLUGIN_DLL, LEGACY_RENDERER_FILE, LEGACY_RENDERER_DLL))
REGISTRY_KEY = r"Software\N64 Emulation\Project64 Version 1.6"
REGISTRY_DLL_SUBKEY = REGISTRY_KEY + r"\Dll"
GRAPHICS_DLL_VALUE = "Graphics Dll"
PLUGIN_DIR_VALUE = "Plugin Directory"
USE_DEFAULT_PLUGIN_DIR_VALUE = "Use Default Plugin Dir"

# `LayerStatus.state`
NOT_INSTALLED = "not_installed"   # nothing of ours in PJ64 (consent not given, or undone)
NEEDS_RESTART = "needs_restart"   # installed and selected; PJ64 has not loaded it yet
ACTIVE = "active"                 # the GPU route's control page is live (core/setup_gpu.py)
REGRESSED = "regressed"           # we installed it, but the registry names another plugin now
UNAVAILABLE = "unavailable"       # no PJ64 folder known / no DLL shipped with this build

_OVERLAY_DEFAULTS = {
    "consented_at": None,
    "pj64_dir": None,
    # What an older install's ini wrapped: before 2026-09-16 the user's own
    # plugin, which undo selects again (`_previous_graphics_dll`).
    "wrapped": None,
    "uninstalled_at": None,
    "installed_sha256": None,
    "installation_receipt": None,
    "previous_graphics_dll": None,
    # Who installed the plugin and what build it was (core/plugin_ownership.py),
    # and the installed plugin's hash that proves nobody replaced it since.
    # `installed_sha256` above is what OLDER trainers' refresh trusts: it is
    # written only for a frozen installer.
    "installed_by": None,
    "installed_build": None,
    "owned_sha256": None,
    # {GLideN64.ini path: {created, previous}} for each file whose frame buffer
    # emulation the trainer turned off: what undo puts back.
    "renderer_settings": None,
}


class LayerRefused(Exception):
    """An install or uninstall that cannot proceed; `str(exc)` is the
    sentence the setup screen shows where the click landed."""


class Registry(Protocol):
    def get(self, subkey: str, name: str) -> str | int | None: ...
    def set(self, subkey: str, name: str, value: str) -> None: ...
    def delete(self, subkey: str, name: str) -> None: ...


class Processes(Protocol):
    def pj64_image_path(self) -> str | None:
        """Full path of the running Project64.exe, or None."""
        ...


@dataclass(frozen=True)
class LayerStatus:
    """The setup screen's view of the install. The `wrapper_*` fields name
    the graphics plugin file, `sm64_trainer_gfx.dll` (their names are part
    of the setup API and of every debug report)."""
    pj64_dir: str | None
    pj64_running: bool
    registry_graphics_dll: str | None
    wrapper_present: bool         # the plugin file sits in the Plugin folder
    wrapper_current: bool         # ...and equals the one shipped with this build
    wrapper_selected: bool        # the registry names it
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
    #: an older install's files of ours still in the Plugin folder (names, in
    #: removal order); any one makes the install stale until refresh or
    #: Install deletes it
    leftovers: list = field(default_factory=list)
    previous_graphics_dll: str | None = None   # what undo selects again
    #: this build refreshes the installed plugin by itself once Project64 is
    #: closed (packaged build, our own earlier install); the UI must not
    #: reopen onboarding for a stale layer it will update anyway
    automatic_update: bool = False
    # Internal typed evidence is excluded from the public setup response.
    gpu_observation: GpuSetupEvidence | None = field(default=None, repr=False)
    #: every GLideN64.ini Project64 may read (the real one, an existing
    #: VirtualStore copy) loads frame buffer emulation as off; with it on,
    #: ghosts do not draw. Stale like an old DLL: refresh or Install fixes it.
    frame_buffer_emulation_off: bool = True

    def as_dict(self) -> dict:
        result = asdict(self)
        result.pop("gpu_observation")
        return {**result, "installation_verified": self.installation_verified}

    @property
    def installation_verified(self) -> bool:
        """Current files/configuration, independent of this checkout's history."""
        return (self.wrapper_present and self.wrapper_current and self.wrapper_selected
                and not self.leftovers and self.frame_buffer_emulation_off)


def virtualstore_copy(local_appdata: str | None, folder: Path, name: str) -> Path | None:
    """The existing per-user VirtualStore copy of `folder`/`name`, or None.

    Windows gives a virtualized legacy program this copy before the real file.
    Only an existing copy counts: never create one or scan a tree."""
    if not local_appdata or not folder.is_absolute() or folder.drive.startswith("\\\\"):
        return None
    appdata = Path(local_appdata)
    if not appdata.is_absolute():
        return None
    folder = Path(os.path.abspath(folder))
    shadow = appdata / "VirtualStore" / folder.relative_to(folder.anchor) / name
    return shadow if shadow.is_file() else None


def _names(value: str | None, name: str) -> bool:
    return (value or "").lower() == name.lower()


def _not_ours(name: str | None) -> str | None:
    """A plugin file name that is not one of the trainer's own, else None."""
    return name if name and name.lower() not in OWN_FILES else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Every native build id ends in its role (-gpu-runtime, -gpu-encoder, and
# -renderer for an older install's renderer). The suffix is required: the
# renderer's code carries hundreds of 64-digit decimal tables that a bare hex
# pattern also matches.
_BUILD_ID = re.compile(rb"[0-9a-f]{64}-[a-z][a-z-]*")


_identity_cache: dict = {}


def _build_identity(path: Path) -> bytes | None:
    """A native file's embedded build id: a digest of the sources it was
    compiled from, plus its role suffix. None for a file without one.
    Cached by (path, size, mtime): the refresh loop and the setup screen's
    polling would otherwise scan the 12 MB plugin every two seconds."""
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _identity_cache:
        ids = set(_BUILD_ID.findall(path.read_bytes()))
        _identity_cache[key] = ids.pop() if len(ids) == 1 else None
    return _identity_cache[key]


def _files_match(path_a: Path, path_b: Path) -> bool:
    """Same graphics plugin. Two plugins built from the same native sources
    carry the same embedded build id, so a rebuild of an unchanged tree must
    not tell him his installed candidate "differs from this build" (his
    report, 2026-09-15). Files without an id fall back to the byte hash; a
    stale-but-same-size DLL still never reads as current."""
    try:
        own, other = _build_identity(path_a), _build_identity(path_b)
        if own is not None and other is not None:
            return own == other
        return _sha256(path_a) == _sha256(path_b)
    except OSError:
        return False


def _problems(state: str, pj64_dir, running: bool, plugin_selected: bool, registry_graphics_dll,
              layer_current: bool, automatic: bool, fbe_off: bool) -> list[str]:
    """Fixable sentences, in order, for the setup screen."""
    problems: list[str] = []
    if state == NOT_INSTALLED and pj64_dir is None:
        problems.append("start Project64 once so the trainer can find it")
    elif state == NOT_INSTALLED and running:
        problems.append("Project64 is running -- close it before installing")
    elif state == REGRESSED and pj64_dir is None:
        problems.append("start Project64 once so the trainer can find it again")
    elif state == REGRESSED and not plugin_selected:
        problems.append(
            f"another graphics plugin is selected ({registry_graphics_dll}); "
            "re-install to restore the capture layer")
    elif state == REGRESSED:
        problems.append("the capture layer's plugin is missing from Project64's "
                        "Plugin folder; re-install to restore it")
    elif state == NEEDS_RESTART:
        problems.append(("restart" if running else "start")
                        + " Project64 to load the capture layer")
    if state in (ACTIVE, NEEDS_RESTART) and not layer_current:
        problems.append("the installed capture layer differs from this build; "
                        + _update_guidance(running, automatic))
    if state in (ACTIVE, NEEDS_RESTART) and not fbe_off:
        problems.append(_frame_buffer_emulation_problem(running, automatic))
    return problems


def _frame_buffer_emulation_problem(running: bool, automatic: bool) -> str:
    """Whole sentences: what is wrong, what it does, what fixes it."""
    if automatic:
        fix = ("Close Project64, and the trainer turns it off by itself." if running
               else "The trainer turns it off by itself in a moment.")
    else:
        fix = ("Close Project64, then choose Install to turn it off." if running
               else "Choose Install to turn it off.")
    return ("Frame buffer emulation is on in Project64's graphics settings (GLideN64.ini). "
            "This setting hides ghosts and makes the game lag. " + fix)


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
                 gpu_observation: Callable[[], GpuSetupEvidence | None] | None = None,
                 auto_refresh: bool = False, installer: dict | None = None):
        self._registry = registry
        self._processes = processes
        self._settings_path = settings_path
        self._dll_source = dll_source
        # Resolve in the original process, never under a file helper's alternate
        # administrator account. That helper owns only the protected plugin files.
        self._local_appdata = os.environ.get("LOCALAPPDATA")
        self._gpu_observation = gpu_observation or (lambda: None)
        # A worktree's bundled bytes may be older than a manually installed
        # candidate. Hash inequality proves difference, never update ordering:
        # refresh needs this installer's own install and a newer link time.
        self.auto_refresh = auto_refresh
        self._installer = installer
        # Setup's PJ64 Fixups (core/fixups.py), hung here by main.py; None in
        # tests and tools, which then install the graphics plugin alone.
        self.fixups = None
        # Install, the packaged refresh tick and Remove all copy the same
        # files and rewrite the same overlay; one at a time, or one side's
        # rollback snapshot can undo the other's finished copy.
        self._install_lock = threading.RLock()
        self._deferred = False  # a refresh is waiting on a held DLL (logged once)
        self._refresh_blocked = None  # (bundle/target identity, reason); user gesture retries
        self._last_status = None
        self._status_observed = None
        self._refresh_stop = threading.Event()
        self._refresh_thread = None

    @property
    def registry(self) -> Registry:
        """The same per-user Project64 settings this layer reads; the setup
        check (`core/setup_profile.py`) only reads them."""
        return self._registry

    @property
    def local_appdata(self) -> str | None:
        return self._local_appdata

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

    @contextmanager
    def _registry_install_transaction(self):
        """Keep HKCU in the same transaction as the helper's final acknowledgment."""
        previous = self._registry_graphics_dll()
        attempted = False

        def select_plugin():
            nonlocal attempted
            attempted = True
            self._set_registry_graphics_dll(PLUGIN_DLL)

        try:
            yield select_plugin
        except BaseException:
            if attempted:
                try:
                    if previous is None:
                        self._registry.delete(REGISTRY_DLL_SUBKEY, GRAPHICS_DLL_VALUE)
                    else:
                        self._set_registry_graphics_dll(previous)
                except Exception:
                    log.exception("could not restore Project64's previous graphics selection")
            raise

    @staticmethod
    def _read_wrapped_name(ini_path: Path) -> str | None:
        """What an older install's ini told its wrapper to load, read as the
        wrapper did (gfxwrap.c::read_ini: leading indentation accepted, the
        last wrapped= line wins). Before 2026-09-16 that was the user's own
        plugin, which undo must select again."""
        try:
            text = ini_path.read_text()
        except (OSError, UnicodeError):
            return None
        wrapped = None
        for line in text.splitlines():
            line = line.rstrip(" \r\n").lstrip(" \t")
            if line.startswith("wrapped="):
                wrapped = line[len("wrapped="):]
        return wrapped

    def _leftovers(self, plugin_dir: Path, *, keep_selected: bool) -> list[Path]:
        """Our older files in `plugin_dir`; with `keep_selected`, without the
        one Project64's `Graphics Dll` names (never deleted)."""
        keep = self._registry_graphics_dll() if keep_selected else None
        return our_leftovers(plugin_dir, keep=keep, identity=_build_identity)

    # -- the renderer's own settings (core/renderer_settings.py) ---------

    def _shadow_ini(self, plugin_dir: Path, name: str) -> Path | None:
        """Only the existing per-user counterpart of `name` in the Plugin folder.

        Windows searches this copy first for a virtualized legacy application.
        A manifested/elevated Project64 may instead read the physical file, so
        both copies must agree. Never create a VirtualStore file or scan a tree.
        """
        return virtualstore_copy(self._local_appdata, plugin_dir, name)

    def _renderer_settings_files(self, plugin_dir: Path) -> list[Path]:
        """Every GLideN64.ini Project64 may read: the real file, which install
        and refresh create when missing, and an existing per-user VirtualStore
        copy, which they fix but never create."""
        shadow = self._shadow_ini(plugin_dir, RENDERER_INI)
        return [plugin_dir / RENDERER_INI, *([shadow] if shadow is not None else [])]

    def _frame_buffer_emulation_off(self, plugin_dir: Path) -> bool:
        """Read only: a status poll never writes a file Project64 may be using."""
        try:
            return all(renderer_settings.frame_buffer_emulation_off(renderer_settings.read(path))
                       for path in self._renderer_settings_files(plugin_dir))
        except OSError:
            return False  # an unreadable file is no proof that the setting is off

    @staticmethod
    def _remember_renderer_settings(overlay: dict, files: list[Path]) -> None:
        """What undo puts back: each file's value before the trainer first
        changed it. A later install or refresh keeps that first record."""
        records = overlay.get("renderer_settings")
        records = dict(records) if isinstance(records, dict) else {}
        for path in files:
            data = renderer_settings.read(path)
            if str(path) not in records and not renderer_settings.frame_buffer_emulation_off(data):
                records[str(path)] = {"created": data is None,
                                      "previous": renderer_settings.previous_value(data)}
        overlay["renderer_settings"] = records or None

    @staticmethod
    def _turn_off_frame_buffer_emulation(files: list[Path]) -> None:
        for path in files:
            with file_operation("turn off frame buffer emulation", path):
                renderer_settings.turn_off(path)

    @staticmethod
    def _restore_renderer_settings(overlay: dict) -> None:
        """Undo's half: put back each value the trainer changed. A file this
        user may not write (a protected Plugin folder; undo never asks for
        administrator permission) keeps its record and a warning in the log,
        and the plugin selection is restored regardless."""
        records = overlay.get("renderer_settings")
        kept = {}
        for name, record in (records.items() if isinstance(records, dict) else ()):
            try:
                renderer_settings.restore(Path(name), record)
            except OSError:
                log.warning("could not restore frame buffer emulation in %s", name, exc_info=True)
                kept[name] = record
        overlay["renderer_settings"] = kept or None

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

    def _previous_graphics_dll(self, overlay: dict, ini_wrapped: str | None) -> str | None:
        """The plugin undo selects: what the registry names now if that is
        not ours, else what we remembered, else what an older install's ini
        wrapped (that scheme wrapped the user's own plugin)."""
        return (_not_ours(self._registry_graphics_dll())
                or _not_ours(overlay.get("previous_graphics_dll"))
                or _not_ours(ini_wrapped)
                or _not_ours(overlay.get("wrapped")))

    def _legacy_wrapped(self, plugin_dir: Path) -> str | None:
        return self._read_wrapped_name(plugin_dir / LEGACY_WRAPPER_INI)

    def status(self) -> LayerStatus:
        source = self._source_dll()
        pj64_dir = self.locate()
        running = self._processes.pj64_image_path() is not None
        overlay = self._load_overlay()
        automatic = self._automatic_refresh(pj64_dir)

        registry_graphics_dll = self._registry_graphics_dll()
        selected = _names(registry_graphics_dll, PLUGIN_DLL)
        plugin_dir = self._plugin_dir(pj64_dir) if pj64_dir is not None else None
        if plugin_dir is None:
            present = current = False
            leftovers, fbe_off, legacy_wrapped = [], False, None
        else:
            dll_path = plugin_dir / PLUGIN_DLL
            present = dll_path.is_file()
            current = present and source is not None and _files_match(dll_path, source)
            leftovers = self._leftovers(plugin_dir, keep_selected=False)
            fbe_off = self._frame_buffer_emulation_off(plugin_dir)
            legacy_wrapped = self._legacy_wrapped(plugin_dir)
        configured = present and selected
        # An older install's leftovers are stale the same way an older DLL
        # is: the same update steps remove them.
        layer_current = current and not leftovers

        # The only liveness evidence is the GPU route's control page and its
        # accepted-picture count (core/setup_gpu.py); no observation means the
        # plugin has not published a page this server can see.
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
        if source is None:
            state = UNAVAILABLE
        elif overlay.get("consented_at") is None and not configured:
            state = NOT_INSTALLED
        elif pj64_dir is None or not configured:
            state = REGRESSED
        elif layer_alive:
            state = ACTIVE
        else:
            state = NEEDS_RESTART

        problems = _problems(state, pj64_dir, running, selected, registry_graphics_dll,
                             layer_current, automatic, fbe_off)
        # A frame buffer emulation setting turned on is stale the same way an
        # older DLL is: the same update steps lead to the fix.
        steps = self._steps(state, running,
                            source is not None and present and not (layer_current and fbe_off),
                            layer_alive, automatic)

        status = LayerStatus(
            pj64_dir=str(pj64_dir) if pj64_dir is not None else None,
            pj64_running=running,
            registry_graphics_dll=registry_graphics_dll,
            wrapper_present=present,
            wrapper_current=current,
            wrapper_selected=selected,
            layer_alive=layer_alive,
            pictures_via=pictures_via,
            steps=steps,
            consented_at=overlay.get("consented_at"),
            problems=problems,
            state=state,
            plugin_pid=plugin_pid,
            pictures_flowing=activity.get("pictures", False),
            plugin_dir=str(plugin_dir) if plugin_dir is not None else None,
            leftovers=[path.name for path in leftovers],
            previous_graphics_dll=self._previous_graphics_dll(overlay, legacy_wrapped),
            automatic_update=automatic,
            gpu_observation=gpu,
            frame_buffer_emulation_off=fbe_off,
        )
        self._last_status, self._status_observed = status, time.time()
        return status

    def diagnostic_status(self) -> dict | None:
        """Last observed setup state only; never discover, install or refresh."""
        status = self._last_status
        if status is None:
            return None
        return {**status.as_dict(), "observed_unix_s": self._status_observed,
                "refresh_error": self._refresh_blocked[1] if self._refresh_blocked else None}

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
        try:
            return self._install_files(pj64_dir)
        except DestinationInUse as exc:
            # Project64 started after the check above and loaded the plugin.
            raise LayerRefused("close Project64, then install") from exc
        except PluginInstallError as exc:
            plugin = self._plugin_dir(pj64_dir)
            # Only a destination write denial earns a UAC retry. Per-user
            # settings, source failures and background refresh never elevate.
            eligible = {plugin, *(plugin / name for name in (PLUGIN_DLL, RENDERER_INI, *LEFTOVERS))}
            if exc.reason != "access_denied" or Path(exc.filename) not in eligible:
                raise
            if in_use(Path(exc.filename)):
                raise LayerRefused("close Project64, then install") from exc
            from sm64_events.core.plugin_elevation import file_failure
            with file_failure("install Practice Replay as administrator", plugin):
                return self._install_files(pj64_dir, elevated=True)

    def _install_files(self, pj64_dir: Path, *, elevated=False) -> LayerStatus:
        source = self._source_dll()
        if source is None:
            raise LayerRefused("this build carries no capture layer")
        validate = getattr(self._processes, "check_folder", None)
        if validate is not None:
            target = validate(str(pj64_dir))
            if target["state"] != "ready":
                raise LayerRefused(target["message"])

        plugin_dir = self._plugin_dir(pj64_dir)
        dll_path = plugin_dir / PLUGIN_DLL
        # The per-user GLideN64.ini belongs to this original user; the real
        # one is a plugin file (elevated or not).
        settings_shadow = self._shadow_ini(plugin_dir, RENDERER_INI)
        per_user = [settings_shadow] if settings_shadow is not None else []

        overlay = self._load_overlay()
        # The plugin undo returns to. A re-install finds OUR plugin in the
        # registry, so the answer is what we remembered (or what an older
        # install's ini wrapped), never one of our own files.
        overlay["previous_graphics_dll"] = self._previous_graphics_dll(
            overlay, self._legacy_wrapped(plugin_dir))
        overlay["consented_at"] = _now_iso()
        overlay["pj64_dir"] = str(pj64_dir)
        overlay["wrapped"] = None
        overlay["uninstalled_at"] = None
        # Before any write: undo needs each file's value as the user had it.
        self._remember_renderer_settings(overlay, self._renderer_settings_files(plugin_dir))
        from sm64_events.core.setup_files import restore_on_failure
        with self._registry_install_transaction() as select_plugin, restore_on_failure([
                self._settings_path, *per_user]), self._install_plugin_files(
                pj64_dir, source, elevated=elevated) as receipts:
            receipt = receipts["installation_receipt"]
            overlay["installation_receipt"] = receipt
            overlay["installed_by"] = self._installer
            self._record_plugin(overlay, receipt["after"]["sha256"], dll_path)
            self._turn_off_frame_buffer_emulation(per_user)
            self._save_overlay(overlay)
            # HKCU always belongs to this original user, even if UAC used
            # another administrator's credentials for the file transaction.
            select_plugin()

        self._refresh_blocked = None
        return self.status()

    @contextmanager
    def _install_plugin_files(self, pj64_dir, source, *, elevated):
        plugin = self._plugin_dir(pj64_dir)
        if elevated:
            from sm64_events.core.plugin_elevation import elevated_install
            # One permission prompt covers the PJ64 Fixups too. Without a kept
            # download (offline) the plugin installs alone and setup says so.
            fixups_dll = self._fixups_dll()
            extra = {"fixups_dll": fixups_dll} if fixups_dll is not None else {}
            with elevated_install(pj64_dir, plugin, source, **extra) as receipts:
                yield receipts
            return
        with file_operation("create plugin folder", plugin):
            plugin.mkdir(parents=True, exist_ok=True)
        dll, settings = plugin / PLUGIN_DLL, plugin / RENDERER_INI
        with verified_copy(source, dll, reason="explicit_install", rollback=(settings,)) as receipt:
            self._turn_off_frame_buffer_emulation([settings])
            yield {"installation_receipt": receipt}
            # Last, once the caller has selected the plugin: a refused
            # delete puts back what it removed, then rolls back every file
            # and that selection.
            remove_files(self._leftovers(plugin, keep_selected=True))

    def _fixups_dll(self) -> Path | None:
        if self.fixups is None:
            return None
        from sm64_events.core.fixups import FixupError
        try:
            return self.fixups.cached()
        except FixupError:
            return None

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

    def _record_plugin(self, overlay: dict, sha: str, dll_path: Path) -> None:
        """The installed plugin's hash and the installed build. Older
        trainers refresh whatever `installed_sha256` names, so only a frozen
        installer writes it; they then leave a checkout's install alone."""
        frozen = isinstance(self._installer, dict) and self._installer.get("kind") == FROZEN
        overlay["owned_sha256"] = sha
        overlay["installed_sha256"] = sha if frozen else None
        overlay["installed_build"] = build_stamp(dll_path, _build_identity)

    def _automatic_refresh(self, pj64_dir: Path | None) -> bool:
        """This trainer may update the installed plugin by itself: consent, an
        install by an installer like this one (the frozen app's by a frozen
        app, a checkout's only by that checkout), the file unchanged since
        (a manual or external replacement transfers nothing), and a bundle no
        older than what is installed (never a downgrade). An older install's
        wrapper counts as the installed plugin: replacing it is the move."""
        if not self.auto_refresh or pj64_dir is None:
            return False
        overlay = self._load_overlay()
        expected = overlay.get("owned_sha256")
        if not expected or not overlay.get("consented_at"):
            return False
        if not owns(overlay.get("installed_by"), self._installer):
            return False
        plugin_dir = self._plugin_dir(pj64_dir)
        source = self._source_dll()
        if source is None:
            return False
        try:
            key = self._refresh_key(pj64_dir, source)
            if self._refresh_blocked is not None:
                if self._refresh_blocked[0] == key:
                    return False
                self._refresh_blocked = None  # New target/bundle earns a fresh attempt.
            if _sha256(plugin_dir / PLUGIN_DLL) != expected:
                return False
        except OSError:
            return False
        return not_older(source, plugin_dir / PLUGIN_DLL)

    def _refresh_key(self, pj64_dir, source):
        stat = source.stat()
        return (str(pj64_dir.resolve()), str(self._plugin_dir(pj64_dir).resolve()),
                (str(source.resolve()), stat.st_size, stat.st_mtime_ns))

    def refresh_if_stale(self) -> bool:
        with self._install_lock:
            return self._refresh_if_stale()

    def _refresh_if_stale(self) -> bool:
        """Copy this build's plugin over a differing one and delete an older
        install's leftovers when `_automatic_refresh` allows it: this
        installer's own earlier install, never a downgrade, a manual or
        another checkout's candidate left alone. Project64 must be closed.
        True when something changed; False when nothing was installed, the
        install is current, or PJ64 holds a file open. Runs at boot under the
        consent already given -- the one update path for a plugin fix, so a
        user never re-consents to what they already chose."""
        if not self.auto_refresh:
            return False
        source = self._source_dll()
        if source is None:
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
        dll_path = plugin_dir / PLUGIN_DLL
        if not dll_path.exists():
            return False
        # A refresh never changes Project64's selection, so a leftover it
        # names directly stays until Install selects the plugin.
        leftovers = self._leftovers(plugin_dir, keep_selected=True)
        stale = {"plugin": not _files_match(dll_path, source), "leftovers": bool(leftovers),
                 "renderer_settings": not self._frame_buffer_emulation_off(plugin_dir)}
        if not any(stale.values()):
            self._deferred = False
            return False
        # A running Project64 maps the DLLs, and the process list can miss it
        # for an instant: at his server start on 2026-09-28 Project64 loaded
        # its plugins in the moment after the check above found none. A held
        # file is the same "not now" as a running Project64.
        held = [path for path in ([dll_path] if stale["plugin"] else []) + leftovers if in_use(path)]
        if held:
            return self._defer(held[0])
        overlay = self._load_overlay()
        if leftovers:
            # An older install wrapped the user's own plugin: that name is
            # what undo must return to once the ini is gone.
            overlay["previous_graphics_dll"] = self._previous_graphics_dll(
                overlay, self._legacy_wrapped(plugin_dir))
            overlay["wrapped"] = None
        # Only a plugin that differs is copied; the copy is verified and
        # rolls the whole refresh back on failure.
        refresh_key = self._refresh_key(pj64_dir, source)
        try:
            self._copy_stale(overlay, source, plugin_dir, stale["plugin"], leftovers)
        except DestinationInUse as exc:
            return self._defer(Path(exc.filename))
        except OSError as exc:
            # Background refresh never opens UAC. Stop promising an automatic
            # update after a write failure; the existing Install gesture owns
            # a normal attempt and its permission-specific elevated retry.
            self._refresh_blocked = (refresh_key, str(exc)[:512])
            raise
        self._deferred = False
        return True

    def _defer(self, path: Path) -> bool:
        """The refresh waits for Project64 to let go: one INFO line per wait,
        not one per loop turn, and no rollback of files never written."""
        if not self._deferred:
            log.info("capture layer refresh waits: %s is in use (Project64 is running); "
                     "it retries once Project64 closes", path.name)
        self._deferred = True
        return False

    def _copy_stale(self, overlay, source, plugin_dir: Path, copy_plugin: bool, leftovers: list[Path]):
        dll_path = plugin_dir / PLUGIN_DLL
        settings_files = self._renderer_settings_files(plugin_dir)
        from sm64_events.core.setup_files import restore_on_failure
        with restore_on_failure([self._settings_path, *settings_files]), ExitStack() as copies:
            if copy_plugin:
                receipt = copies.enter_context(verified_copy(
                    source, dll_path, reason="automatic_refresh", rollback=(self._settings_path,)))
                overlay["installation_receipt"] = receipt
            self._record_plugin(overlay, _sha256(dll_path), dll_path)
            # A write this user may not make raises; refresh never elevates,
            # so status then offers Install, which can.
            self._remember_renderer_settings(overlay, settings_files)
            self._turn_off_frame_buffer_emulation(settings_files)
            self._save_overlay(overlay)
            # Last, once the plugin is in place: a refused delete puts back
            # what it removed and rolls back everything above.
            remove_files(leftovers)

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
                    log.info("capture layer refreshed to this build's plugin "
                             "(Project64 was closed)")
            except Exception:
                if log is not None:
                    log.exception("capture layer refresh failed")

    def uninstall(self) -> LayerStatus:
        """Select the previous graphics plugin again. When none is known
        (the registry named one of our own files when we installed), the
        plugin stays selected: it still draws the game, and the setup
        page says so instead of promising a restore."""
        with self._install_lock:
            return self._uninstall()

    def _uninstall(self) -> LayerStatus:
        if self._processes.pj64_image_path() is not None:
            raise LayerRefused("close Project64, then uninstall")

        pj64_dir = self.locate()
        overlay = self._load_overlay()
        legacy_wrapped = self._legacy_wrapped(self._plugin_dir(pj64_dir)) if pj64_dir is not None else None
        previous = self._previous_graphics_dll(overlay, legacy_wrapped)
        if previous is not None:
            self._set_registry_graphics_dll(previous)
        # The user's own GLideN64 in the same folder reads the same file.
        self._restore_renderer_settings(overlay)

        overlay["uninstalled_at"] = _now_iso()
        overlay["consented_at"] = None
        self._save_overlay(overlay)
        self._refresh_blocked = None

        return self.status()


# The real Registry/Processes adapters (WinRegistry, WinProcesses) moved to
# capturelayer_win.py once this module passed its size budget -- re-exported
# here so `from sm64_events.core.capturelayer import WinRegistry` still
# works for a future wiring call site.
from sm64_events.core.capturelayer_win import WinProcesses, WinRegistry  # noqa: E402,F401
