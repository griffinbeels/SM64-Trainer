"""GET|PUT|POST|DELETE /api/setup -- the first-run setup screen's API.

The setup screen (setupmodal.js) is a checklist over TWO things this project
already persists separately: which platform the player practices on
(core/modes.py, EMU/N64 -- the same record header.js's Game version dropdown
reads) and the CAPTURE LAYER's own state (core/capturelayer.py -- whether the
frame-exact wrapper plugin is installed into Project64). This router is a thin
skin over both: it never derives anything itself, it asks
`capture_layer.status()` and the mode file and hands back one payload the
modal renders directly.

`capture_layer` is injected exactly like `inputs`/`compare` in server/app.py --
`create_app(..., capture_layer=None)` mounts this router only when one is
given, so a broadcast-only second instance (no capture layer object at all)
carries no dead routes. A `LayerRefused` from `install`/`uninstall` becomes a
409 whose body IS the sentence the checklist shows where the click landed --
never a bare status code, the same contract `inputs_api.py` follows for its
own domain exceptions.

The platform PUT touches only `ModeConfig.mode`; the stored `version` (JP/US/
Auto-detect, header.js's own Game version control) is read back unchanged.
Nothing here re-grades or broadcasts -- unlike the game version, a practice
platform choice does not change which standards apply."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sm64_events.core.capturelayer import CaptureLayer, LayerRefused
from sm64_events.core.modes import (ModeConfig, TrackerMode,
                                    load_mode_config, save_mode_config)


class PlatformBody(BaseModel):
    platform: str


class ConsentBody(BaseModel):
    consent: bool


def setup_json(mode_cfg: ModeConfig, capture_layer: CaptureLayer) -> dict:
    return {
        "platform": mode_cfg.mode.value,
        "emu": capture_layer.status().as_dict(),
        # The N64 front-end (console-support's own vision pipeline) has
        # nothing to report yet -- this shape is what SETUP_PANES.n64 checks
        # to render its one placeholder line rather than a picker with
        # nothing behind it.
        "n64": {"available": False},
    }


def create_setup_router(capture_layer: CaptureLayer,
                        mode_path: Path | None = None) -> APIRouter:
    """`mode_path` overrides where the platform setting persists (tests);
    production takes core.paths.mode_settings_path() via load/save's own
    default, exactly like server/mode_api.py."""
    router = APIRouter(prefix="/api")

    @router.get("/setup")
    def get_setup():
        return setup_json(load_mode_config(mode_path), capture_layer)

    @router.put("/setup/platform")
    async def put_platform(body: PlatformBody):
        try:
            mode = TrackerMode(body.platform.strip().lower())
        except ValueError:
            return JSONResponse(status_code=422, content={
                "detail": f"unknown platform {body.platform!r}"})
        current = load_mode_config(mode_path)
        cfg = ModeConfig(mode=mode, version=current.version)
        save_mode_config(cfg, mode_path)
        return setup_json(cfg, capture_layer)

    @router.post("/setup/capture-layer")
    async def install_capture_layer(body: ConsentBody):
        try:
            capture_layer.install(body.consent)
        except LayerRefused as exc:
            return JSONResponse(status_code=409, content={"detail": str(exc)})
        return setup_json(load_mode_config(mode_path), capture_layer)

    @router.delete("/setup/capture-layer")
    async def uninstall_capture_layer():
        try:
            capture_layer.uninstall()
        except LayerRefused as exc:
            return JSONResponse(status_code=409, content={"detail": str(exc)})
        return setup_json(load_mode_config(mode_path), capture_layer)

    return router
