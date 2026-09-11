"""Request-scoped calibration reads for routers embedded without the full app."""
from contextlib import nullcontext
from fastapi import Request


def read_dependency(standards):
    async def reading(request: Request):
        # Async dependencies carry ContextVars into FastAPI's sync threadpool.
        context = (standards.read_context() if request.method == "GET" and standards is not None
                   and hasattr(standards, "read_context") else nullcontext())
        with context:
            yield
    return reading


def install_calibration_reads(app, standards, library):
    @app.middleware("http")
    async def calibration_read(request, call_next):
        if request.method != "GET" or not request.url.path.startswith("/api/"):
            return await call_next(request)
        context = (standards.read_context() if hasattr(standards, "read_context")
                   else library.calibrations.pin())
        with context:
            response = await call_next(request)
            if hasattr(standards, "calibration_revision"):
                response.headers["X-Rank-Calibration"] = standards.calibration_revision
            return response
