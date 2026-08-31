# src/sm64_events/server/inputs_api.py
"""Captured-input REST surface: a thin skin over `inputs/service.py`.

No logic lives here. Every route asks the service and maps its exceptions,
with the same taxonomy as api.py: LookupError -> 404, ValueError -> 409,
RuntimeError -> 503. A `DocumentError` is a ValueError, so an unloadable
document comes back as a 409 carrying the reason — never a 500, and never a
silent acceptance that would store something the timeline cannot draw.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

MAX_DOCUMENT_BYTES = 4 * 1024 * 1024      # ~30 minutes of dense play


class MarkBody(BaseModel):
    name: str | None = None


class ImportBody(BaseModel):
    kind: str
    entity_key: str
    strat_tag: str | None = None
    name: str
    document: str


def _http(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(404, str(error))
    if isinstance(error, ValueError):
        return HTTPException(409, str(error))
    return HTTPException(503, str(error))


def _template_summary(template) -> dict:
    return {"id": template.id, "kind": template.kind,
            "entity_key": template.entity_key, "strat_tag": template.strat_tag,
            "name": template.name, "origin": template.origin,
            "active": template.active, "created_utc": template.created_utc}


def create_inputs_router(inputs) -> APIRouter:
    """`inputs` is the `InputsService`; its `.templates` is the store."""
    router = APIRouter(prefix="/api")
    templates = inputs.templates

    @router.get("/attempts/{attempt_id}/inputs")
    def timeline(attempt_id: int, from_frame: int | None = None,
                 to_frame: int | None = None):
        # The clip's own frame range, when the caller has one: the timeline
        # then spans exactly what the video shows (round 32 item 53).
        span = (None if from_frame is None or to_frame is None
                else (from_frame, to_frame))
        try:
            return inputs.timeline(attempt_id, span=span)
        except Exception as error:
            raise _http(error) from error

    @router.get("/attempts/{attempt_id}/inputs/document",
                response_class=PlainTextResponse)
    def document(attempt_id: int):
        try:
            return inputs.document(attempt_id)
        except Exception as error:
            raise _http(error) from error

    @router.post("/attempts/{attempt_id}/inputs/template")
    def mark(attempt_id: int, body: MarkBody):
        """Make this attempt the template for its target and strategy."""
        try:
            template = inputs.mark_template(attempt_id, body.name)
        except Exception as error:
            raise _http(error) from error
        return {"id": template.id, "name": template.name,
                "origin": template.origin}

    @router.get("/inputs/templates")
    def list_templates(kind: str | None = None, entity_key: str | None = None):
        rows = (templates.list_for(kind, entity_key)
                if kind and entity_key else templates.all())
        return {"templates": [_template_summary(row) for row in rows]}

    @router.get("/inputs/templates/{template_id}/document",
                response_class=PlainTextResponse)
    def template_document(template_id: int):
        try:
            return templates.get(template_id).document
        except Exception as error:
            raise _http(error) from error

    @router.post("/inputs/templates")
    async def import_template(request: Request):
        """Import a document another player sent, or one written by hand.

        Takes JSON. The document is validated by the store before it lands, so
        an unloadable one is a 409 naming the reason rather than a row that
        breaks a drawer weeks later.
        """
        raw = await request.body()
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise HTTPException(413, "document too large")
        try:
            body = ImportBody.model_validate_json(raw)
        except Exception as error:
            raise HTTPException(422, str(error)) from error
        try:
            template = templates.save(
                kind=body.kind, entity_key=body.entity_key,
                strat_tag=body.strat_tag, name=body.name,
                origin=f"import:{body.name}", document=body.document)
        except Exception as error:
            raise _http(error) from error
        return {"id": template.id, "name": template.name,
                "origin": template.origin}

    @router.post("/inputs/templates/{template_id}/activate")
    def activate(template_id: int):
        try:
            template = templates.activate(template_id)
        except Exception as error:
            raise _http(error) from error
        return {"id": template.id, "active": template.active}

    @router.delete("/inputs/templates/{template_id}")
    def delete(template_id: int):
        templates.delete(template_id)
        return {"deleted": template_id}

    return router
