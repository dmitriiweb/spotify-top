from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLite engine with foreign-key enforcement enabled."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError("Only SQLite database URLs are supported")

    database = url.database
    in_memory = database in (None, "", ":memory:") or url.query.get("mode") == "memory"
    if database and database != ":memory:" and not database.startswith("file:"):
        Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    options: dict[str, object] = {"future": True}
    if in_memory:
        options.update(poolclass=StaticPool, connect_args={"check_same_thread": False})
    engine = create_engine(url, **options)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(
        dbapi_connection: object, connection_record: object
    ) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create all tables for the supplied engine."""
    from spotify_analytics.core.models import Base

    Base.metadata.create_all(engine)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create sessions that retain loaded values after commit."""
    return sessionmaker(bind=engine, expire_on_commit=False)
