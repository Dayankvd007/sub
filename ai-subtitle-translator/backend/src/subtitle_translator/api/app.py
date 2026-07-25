"""FastAPI application factory for the local translation service.

Security boundary (architecture §4): bind to 127.0.0.1, allow only explicitly
configured origins, keep provider credentials out of every response and log,
and reject oversized payloads before any provider call is made.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from .. import __version__ as _pkg_version
from .deps import get_settings, get_worker
from .routes import router
from .settings import Settings


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized bodies using the declared Content-Length.

    A cheap first gate so a huge payload is refused before parsing. The cue
    count cap in the route is the authoritative limit.
    """

    def __init__(self, app, max_body_bytes: int):
        super().__init__(app)
        self._max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError:
                declared = 0
            if declared > self._max_body_bytes:
                return JSONResponse(
                    status_code=413,  # payload too large
                    content={
                        "error_code": "payload_too_large",
                        "message": (
                            f"Request body of {declared} bytes exceeds the "
                            f"limit of {self._max_body_bytes} bytes."
                        ),
                    },
                )
        return await call_next(request)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Start the job worker, which first recovers jobs a previous run left open.

        Startup recovery is why the in-memory queue can be thrown away on exit:
        every non-terminal job is rediscovered from SQLite here.
        """
        worker = get_worker() if settings.job_worker_enabled else None
        if worker is not None:
            worker.start()
        try:
            yield
        finally:
            if worker is not None:
                # Waits for the window in flight to commit rather than losing it.
                worker.stop()

    app = FastAPI(
        title="AI Subtitle Translator — Local API",
        version=_pkg_version,
        description=(
            "Local service over the Phase 1 translation engine: synchronous "
            "translation, a SQLite cache, and background translation jobs with "
            "progressive delivery. Localhost only; no accounts or cloud deployment."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=settings.max_body_bytes)

    # Explicit allowlist only — never "*". Chrome extension origins look like
    # chrome-extension://<id> and must be configured via ALLOWED_ORIGINS.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # Routes raise HTTPException with a {"error_code", "message"} detail;
        # flatten it so every error response has the same shape.
        if isinstance(exc.detail, dict) and "error_code" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": "http_error", "message": str(exc.detail)},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Never leak a stack trace, prompt, or provider payload to the client.
        return JSONResponse(
            status_code=500,
            content={"error_code": "internal_error", "message": "Internal server error."},
        )

    app.include_router(router)
    return app
