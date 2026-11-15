from sqlalchemy import func, select

from spotify_analytics.core.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from spotify_analytics.core.models import SavedTrack, SyncState, Track
from spotify_analytics.main import main


class ReadyServer:
    def __init__(self, config, events):
        self.config = config
        self.events = events
        self.started = False
        self.should_exit = False

    def run(self):
        self.started = True
        self.events.append("ready")


def test_launcher_disables_uvicorn_access_logs_and_opens_browser_when_ready(
    monkeypatch,
):
    events = []
    servers = []

    def server_factory(config):
        server = ReadyServer(config, events)
        servers.append(server)
        return server

    monkeypatch.setattr("spotify_analytics.main.uvicorn.Server", server_factory)
    monkeypatch.setattr(
        "spotify_analytics.main.webbrowser.open",
        lambda url: events.append(("browser", url)),
    )

    main(["--host", "0.0.0.0", "--port", "8123"])

    assert servers[0].config.access_log is False
    assert events == ["ready", ("browser", "http://127.0.0.1:8123/")]


def test_no_browser_avoids_browser_launch(monkeypatch):
    events = []
    monkeypatch.setattr(
        "spotify_analytics.main.uvicorn.Server",
        lambda config: ReadyServer(config, events),
    )
    monkeypatch.setattr(
        "spotify_analytics.main.webbrowser.open",
        lambda url: events.append(("browser", url)),
    )

    main(["--no-browser"])

    assert events == ["ready"]


def test_demo_uses_separate_sibling_database_without_spotify_credentials(
    tmp_path, monkeypatch
):
    library_path = tmp_path / "spotify-analytics.db"
    demo_path = tmp_path / "spotify-analytics-demo.db"
    monkeypatch.setenv("SPOTIFY_ANALYTICS_DATABASE_URL", f"sqlite:///{library_path}")
    monkeypatch.delenv("SPOTIFY_ANALYTICS_SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_ANALYTICS_SPOTIFY_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SPOTIFY_ANALYTICS_SESSION_SECRET", raising=False)
    events = []
    monkeypatch.setattr(
        "spotify_analytics.main.uvicorn.Server",
        lambda config: ReadyServer(config, events),
    )

    real_engine = create_db_engine(f"sqlite:///{library_path}")
    init_db(real_engine)
    with create_session_factory(real_engine).begin() as session:
        session.add(
            Track(
                id="real-track",
                name="Real library",
                duration_ms=1,
                explicit=False,
            )
        )
    real_engine.dispose()

    main(["--demo", "--no-browser"])

    assert library_path.is_file()
    assert demo_path.is_file()
    real_engine = create_db_engine(f"sqlite:///{library_path}")
    demo_engine = create_db_engine(f"sqlite:///{demo_path}")
    try:
        with create_session_factory(real_engine)() as session:
            assert session.get(Track, "real-track") is not None
            assert session.scalar(select(func.count()).select_from(SavedTrack)) == 0
        with create_session_factory(demo_engine)() as session:
            assert session.scalar(select(func.count()).select_from(SavedTrack)) == 120
            assert session.get(SyncState, "demo").status == "succeeded"
    finally:
        real_engine.dispose()
        demo_engine.dispose()
    assert events == ["ready"]
