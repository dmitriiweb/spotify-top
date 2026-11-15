from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def create_db_engine(database_url: str) -> Engine:
    """Create a SQLite engine with foreign keys and explicit transactions."""
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
    def _configure_sqlite_transactions(
        dbapi_connection: object, connection_record: object
    ) -> None:
        # sqlite3's legacy mode skips BEGIN for SELECT, so a Session transaction
        # alone does not guarantee a consistent snapshot across multiple reads.
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin_sqlite_transaction(connection: object) -> None:
        begin_mode = connection.get_execution_options().get(  # type: ignore[attr-defined]
            "sqlite_begin_mode", "DEFERRED"
        )
        if begin_mode not in ("DEFERRED", "IMMEDIATE", "EXCLUSIVE"):
            raise ValueError(f"Unsupported SQLite begin mode: {begin_mode}")
        connection.exec_driver_sql(  # type: ignore[attr-defined]
            f"BEGIN {begin_mode}"
        )

    return engine


def init_db(engine: Engine) -> None:
    """Create all tables for the supplied engine."""
    from spotify_analytics.core.models import Base

    Base.metadata.create_all(engine)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create sessions that retain loaded values after commit."""
    return sessionmaker(bind=engine, expire_on_commit=False)
