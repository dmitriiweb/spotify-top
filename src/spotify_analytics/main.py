import argparse
from collections.abc import Sequence

import uvicorn

from spotify_analytics.core.config import Settings
from spotify_analytics.core.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from spotify_analytics.core.fixtures import load_demo
from spotify_analytics.web.app import create_app


def main(argv: Sequence[str] | None = None) -> None:
    """Initialize, seed, or serve the local Spotify analytics application."""
    parser = argparse.ArgumentParser(
        prog="spotify-analytics",
        description="Run the local Spotify library analytics foundation.",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--init-db",
        action="store_true",
        help="create the database schema and exit",
    )
    actions.add_argument(
        "--load-demo",
        action="store_true",
        help="create the schema, load demo data, and exit",
    )
    parser.add_argument("--host", help="server host (default from settings)")
    parser.add_argument("--port", type=int, help="server port (default from settings)")
    args = parser.parse_args(argv)
    settings = Settings()

    if args.init_db or args.load_demo:
        engine = create_db_engine(settings.database_url)
        try:
            init_db(engine)
            if args.load_demo:
                session_factory = create_session_factory(engine)
                with session_factory.begin() as session:
                    load_demo(session)
        finally:
            engine.dispose()
        return

    uvicorn.run(
        create_app(settings),
        host=args.host or settings.host,
        port=args.port if args.port is not None else settings.port,
    )


if __name__ == "__main__":
    main()
