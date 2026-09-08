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
from sm64_events.core.modes import (ModeConfig, TrackerMode,
                                    load_mode_config, save_mode_config)
from sm64_events.core.onboarding import SetupRecord, identify_rom, readiness
from sm64_events.core.paths import mode_settings_path

log = logging.getLogger("sm64.setup")


class PlatformBody(BaseModel):
    platform: str


class ConsentBody(BaseModel):
    consent: bool


def setup_json(mode_cfg: ModeConfig, capture_layer: CaptureLayer,
               observer=None, record=None) -> dict:
    layer = capture_layer.status()
    observed = observer(layer) if observer else {
        "target": {"state": "unknown", "message": "Waiting for the setup connection."},
        "rom": identify_rom(None), "checks": {}}
    return {"platform": mode_cfg.mode.value,
            TrackerMode.EMU.value: {**layer.as_dict(), **observed,
                    "verification": readiness(layer, observed)},
            "onboarding": record.read() if record else {},
            TrackerMode.N64.value: {"available": False}}


def create_setup_router(capture_layer: CaptureLayer, mode_path: Path | None = None,
                        observer=None) -> APIRouter:
    router = APIRouter(prefix="/api")
    record = SetupRecord((mode_path or mode_settings_path()).with_name("onboarding.json"))

    def payload():
        return setup_json(load_mode_config(mode_path), capture_layer, observer, record)

    def save_platform(platform):
        current = load_mode_config(mode_path)
        save_mode_config(ModeConfig(mode=platform, version=current.version), mode_path)

    @router.get("/setup")
    def get_setup():
        return payload()

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
            capture_layer.install(body.consent)
            record.write(started=True, completed_at=None)
        except LayerRefused as exc:
            return JSONResponse(status_code=409, content={"detail": str(exc)})
        except OSError:
            log.exception("setup installation failed")
            return JSONResponse(status_code=409, content={
                "detail": "Couldn't write to the Project64 folder. Check its permissions, then try again."})
        return payload()

    @router.delete("/setup/capture-layer")
    def uninstall_capture_layer():
        try:
            capture_layer.uninstall()
            record.write(started=True, completed_at=None)
        except LayerRefused as exc:
            return JSONResponse(status_code=409, content={"detail": str(exc)})
        except OSError:
            log.exception("setup removal failed")
            return JSONResponse(status_code=409, content={
                "detail": "Couldn't restore your graphics setting. Close Project64 and try again."})
        return payload()

    return router
