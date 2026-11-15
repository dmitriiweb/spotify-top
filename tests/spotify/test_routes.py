from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from spotify_analytics.core.config import Settings
from spotify_analytics.core.models import SavedTrack, Track
from spotify_analytics.core.schemas import ErrorResponse, SyncStatusResponse
from spotify_analytics.spotify.routes import router
from spotify_analytics.spotify.sync import SyncInProgress
from spotify_analytics.web.app import create_app

_SECRET = "a-secure-test-session-secret-with-entropy"


def _app(tmp_path, **settings_overrides):
    values = {
        "database_url": f"sqlite:///{tmp_path / 'routes.db'}",
        "spotify_client_id": "client-id",
        "spotify_client_secret": "client-secret",
        "session_secret": _SECRET,
    }
    values.update(settings_overrides)
    app = create_app(Settings(**values))
    app.include_router(router)
    return app


def _error(response, status, code):
    assert response.status_code == status
    ErrorResponse.model_validate(response.json())
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details"}
    assert error["code"] == code
    assert error["message"]
    assert isinstance(error["details"], list)


def test_login_configuration_redirect_and_cookie_flags(tmp_path):
    app = _app(tmp_path)
    with TestClient(
        app, base_url="https://testserver", follow_redirects=False
    ) as client:
        response = client.get("/auth/login")
        assert response.status_code == 302
        params = parse_qs(urlsplit(response.headers["location"]).query)
        assert params["scope"] == ["user-library-read"]
        assert params["code_challenge_method"] == ["S256"]
        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["client-id"]
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie
        assert "path=/" in cookie
        assert "verifier" not in response.text and "client-secret" not in response.text
    with TestClient(app, base_url="http://127.0.0.1", follow_redirects=False) as client:
        response = client.get("/auth/login")
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert "secure" not in cookie


def test_login_rejects_missing_secrets_and_insecure_redirects(tmp_path):
    for overrides in (
        {"spotify_client_id": None},
        {"spotify_client_secret": ""},
        {"session_secret": "weak"},
        {"session_secret": "x" * 40},
        {"redirect_uri": "http://localhost:8000/auth/callback"},
        {"redirect_uri": "http://example.com/auth/callback"},
    ):
        app = _app(tmp_path, **overrides)
        with TestClient(app, follow_redirects=False) as client:
            response = client.get("/auth/login")
            _error(response, 503, "spotify_not_configured")


