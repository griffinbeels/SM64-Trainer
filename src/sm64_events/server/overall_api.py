"""Personal Overall cutoff pins, independent of strategy-standard edits."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class OverallThresholdBody(BaseModel):
    seconds: float


def create_overall_router(service, absorb) -> APIRouter:
    router = APIRouter(prefix="/ranks/overall")

    async def changed():
        absorb(service)
        await service._rank_standards_changed()
        return {"ok": True, "calibration_revision": service.ranks.calibration_revision}

    def standards():
        if service.ranks is None:
            raise HTTPException(503, "rank standards unavailable")
        return service.ranks

    @router.put("/{entity}/{rank}")
    async def put_overall(entity: str, rank: str, body: OverallThresholdBody, version: str = "us"):
        try:
            standards().set_overall_threshold(entity, rank, body.seconds, version)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return await changed()

    @router.delete("/{entity}")
    async def reset_overall(entity: str, version: str | None = None):
        try:
            standards().reset_overall(entity, version)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return await changed()

    return router
