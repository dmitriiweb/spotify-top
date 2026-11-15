from fastapi.testclient import TestClient

from spotify_analytics.core.config import Settings
from spotify_analytics.web.app import create_app


def test_dashboard_and_chart_assets_are_served_locally(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'dashboard.db'}"))

    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        html = page.text
        assert 'name="album_id"' in html
        assert (
            'href="/api/export/artists.csv"' in html
            and 'download="artists.csv"' in html
        )
        assert (
            'href="/api/export/tracks.csv"' in html and 'download="tracks.csv"' in html
        )
        assert (
            'href="/api/export/library.json"' in html
            and 'download="library.json"' in html
        )
        for screen in (
            "overview",
            "artists",
            "tracks",
            "timeline",
            "discovery",
            "settings",
        ):
            assert f'id="screen-{screen}"' in html

        assert 'src="/static/plotly.min.js"' in html
        assert "cdn.plot.ly" not in html
        assert "https://" not in html

        plotly = client.get("/static/plotly.min.js")
        assert plotly.status_code == 200
        assert plotly.content
        assert plotly.headers["content-type"].startswith("text/javascript")
        license_file = client.get("/static/plotly.min.js.LICENSE.txt")
        assert license_file.status_code == 200
        assert "MIT License" in license_file.text

        app_script = client.get("/static/app.js")
        assert app_script.status_code == 200
        assert app_script.headers["content-type"].startswith("text/javascript")
