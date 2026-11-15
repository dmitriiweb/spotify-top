from __future__ import annotations

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from spotify_analytics.core.config import Settings
from spotify_analytics.core.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from spotify_analytics.core.fixtures import load_demo
from spotify_analytics.web.app import create_app


@pytest.fixture
def demo_client(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    engine = create_db_engine(database_url)
    init_db(engine)
    with create_session_factory(engine)() as session:
        load_demo(session)
        session.commit()
    engine.dispose()
    app = create_app(Settings(database_url=database_url))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def empty_client(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'empty.db'}"))
    with TestClient(app) as client:
        yield client


def test_analytical_routes_cover_empty_database(empty_client):
    client = empty_client
    assert client.get("/api/overview").json()["total_tracks"] == 0
    assert client.get("/api/artists").json()["items"] == []
    assert client.get("/api/tracks").json()["items"] == []
    assert client.get("/api/timeline").json()["buckets"] == []
    assert client.get("/api/distribution").json()["buckets"] == []
    assert client.get("/api/insights").json()["items"] == []
    assert client.get("/api/artists/demo-artist-01").status_code == 404
    assert client.get("/api/sync/status").json()["status"] == "idle"


def test_all_analytics_routes_filters_sorting_and_pagination(demo_client):
    client = demo_client
    assert client.get("/api/overview").json()["total_tracks"] == 120

    filtered = client.get(
        "/api/tracks", params={"album_id": "demo-album-01", "limit": 3}
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 10
    assert len(filtered.json()["items"]) == 3
    assert filtered.json()["limit"] == 3

    all_tracks = client.get("/api/tracks?sort=name_asc&offset=1000").json()
    assert all_tracks["items"] == []
    assert all_tracks["total"] == 120
    assert all_tracks["offset"] == 1000
    for sort in ("added_at_asc", "duration_desc", "name_asc"):
        assert client.get("/api/tracks", params={"sort": sort}).status_code == 200

    artists = client.get("/api/artists?sort=name_asc&limit=200").json()
    assert artists["total"] == 24
    assert len(artists["items"]) == 24
    assert sorted(item["rank"] for item in artists["items"]) == list(range(1, 25))
    assert (
        client.get("/api/artists?sort=saved_tracks_desc&offset=100").json()["items"]
        == []
    )

    singleton = client.get("/api/artists/demo-artist-23").json()
    assert singleton["saved_tracks"] == 1
    assert len(singleton["tracks"]["items"][0]["artists"]) == 1
    shared_track = client.get("/api/tracks?sort=name_asc&limit=10").json()["items"]
    collaborator = next(item for item in shared_track if item["id"] == "demo-track-004")
    assert len(collaborator["artists"]) == 2

    artist_id = "demo-artist-01"
    detail = client.get(f"/api/artists/{artist_id}?limit=2&sort=duration_desc").json()
    assert detail["artist"]["id"] == artist_id
    assert detail["tracks"]["limit"] == 2
    assert detail["saved_tracks"] > 0
    assert (
        client.get(
            f"/api/artists/{artist_id}?artist_id=demo-artist-02&album_id=missing"
        ).status_code
        == 200
    )
    empty_detail = client.get(f"/api/artists/{artist_id}?album_id=missing").json()
    assert empty_detail["saved_tracks"] == 0
    assert empty_detail["rank"] is None
    assert empty_detail["tracks"]["total"] == 0
    assert client.get("/api/artists/unknown-id").json()["error"]["code"] == "not_found"

    assert client.get("/api/timeline?interval=year").json()["interval"] == "year"
    for dimension in ("release_decade", "duration", "explicit"):
        assert (
            client.get("/api/distribution", params={"dimension": dimension}).status_code
            == 200
        )
    assert client.get("/api/insights?q=  BjÖRK  ").status_code == 200
    assert (
        client.get("/api/tracks?added_from=2024-01-01&added_to=2024-01-31").status_code
        == 200
    )
    assert client.get("/api/artists?artist_id=demo-artist-01").status_code == 200
    assert client.get("/api/tracks?explicit=true").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/api/overview?unrecognized=1",
        "/api/artists?unrecognized=1",
        "/api/artists/demo-artist-01?unrecognized=1",
        "/api/tracks?unrecognized=1",
        "/api/timeline?unrecognized=1",
        "/api/distribution?unrecognized=1",
        "/api/insights?unrecognized=1",
        "/api/export/artists.csv?unrecognized=1",
        "/api/export/tracks.csv?unrecognized=1",
        "/api/export/library.json?unrecognized=1",
    ],
)
def test_every_analytics_route_rejects_unknown_query_keys(demo_client, path):
    response = demo_client.get(path)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_query"


