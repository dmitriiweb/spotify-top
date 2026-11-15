from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from spotify_analytics.core.config import Settings
from spotify_analytics.core.models import Album, Artist, SavedTrack, SyncState, Track
from spotify_analytics.web.app import create_app

_SECRET = "a-secure-test-session-secret-with-entropy"


def test_app_sync_lifecycle_and_restart_recovery(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'integration.db'}",
        spotify_client_id="client-id",
        spotify_client_secret="client-secret",
        session_secret=_SECRET,
    )
    account = {"id": "account-a"}
    mode = {"response": "initial"}

    def saved_track(track_id: str, name: str | None = None) -> dict:
        return {
            "added_at": "2024-01-10T09:30:00Z",
            "track": {
                "id": track_id,
                "name": name or f"Track {track_id}",
                "duration_ms": 210000,
                "explicit": False,
                "external_urls": {},
                "artists": [
                    {
                        "id": "artist-1",
                        "name": "Artist One",
                        "images": [],
                        "external_urls": {},
                    }
                ],
                "album": {
                    "id": "album-1",
                    "name": "Album One",
                    "release_date": "2024",
                    "images": [],
                    "external_urls": {},
                },
            },
        }

    snapshots = {
        "initial": [saved_track(f"track-{index}") for index in range(51)],
        "repeat": [saved_track("track-0", "Updated Track")],
        "switch": [saved_track("other-track", "Other Account Track")],
        "empty": [],
    }

    def spotify_response(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            return httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                },
            )
        if request.url.path == "/v1/me":
            return httpx.Response(200, json=account.copy())
        if request.url.path == "/v1/me/tracks":
            if mode["response"] == "failure":
                return httpx.Response(503, json={"error": "offline"})
            tracks = snapshots[mode["response"]]
            offset = int(request.url.params["offset"])
            page = tracks[offset : offset + 50]
            next_url = (
                "https://api.spotify.com/v1/me/tracks?limit=50&offset=50"
                if offset == 0 and len(tracks) > 50
                else None
            )
            return httpx.Response(
                200,
                json={
                    "items": page,
                    "offset": offset,
                    "limit": 50,
                    "total": len(tracks),
                    "next": next_url,
                },
            )
        raise AssertionError(f"Unexpected Spotify request: {request.url}")

    app = create_app(settings)
    with TestClient(app, follow_redirects=False) as client:
        # Initialize the lazy runtime without following its external OAuth redirect.
        first_login = client.get("/auth/login")
        first_state = parse_qs(urlsplit(first_login.headers["location"]).query)[
            "state"
        ][0]
        app.state.spotify_runtime.http_client_factory = lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(spotify_response)
        )

        def authenticate() -> None:
            login = client.get("/auth/login")
            state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
            callback = client.get(f"/auth/callback?code=code&state={state}")
            assert callback.status_code == 302
            assert callback.headers["location"] == "/"

        def sync() -> dict:
            result = client.post("/api/sync")
            assert result.status_code == 202
            response = client.get("/api/sync/status")
            assert response.status_code == 200
            return response.json()

        callback = client.get(f"/auth/callback?code=code&state={first_state}")
        assert callback.status_code == 302
        assert callback.headers["location"] == "/"
        assert sync()["status"] == "succeeded"
        assert client.get("/api/tracks").json()["total"] == 51

        mode["response"] = "repeat"
        repeated = sync()
        assert repeated["status"] == "succeeded" and repeated["saved_tracks"] == 1
        assert client.get("/api/tracks").json()["total"] == 1
        with app.state.session_factory() as session:
            assert session.get(Track, "track-1") is not None
            assert session.get(Artist, "artist-1") is not None
            assert session.get(Album, "album-1") is not None
            assert session.get(SavedTrack, "track-1") is None

        mode["response"] = "failure"
        failed = sync()
        assert failed["status"] == "failed"
        assert failed["last_successful_sync"] == repeated["last_successful_sync"]
        assert failed["saved_tracks"] == 1
        assert client.get("/api/tracks").json()["total"] == 1

        # A failed account switch cannot replace the current account's library.
        account["id"] = "account-b"
        authenticate()
        mode["response"] = "failure"
        failed_switch = sync()
        assert failed_switch["status"] == "failed"
        assert failed_switch["account_id"] == "account-a"
        assert client.get("/api/tracks").json()["total"] == 1

        # A complete new-account snapshot replaces, rather than mixes, the library.
        mode["response"] = "switch"
        switched = sync()
        assert switched["status"] == "succeeded"
        assert switched["account_id"] == "account-b"
        assert client.get("/api/tracks").json()["total"] == 1
        with app.state.session_factory() as session:
            assert session.get(SavedTrack, "track-0") is None
            assert session.get(SavedTrack, "other-track") is not None

        # The active claim rejects concurrent sync while leaving analytics usable.
        with app.state.session_factory() as session:
            state = session.scalar(
                select(SyncState).where(SyncState.account_id == "account-b")
            )
            state.status = "running"
            session.commit()
        conflict = client.post("/api/sync")
        assert conflict.status_code == 409
        assert client.get("/api/sync/status").json()["status"] == "running"
        assert client.get("/api/tracks").json()["total"] == 1

    restarted_app = create_app(settings)
    with TestClient(restarted_app, follow_redirects=False) as client:
        recovered = client.get("/api/sync/status").json()
        assert recovered["status"] == "failed"
        assert recovered["error"] == "Synchronization was interrupted"
        assert recovered["saved_tracks"] == 1
        assert client.get("/api/tracks").json()["total"] == 1
        assert client.post("/api/sync").status_code == 401

        # Switching to an empty library removes relations but retains metadata.
        login = client.get("/auth/login")
        state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
        restarted_app.state.spotify_runtime.http_client_factory = lambda: (
            httpx.AsyncClient(transport=httpx.MockTransport(spotify_response))
        )
        account["id"] = "account-c"
        callback = client.get(f"/auth/callback?code=code&state={state}")
        assert callback.status_code == 302
        mode["response"] = "empty"
        result = client.post("/api/sync")
        assert result.status_code == 202
        empty = client.get("/api/sync/status").json()
        assert empty["status"] == "succeeded"
        assert empty["account_id"] == "account-c"
        assert empty["saved_tracks"] == 0
        assert client.get("/api/tracks").json()["total"] == 0
        with restarted_app.state.session_factory() as session:
            assert session.get(Track, "other-track") is not None
            assert session.get(Artist, "artist-1") is not None
            assert session.get(Album, "album-1") is not None
