"""Installation, observed readiness and completion for the setup wizard.

The observer is injected by main; fixtures never inspect the live environment.
Platform selection stays local in the wizard until completion. The existing
platform PUT remains available and preserves the user's grading preference.
"""
import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sm64_events.core.capturelayer import CaptureLayer, LayerRefused
from sm64_events.core import error_reporting
from sm64_events.core.modes import (ModeConfig, TrackerMode,
                                    load_mode_config, save_mode_config)
from sm64_events.core.onboarding import SetupRecord, identify_rom, readiness
from sm64_events.core.paths import mode_settings_path

log = logging.getLogger("sm64.setup")


#: the setup check's row for the PJ64 Fixups (data/setup_baseline.json)
FIXUPS_ITEM = "rsp_plugin"


class PlatformBody(BaseModel):
    platform: str


class ConsentBody(BaseModel):
    consent: bool


class RiskBody(BaseModel):
    items: list[str]


def _install_fixups(fixups, layer) -> None:
    """Add the PJ64 Fixups to this Project64. A failure never undoes or blocks
    Practice Replay: `fixups.status()` keeps its sentence for the wizard, and
    the setup check then shows the difference behind the warning."""
    from sm64_events.core.fixups import FixupError
    plugin_dir = layer.as_dict().get("plugin_dir")
    if fixups is None or not plugin_dir:
        return
    try:
        fixups.install(Path(plugin_dir))
    except FixupError as error:
        log.warning("PJ64 Fixups not installed: %s", error)


def _failed_operation(error: OSError, operation: str) -> JSONResponse:
    # Windows cancellation is an intentional choice, not an application fault.
    if getattr(error, "winerror", None) == 1223:
        return JSONResponse(status_code=409, content={
            "detail": "Installation cancelled at the Windows permission prompt. You can try again."})
    error_reporting.capture_exception(error, context={
        "component": "practice_replay_install", "operation": getattr(error, "operation", operation),
        "winerror": getattr(error, "winerror", None), "path": error.filename,
        "file": Path(error.filename).name if error.filename else "unknown"})
    log.warning("Practice Replay %s failed: %s", operation, error)
    detail = str(error)
    return JSONResponse(status_code=409, content={
        "detail": f"Practice Replay {operation} failed: {detail}"})


def _observed(capture_layer, observer):
    layer = capture_layer.status()
    observed = observer(layer) if observer else {
        "target": {"state": "unknown", "message": "Waiting for the setup connection."},
        "rom": identify_rom(None), "checks": {}}
    return layer, observed


def setup_check(layer, observed, profile) -> dict | None:
    """The setup check (`core/setup_profile.py`) for this observation."""
    if profile is None:
        return None
    lab_roms = observed.get("lab_roms")    # the Ghost Lab's ROM copies, when setup watches a ROM store
    if lab_roms is None:
        return profile.compare(layer.as_dict(), observed.get("rom"))
    return profile.compare(layer.as_dict(), observed.get("rom"), lab_roms)


def setup_json(mode_cfg: ModeConfig, capture_layer: CaptureLayer,
               observer=None, record=None, profile=None, fixups=None) -> dict:
    layer, observed = _observed(capture_layer, observer)
    onboarding = record.read() if record else {}
    note = getattr(observer, "note_setup_state", None)
    if note is not None:
        note(onboarding)
    check = setup_check(layer, observed, profile)
    accepted = (onboarding.get("baseline_risk") or {}).get("items", ())
    fixups_needed = FIXUPS_ITEM in (check or {}).get("gate_differs", ()) and FIXUPS_ITEM not in accepted
    return {"platform": mode_cfg.mode.value,
            TrackerMode.EMU.value: {**layer.as_dict(), **observed,
                    "verification": readiness(layer, observed, fixups_needed=fixups_needed),
                    "setup_check": check,
                    "fixups": fixups.status() if fixups is not None else None},
            "onboarding": onboarding,
            TrackerMode.N64.value: {"available": False}}


def _mount_setup_check(router, capture_layer, observer, profile) -> None:
    @router.get("/setup/check")
    def get_setup_check():
        """Only the setup check: the practice card's ghost notice asks for it."""
        if profile is None:
            return JSONResponse(status_code=404, content={"detail": "The setup check is unavailable."})
        return setup_check(*_observed(capture_layer, observer), profile)


