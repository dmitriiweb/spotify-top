from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from spotify_analytics.core.config import Settings
from spotify_analytics.core.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)

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
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Spotify Library Analytics",
        description=(
            "Phase 0 foundation only: the local health and landing pages are "
            "available; analytics and Spotify integration are not implemented."
        ),
        lifespan=lifespan,
    )
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
