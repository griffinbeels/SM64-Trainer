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
                                    effective_version, load_mode_config,
                                    save_mode_config)
from sm64_events.server.ranks_api import absorb_after_regrade


class ModeBody(BaseModel):
    mode: str | None = None
    version: str | None = None


def _effective(cfg: ModeConfig, service) -> str:
    """The setting resolved on the running cartridge (AUTO follows it)."""
    resolve = getattr(service, "effective_game_version", None)
    return resolve(cfg) if resolve is not None else effective_version(cfg)


def mode_json(cfg: ModeConfig, service) -> dict:
    return {"mode": cfg.mode.value, "version": cfg.version.value,
            "effective": _effective(cfg, service),
            "unsupported": cfg.mode is TrackerMode.EMU
            and cfg.version is GameVersion.JP}


def create_mode_router(service, mode_path: Path | None = None) -> APIRouter:
    """`mode_path` overrides where the setting persists (tests; production
    takes core.paths.mode_settings_path())."""
    router = APIRouter(prefix="/api")
    # A cartridge change under AUTO re-grades too (`service.note_running_version`),
    # and it is no more a rank-up than a setting flip is.
    if getattr(service, "on_regrade", False) is None:
        service.on_regrade = lambda: absorb_after_regrade(service)

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
        # Re-grade FIRST, then move every MARELO watermark to where the
        # scopes now sit -- a rank that changed because the ladders changed
        # is not a rank-up (server/ranks_api.py::absorb_after_regrade). The
        # broadcast inside set_game_version is what makes clients refetch,
        # and their /api/marelo fetch races this handler, so the absorb runs
        # BEFORE the broadcast rather than after it.
        service.mode_config = cfg
        if service.ranks is not None:
            service.ranks.grading_version = _effective(cfg, service)
        absorb_after_regrade(service)
        await service.set_game_version(cfg)
        return mode_json(cfg, service)

    return router


def apply_persisted_mode(service, mode_path: Path | None = None) -> ModeConfig:
    """Boot: read the persisted setting and make the store grade on it
    BEFORE the app serves. Synchronous on purpose (no broadcast -- nobody is
    connected yet); `service.mode_config` and the grading version end up
    exactly as a PUT would leave them."""
    cfg = load_mode_config(mode_path)
    service.mode_config = cfg
    if service.ranks is not None:
        service.ranks.grading_version = _effective(cfg, service)
    return cfg