def _mount_baseline_gate(router, capture_layer, fixups, profile, record, payload) -> None:
    """The gate's two writes: add the PJ64 Fixups, or accept the warning."""
    @router.post("/setup/fixups")
    def install_fixups():
        """Only the PJ64 Fixups, for a Project64 that already has Practice Replay."""
        layer = capture_layer.status()
        if fixups is None or not layer.as_dict().get("plugin_dir"):
            return JSONResponse(status_code=409, content={
                "detail": "Open Project64 once so we can find its folder."})
        if layer.pj64_running:
            return JSONResponse(status_code=409, content={
                "detail": "Close Project64, then install the PJ64 Fixups."})
        _install_fixups(fixups, layer)
        if fixups.status().get("reason") == "access_denied":
            # Windows protects this Plugin folder. Install asks for administrator
            # permission once and its helper copies the fixups with the plugin.
            try:
                _install_fixups(fixups, capture_layer.install(True))
            except LayerRefused as exc:
                return JSONResponse(status_code=409, content={"detail": str(exc)})
            except OSError as failure:
                return _failed_operation(failure, "installation")
        error = fixups.status()["error"]
        return JSONResponse(status_code=409, content={"detail": error}) if error else payload()

    @router.post("/setup/baseline-risk")
    def accept_baseline_risk(body: RiskBody):
        """The user read the warning and continues on an untested setup."""
        from datetime import datetime, timezone
        from sm64_events.core.setup_profile import load_baseline
        known = set((getattr(profile, "baseline", None) or load_baseline()).get("gate", ()))
        items = sorted(set(body.items) & known)
        record.write(baseline_risk={"items": items, "accepted_at": datetime.now(timezone.utc).isoformat()})
        return payload()


def create_setup_router(capture_layer: CaptureLayer, mode_path: Path | None = None,
                        observer=None, profile=None, fixups=None) -> APIRouter:
    router = APIRouter(prefix="/api")
    fixups = fixups if fixups is not None else getattr(capture_layer, "fixups", None)
    record = SetupRecord((mode_path or mode_settings_path()).with_name("onboarding.json"))

    def payload():
        return setup_json(load_mode_config(mode_path), capture_layer, observer, record, profile, fixups)

    def save_platform(platform):
        current = load_mode_config(mode_path)
        save_mode_config(ModeConfig(mode=platform, version=current.version), mode_path)

    @router.get("/setup")
    def get_setup():
        return payload()

    _mount_setup_check(router, capture_layer, observer, profile)

    @router.put("/setup/platform")
    def put_platform(body: PlatformBody):
        try:
            platform = TrackerMode(body.platform.strip().lower())
        except ValueError:
            return JSONResponse(status_code=422, content={"detail": f"Unknown platform: {body.platform}."})
        save_platform(platform)
        return payload()

    @router.post("/setup/complete")
    def complete_setup(body: PlatformBody):
        try:
            platform = TrackerMode(body.platform.strip().lower())
        except ValueError:
            return JSONResponse(status_code=422, content={"detail": "Unknown platform."})
        verification = payload()[TrackerMode.EMU.value]["verification"]
        if platform is TrackerMode.EMU and not verification["ready"]:
            return JSONResponse(status_code=409, content={"detail": verification["message"]})
        save_platform(platform)
        record.complete(platform.value, platform is TrackerMode.N64 or verification["limited"])
        return payload()

    @router.post("/setup/capture-layer")
    def install_capture_layer(body: ConsentBody):
        try:
            layer = capture_layer.install(body.consent)
            record.write(started=True, completed_at=None)
            _install_fixups(fixups, layer)
        except LayerRefused as exc:
            return JSONResponse(status_code=409, content={"detail": str(exc)})
        except OSError as error:
            return _failed_operation(error, "installation")
        return payload()

    _mount_baseline_gate(router, capture_layer, fixups, profile, record, payload)

    @router.delete("/setup/capture-layer")
    def uninstall_capture_layer():
        try:
            capture_layer.uninstall()
            record.write(started=True, completed_at=None)
        except LayerRefused as exc:
            return JSONResponse(status_code=409, content={"detail": str(exc)})
        except OSError as error:
            return _failed_operation(error, "removal")
        return payload()

    return router