def test_login_rejects_query_and_status_is_schema_valid(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        _error(client.get("/auth/login?unexpected=1"), 422, "invalid_query")
        status = client.get("/api/sync/status")
        assert status.status_code == 200
        assert (
            SyncStatusResponse.model_validate(status.json()).model_dump(mode="json")
            == status.json()
        )
        assert status.json() == {
            "account_id": None,
            "status": "idle",
            "last_successful_sync": None,
            "error": None,
            "saved_tracks": 0,
            "artists": 0,
            "albums": 0,
            "processed_tracks": 0,
        }
        _error(client.get("/api/sync/status?unknown=1"), 422, "invalid_query")
        _error(client.post("/api/sync?unknown=1"), 422, "invalid_query")
        _error(client.post("/api/sync", json={}), 422, "invalid_query")
        _error(client.post("/api/sync"), 401, "auth_required")


def test_callback_checks_state_before_denial_and_rejects_duplicate_parameters(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        invalid = client.get("/auth/callback?error=access_denied&state=wrong")
        _error(invalid, 400, "invalid_oauth_state")
        unicode_state = client.get("/auth/callback?error=access_denied&state=%E9%9B%AA")
        _error(unicode_state, 400, "invalid_oauth_state")
        login = client.get("/auth/login")
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        denied = client.get(f"/auth/callback?error=access_denied&state={state}")
        _error(denied, 400, "oauth_denied")
        replay = client.get(f"/auth/callback?error=access_denied&state={state}")
        _error(replay, 400, "invalid_oauth_state")
        login = client.get("/auth/login")
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        missing_code = client.get(f"/auth/callback?state={state}")
        _error(missing_code, 422, "invalid_query")
        _error(client.get("/auth/callback?state=x&state=y"), 422, "invalid_query")
        _error(client.get("/auth/callback?state=x&extra=y"), 422, "invalid_query")


def test_callback_authenticates_then_full_sync_closes_clients(
    tmp_path, monkeypatch, caplog
):
    created = []
    calls = []
    mode = {"current": "initial"}

    def saved_item(track_id, name, album_id="album-one"):
        return {
            "added_at": "2024-01-10T09:30:00Z",
            "track": {
                "id": track_id,
                "name": name,
                "duration_ms": 210000,
                "explicit": False,
                "external_urls": {},
                "artists": [
                    {
                        "id": "artist-one",
                        "name": "Artist One",
                        "images": [],
                        "external_urls": {},
                    }
                ],
                "album": {
                    "id": album_id,
                    "name": f"Album {album_id}",
                    "release_date": "2024-01",
                    "images": [],
                    "external_urls": {},
                },
            },
        }

    initial_items = [
        saved_item(f"track-{index}", f"Track {index}") for index in range(51)
    ]
    updated_item = saved_item("track-0", "Updated Track", "album-new")

    def page(items, offset, total, next_url=None):
        return {
            "items": items,
            "offset": offset,
            "limit": 50,
            "total": total,
            "next": next_url,
        }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.url.path == "/api/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert "client_secret" not in form
            return httpx.Response(
                200,
                json={
                    "access_token": "private-access",
                    "refresh_token": "private-refresh",
                    "expires_in": 3600,
                },
            )
        if request.url.path == "/v1/me":
            return httpx.Response(200, json={"account_id": "account-1"})
        if request.url.path == "/v1/me/tracks":
            offset = int(request.url.params["offset"])
            if mode["current"] == "initial":
                if offset == 0:
                    return httpx.Response(
                        200,
                        json=page(
                            initial_items[:50],
                            0,
                            51,
                            "https://api.spotify.com/v1/me/tracks?limit=50&offset=50",
                        ),
                    )
                return httpx.Response(200, json=page(initial_items[50:], 50, 51))
            if mode["current"] == "updated":
                return httpx.Response(200, json=page([updated_item], 0, 1))
            if offset == 0:
                return httpx.Response(
                    200,
                    json=page(
                        initial_items[:50],
                        0,
                        51,
                        "https://api.spotify.com/v1/me/tracks?limit=50&offset=50",
                    ),
                )
            # An inconsistent later page must fail the full sync, not publish
            # the first page as a partial library.
            return httpx.Response(200, json=page(initial_items[50:], 0, 51))
        raise AssertionError("unexpected Spotify request")

    def factory():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        created.append(client)
        return client

    app = _app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        login = client.get("/auth/login")
        params = parse_qs(urlsplit(login.headers["location"]).query)
        state = params["state"][0]
        app.state.spotify_runtime.http_client_factory = factory
        callback = client.get(f"/auth/callback?code=one-time-code&state={state}")
        assert callback.status_code == 302 and callback.headers["location"] == "/"
        assert (
            "private-access" not in callback.text
            and "private-refresh" not in callback.text
        )
        assert callback.headers["set-cookie"]
        assert (
            client.get(f"/auth/callback?code=one-time-code&state={state}").status_code
            == 400
        )

        def already_running(_account_id):
            raise SyncInProgress

        monkeypatch.setattr(
            app.state.spotify_runtime.sync_service, "start", already_running
        )
        _error(client.post("/api/sync"), 409, "sync_in_progress")

        def unexpected_failure(_account_id):
            raise RuntimeError("private database failure")

        monkeypatch.setattr(
            app.state.spotify_runtime.sync_service, "start", unexpected_failure
        )
        failure = client.post("/api/sync")
        _error(failure, 500, "internal_error")
        assert "private database failure" not in failure.text
        monkeypatch.undo()

        accepted = client.post("/api/sync")
        assert accepted.status_code == 202
        initial = SyncStatusResponse.model_validate(accepted.json())
        assert initial.account_id == "account-1" and initial.status == "running"
        first_status = SyncStatusResponse.model_validate(
            client.get("/api/sync/status").json()
        )
        assert first_status.status == "succeeded" and first_status.saved_tracks == 51
        assert first_status.artists == 1 and first_status.albums == 1
        with app.state.session_factory() as session:
            assert session.scalar(
                select(SavedTrack).where(SavedTrack.track_id == "track-50")
            )

        mode["current"] = "updated"
        accepted = client.post("/api/sync")
        assert accepted.status_code == 202
        second_status = SyncStatusResponse.model_validate(
            client.get("/api/sync/status").json()
        )
        assert second_status.status == "succeeded" and second_status.saved_tracks == 1
        assert second_status.last_successful_sync is not None
        with app.state.session_factory() as session:
            assert session.get(Track, "track-0").name == "Updated Track"
            assert session.get(Track, "track-1") is not None
            assert session.scalar(
                select(SavedTrack).where(SavedTrack.track_id == "track-0")
            )
            assert (
                session.scalar(
                    select(SavedTrack).where(SavedTrack.track_id == "track-1")
                )
                is None
            )

        mode["current"] = "failure"
        accepted = client.post("/api/sync")
        assert accepted.status_code == 202
        failed_status = SyncStatusResponse.model_validate(
            client.get("/api/sync/status").json()
        )
        assert failed_status.status == "failed"
        assert failed_status.error == "Synchronization failed"
        assert failed_status.processed_tracks == 50
        assert failed_status.saved_tracks == second_status.saved_tracks == 1
        assert failed_status.last_successful_sync == second_status.last_successful_sync
        with app.state.session_factory() as session:
            assert session.get(Track, "track-0").name == "Updated Track"
            assert session.scalar(
                select(SavedTrack).where(SavedTrack.track_id == "track-0")
            )
            assert (
                session.scalar(
                    select(SavedTrack).where(SavedTrack.track_id == "track-1")
                )
                is None
            )

        assert len(created) == 4 and all(http.is_closed for http in created)
        track_requests = [
            urlsplit(url) for _, url in calls if urlsplit(url).path == "/v1/me/tracks"
        ]
        assert [request.query for request in track_requests] == [
            "limit=50&offset=0",
            "limit=50&offset=50",
            "limit=50&offset=0",
            "limit=50&offset=0",
            "limit=50&offset=50",
        ]
        assert all(
            secret not in caplog.text
            for secret in (
                "private-access",
                "private-refresh",
                "client-secret",
                "one-time-code",
            )
        )


def test_callback_exchange_failure_is_safe_and_client_closes(tmp_path):
    clients = []

    def factory():
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, text="private failure detail")
            )
        )
        clients.append(client)
        return client

    app = _app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        login = client.get("/auth/login")
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        app.state.spotify_runtime.http_client_factory = factory
        response = client.get(f"/auth/callback?code=authorization-code&state={state}")
        _error(response, 502, "spotify_unavailable")
        assert "authorization-code" not in response.text
        assert "private failure detail" not in response.text
        assert len(clients) == 1 and clients[0].is_closed
