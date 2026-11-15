import argparse
import logging
import threading
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from time import sleep

import uvicorn
from sqlalchemy.engine import make_url

from spotify_analytics.core.config import Settings
from spotify_analytics.core.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from spotify_analytics.core.fixtures import load_demo
from spotify_analytics.web.app import create_app

_LOGGER = logging.getLogger(__name__)


def _demo_settings(settings: Settings) -> Settings:
    url = make_url(settings.database_url)
    database = url.database
    if not database or database == ":memory:" or database.startswith("file:"):
        raise ValueError("--demo requires a file-backed SQLite database URL")
    path = Path(database)
    demo_path = path.with_name(f"{path.stem}-demo{path.suffix}")
    return settings.model_copy(
        update={"database_url": url.set(database=str(demo_path)).render_as_string()}
    )


def _initialize_database(settings: Settings, *, seed_demo: bool = False) -> None:
    engine = create_db_engine(settings.database_url)
    try:
        init_db(engine)
        if seed_demo:
            session_factory = create_session_factory(engine)
            with session_factory.begin() as session:
                load_demo(session)
    finally:
        engine.dispose()


def _serve(settings: Settings, *, open_browser: bool) -> None:
    host = settings.host
    port = settings.port
    app = create_app(settings)
    config = uvicorn.Config(app, host=host, port=port, access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn-server", daemon=True)
    thread.start()
    try:
        while thread.is_alive() and not server.started:
            sleep(0.05)
        if not server.started:
            raise RuntimeError("The application server stopped before it became ready")
        if open_browser:
            browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
            try:
                webbrowser.open(f"http://{browser_host}:{port}/")
            except Exception:
                _LOGGER.warning("Could not open the dashboard browser", exc_info=True)
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.should_exit = True
        thread.join()


def main(argv: Sequence[str] | None = None) -> None:
    """Initialize, seed, or serve the local Spotify analytics application."""
    parser = argparse.ArgumentParser(
        prog="spotify-analytics",
        description="Run the local Spotify library analytics dashboard.",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--init-db",
        action="store_true",
        help="create the configured database schema and exit",
    )
    actions.add_argument(
        "--load-demo",
        action="store_true",
        help="create the configured database, load demo data, and exit",
    )
    actions.add_argument(
        "--demo",
        action="store_true",
        help="serve demo data from a separate sibling SQLite database",
    )
    parser.add_argument("--host", help="server host (default from settings)")
    parser.add_argument("--port", type=int, help="server port (default from settings)")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="do not open a browser after the server is ready",
    )
    args = parser.parse_args(argv)
    settings = Settings()

    if args.demo:
        settings = _demo_settings(settings)
        _initialize_database(settings, seed_demo=True)
    elif args.init_db or args.load_demo:
        _initialize_database(settings, seed_demo=args.load_demo)
        return

    settings = settings.model_copy(
        update={
            "host": args.host or settings.host,
            "port": args.port if args.port is not None else settings.port,
        }
    )
    _serve(settings, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
