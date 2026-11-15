from fastapi.testclient import TestClient
from sqlalchemy import func, select

from spotify_analytics.core.config import Settings
from spotify_analytics.core.models import SavedTrack, SyncState
from spotify_analytics.main import main
from spotify_analytics.web.app import create_app


def test_app_lifecycle_health_landing_and_assets(tmp_path):
    database_path = tmp_path / "app.db"
    app = create_app(Settings(database_url=f"sqlite:///{database_path}"))

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        landing = client.get("/")
        assert landing.status_code == 200
        assert landing.headers["content-type"].startswith("text/html")

        javascript = client.get("/static/app.js")
        stylesheet = client.get("/static/styles.css")
        assert javascript.status_code == 200
        assert javascript.headers["content-type"].startswith("text/javascript")
        assert stylesheet.status_code == 200
        assert stylesheet.headers["content-type"].startswith("text/css")

        docs = client.get("/docs")
        assert docs.status_code == 200
        assert docs.headers["content-type"].startswith("text/html")


def test_init_db_cli_creates_database(tmp_path, monkeypatch):
    database_path = tmp_path / "initialized.db"
    monkeypatch.setenv("SPOTIFY_ANALYTICS_DATABASE_URL", f"sqlite:///{database_path}")

    main(["--init-db"])

    assert database_path.is_file()


def test_load_demo_cli_initializes_and_populates_database(tmp_path, monkeypatch):
    database_path = tmp_path / "demo.db"
    monkeypatch.setenv("SPOTIFY_ANALYTICS_DATABASE_URL", f"sqlite:///{database_path}")

    main(["--load-demo"])

    assert database_path.is_file()
    settings = Settings(database_url=f"sqlite:///{database_path}")
    app = create_app(settings)
    with TestClient(app):
        with app.state.session_factory() as session:
            saved_track_count = session.scalar(
                select(func.count()).select_from(SavedTrack)
            )
            sync_state = session.get(SyncState, "demo")
            assert saved_track_count == 120
            assert sync_state is not None
            assert sync_state.status == "succeeded"
            assert sync_state.saved_tracks == 120
            assert sync_state.artists == 24
            assert sync_state.albums == 12
            assert sync_state.processed_tracks == 120
