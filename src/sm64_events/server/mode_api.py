"""GET|PUT /api/mode -- the tracker mode + game version setting.

The game version (Auto-detect / JP / US, core/modes.py) applies LIVE: a PUT
persists the choice and hands it to `service.set_game_version`, which flips
the standards store's grading version and broadcasts `game_version_changed`,
so every open client re-grades on its next view. Nothing here needs a
restart, so no `restart_required` field is served (his ruling 2026-08-15:
freely swap between ROMs).

`mode` (EMU/N64) is stored and echoed but nothing on main reads it: the N64
front-end is feature/console-support's. That branch carries its OWN copy of
this route inside server/vision_api.py and a Settings dropdown for the same
record; when it merges main it must DELETE both and keep only its Tracking
mode + N64 setup entries -- FastAPI registers a duplicate path silently and
the first one wins, so a leftover copy is a shadow, not an error. Its
`restart_required` belongs to the vision half (detectors are built at boot)
and can come back beside `effective` then.

`unsupported` = the emulator path with an explicit JP: no verified JP
addresses exist, so detection stays US while grading uses JP standards.
Informational only -- the settings note reads it; the store still applies
the choice, because refusing to represent a stored value would make the
setting unreadable the moment support lands."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sm64_events.core.modes import (GameVersion, ModeConfig, TrackerMode,
                                    load_mode_config, save_mode_config)


class ModeBody(BaseModel):
    mode: str | None = None
    version: str | None = None


def mode_json(cfg: ModeConfig, service) -> dict:
    game = service.game_version()
    return {"mode": cfg.mode.value, "version": cfg.version.value,
            "effective": game["effective"],
            "unsupported": cfg.mode is TrackerMode.EMU
            and cfg.version is GameVersion.JP}


def create_mode_router(service, mode_path: Path | None = None) -> APIRouter:
    """`mode_path` overrides where the setting persists (tests; production
    takes core.paths.mode_settings_path())."""
    router = APIRouter(prefix="/api")

    @router.get("/mode")
    def get_mode():
        return mode_json(service.mode_config, service)

    @router.put("/mode")
    async def put_mode(body: ModeBody):
        current = service.mode_config
        try:
            mode = current.mode if body.mode is None \
                else TrackerMode(body.mode.strip().lower())
            version = current.version if body.version is None \
                else GameVersion(body.version.strip().lower())
        except ValueError:
            return JSONResponse(status_code=400, content={
                "detail": f"unknown mode/version in {body.model_dump()!r}"})
        cfg = ModeConfig(mode=mode, version=version)
        save_mode_config(cfg, mode_path)
        await service.set_game_version(cfg)
        return mode_json(cfg, service)

    return router


def apply_persisted_mode(service, mode_path: Path | None = None) -> ModeConfig:
    """Boot: read the persisted setting and make the store grade on it
    BEFORE the app serves. Synchronous on purpose (no broadcast -- nobody is
    connected yet); `service.mode_config` and the grading version end up
    exactly as a PUT would leave them."""
    from sm64_events.core.modes import effective_version
    cfg = load_mode_config(mode_path)
    service.mode_config = cfg
    if service.ranks is not None:
        service.ranks.grading_version = effective_version(cfg)
    return cfg
