# src/sm64_events/server/ranks_api.py
"""REST CRUD for rank standards. Same error taxonomy as api.py/replay_api.py:
LookupError->404, ValueError->409, RuntimeError->503."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(409, str(e))
    return HTTPException(503, str(e))


class ThresholdBody(BaseModel):
    seconds: float


class StrategyBody(BaseModel):
    strategy: str


def create_ranks_router(service) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/ranks/standards")
    def get_standards(entity: str | None = None):
        if service.ranks is None:
            raise HTTPException(503, "rank standards unavailable")
        if entity is None:
            return service.ranks.to_json()
        return {"entity": entity, "clock": service.ranks.clock_for(entity),
                "strategies": service.ranks.ladders(entity)}

    @router.put("/ranks/standards/{entity}/{strategy}/{rank}")
    async def put_threshold(entity: str, strategy: str, rank: str, body: ThresholdBody):
        try:
            await service.set_rank_threshold(entity, strategy, rank, body.seconds)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/ranks/standards/{entity}")
    async def create_strategy(entity: str, body: StrategyBody):
        try:
            await service.create_rank_strategy(entity, body.strategy)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.delete("/ranks/standards/{entity}/{strategy}")
    async def delete_strategy(entity: str, strategy: str):
        try:
            await service.delete_rank_strategy(entity, strategy)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/ranks/standards/{entity}/reset")
    async def reset_entity(entity: str):
        try:
            await service.reset_rank_entity(entity)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    return router