def test_duplicate_and_invalid_queries_use_contract_errors(demo_client):
    cases = (
        ("/api/tracks", "q=a&q=b"),
        ("/api/tracks", "limit=10&limit=20"),
        ("/api/tracks", "limit=0"),
        ("/api/tracks", "offset=-1"),
        ("/api/artists", "sort=bogus"),
        ("/api/timeline", "interval=week"),
        ("/api/distribution", "dimension=bogus"),
        ("/api/tracks", "explicit=maybe"),
        ("/api/tracks", "added_from=2024-03-01&added_to=2024-02-01"),
        ("/api/tracks", "artist_id="),
        ("/api/tracks", "q=" + "x" * 201),
    )
    for path, query in cases:
        response = demo_client.get(f"{path}?{query}")
        assert response.status_code == 422, query
        assert response.json()["error"]["code"] == "invalid_query"
        assert "details" in response.json()["error"]


def test_artist_detail_filter_intersects_instead_of_replacing(demo_client):
    response = demo_client.get(
        "/api/artists/demo-artist-01", params={"artist_id": "demo-artist-02"}
    )
    assert response.status_code == 200
    assert response.json()["filters"]["artist_id"] == "demo-artist-02"
    assert response.json()["saved_tracks"] == 0


def test_exports_download_complete_filtered_rows(demo_client):
    artists = demo_client.get("/api/export/artists.csv")
    assert artists.status_code == 200
    assert (
        artists.headers["content-disposition"] == 'attachment; filename="artists.csv"'
    )
    artist_rows = list(csv.DictReader(io.StringIO(artists.text)))
    assert len(artist_rows) == 24

    tracks = demo_client.get(
        "/api/export/tracks.csv", params={"album_id": "demo-album-01"}
    )
    assert tracks.headers["content-disposition"] == 'attachment; filename="tracks.csv"'
    assert tracks.headers["content-type"].startswith("text/csv; charset=utf-8")
    assert len(list(csv.DictReader(io.StringIO(tracks.text)))) == 10

    library = demo_client.get("/api/export/library.json")
    assert (
        library.headers["content-disposition"] == 'attachment; filename="library.json"'
    )
    assert library.headers["content-type"].startswith("application/json; charset=utf-8")
    exported = json.loads(library.content)
    assert len(exported["artists"]) == 24
    assert len(exported["tracks"]) == 120


def test_generic_service_and_database_failures_hide_details_and_close_sessions(
    demo_client, monkeypatch
):
    from contextlib import contextmanager

    from spotify_analytics.analytics.service import AnalyticsService

    closed = []
    original_factory = demo_client.app.state.session_factory

    @contextmanager
    def tracked_factory():
        with original_factory() as session:
            try:
                yield session
            finally:
                closed.append(True)

    demo_client.app.state.session_factory = tracked_factory

    def fail_service(self, filters):
        raise RuntimeError("private-token-value")

    monkeypatch.setattr(AnalyticsService, "overview", fail_service)
    response = demo_client.get("/api/overview")
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An internal error occurred",
            "details": [],
        }
    }
    assert "private-token-value" not in response.text
    assert closed == [True]
    demo_client.app.state.session_factory = lambda: (_ for _ in ()).throw(
        RuntimeError("database-secret")
    )
    database_failure = demo_client.get("/api/overview")
    assert database_failure.status_code == 500
    assert database_failure.json()["error"]["code"] == "internal_error"
    assert "database-secret" not in database_failure.text
