from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from starlette.exceptions import HTTPException

from spotify_analytics.core.config import Settings
from spotify_analytics.core.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from spotify_analytics.core.schemas import ErrorResponse
from spotify_analytics.spotify.routes import router as spotify_router
from spotify_analytics.spotify.sync import SyncService
from spotify_analytics.web.routes import router as analytics_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


_WEB_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB_DIR / "templates"))


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the web app; database resources are owned by its lifespan."""
    resolved_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(resolved_settings.database_url)
        try:
            init_db(engine)
            app.state.engine = engine
            app.state.session_factory = create_session_factory(engine)
            SyncService(app.state.session_factory).recover_interrupted()
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Spotify Library Analytics",
        description="Interactive analytics for a saved Spotify library.",
        lifespan=lifespan,
    )
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unexpected_exception_handler)

    app.include_router(spotify_router)
    app.include_router(analytics_router)
    app.state.settings = resolved_settings
    app.mount(
        "/static",
        StaticFiles(directory=str(_WEB_DIR / "static")),
        name="static",
    )

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def landing_page(request: Request) -> HTMLResponse:
        return _TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={},
        )

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        with app.state.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


def _error_response(
    status: int, code: str, message: str, details: list[dict] | None = None
):
    payload = ErrorResponse(
        error={"code": code, "message": message, "details": details or []}
    )
    return JSONResponse(payload.model_dump(mode="json"), status_code=status)


def _validation_exception_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    details = [
        {
            "field": str(item["loc"][-1]) if item.get("loc") else "query",
            "message": item["msg"],
        }
        for item in error.errors()
    ]
    return _error_response(422, "invalid_query", "Request validation failed", details)


def _http_exception_handler(request: Request, error: HTTPException) -> JSONResponse:
    status = error.status_code
    if status == 404:
        return _error_response(status, "not_found", "Resource not found")
    return _error_response(status, "http_error", "Request could not be completed")


def _unexpected_exception_handler(request: Request, error: Exception) -> JSONResponse:
    return _error_response(500, "internal_error", "An internal error occurred")
