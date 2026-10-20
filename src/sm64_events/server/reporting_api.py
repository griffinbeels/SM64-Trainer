"""Small local reporting boundary; protected by the app's local-action token."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from sm64_events.core import error_reporting


class ReportingPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class BrowserError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(max_length=1000)
    stack: str = Field(default="", max_length=6000)
    source: str = Field(default="browser", max_length=120)


def create_reporting_router() -> APIRouter:
    router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

    @router.get("/reporting")
    def reporting_status():
        return error_reporting.status()

    @router.put("/reporting")
    def reporting_preference(body: ReportingPreference):
        error_reporting.set_enabled(body.enabled)
        return error_reporting.status()

    @router.post("/browser-error", status_code=202)
    def browser_error(body: BrowserError):
        error_reporting.capture_problem(body.message, context={
            "component": "browser", "source": body.source, "stack": body.stack})
        return {"accepted": True}

    return router
